import serial
import struct
import os
from datetime import datetime

SAVE_DIR = os.path.join(os.path.dirname(__file__), '..', 'images')
os.makedirs(SAVE_DIR, exist_ok=True)

sock = serial.Serial('/dev/tty.cubesat', 115200, timeout=30)
print("Connected. Press Enter to capture, q to quit.")

while True:
    cmd = input("> ").strip().lower()
    if cmd == 'q':
        sock.write(b'Q')
        break
    else:
        print("Capturing...")
        print("Sending C byte...")
        bytes_written = sock.write(b'C')
        sock.flush()
        print(f"Sent {bytes_written} bytes")
        raw_size = sock.read(4)

        if len(raw_size) < 4:
            print("Timeout or connection lost")
            continue
        size = struct.unpack('>I', raw_size)[0]
        print(f"Receiving {size} bytes...")

        data = b''
        while len(data) < size:
            chunk = sock.read(min(4096, size - len(data)))
            if not chunk:
                print("Connection lost mid-transfer")
                break
            data += chunk

        filename = os.path.join(SAVE_DIR, f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
        with open(filename, 'wb') as f:
            f.write(data)
        print(f"Saved {filename}")

sock.close()