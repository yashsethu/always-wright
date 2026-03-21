"""
Lunar Terrain Height Map — GUI
================================
Run:  python heightmap_gui.py
"""

import heapq
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
import cv2
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
from scipy.ndimage import gaussian_filter

# ─────────────────────────────── constants ────────────────────────────────────

BG        = "#0f0f0f"
PANEL_BG  = "#1a1a1a"
ACCENT    = "#4a9eff"
BTN_BG    = "#2a2a2a"
BTN_HOV   = "#3a3a3a"
FG        = "#e8e8e8"
FG_DIM    = "#888888"
FONT      = ("Helvetica", 11)
FONT_SM   = ("Helvetica", 9)
FONT_BOLD = ("Helvetica", 11, "bold")

BLUR_SIGMA   = 2
SMOOTH_3D    = 6
DOWNSAMPLE   = 0.6
GAMMA        = 0.45
HEIGHT_RANGE = (0.30, 0.50)

# ── Set your image path here ──────────────────────────────────────────────────
IMAGE_PATH = "/tmp/cubesat_frame.jpg"
# ─────────────────────────────────────────────────────────────────────────────

# ──────────────────────────── processing core ─────────────────────────────────

def norm(arr):
    lo, hi = arr.min(), arr.max()
    return (arr - lo) / (hi - lo + 1e-8)

def to_gray(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0

def compress_height(arr, gamma, height_range):
    compressed = np.power(np.clip(arr, 0, 1), gamma)
    lo, hi = height_range
    return lo + compressed * (hi - lo)

def algo_inverted_shadow(rgb, blur):
    return norm(gaussian_filter(to_gray(rgb), sigma=blur))

def algo_log_crater_rim(rgb, blur):
    gray = to_gray(rgb)
    scales, weights = [0.7, 1.5, 4.0], [1.0, 0.7, 0.4]
    curvature = np.zeros_like(gray)
    for sigma, w in zip(scales, weights):
        smoothed = gaussian_filter(gray, sigma=sigma)
        lap = cv2.Laplacian((smoothed * 255).astype(np.uint8), cv2.CV_64F)
        curvature += lap.astype(np.float32) * w
    return norm(gaussian_filter(curvature, sigma=0.5))

def frankot_chellappa(gx, gy):
    H, W = gx.shape
    fx = np.fft.fftfreq(W).reshape(1, -1)
    fy = np.fft.fftfreq(H).reshape(-1, 1)
    GX, GY = np.fft.fft2(gx), np.fft.fft2(gy)
    Z_freq = (-1j * fx * GX + -1j * fy * GY) / (fx**2 + fy**2 + 1e-12)
    Z_freq[0, 0] = 0.0
    return np.real(np.fft.ifft2(Z_freq)).astype(np.float32)

def algo_shadow_gradient(rgb, blur):
    g_u8 = (to_gray(rgb) * 255).astype(np.uint8)
    gx = cv2.Sobel(g_u8, cv2.CV_64F, 1, 0, ksize=3).astype(np.float32)
    gy = cv2.Sobel(g_u8, cv2.CV_64F, 0, 1, ksize=3).astype(np.float32)
    return norm(gaussian_filter(frankot_chellappa(gx, gy), sigma=0.5))

def algo_clahe_plains(rgb, blur):
    g = (to_gray(rgb) * 255).astype(np.uint8)
    eq = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8)).apply(g).astype(np.float32)
    return norm(gaussian_filter(eq, sigma=blur))

def algo_dog_texture(rgb, blur):
    g = to_gray(rgb)
    bands = [(0.5, 1.2, 1.0), (1.2, 3.5, 0.6), (3.5, 9.0, 0.3)]
    result = np.zeros_like(g)
    for fs, cs, w in bands:
        result += (gaussian_filter(g, sigma=fs) - gaussian_filter(g, sigma=cs)) * w
    return norm(result)

def algo_hough_craters(rgb, blur):
    H, W = rgb.shape[:2]
    hmap = np.zeros((H, W), dtype=np.float32)
    gray_u8 = (to_gray(rgb) * 255).astype(np.uint8)
    blurred = cv2.GaussianBlur(gray_u8, (0, 0), sigmaX=blur)
    edges   = cv2.Canny(blurred, 20, 60)
    min_r   = max(4,  int(min(H, W) * 0.01))
    max_r   = max(20, int(min(H, W) * 0.25))
    circles = cv2.HoughCircles(edges, cv2.HOUGH_GRADIENT, dp=1.5,
                               minDist=min_r*2, param1=40, param2=18,
                               minRadius=min_r, maxRadius=max_r)
    if circles is not None:
        circles = np.round(circles[0]).astype(int)
        ys, xs = np.mgrid[0:H, 0:W]
        for (cx, cy, r) in circles:
            r = max(r, 3)
            y0, y1 = max(0, cy-r-2), min(H, cy+r+3)
            x0, x1 = max(0, cx-r-2), min(W, cx+r+3)
            dist   = np.sqrt((xs[y0:y1,x0:x1]-cx)**2 + (ys[y0:y1,x0:x1]-cy)**2).astype(np.float32)
            r_norm = np.clip(dist / r, 0, 1.2)
            rim    =  1.0 * np.exp(-8.0 * (r_norm - 1.0)**2)
            pit    = -0.7 * (1.0 - r_norm**2) * (r_norm < 0.85)
            hmap[y0:y1, x0:x1] += rim + pit
    hmap -= hmap.min()
    return norm(gaussian_filter(hmap, sigma=blur * 1.5))

# ── Your custom weights from your version ─────────────────────────────────────
ALGOS_DEF = [
    ("Inv. Shadow (luminance)",       algo_inverted_shadow,  0.5),
    ("LoG Crater Rim (curvature)",    algo_log_crater_rim,   1.6),
    ("Shadow Gradient (Sobel)",       algo_shadow_gradient,  2.0),
    ("CLAHE Plains (local contrast)", algo_clahe_plains,     1.2),
    ("DoG Texture (fine detail)",     algo_dog_texture,      6.0),
    ("Hough Craters (geometry)",      algo_hough_craters,    0.5),
]

def build_ensemble(rgb, blur, progress_cb=None):
    results = {}
    for name, fn, weight in ALGOS_DEF:
        if progress_cb:
            progress_cb(f"Running: {name}...")
        results[name] = (norm(fn(rgb, blur)), weight)
    total_w  = sum(w for _, w in results.values())
    ensemble = sum(hm * w for hm, w in results.values()) / total_w
    ensemble = norm(gaussian_filter(ensemble, sigma=max(1, blur * 0.5)))
    individual = {n: hm for n, (hm, _) in results.items()}
    return ensemble, individual


