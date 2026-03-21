import struct
import io
import logging
import time
import threading
from bluezero import peripheral, adapter
from picamera2 import Picamera2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID     = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID    = '12345678-1234-5678-1234-56789abcdef2'

CAPTURE_RES  = (1920, 1080)  # full res for single captures

data_char  = None
streaming  = False
stream_thread = None

app    = peripheral.Peripheral(list(adapter.Adapter.available())[0].address, local_name='cubesat')
picam2 = Picamera2()

STREAM_RES  = (160, 120)   # change from 320x240
CHUNK_SIZE  = 512
sending     = False        # add this global flag next to streaming

def send_frame(data):
    global sending
    if data_char is None or sending:
        return
    sending = True
    try:
        size = len(data)
        data_char.set_value(list(struct.pack('>I', size)))
        for i in range(0, size, CHUNK_SIZE):
            data_char.set_value(list(data[i:i + CHUNK_SIZE]))
    finally:
        sending = False

def capture_single(res=CAPTURE_RES):
    config = picam2.create_still_configuration(main={'size': res})
    picam2.configure(config)
    picam2.start()
    buf = io.BytesIO()
    picam2.capture_file(buf, format='jpeg')
    picam2.stop()
    return buf.getvalue()

def capture_stream_frame():
    buf = io.BytesIO()
    picam2.capture_file(buf, format='jpeg')
    return buf.getvalue()

def stream_loop():
    global streaming
    log.info("Stream started")
    config = picam2.create_video_configuration(main={'size': STREAM_RES})
    picam2.configure(config)
    picam2.start()
    frame_count = 0
    t0 = time.time()
    try:
        while streaming:
            data = capture_stream_frame()
            send_frame(data)
            frame_count += 1
            if frame_count % 10 == 0:
                fps = frame_count / (time.time() - t0)
                log.info(f"Streaming: {fps:.1f} FPS  {len(data):,} bytes/frame")
            log.info(f"Sending frame {frame_count}: {len(data):,} bytes")
    finally:
        picam2.stop()
        log.info("Stream stopped")

def on_notify(notifying, characteristic):
    global data_char
    if notifying:
        log.info("Client subscribed — ready")
        data_char = characteristic
    else:
        log.info("Client unsubscribed")
        data_char = None

def on_command(value, options):
    global streaming, stream_thread, data_char
    cmd = bytes(value)
    if data_char is None:
        data_char = app.service_list[0].characteristic_list[1]

    if cmd == b'C':
        log.info("Single capture triggered")
        t0 = time.time()
        config = picam2.create_still_configuration(main={'size': (1080, 720)})
        picam2.configure(config)
        data = capture_single()
        log.info(f"Captured {len(data):,} bytes in {time.time()-t0:.2f}s")
        send_frame(data)
        log.info("Sent")

    elif cmd == b'S':
        if not streaming:
            streaming = True
            stream_thread = threading.Thread(target=stream_loop, daemon=True)
            stream_thread.start()
        else:
            log.info("Already streaming")

    elif cmd == b'X':
        streaming = False
        log.info("Stop stream requested")

    else:
        log.warning(f"Unknown command: {repr(cmd)}")

app.add_service(srv_id=1, uuid=SERVICE_UUID, primary=True)
app.add_characteristic(
    srv_id=1, chr_id=1, uuid=CMD_UUID,
    value=[], notifying=False,
    flags=['write', 'write-without-response'],
    write_callback=on_command
)
app.add_characteristic(
    srv_id=1, chr_id=2, uuid=DATA_UUID,
    value=[], notifying=False,
    flags=['notify'],
    notify_callback=on_notify
)
app.add_descriptor(srv_id=1, chr_id=1, dsc_id=1, uuid='2901', value=list(b'Command'), flags=['read'])
app.add_descriptor(srv_id=1, chr_id=2, dsc_id=1, uuid='2901', value=list(b'Data'),    flags=['read'])

log.info("cubesat BLE camera server starting...")
app.publish()