import asyncio
import struct
import os
from datetime import datetime
from bleak import BleakClient, BleakScanner

SERVICE_UUID  = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID      = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID     = '12345678-1234-5678-1234-56789abcdef2'

SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'images')
os.makedirs(SAVE_DIR, exist_ok=True)

image_buffer = bytearray()
expected_size = None

def on_data(sender, data):
    global image_buffer, expected_size
    if expected_size is None and len(data) == 4:
        expected_size = struct.unpack('>I', bytes(data))[0]
        image_buffer = bytearray()
        print(f"[INFO] Expecting {expected_size} bytes...")
    else:
        image_buffer.extend(data)
        print(f"[INFO] Received {len(image_buffer)}/{expected_size} bytes", end='\r')
        if expected_size and len(image_buffer) >= expected_size:
            filename = os.path.join(SAVE_DIR, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
            with open(filename, 'wb') as f:
                f.write(image_buffer[:expected_size])
            print(f"\n[INFO] Saved {filename}")
            expected_size = None
            image_buffer = bytearray()

async def main():
    print("[INFO] Scanning for cubesat...")
    device = await BleakScanner.find_device_by_name("cubesat", timeout=10)
    if not device:
        print("[ERROR] cubesat not found")
        return

    async with BleakClient(device) as client:
        print("[INFO] Connected. Press Enter to capture, q to quit.")
        await client.start_notify(DATA_UUID, on_data)
        while True:
            cmd = input("> ").strip().lower()
            if cmd == 'q':
                break
            else:
                print("[INFO] Triggering capture...")
                await client.write_gatt_char(CMD_UUID, b'C', response=False)

asyncio.run(main())