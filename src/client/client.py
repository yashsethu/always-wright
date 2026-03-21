import asyncio
import struct
import os
import threading
from datetime import datetime
from bleak import BleakClient, BleakScanner
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'utils'))
import utils as heightmap
from PIL import Image

SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID     = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID    = '12345678-1234-5678-1234-56789abcdef2'

SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'images')
os.makedirs(SAVE_DIR, exist_ok=True)

app          = None
ble_client   = None
ble_loop     = None

image_buffer  = bytearray()
expected_size = None

def on_data(sender, data):
    global image_buffer, expected_size
    if expected_size is None and len(data) == 4:
        expected_size = struct.unpack('>I', bytes(data))[0]
        image_buffer  = bytearray()
        return
    image_buffer.extend(data)
    if expected_size and len(image_buffer) >= expected_size:
        frame = bytes(image_buffer[:expected_size])
        with open(heightmap.IMAGE_PATH, 'wb') as f:
            f.write(frame)
        filename = os.path.join(SAVE_DIR, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        with open(filename, 'wb') as f:
            f.write(frame)
        if app is not None:
            app.after(0, app._load_image)
        expected_size = None
        image_buffer  = bytearray()

def send_cmd(cmd):
    if ble_client and ble_loop:
        asyncio.run_coroutine_threadsafe(
            ble_client.write_gatt_char(CMD_UUID, cmd.encode(), response=False),
            ble_loop
        )

async def ble_main():
    global ble_client
    device = await BleakScanner.find_device_by_name("cubesat", timeout=20)
    if not device:
        if app:
            app.after(0, lambda: app.ble_status.set("⬤  Not found"))
        return
    async with BleakClient(device, timeout=20) as client:
        ble_client = client
        if app:
            app.after(0, lambda: app.set_ble_connected(True))
        await client.start_notify(DATA_UUID, on_data)
        # Keep alive until disconnected
        while client.is_connected:
            await asyncio.sleep(1)
        ble_client = None
        if app:
            app.after(0, lambda: app.set_ble_connected(False))

def run_ble():
    global ble_loop
    ble_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ble_loop)
    ble_loop.run_until_complete(ble_main())
    ble_loop.close()

if __name__ == '__main__':
    Image.new('RGB', (160, 120), color=(30, 30, 30)).save(heightmap.IMAGE_PATH)
    app = heightmap.HeightMapApp()
    app._send_ble_cmd = send_cmd
    app.after(500, lambda: threading.Thread(target=run_ble, daemon=True).start())
    app.mainloop()