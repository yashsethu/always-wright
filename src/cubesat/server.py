import sys
import struct
from picamera2 import Picamera2
import io
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] {msg}", file=sys.stderr, flush=True)

def capture_image():
    log("Initializing camera...")
    picam2 = Picamera2()
    picam2.start()
    log("Camera started, capturing...")
    buf = io.BytesIO()
    picam2.capture_file(buf, format='jpeg')
    picam2.stop()
    data = buf.getvalue()
    log(f"Capture complete, {len(data)} bytes")
    return data

inp = sys.stdin.buffer
out = sys.stdout.buffer

log("Server started, waiting for commands on stdin...")
while True:
    log("Blocking on read...")
    cmd = inp.read(1)
    log(f"Read returned: {repr(cmd)}")
    if cmd == b'C':
        log("Capture triggered!")
        data = capture_image()
        size = len(data)
        log(f"Sending size header: {size}")
        out.write(struct.pack('>I', size))
        out.flush()
        log("Sending image data...")
        out.write(data)
        out.flush()
        log(f"Done sending {size} bytes")
    elif cmd == b'Q' or not cmd:
        log("Disconnected or empty read, exiting")
        break