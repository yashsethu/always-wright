import serial
import struct
from picamera2 import Picamera2
import io
import time
import os

def capture_image():
    picam2 = Picamera2()
    picam2.start()
    buf = io.BytesIO()
    picam2.capture_file(buf, format='jpeg')
    picam2.stop()
    return buf.getvalue()

print("Waiting for connection on /dev/rfcomm0...")
while True:
    try:
        if not os.path.exists('/dev/rfcomm0'):
            time.sleep(5)
            continue
        ser = serial.Serial('/dev/rfcomm0', 115200, timeout=60)
        print("Connected, waiting for commands...")
        while True:
            cmd = ser.read(1)
            if cmd == b'C':
                print("Capture triggered!")
                data = capture_image()
                size = len(data)
                ser.write(struct.pack('>I', size))
                ser.write(data)
                print(f"Sent {size} bytes")
            elif cmd == b'Q' or not cmd:
                print("Disconnected")
                break
        ser.close()
    except serial.SerialException:
        print("No connection, retrying in 5 seconds...")
        time.sleep(5)
    except Exception as e:
        print(f"Error: {e}, retrying in 5 seconds...")
        time.sleep(5)