import serial
import struct
import os
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] {msg}", flush=True)

SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'images')
os.makedirs(SAVE_DIR, exist_ok=True)

log("Opening serial port...")
sock = serial.Serial('/dev/cu.cubesat', 115200, timeout=30)
log("Serial port open")
print("Press Enter to capture, q to quit.")

while True:
    cmd = input("> ").strip().lower()
    if cmd == 'q':
        log("Sending Q...")
        sock.write(b'Q')
        break
    else:
        log("Sending C byte...")
        sock.write(b'C')
        sock.flush()
        log("C sent, waiting for size header...")

        raw_size = sock.read(4)
        log(f"Size header received: {repr(raw_size)} ({len(raw_size)} bytes)")
        if len(raw_size) < 4:
            log("ERROR: Timeout waiting for size header")
            continue
        size = struct.unpack('>I', raw_size)[0]
        log(f"Expecting {size} bytes of image data...")

        data = b''
        while len(data) < size:
            chunk = sock.read(min(4096, size - len(data)))
            log(f"Got chunk: {len(chunk)} bytes (total: {len(data)+len(chunk)}/{size})")
            if not chunk:
                log("ERROR: Connection lost mid-transfer")
                break
            data += chunk

        filename = os.path.join(SAVE_DIR, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        with open(filename, 'wb') as f:
            f.write(data)
        log(f"Saved {filename}")

sock.close()