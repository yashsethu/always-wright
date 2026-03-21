import struct
import io
import logging
import time
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

CHUNK_SIZE = 512
data_char  = None
picam2     = Picamera2()

app = peripheral.Peripheral(list(adapter.Adapter.available())[0].address, local_name='cubesat')

def capture_image():
    log.info("Capturing image...")
    t0 = time.time()
    picam2.start()
    buf = io.BytesIO()
    picam2.capture_file(buf, format='jpeg')
    picam2.stop()
    data = buf.getvalue()
    elapsed = time.time() - t0
    log.info(f"Captured {len(data):,} bytes in {elapsed:.2f}s")
    return data

def on_notify(notifying, characteristic):
    global data_char
    if notifying:
        log.info("Client subscribed — ready to capture")
        data_char = characteristic
    else:
        log.info("Client unsubscribed")
        data_char = None

def on_command(value, options):
    global data_char
    cmd = bytes(value)
    if cmd == b'C':
        if data_char is None:
            data_char = app.service_list[0].characteristic_list[1]
        data = capture_image()
        size  = len(data)
        total = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        t0    = time.time()
        try:
            data_char.set_value(list(struct.pack('>I', size)))
            for i in range(0, size, CHUNK_SIZE):
                data_char.set_value(list(data[i:i + CHUNK_SIZE]))
            elapsed = time.time() - t0
            log.info(f"Sent {total} chunks ({size:,} bytes) in {elapsed:.2f}s  "
                     f"[{size / elapsed / 1024:.1f} KB/s]")
        except Exception as e:
            log.error(f"Transfer failed: {e}", exc_info=True)
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
log.info(f"Adapter: {list(adapter.Adapter.available())[0].address}")
app.publish()