# ──────────────────────────── Dijkstra pathfinding ────────────────────────────

def _precompute_terrain_costs(hmap):
    h_u8  = (hmap * 255).astype(np.uint8)
    gx    = cv2.Sobel(h_u8, cv2.CV_64F, 1, 0, ksize=3).astype(np.float32)
    gy    = cv2.Sobel(h_u8, cv2.CV_64F, 0, 1, ksize=3).astype(np.float32)
    slope = np.hypot(gx, gy)
    s_max = slope.max()
    if s_max > 0:
        slope /= s_max
    return gx, gy, slope


def dijkstra_path(hmap, start, end,
                  w_height=2.0, w_steepness=9.7, w_direction=1.0, w_curvature=0.1,
                  stride=5):
    """
    Multi-factor strided Dijkstra.

    stride=5 — graph nodes every 5 pixels (~25x fewer nodes, much faster).
    Each edge samples the worst-case cost along the segment so hazards
    between stride nodes are not missed. Path is densified back to pixel
    level after reconstruction so the drawn line is smooth.

    Cost factors:
      1. HEIGHT DIFFERENCE   — max |dh| along segment
      2. STEEPNESS           — max slope along segment
      3. DIRECTION OF TRAVEL — dot(step, slope) at midpoint
      4. CURVATURE           — direction change from previous step
    """
    H, W = hmap.shape
    INF  = float("inf")

    gx, gy, slope = _precompute_terrain_costs(hmap)

    def snap(r, c):
        return (int(np.clip(round(r / stride) * stride, 0, H - 1)),
                int(np.clip(round(c / stride) * stride, 0, W - 1)))

    sr, sc = snap(*start)
    er, ec = snap(*end)

    # 8-connected neighbours at stride distance
    s_neighbours = [(dr, dc)
                    for dr in [-stride, 0, stride]
                    for dc in [-stride, 0, stride]
                    if not (dr == 0 and dc == 0)]

    dist   = np.full((H, W), INF,  dtype=np.float64)
    prev   = np.full((H, W, 2), -1, dtype=np.int32)
    in_dir = np.zeros((H, W, 2),    dtype=np.float32)

    dist[sr, sc] = 0.0
    heap = [(0.0, sr, sc, 0, 0)]

    while heap:
        cost, r, c, idr, idc = heapq.heappop(heap)
        if cost > dist[r, c]:
            continue
        if r == er and c == ec:
            break

        for dr, dc in s_neighbours:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < H and 0 <= nc < W):
                continue

            step = np.hypot(dr, dc)

            # Sample worst-case height change and steepness along segment
            n_samples = max(2, stride)
            max_dh    = 0.0
            max_steep = 0.0
            h_prev    = float(hmap[r, c])
            for t in range(1, n_samples + 1):
                alpha = t / n_samples
                sr_s  = int(np.clip(round(r + dr * alpha), 0, H - 1))
                sc_s  = int(np.clip(round(c + dc * alpha), 0, W - 1))
                h_cur = float(hmap[sr_s, sc_s])
                max_dh    = max(max_dh,    abs(h_cur - h_prev))
                max_steep = max(max_steep, float(slope[sr_s, sc_s]))
                h_prev = h_cur

            # Direction of travel at midpoint
            mid_r = int(np.clip(r + dr // 2, 0, H - 1))
            mid_c = int(np.clip(c + dc // 2, 0, W - 1))
            sx, sy = dc / step, dr / step
            sgx, sgy = float(gx[mid_r, mid_c]), float(gy[mid_r, mid_c])
            smag = max(np.hypot(sgx, sgy), 1e-6)
            dot  = (sx * sgx + sy * sgy) / smag
            dir_cost = (dot + 1.0) * 0.5

            # Curvature
            if idr == 0 and idc == 0:
                curve_cost = 0.0
            else:
                prev_len  = max(np.hypot(idr, idc), 1e-6)
                psx, psy  = idc / prev_len, idr / prev_len
                cos_angle = np.clip(psx * sx + psy * sy, -1.0, 1.0)
                curve_cost = 1.0 - cos_angle

            edge_cost = (w_height    * max_dh
                       + w_steepness * max_steep
                       + w_direction * dir_cost
                       + w_curvature * curve_cost
                       + 0.001)

            new_cost = cost + step * edge_cost
            if new_cost < dist[nr, nc]:
                dist[nr, nc]   = new_cost
                prev[nr, nc]   = [r, c]
                in_dir[nr, nc] = [dr, dc]
                heapq.heappush(heap, (new_cost, nr, nc, dr, dc))

    # Reconstruct strided waypoints
    waypoints = []
    r, c = er, ec
    while not (r == sr and c == sc):
        waypoints.append((r, c))
        pr, pc = int(prev[r, c, 0]), int(prev[r, c, 1])
        if pr == -1:
            return []
        r, c = pr, pc
    waypoints.append((sr, sc))
    waypoints.reverse()

    # Densify back to pixel level
    path = []
    for i in range(len(waypoints) - 1):
        r0, c0 = waypoints[i]
        r1, c1 = waypoints[i + 1]
        steps  = max(abs(r1 - r0), abs(c1 - c0), 1)
        for t in range(steps):
            alpha = t / steps
            path.append((int(round(r0 + (r1 - r0) * alpha)),
                         int(round(c0 + (c1 - c0) * alpha))))
    path.append(waypoints[-1])

    # Restore exact start/end
    if path:
        path[0]  = start
        path[-1] = end

    return path


# ──────────────────────────── figure factories ────────────────────────────────

def make_topographic(hmap, n_levels=18):
    fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
    ax.set_facecolor(BG)
    cf = ax.contourf(hmap, levels=n_levels, cmap="gist_earth", alpha=0.92)
    ax.contour(hmap, levels=n_levels, colors="white", linewidths=0.35, alpha=0.45)
    cb = fig.colorbar(cf, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("Relative Height", color=FG, fontsize=8)
    cb.ax.yaxis.set_tick_params(color=FG)
    plt.setp(cb.ax.yaxis.get_ticklabels(), color=FG, fontsize=7)
    ax.set_title("Topographic Map", color=FG, fontsize=11, fontweight="bold", pad=8)
    ax.axis("off")
    fig.tight_layout(pad=0.5)
    return fig

def make_heightmap_2d(hmap):
    fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
    ax.set_facecolor(BG)
    im = ax.imshow(hmap, cmap="gist_earth", vmin=0, vmax=1, origin="upper")
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("Relative Height", color=FG, fontsize=8)
    cb.ax.yaxis.set_tick_params(color=FG)
    plt.setp(cb.ax.yaxis.get_ticklabels(), color=FG, fontsize=7)
    ax.set_title("2D Height Map", color=FG, fontsize=11, fontweight="bold", pad=8)
    ax.axis("off")
    fig.tight_layout(pad=0.5)
    return fig

def make_3d_surface(hmap, smooth_3d, downsample, gamma, height_range):
    h_ds = max(4, int(hmap.shape[0] * downsample))
    w_ds = max(4, int(hmap.shape[1] * downsample))
    ds   = norm(gaussian_filter(
        cv2.resize(hmap, (w_ds, h_ds), interpolation=cv2.INTER_AREA),
        sigma=smooth_3d))
    Z    = compress_height(ds, gamma, height_range)
    H, W = Z.shape
    X, Y = np.meshgrid(np.linspace(0, 1, W), np.linspace(0, 1, H))
    fig  = plt.figure(figsize=(6, 6), facecolor=BG)
    ax   = fig.add_subplot(111, projection="3d")
    ax.set_facecolor(BG)
    ax.plot_surface(X, Y, Z, facecolors=plt.cm.gray(ds * 0.82 + 0.06),
                    linewidth=0, antialiased=True, shade=True, rstride=1, cstride=1)
    ax.set_title("Interactive 3D  (drag to rotate)", color=FG,
                 fontsize=10, fontweight="bold", pad=4)
    ax.set_axis_off()
    ax.view_init(elev=35.264, azim=45)
    ax.set_box_aspect([W, H, max(1, (height_range[1] - height_range[0]) * 30)])
    fig.tight_layout(pad=0.2)
    return fig

def make_pathfinding_view(hmap, points, path):
    fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
    ax.set_facecolor(BG)
    ax.imshow(hmap, cmap="gist_earth", vmin=0, vmax=1, origin="upper")

    if len(path) > 1:
        rows = [p[0] for p in path]
        cols = [p[1] for p in path]
        ax.plot(cols, rows, color="red", linewidth=1.5, alpha=0.85, zorder=3)

    for i, (r, c) in enumerate(points):
        ax.plot(c, r, "o", color="red", markersize=10,
                markeredgecolor="white", markeredgewidth=1.5, zorder=5)
        ax.text(c + hmap.shape[1] * 0.015, r, ["A", "B"][i],
                color="white", fontsize=9, fontweight="bold",
                va="center", zorder=6)

    n_pts = len(points)
    if n_pts == 0:
        title = "Path Finder  —  click to place point A"
    elif n_pts == 1:
        title = "Path Finder  —  click to place point B"
    elif path:
        title = f"Path Finder  —  path length: {len(path)} px  |  right-click to reset"
    else:
        title = "Path Finder  —  no route found  |  right-click to reset"

    ax.set_title(title, color=FG, fontsize=10, fontweight="bold", pad=8)
    ax.axis("off")
    fig.tight_layout(pad=0.5)
    return fig


# ───────────────────────────────── GUI ────────────────────────────────────────

class HeightMapApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Lunar Height Map Tool")
        self.configure(bg=BG)
        self.minsize(860, 720)
        self.geometry("960x800")

        self.rgb            = None
        self.ensemble       = None
        self.canvas_widget  = None
        self.current_fig    = None
        self._processing    = False

        self._path_mode     = False
        self._path_points   = []
        self._path_result   = []
        self._click_cid     = None

        self._build_ui()
        self._style_ttk()
        self.after(100, self._load_image)

    def _build_ui(self):
        top = tk.Frame(self, bg=BG, pady=10)
        top.pack(fill="x", padx=16)
        tk.Label(top, text="LUNAR HEIGHT MAP", bg=BG, fg=ACCENT,
                 font=("Helvetica", 14, "bold")).pack(side="left", padx=(0, 16))

        self.status_var = tk.StringVar(value="Starting...")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=FG_DIM,
                 font=FONT_SM, anchor="w").pack(fill="x", padx=18, pady=(0, 4))

        ctrl = tk.Frame(self, bg=PANEL_BG, pady=8, padx=14)
        ctrl.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(ctrl, text="Display Mode:", bg=PANEL_BG, fg=FG,
                 font=FONT_BOLD).pack(side="left", padx=(0, 10))
        self.view_var = tk.StringVar(value="2D Height Map")
        self.dropdown = ttk.Combobox(
            ctrl, textvariable=self.view_var,
            values=["Raw Feed", "2D Height Map", "3D Interactive", "Topographic Map"],
            state="readonly", width=20, font=FONT)
        self.dropdown.pack(side="left")
        self.dropdown.bind("<<ComboboxSelected>>", self._on_dropdown_change)

        self.map_frame = tk.Frame(self, bg=PANEL_BG, bd=0,
                                  highlightthickness=1, highlightbackground="#333")
        self.map_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self.placeholder = tk.Label(self.map_frame, text="Loading...",
                                    bg=PANEL_BG, fg=FG_DIM, font=("Helvetica", 15))
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self.progress = ttk.Progressbar(self.map_frame, mode="indeterminate", length=300)

        btn_strip = tk.Frame(self, bg=BG, pady=10)
        btn_strip.pack(fill="x", padx=16, pady=(0, 12))
        self.action_btns = []

        b1 = self._make_btn(btn_strip, "Path Finder", self._activate_path_mode)
        b1.pack(side="left", expand=True, fill="x", padx=5)
        self.action_btns.append(b1)
        self.path_btn = b1

        b2 = self._make_btn(btn_strip, "Best Landing Zone", self._find_landing_zone)
        b2.pack(side="left", expand=True, fill="x", padx=5)
        self.action_btns.append(b2)

        b3 = self._make_btn(btn_strip, "Deep Crater Scan", self._find_deep_craters)
        b3.pack(side="left", expand=True, fill="x", padx=5)
        self.action_btns.append(b3)

        b4 = self._make_btn(btn_strip, "Hazard Overlay", self._hazard_overlay)
        b4.pack(side="left", expand=True, fill="x", padx=5)
        self.action_btns.append(b4)

        # BLE controls
        ble_strip = tk.Frame(self, bg=BG, pady=6)
        ble_strip.pack(fill="x", padx=16, pady=(0, 8))

        self.ble_status = tk.StringVar(value="⬤  Disconnected")
        self.ble_label = tk.Label(ble_strip, textvariable=self.ble_status, bg=BG, fg="#ff4444",
            font=FONT_SM)
        self.ble_label.pack(side="left", padx=(0, 16))

        self._make_btn(ble_strip, "▶  Stream",   lambda: self._ble_cmd('S')).pack(side="left", padx=4)
        self._make_btn(ble_strip, "■  Stop",     lambda: self._ble_cmd('X')).pack(side="left", padx=4)
        self._make_btn(ble_strip, "⏎  Capture",  lambda: self._ble_cmd('C')).pack(side="left", padx=4)

    def _style_ttk(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TCombobox",
                    fieldbackground=BTN_BG, background=BTN_BG,
                    foreground=FG, selectbackground=ACCENT,
                    bordercolor="#444", lightcolor="#444", darkcolor="#444",
                    arrowcolor=FG)
        s.map("TCombobox", fieldbackground=[("readonly", BTN_BG)],
              foreground=[("readonly", FG)])
        s.configure("TProgressbar", troughcolor=PANEL_BG,
                    bordercolor=PANEL_BG, background=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT)

    def _make_btn(self, parent, text, command):
        btn = tk.Button(
            parent, text=text, command=command,
            bg=BTN_BG, fg="#000000",
            activebackground=ACCENT, activeforeground="#000000",
            font=FONT, padx=14, pady=7, cursor="hand2",
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground="#555555",
        )
        btn.bind("<Enter>", lambda _, b=btn: b.config(bg=BTN_HOV, fg=ACCENT))
        btn.bind("<Leave>", lambda _, b=btn: b.config(bg=BTN_BG,  fg="#000000"))
        return btn

    # ── image loading ─────────────────────────────────────────────────────────

    def _load_image(self):
        if self._processing:
            return
        img = cv2.imread(IMAGE_PATH)
        if img is None:
            self.status_var.set(f"Error: could not load {IMAGE_PATH}")
            return
        self.rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        if self.view_var.get() == "Raw Feed":
            plt.close("all")
            self._clear_canvas()
            fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
            ax.set_facecolor(BG)
            ax.imshow(self.rgb, origin="upper")
            ax.axis("off")
            fig.tight_layout(pad=0)
            self.current_fig = fig
            self._embed_figure(fig)
            return

        fname = IMAGE_PATH.replace("\\", "/").split("/")[-1]
        self.status_var.set(f"{fname}  •  processing...")
        self._start_processing()

    def _start_processing(self):
        self._processing = True
        self.ensemble    = None
        self.dropdown.config(state="disabled")
        self.placeholder.place_forget()
        self._clear_canvas()
        self.progress.place(relx=0.5, rely=0.5, anchor="center")
        self.progress.start(12)
        threading.Thread(target=self._process_worker, daemon=True).start()

    def _process_worker(self):
        try:
            ensemble, _ = build_ensemble(
                self.rgb, BLUR_SIGMA,
                progress_cb=lambda msg: self.after(0, self.status_var.set, msg))
            self.ensemble = ensemble
            self.after(0, self._on_processing_done)
        except Exception as e:
            self.after(0, self._on_processing_error, str(e))

    def _on_processing_done(self):
        self.progress.stop()
        self.progress.place_forget()
        self._processing = False
        self.dropdown.config(state="readonly")
        name = self.status_var.get().split("•")[0].strip()
        self.status_var.set(f"{name}  •  ready")
        self._refresh_view()

    def _on_processing_error(self, msg):
        self.progress.stop()
        self.progress.place_forget()
        self._processing = False
        self.status_var.set("Error during processing")
        messagebox.showerror("Processing Error", msg)

    # ── view rendering ────────────────────────────────────────────────────────

    def _on_dropdown_change(self, _event=None):
        self._deactivate_path_mode()
        self._refresh_view()

    def _refresh_view(self):
        if self.ensemble is None and self.view_var.get() != "Raw Feed":
            return
        mode = self.view_var.get()
        self.status_var.set(self.status_var.get().split("•")[0].strip() + f"  •  {mode}")
        plt.close("all")
        self._clear_canvas()

        if mode == "Raw Feed":
            if self.rgb is None:
                return
            fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
            ax.set_facecolor(BG)
            ax.imshow(self.rgb, origin="upper")
            ax.axis("off")
            fig.tight_layout(pad=0)
            self.current_fig = fig
            self._embed_figure(fig)
            return

        if mode == "2D Height Map":
            fig = make_heightmap_2d(self.ensemble)
        elif mode == "3D Interactive":
            fig = make_3d_surface(self.ensemble, SMOOTH_3D, DOWNSAMPLE, GAMMA, HEIGHT_RANGE)
        else:
            fig = make_topographic(self.ensemble)

        self.current_fig = fig
        self._embed_figure(fig)

    def _clear_canvas(self):
        if self._click_cid is not None and self.current_fig is not None:
            try:
                self.current_fig.canvas.mpl_disconnect(self._click_cid)
            except Exception:
                pass
            self._click_cid = None
        if self.canvas_widget is not None:
            self.canvas_widget.get_tk_widget().destroy()
            self.canvas_widget = None

    def _embed_figure(self, fig):
        fig.patch.set_facecolor(BG)
        canvas = FigureCanvasTkAgg(fig, master=self.map_frame)
        canvas.draw()
        widget = canvas.get_tk_widget()
        widget.configure(bg=BG, highlightthickness=0)
        widget.pack(fill="both", expand=True)
        self.canvas_widget = canvas

    # ── path finder ───────────────────────────────────────────────────────────

    def _activate_path_mode(self):
        print("[PATH] Button clicked")
        if self.ensemble is None:
            print("[PATH] Aborted — no ensemble loaded yet")
            return

        print("[PATH] Activating path finder mode")
        self._path_mode   = True
        self._path_points = []
        self._path_result = []

        self.view_var.set("2D Height Map")
        plt.close("all")
        self._clear_canvas()

        fig = make_pathfinding_view(self.ensemble, [], [])
        self.current_fig = fig
        self._embed_figure(fig)

        self.path_btn.config(bg=ACCENT, fg="#000000")

        self._click_cid = fig.canvas.mpl_connect("button_press_event",
                                                  self._on_map_click)
        print(f"[PATH] Click handler connected (cid={self._click_cid})")
        self.status_var.set(
            self.status_var.get().split("•")[0].strip()
            + "  •  Path Finder active — click point A")
        print("[PATH] Ready — waiting for point A click")

    def _deactivate_path_mode(self):
        self._path_mode = False
        if self._click_cid is not None and self.current_fig is not None:
            try:
                self.current_fig.canvas.mpl_disconnect(self._click_cid)
            except Exception:
                pass
            self._click_cid = None
        # Use BTN_BG so it matches _make_btn exactly
        self.path_btn.config(bg=BTN_BG, fg="#000000")

    def _on_map_click(self, event):
        print(f"[CLICK] button={event.button} inaxes={event.inaxes is not None} "
              f"x={event.xdata} y={event.ydata}")

        if event.inaxes is None:
            print("[CLICK] Ignored — click was outside axes")
            return

        if event.button == 3:
            print("[CLICK] Right-click — resetting points")
            self._path_points = []
            self._path_result = []
            self._redraw_path_view()
            self.status_var.set(
                self.status_var.get().split("•")[0].strip()
                + "  •  Path Finder reset — click point A")
            return

        if event.button != 1:
            print(f"[CLICK] Ignored — unexpected button {event.button}")
            return

        H, W = self.ensemble.shape
        col = int(np.clip(round(event.xdata), 0, W - 1))
        row = int(np.clip(round(event.ydata), 0, H - 1))
        print(f"[CLICK] Mapped to pixel row={row}, col={col}  (image {H}x{W})")

        if len(self._path_points) >= 2:
            print("[CLICK] Already had 2 points — resetting before adding new one")
            self._path_points = []
            self._path_result = []

        self._path_points.append((row, col))
        print(f"[CLICK] Points so far: {self._path_points}")
        self._redraw_path_view()

        if len(self._path_points) == 1:
            print("[PATH] Point A placed — waiting for B")
            self.status_var.set(
                self.status_var.get().split("•")[0].strip()
                + "  •  Point A placed — click point B")
        elif len(self._path_points) == 2:
            print(f"[PATH] Both points set: A={self._path_points[0]} B={self._path_points[1]}")
            print("[PATH] Launching Dijkstra thread...")
            self.status_var.set(
                self.status_var.get().split("•")[0].strip()
                + "  •  Computing path...")
            threading.Thread(target=self._run_dijkstra, daemon=True).start()

    def _run_dijkstra(self):
        start, end = self._path_points[0], self._path_points[1]
        print(f"[DIJKSTRA] Running from {start} to {end} on {self.ensemble.shape} map...")
        path = dijkstra_path(self.ensemble, start, end)
        print(f"[DIJKSTRA] Done — path length: {len(path)} pixels")
        self._path_result = path
        self.after(0, self._on_path_done)

    def _on_path_done(self):
        self._redraw_path_view()
        if self._path_result:
            self.status_var.set(
                self.status_var.get().split("•")[0].strip()
                + f"  •  Path found: {len(self._path_result)} px — right-click to reset")
        else:
            self.status_var.set(
                self.status_var.get().split("•")[0].strip()
                + "  •  No path found — right-click to reset")

    def _redraw_path_view(self):
        if self.current_fig is None:
            return

        ax = self.current_fig.axes[0]
        for artist in ax.lines + ax.texts:
            artist.remove()
        for artist in list(ax.collections):
            if hasattr(artist, "_is_path_marker"):
                artist.remove()

        if len(self._path_result) > 1:
            rows = [p[0] for p in self._path_result]
            cols = [p[1] for p in self._path_result]
            ax.plot(cols, rows, color="red", linewidth=1.5, alpha=0.85, zorder=3)

        for i, (r, c) in enumerate(self._path_points):
            ax.plot(c, r, "o", color="red", markersize=10,
                    markeredgecolor="white", markeredgewidth=1.5, zorder=5)
            ax.text(c + self.ensemble.shape[1] * 0.015, r, ["A", "B"][i],
                    color="white", fontsize=9, fontweight="bold",
                    va="center", zorder=6)

        n = len(self._path_points)
        if n == 0:
            title = "Path Finder  —  click to place point A"
        elif n == 1:
            title = "Path Finder  —  click to place point B"
        elif self._path_result:
            title = f"Path Finder  —  {len(self._path_result)} px  |  right-click to reset"
        else:
            title = "Path Finder  —  no route  |  right-click to reset"
        ax.set_title(title, color=FG, fontsize=10, fontweight="bold", pad=8)

        self.current_fig.canvas.draw_idle()

    # ── Button 2: Settlement + Landing Zone ─────────────────────────────────────
    #
    # Two-tier analysis:
    #   SETTLEMENT ZONE  (large cyan box)  — the largest contiguous flat region
    #     suitable for building a base. Uses a large sliding window (~20% of image)
    #     to find the macro-scale flat area.
    #   LANDING ZONE     (small green box) — the single flattest patch WITHIN the
    #     settlement zone, sized for a lander footprint (~5% of image).
    #     Both are overlaid on the real photo, not the height map.

    def _find_landing_zone(self):
        if self.ensemble is None:
            return
        print("[LANDING] Scanning for settlement + landing zones...")
        self.status_var.set(
            self.status_var.get().split("•")[0].strip() + "  •  Scanning zones...")
        threading.Thread(target=self._landing_worker, daemon=True).start()

    def _landing_worker(self):
        from scipy.ndimage import label as scipy_label
        hmap = self.ensemble
        H, W = hmap.shape

        # ── Per-pixel flatness score ───────────────────────────────────────────
        h_u8  = (hmap * 255).astype(np.uint8)
        gx    = cv2.Sobel(h_u8, cv2.CV_64F, 1, 0, ksize=3).astype(np.float32)
        gy    = cv2.Sobel(h_u8, cv2.CV_64F, 0, 1, ksize=3).astype(np.float32)
        slope = norm(np.hypot(gx, gy))

        mean_k    = np.ones((7, 7), dtype=np.float32) / 49
        h_mean    = cv2.filter2D(hmap.astype(np.float32), -1, mean_k)
        h_sq_mean = cv2.filter2D((hmap**2).astype(np.float32), -1, mean_k)
        roughness = norm(np.sqrt(np.clip(h_sq_mean - h_mean**2, 0, None)))

        elev_penalty = norm(np.abs(hmap - np.percentile(hmap, 40)))
        flatness = np.clip(1.0 - slope*0.5 - roughness*0.3 - elev_penalty*0.1, 0, 1)

        # ── Settlement zone: largest solid connected flat region ─────────────────
        #
        # The key insight: we need to ERODE before labelling, then DILATE back.
        # This is called "opening" and it breaks thin bridges between distant
        # flat areas — without it, two flat patches connected by a single-pixel
        # ridge across a crater count as one giant region spanning the whole map.
        #
        # Pipeline:
        #   1. High threshold (70th pct) — only genuinely flat pixels qualify
        #   2. Erode with a medium kernel (~3% of image) — severs thin bridges
        #      and removes isolated specks
        #   3. Label connected components on the eroded mask
        #   4. Score each component by area × mean_flatness
        #   5. Dilate the winning component back (~2% kernel) to recover
        #      pixels lost in the erosion, staying within the original flat mask
        #   6. Fill interior holes with flood fill so the region has no major gaps

        # Threshold at 45th percentile — generous enough to include large flat plains
        flat_thresh = np.percentile(flatness, 35)
        binary = (flatness >= flat_thresh).astype(np.uint8)

        # Erosion kernel: ~1.2% of image — only severs very thin bridges
        # (single-pixel ridges across craters), but preserves large flat plains
        erode_k = max(3, int(min(H, W) * 0.012))
        erode_k += (erode_k % 2 == 0)
        erode_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                  (erode_k, erode_k))
        eroded = cv2.erode(binary, erode_kernel, iterations=1)

        labeled, n_labels = scipy_label(eroded)
        best_label = 0
        best_score = -1.0
        for lbl in range(1, n_labels + 1):
            region_mask = labeled == lbl
            area        = float(region_mask.sum())
            if area < 50:
                continue
            mean_flat = float(flatness[region_mask].mean())
            # Cap area contribution so flatness quality still matters
            # sqrt(area) prevents the single biggest blob always winning
            score = (area ** 0.6) * mean_flat
            if score > best_score:
                best_score = score
                best_label = lbl

        if best_label == 0:
            settlement_mask = binary
        else:
            # Dilate the winner back out generously to recover pixels lost to erosion
            winner = (labeled == best_label).astype(np.uint8)
            dilate_k = max(5, int(min(H, W) * 0.025))
            dilate_k += (dilate_k % 2 == 0)
            dilate_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                       (dilate_k, dilate_k))
            dilated = cv2.dilate(winner, dilate_kernel, iterations=1)
            # Constrain to original flat mask so we don't bleed into craters
            settlement_mask = (dilated & binary).astype(np.uint8)

            # Fill interior holes — any enclosed gap fully surrounded by
            # settlement pixels gets filled in so the region is solid
            h_fill, w_fill = settlement_mask.shape
            flood_seed = np.zeros((h_fill + 2, w_fill + 2), np.uint8)
            flood = (1 - settlement_mask).astype(np.uint8).copy()
            cv2.floodFill(flood, flood_seed, (0, 0), 1)
            interior_holes = (flood == 0).astype(np.uint8)
            settlement_mask = np.clip(settlement_mask + interior_holes, 0, 1).astype(np.uint8)

        # Safety cap: if the region still covers more than 50% of the image,
        # re-run with a tighter threshold until it's within bounds
        max_coverage = 0.50
        for tighten in [0.55, 0.62, 0.70, 0.78]:
            coverage = settlement_mask.sum() / (H * W)
            if coverage <= max_coverage:
                break
            flat_thresh2 = np.percentile(flatness, tighten * 100)
            binary2  = (flatness >= flat_thresh2).astype(np.uint8)
            eroded2  = cv2.erode(binary2, erode_kernel, iterations=1)
            lab2, _  = scipy_label(eroded2)
            best2, sc2 = 0, -1.0
            for lbl in range(1, lab2.max() + 1):
                rm = lab2 == lbl
                a  = float(rm.sum())
                if a < 50: continue
                s = (a ** 0.6) * float(flatness[rm].mean())
                if s > sc2: sc2, best2 = s, lbl
            if best2:
                w2 = (lab2 == best2).astype(np.uint8)
                d2 = cv2.dilate(w2, dilate_kernel, iterations=1)
                sm2 = (d2 & binary2).astype(np.uint8)
                flood2 = (1 - sm2).astype(np.uint8).copy()
                fs2 = np.zeros((h_fill + 2, w_fill + 2), np.uint8)
                cv2.floodFill(flood2, fs2, (0, 0), 1)
                sm2 = np.clip(sm2 + (flood2 == 0), 0, 1).astype(np.uint8)
                settlement_mask = sm2
        s_area      = int(settlement_mask.sum())
        s_mean_flat = float(flatness[settlement_mask.astype(bool)].mean())
        print(f"[LANDING] Settlement region: {s_area} px, mean flatness={s_mean_flat:.3f}")

        # ── Landing zone: flattest small window inside the settlement region ───
        l_win  = max(15, int(min(H, W) * 0.05))
        kernel = np.ones((l_win, l_win), dtype=np.float32) / (l_win * l_win)
        # Only score inside settlement
        flat_inside = flatness * settlement_mask.astype(np.float32)
        l_score_map = cv2.filter2D(flat_inside, -1, kernel)
        pad = l_win // 2
        l_score_map[:pad,:] = l_score_map[-pad:,:] = 0
        l_score_map[:,:pad] = l_score_map[:,-pad:] = 0
        l_idx = np.argmax(l_score_map)
        l_r, l_c = np.unravel_index(l_idx, l_score_map.shape)
        l_score = float(l_score_map[l_r, l_c])
        print(f"[LANDING] Landing spot: row={l_r}, col={l_c}, score={l_score:.3f}")

        self.after(0, self._show_landing_zone,
                   settlement_mask, s_mean_flat, s_area,
                   int(l_r), int(l_c), l_win, l_score)

    def _show_landing_zone(self, settlement_mask, s_mean_flat, s_area,
                                 l_r, l_c, l_win, l_score):
        from matplotlib.patches import Patch
        plt.close("all")
        self._clear_canvas()
        self._deactivate_path_mode()

        fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
        ax.set_facecolor(BG)

        # Background: real photo
        ax.imshow(self.rgb, origin="upper")

        # Settlement zone: irregular cyan filled overlay
        H, W = settlement_mask.shape
        s_overlay = np.zeros((H, W, 4), dtype=np.float32)
        s_overlay[settlement_mask.astype(bool)] = [0.00, 0.81, 1.00, 0.30]
        ax.imshow(s_overlay, origin="upper", zorder=2)

        # Settlement outline — draw contour of the mask
        contours, _ = cv2.findContours(
            settlement_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            pts = cnt[:, 0, :]   # shape (N, 2): (x, y)
            ax.plot(pts[:, 0], pts[:, 1],
                    color="#00cfff", linewidth=1.8, zorder=3)

        # Label the settlement zone near its centroid
        ys, xs = np.where(settlement_mask)
        if len(xs):
            cx_s, cy_s = int(xs.mean()), int(ys.mean())
            ax.text(cx_s, cy_s,
                    f"SETTLEMENT  {s_area:,} px  flat={s_mean_flat:.3f}",
                    color="#00cfff", fontsize=7.5, fontweight="bold",
                    ha="center", va="center", zorder=6,
                    bbox=dict(facecolor="#00000077", edgecolor="none",
                              boxstyle="round,pad=0.3"))

        # Landing zone: small green rectangle
        l_half = l_win // 2
        ax.add_patch(plt.Rectangle(
            (l_c - l_half, l_r - l_half), l_win, l_win,
            linewidth=2, edgecolor="#39ff14",
            facecolor="#39ff1430", zorder=4))
        ax.plot(l_c, l_r, "+", color="#39ff14",
                markersize=14, markeredgewidth=2.5, zorder=6)
        ax.text(l_c, l_r + l_half + H*0.018,
                f"LANDING  {l_score:.3f}",
                color="#39ff14", fontsize=8, fontweight="bold",
                ha="center", zorder=5)

        legend = [Patch(facecolor="#00cfff55", edgecolor="#00cfff",
                        label=f"Settlement zone  ({s_area:,} px)"),
                  Patch(facecolor="#39ff1455", edgecolor="#39ff14",
                        label=f"Landing zone  ({l_win}x{l_win} px)")]
        ax.legend(handles=legend, loc="lower left", fontsize=8,
                  facecolor="#111", edgecolor="#444",
                  labelcolor="white", framealpha=0.88)

        ax.set_title("Settlement + Landing Zone Analysis",
                     color=FG, fontsize=10, fontweight="bold", pad=8)
        ax.axis("off")
        fig.tight_layout(pad=0.5)
        self.current_fig = fig
        self._embed_figure(fig)
        self.status_var.set(
            self.status_var.get().split("•")[0].strip()
            + f"  •  Settlement {s_area:,}px  |  Landing score {l_score:.3f}")

    # ── Button 3: Resource Crater Scan ───────────────────────────────────────
    #
    # Scoring based on published lunar resource science:
    #   SHADOW PROXY     (LCROSS 2010, Nozette 1996) — dark floors trap volatiles
    #   DEPTH/DIAMETER   (Pike 1977, Melosh 1989)    — fresh craters = more melt
    #   RIM PROMINENCE   (Sharpton 1994)             — intact rim = preserved melt sheet
    #   FLOOR FLATNESS   (ESA PROSPECT, NASA Artemis) — flat floor = pooled impact melt
    #   SIZE PREFERENCE  (Fa & Jin 2010, LRO LEND)   — mid-size craters optimal

    def _find_deep_craters(self):
        if self.ensemble is None:
            return
        print("[RESOURCES] Scanning for resource craters...")
        self.status_var.set(
            self.status_var.get().split("•")[0].strip() + "  •  Scanning resource craters...")
        threading.Thread(target=self._crater_worker, daemon=True).start()

    def _crater_worker(self):
        hmap = self.ensemble
        H, W = hmap.shape

        regional_blur = gaussian_filter(hmap, sigma=max(H, W) * 0.04)
        local_blur    = gaussian_filter(hmap, sigma=2)
        depth_map     = norm(np.clip(regional_blur - local_blur, 0, None))

        depth_u8 = (depth_map * 255).astype(np.uint8)
        edges    = cv2.Canny(cv2.GaussianBlur(depth_u8, (0, 0), 2), 15, 50)
        min_r    = max(6,  int(min(H, W) * 0.012))
        max_r    = max(30, int(min(H, W) * 0.18))

        circles = cv2.HoughCircles(edges, cv2.HOUGH_GRADIENT, dp=1.5,
                                   minDist=min_r * 2, param1=35, param2=14,
                                   minRadius=min_r, maxRadius=max_r)
        if circles is None:
            self.after(0, self._show_deep_craters, [], depth_map)
            return

        craters = []
        for cx, cy, r in np.round(circles[0]).astype(int):
            # Clamp to valid image bounds — Hough can return centres outside the image
            cx = int(np.clip(cx, 0, W - 1))
            cy = int(np.clip(cy, 0, H - 1))
            r  = max(int(r), 4)

            # Build annular mask without a closure (capture cx/cy explicitly)
            def annular_vals(r_in, r_out, _cx=cx, _cy=cy):
                ys, xs = np.ogrid[-_cy:H-_cy, -_cx:W-_cx]
                mask = (xs*xs + ys*ys >= r_in**2) & (xs*xs + ys*ys < r_out**2)
                v = hmap[mask]
                return v if v.size > 0 else np.array([0.0])

            floor_r   = max(1, int(r * 0.55))
            rim_inner = max(1, int(r * 0.80))
            rim_outer = min(int(r * 1.25), min(H // 2, W // 2))

            floor_vals = annular_vals(0,         floor_r)
            rim_vals   = annular_vals(rim_inner,  rim_outer)

            abs_depth    = float(regional_blur[cy, cx]) - float(np.mean(floor_vals))
            dd_score     = float(np.clip((abs_depth / (2*r + 1e-8)) / 0.25, 0, 1))

            global_mean  = float(hmap.mean())
            shadow_score = float(np.clip((global_mean - float(np.mean(floor_vals))) / (global_mean + 1e-8), 0, 1))

            rim_prom     = float(np.clip((float(np.mean(rim_vals)) - float(np.mean(floor_vals))) * 5, 0, 1))

            r_frac       = r / min(H, W)
            size_score   = float(np.exp(-((r_frac - 0.07)**2) / (2 * 0.04**2)))

            flat_score   = float(np.clip(1.0 - float(np.std(floor_vals)) * 10, 0, 1))

            resource_score = (shadow_score * 0.30 + dd_score * 0.25 +
                              rim_prom * 0.20 + flat_score * 0.15 + size_score * 0.10)

            craters.append({"score": resource_score, "cy": cy, "cx": cx, "r": r,
                            "dd": dd_score, "shadow": shadow_score,
                            "rim": rim_prom, "size": size_score, "floor": flat_score})

        craters.sort(key=lambda x: x["score"], reverse=True)
        craters = craters[:8]
        print(f"[RESOURCES] Top {len(craters)} resource craters scored")
        self.after(0, self._show_deep_craters, craters, depth_map)

    def _show_deep_craters(self, craters, depth_map):
        from matplotlib.patches import Patch
        plt.close("all")
        self._clear_canvas()
        self._deactivate_path_mode()

        fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
        ax.set_facecolor(BG)
        ax.imshow(self.rgb, origin="upper")

        tier_colors = ["#ff4444","#ff8844","#ffcc00","#aaff44",
                       "#44ffcc","#44aaff","#cc88ff","#ffffff"]

        for i, c in enumerate(craters):
            col = tier_colors[i % len(tier_colors)]
            ax.add_patch(plt.Circle((c["cx"], c["cy"]), c["r"],
                                    linewidth=1.8, edgecolor=col,
                                    facecolor=col+"18", zorder=4))
            ax.plot(c["cx"], c["cy"], "x", color=col,
                    markersize=8, markeredgewidth=1.5, zorder=5)
            label = (f"#{i+1} {c['score']:.2f}\n"
                     f"shd:{c['shadow']:.2f} d/D:{c['dd']:.2f}")
            ax.text(c["cx"] + depth_map.shape[1]*0.012, c["cy"],
                    label, color=col, fontsize=6.5, fontweight="bold",
                    va="center", zorder=6,
                    bbox=dict(facecolor="#00000066", edgecolor="none",
                              boxstyle="round,pad=0.15"))

        ax.legend(handles=[Patch(facecolor="#00000000", edgecolor="#aaaaaa",
                  label="Score: shadow(30%) d/D(25%) rim(20%) floor(15%) size(10%)")],
                  loc="lower left", fontsize=7, facecolor="#111",
                  edgecolor="#444", labelcolor="white", framealpha=0.88)

        title = (f"Resource Crater Scan  —  top {len(craters)} by resource potential"
                 if craters else "Resource Crater Scan  —  no craters detected")
        ax.set_title(title, color=FG, fontsize=10, fontweight="bold", pad=8)
        ax.axis("off")
        fig.tight_layout(pad=0.5)
        self.current_fig = fig
        self._embed_figure(fig)
        self.status_var.set(
            self.status_var.get().split("•")[0].strip()
            + f"  •  {len(craters)} resource craters ranked")

    # ── Button 4: Hazard Overlay (aggressive) ────────────────────────────────
    #
    # Four-tier classification. Slope is squared, pits are cubed, roughness
    # added — all to exaggerate hazards vs the previous gentle version.
    # Thresholds tightened: safe zone is only < 18% hazard (was < 30%).

    def _hazard_overlay(self):
        if self.ensemble is None:
            return
        print("[HAZARD] Computing aggressive hazard overlay...")
        self.status_var.set(
            self.status_var.get().split("•")[0].strip() + "  •  Computing hazard overlay...")
        threading.Thread(target=self._hazard_worker, daemon=True).start()

    def _hazard_worker(self):
        hmap = self.ensemble
        H, W = hmap.shape

        h_u8 = (hmap * 255).astype(np.uint8)
        gx   = cv2.Sobel(h_u8, cv2.CV_64F, 1, 0, ksize=3).astype(np.float32)
        gy   = cv2.Sobel(h_u8, cv2.CV_64F, 0, 1, ksize=3).astype(np.float32)
        slope = norm(np.hypot(gx, gy) ** 2)  # squared = aggressive

        regional  = gaussian_filter(hmap, sigma=max(H, W) * 0.04)
        pits      = norm(np.clip(regional - hmap, 0, None) ** 3)  # cubed

        mean_k    = np.ones((9, 9), dtype=np.float32) / 81
        h_f       = hmap.astype(np.float32)
        h_mean    = cv2.filter2D(h_f, -1, mean_k)
        h_sq_mean = cv2.filter2D(h_f**2, -1, mean_k)
        roughness = norm(np.sqrt(np.clip(h_sq_mean - h_mean**2, 0, None)))

        hazard = norm(slope * 0.45 + pits * 0.35 + roughness * 0.20)

        safe    = hazard < 0.18
        caution = (hazard >= 0.18) & (hazard < 0.38)
        warning = (hazard >= 0.38) & (hazard < 0.60)
        danger  = hazard >= 0.60

        self.after(0, self._show_hazard_overlay, safe, caution, warning, danger, hazard)

    def _show_hazard_overlay(self, safe, caution, warning, danger, hazard):
        from matplotlib.patches import Patch
        plt.close("all")
        self._clear_canvas()
        self._deactivate_path_mode()

        fig, ax = plt.subplots(figsize=(6, 6), facecolor=BG)
        ax.set_facecolor(BG)
        ax.imshow(self.rgb, origin="upper", alpha=0.45)

        H, W = hazard.shape
        overlay = np.zeros((H, W, 4), dtype=np.float32)
        overlay[safe,    :] = [0.08, 0.92, 0.22, 0.50]
        overlay[caution, :] = [1.00, 0.92, 0.00, 0.55]
        overlay[warning, :] = [1.00, 0.45, 0.00, 0.62]
        overlay[danger,  :] = [0.95, 0.08, 0.08, 0.75]
        ax.imshow(overlay, origin="upper", zorder=2)

        s_pct = float(safe.mean())    * 100
        c_pct = float(caution.mean()) * 100
        w_pct = float(warning.mean()) * 100
        d_pct = float(danger.mean())  * 100

        legend = [
            Patch(facecolor="#15eb38", label=f"Safe     {s_pct:.1f}%   (< 18%)"),
            Patch(facecolor="#ffeb00", label=f"Caution  {c_pct:.1f}%   (18-38%)"),
            Patch(facecolor="#ff7200", label=f"Warning  {w_pct:.1f}%   (38-60%)"),
            Patch(facecolor="#f21414", label=f"Danger   {d_pct:.1f}%   (> 60%)"),
        ]
        ax.legend(handles=legend, loc="lower left", fontsize=7.5,
                  facecolor="#111", edgecolor="#444",
                  labelcolor="white", framealpha=0.90)

        ax.set_title(f"Hazard Overlay  —  {s_pct:.1f}% safe  |  slope^2 + pits^3 + roughness",
                     color=FG, fontsize=10, fontweight="bold", pad=8)
        ax.axis("off")
        fig.tight_layout(pad=0.5)
        self.current_fig = fig
        self._embed_figure(fig)
        self.status_var.set(
            self.status_var.get().split("•")[0].strip()
            + f"  •  {s_pct:.0f}% safe / {d_pct:.0f}% danger")
    
    def set_ble_connected(self, connected):
        if connected:
            self.ble_status.set("⬤  Connected")
            self.ble_label.config(fg="#39ff14")
        else:
            self.ble_status.set("⬤  Disconnected")
            self.ble_label.config(fg="#ff4444")

    def _ble_cmd(self, cmd):
        # Set by client.py after connecting
        if hasattr(self, '_send_ble_cmd'):
            self._send_ble_cmd(cmd)


# ─────────────────────────────── entry ────────────────────────────────────────

if __name__ == "__main__":
    app = HeightMapApp()
    app.mainloop()