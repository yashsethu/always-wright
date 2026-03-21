import serial
import struct
from picamera2 import Picamera2
import io
import time

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
            if cmd == b'C':
                print("Capture triggered!")
                data = capture_image()
                size = len(data)
                ser.write(struct.pack('>I', size))
                ser.write(data)
                print(f"Sent {size} bytes")
            elif cmd == b'Q':
                print("Mac disconnected")
                break
            elif cmd == b'':
                # timeout with no data, check connection is still alive
                continue
    except serial.SerialException:
        # no connection yet, wait and retry
        print("No connection, retrying in 5 seconds...")
        time.sleep(5)
    except Exception as e:
        print(f"Unexpected error: {e}, retrying in 5 seconds...")
        time.sleep(5)
    finally:
        try:
            ser.close()
        except:
            pass