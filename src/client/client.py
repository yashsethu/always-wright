import asyncio
import struct
import os
import time
from datetime import datetime
from bleak import BleakClient, BleakScanner
from rich.console import Console
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.prompt import Prompt
from rich.panel import Panel
from rich import print as rprint

SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID     = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID    = '12345678-1234-5678-1234-56789abcdef2'

SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'images')
os.makedirs(SAVE_DIR, exist_ok=True)

console      = Console()
image_buffer = bytearray()
expected_size = None
progress     = None
task_id      = None
transfer_start = None

def on_data(sender, data):
    global image_buffer, expected_size, progress, task_id, transfer_start

    if expected_size is None and len(data) == 4:
        expected_size  = struct.unpack('>I', bytes(data))[0]
        image_buffer   = bytearray()
        transfer_start = time.time()
        progress.update(task_id, total=expected_size, visible=True)
        return

    image_buffer.extend(data)
    progress.update(task_id, completed=len(image_buffer))

    if expected_size and len(image_buffer) >= expected_size:
        elapsed  = time.time() - transfer_start
        filename = os.path.join(SAVE_DIR, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        with open(filename, 'wb') as f:
            f.write(image_buffer[:expected_size])
        progress.update(task_id, visible=False)
        rprint(f"\n[bold green]✓[/bold green] Saved [cyan]{filename}[/cyan]  "
               f"([dim]{expected_size:,} bytes in {elapsed:.2f}s — "
               f"{expected_size / elapsed / 1024:.1f} KB/s[/dim])\n")
        expected_size  = None
        image_buffer   = bytearray()

async def main():
    global progress, task_id

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
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as prog:
            progress = prog
            task_id  = prog.add_task("Receiving image...", total=None, visible=False)

            console.print("Press [bold]Enter[/bold] to capture  •  [bold]q[/bold] to quit\n")
            loop = asyncio.get_event_loop()

            while True:
                cmd = await loop.run_in_executor(None, input, "  › ")
                cmd = cmd.strip().lower()
                if cmd == 'q':
                    rprint("[dim]Disconnecting...[/dim]")
                    break
                else:
                    console.print("[yellow]⏎[/yellow]  Triggering capture...")
                    await client.write_gatt_char(CMD_UUID, b'C', response=False)

asyncio.run(main())