import struct
import io
import logging
from bluezero import peripheral, adapter
from picamera2 import Picamera2

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SERVICE_UUID  = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID      = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID     = '12345678-1234-5678-1234-56789abcdef2'

CHUNK_SIZE = 512

app = peripheral.Peripheral(list(adapter.Adapter.available())[0].address, local_name='cubesat')
app.add_service(srv_id=1, uuid=SERVICE_UUID, primary=True)

app.add_characteristic(
    srv_id=1, chr_id=1, uuid=CMD_UUID,
    value=[], notifying=False,
    flags=['write', 'write-without-response']
)
app.add_characteristic(
    srv_id=1, chr_id=2, uuid=DATA_UUID,
    value=[], notifying=False,
    flags=['notify']
)

def capture_image():
    log.info("Capturing image...")
    picam2 = Picamera2()
    picam2.start()
    buf = io.BytesIO()
    picam2.capture_file(buf, format='jpeg')
    picam2.stop()
    data = buf.getvalue()
    log.info(f"Captured {len(data)} bytes")
    return data

def on_command(value, options):
    cmd = bytes(value)
    log.info(f"Received command: {repr(cmd)}")
    if cmd == b'C':
        data = capture_image()
        size = len(data)
        # Send size header first
        app.update_value(srv_id=1, chr_id=2, value=list(struct.pack('>I', size)))
        # Send image in chunks
        for i in range(0, size, CHUNK_SIZE):
            chunk = data[i:i+CHUNK_SIZE]
            app.update_value(srv_id=1, chr_id=2, value=list(chunk))
            log.info(f"Sent chunk {i//CHUNK_SIZE + 1}/{(size+CHUNK_SIZE-1)//CHUNK_SIZE}")
        log.info("Transfer complete")

app.add_descriptor(srv_id=1, chr_id=1, dsc_id=1, uuid='2901', value=list(b'Command'), flags=['read'])
app.add_descriptor(srv_id=1, chr_id=2, dsc_id=1, uuid='2901', value=list(b'Data'),    flags=['read'])

app.on_write_request(srv_id=1, chr_id=1, callback=on_command)

log.info("BLE server started, waiting for connections...")
app.publish()