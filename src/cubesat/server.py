import serial
import struct
from picamera2 import Picamera2
import io

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
        ser = serial.Serial('/dev/rfcomm0', 115200, timeout=60)
        print("Mac connected, waiting for commands...")
        while True:
            cmd = ser.read(1)
            if cmd == b'C':  # Capture command
                print("Capture triggered!")
                data = capture_image()
                size = len(data)
                ser.write(struct.pack('>I', size))
                ser.write(data)
                print(f"Sent {size} bytes")
            elif cmd == b'Q':  # Quit command
                print("Mac disconnected")
                break
    except Exception as e:
        print(f"Error: {e}, restarting...")
    finally:
        try:
            ser.close()
        except:
            pass