import serial
import struct
from datetime import datetime

ser = serial.Serial('/dev/tty.cubesat', 115200, timeout=30)
print("Connected to Pi. Press Enter to capture, q to quit.")

while True:
    cmd = input("> ").strip().lower()
    if cmd == 'q':
        ser.write(b'Q')
        break
    else:  # any other input triggers capture
        print("Capturing...")
        ser.write(b'C')

        # Read length header
        raw_size = ser.read(4)
        if len(raw_size) < 4:
            print("Timeout or connection lost")
            continue
        size = struct.unpack('>I', raw_size)[0]
        print(f"Receiving {size} bytes...")

        # Read image
        data = b''
        while len(data) < size:
            chunk = ser.read(size - len(data))
            if not chunk:
                print("Connection lost mid-transfer")
                break
            data += chunk

        # Save with timestamp
        filename = f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        with open(filename, 'wb') as f:
            f.write(data)
        print(f"Saved {filename}")

ser.close()
print("Disconnected")