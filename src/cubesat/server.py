import struct
from picamera2 import Picamera2
import io
from datetime import datetime
import os
import time

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')}] {msg}", flush=True)

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

log("Server started, waiting for /dev/rfcomm0...")
while True:
    try:
        if not os.path.exists('/dev/rfcomm0'):
            log("No device, retrying in 5 seconds...")
            time.sleep(5)
            continue

        log("Opening /dev/rfcomm0 as raw fd...")
        fd = open('/dev/rfcomm0', 'r+b', buffering=0)
        log("Opened, waiting for commands...")

        while True:
            log("Blocking on read...")
            cmd = fd.read(1)
            log(f"Read returned: {repr(cmd)}")
            if cmd == b'C':
                log("Capture triggered!")
                data = capture_image()
                size = len(data)
                log(f"Sending size header: {size}")
                fd.write(struct.pack('>I', size))
                log("Sending image data...")
                fd.write(data)
                log(f"Done sending {size} bytes")
            elif cmd == b'Q' or not cmd:
                log("Disconnected or empty read, exiting")
                fd.close()
                break
    except Exception as e:
        log(f"Error: {e}, retrying in 5 seconds...")
        time.sleep(5)