import asyncio
import struct
import os
import threading
from datetime import datetime
from io import BytesIO
from bleak import BleakClient, BleakScanner
from rich.console import Console
from rich.panel import Panel
from rich import print as rprint
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'utils'))
import utils as heightmap

SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID     = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID    = '12345678-1234-5678-1234-56789abcdef2'

SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'images')
os.makedirs(SAVE_DIR, exist_ok=True)

console       = Console()
image_buffer  = bytearray()
expected_size = None
app           = None  # set after HeightMapApp is created

# ── BLE data handler ──────────────────────────────────────────────────────────

def on_data(sender, data):
    global image_buffer, expected_size

    if expected_size is None and len(data) == 4:
        expected_size = struct.unpack('>I', bytes(data))[0]
        image_buffer  = bytearray()
        return

    image_buffer.extend(data)

    if expected_size and len(image_buffer) >= expected_size:
        frame = bytes(image_buffer[:expected_size])

        # Save to temp path for heightmap to pick up
        with open(heightmap.IMAGE_PATH, 'wb') as f:
            f.write(frame)

        # Also save timestamped copy
        filename = os.path.join(SAVE_DIR, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        with open(filename, 'wb') as f:
            f.write(frame)

        # Trigger heightmap reprocess on the main thread
        if app is not None:
            app.after(0, app._load_image)

        expected_size = None
        image_buffer  = bytearray()

# ── BLE client ────────────────────────────────────────────────────────────────

async def ble_main(loop_ref):
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

        console.print(
            "  [bold]Enter[/bold] — capture  "
            "[bold]s[/bold] — start stream  "
            "[bold]x[/bold] — stop stream  "
            "[bold]q[/bold] — quit\n"
        )

        streaming = False
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
                console.print("[yellow]▶[/yellow]  Starting stream...")
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
    # Write a blank placeholder so HeightMapApp doesn't fail on startup
    from PIL import Image
    Image.new('RGB', (160, 120), color=(30, 30, 30)).save(heightmap.IMAGE_PATH)

    # BLE in background thread
    ble_thread = threading.Thread(target=run_ble, daemon=True)
    ble_thread.start()

    # HeightMapApp owns the main thread
    app = heightmap.HeightMapApp()
    app.mainloop()