import asyncio
import struct
import os
import time
import threading
import queue
from datetime import datetime
from io import BytesIO
from bleak import BleakClient, BleakScanner
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich import print as rprint
import tkinter as tk
from PIL import Image, ImageTk

SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID     = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID    = '12345678-1234-5678-1234-56789abcdef2'

SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'images')
os.makedirs(SAVE_DIR, exist_ok=True)

console       = Console()
image_buffer  = bytearray()
expected_size = None
transfer_start = None
frame_queue = queue.Queue(maxsize=1) 

# ── Tkinter display window ────────────────────────────────────────────────────

class LiveView(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("cubesat live view")
        self.configure(bg='black')
        self.label = tk.Label(self, bg='black')
        self.label.pack()
        self.fps_label = tk.Label(self, text="", bg='black', fg='#00ff88',
                                  font=('Menlo', 12))
        self.fps_label.pack()
        self.frame_count = 0
        self.t0 = time.time()
        self.poll()

    def poll(self):
        try:
            while True:
                frame_bytes = frame_queue.get_nowait()
                img = Image.open(BytesIO(frame_bytes))
                img = img.resize((480, 360), Image.NEAREST)  # NEAREST is faster than LANCZOS
                photo = ImageTk.PhotoImage(img)
                self.label.configure(image=photo)
                self.label.image = photo  # keep reference
                self.frame_count += 1
                fps = self.frame_count / (time.time() - self.t0)
                self.fps_label.configure(text=f"{fps:.1f} FPS  •  {len(frame_bytes):,} bytes/frame")
        except queue.Empty:
            pass
        self.after(16, self.poll)  # ~60Hz poll

# ── BLE data handler ──────────────────────────────────────────────────────────

progress = None
task_id  = None
streaming = False

def on_data(sender, data):
    global image_buffer, expected_size, transfer_start

    if expected_size is None and len(data) == 4:
        expected_size  = struct.unpack('>I', bytes(data))[0]
        image_buffer   = bytearray()
        transfer_start = time.time()
        if progress and task_id is not None and not streaming:
            progress.update(task_id, total=expected_size, visible=True)
        return

    image_buffer.extend(data)
    if progress and task_id is not None and not streaming:
        progress.update(task_id, completed=len(image_buffer))

    if expected_size and len(image_buffer) >= expected_size:
        frame = bytes(image_buffer[:expected_size])

        if streaming:
            # Always discard old frame and show newest
            while not frame_queue.empty():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    break
            try:
                frame_queue.put_nowait(frame)
            except queue.Full:
                pass
        else:
            # Single capture — save to disk
            elapsed  = time.time() - transfer_start
            filename = os.path.join(SAVE_DIR, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
            with open(filename, 'wb') as f:
                f.write(frame)
            if progress and task_id is not None:
                progress.update(task_id, visible=False)
            rprint(f"\n[bold green]✓[/bold green] Saved [cyan]{filename}[/cyan]  "
                   f"([dim]{expected_size:,} bytes in {elapsed:.2f}s — "
                   f"{expected_size / elapsed / 1024:.1f} KB/s[/dim])\n")

        expected_size = None
        image_buffer  = bytearray()

# ── BLE client ────────────────────────────────────────────────────────────────

async def ble_main(loop_ref):
    global progress, task_id, streaming

    console.print(Panel.fit("[bold cyan]cubesat[/bold cyan] BLE camera client",
                            subtitle="scanning..."))

    device = await BleakScanner.find_device_by_name("cubesat", timeout=20)
    if not device:
        rprint("[bold red]✗[/bold red] cubesat not found — is the server running?")
        return

    async with BleakClient(device, timeout=20) as client:
        console.print(f"[bold green]✓[/bold green] Connected to [cyan]{device.name}[/cyan] "
                      f"([dim]{device.address}[/dim])\n")
        await client.start_notify(DATA_UUID, on_data)

        with Progress(
            "[progress.description]{task.description}",
            BarColumn(), DownloadColumn(),
            TransferSpeedColumn(), TimeRemainingColumn(),
            console=console, transient=True,
        ) as prog:
            progress = prog
            task_id  = prog.add_task("Receiving...", total=None, visible=False)

            console.print(
                "  [bold]Enter[/bold] — capture  "
                "[bold]s[/bold] — start stream  "
                "[bold]x[/bold] — stop stream  "
                "[bold]q[/bold] — quit\n"
            )

            while True:
                cmd = await loop_ref.run_in_executor(None, input, "  › ")
                cmd = cmd.strip().lower()

                if cmd == 'q':
                    if streaming:
                        await client.write_gatt_char(CMD_UUID, b'X', response=False)
                    rprint("[dim]Disconnecting...[/dim]")
                    break

                elif cmd == 's':
                    streaming = True
                    console.print("[yellow]▶[/yellow]  Starting live stream...")
                    await client.write_gatt_char(CMD_UUID, b'S', response=False)

                elif cmd == 'x':
                    streaming = False
                    console.print("[red]■[/red]  Stopping stream")
                    await client.write_gatt_char(CMD_UUID, b'X', response=False)

                else:
                    streaming = False
                    console.print("[yellow]⏎[/yellow]  Capturing...")
                    await client.write_gatt_char(CMD_UUID, b'C', response=False)

def run_ble():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_main(loop))
    loop.close()

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # BLE runs in a background thread, tkinter owns the main thread
    ble_thread = threading.Thread(target=run_ble, daemon=True)
    ble_thread.start()

    app = LiveView()
    app.mainloop()