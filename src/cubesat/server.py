import sys
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

inp = sys.stdin.buffer
out = sys.stdout.buffer

print("Connected, waiting for commands...", file=sys.stderr)
while True:
    cmd = inp.read(1)
    if cmd == b'C':
        print("Capture triggered!", file=sys.stderr)
        data = capture_image()
        size = len(data)
        out.write(struct.pack('>I', size))
        out.write(data)
        out.flush()
        print(f"Sent {size} bytes", file=sys.stderr)
    elif cmd == b'Q' or not cmd:
        print("Disconnected", file=sys.stderr)
        break