import struct
import io
import logging
import time
from bluezero import peripheral, adapter
from picamera2 import Picamera2

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0'
CMD_UUID     = '12345678-1234-5678-1234-56789abcdef1'
DATA_UUID    = '12345678-1234-5678-1234-56789abcdef2'

CHUNK_SIZE = 512
data_char = None

app = peripheral.Peripheral(list(adapter.Adapter.available())[0].address, local_name='cubesat')

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

def on_notify(notifying, characteristic):
    global data_char
    if notifying:
        log.info("Mac subscribed to notifications, storing characteristic reference")
        data_char = characteristic
    else:
        log.info("Mac unsubscribed")
        data_char = None

def on_command(value, options):
    global data_char
    cmd = bytes(value)
    log.info(f"Received command: {repr(cmd)}")
    if cmd == b'C':
        if data_char is None:
            # Try to find it directly
            data_char = app.service_list[0].characteristic_list[1]
            log.info(f"Found data_char directly: {data_char}")
        data = capture_image()
        size = len(data)
        try:
            log.info(f"Sending size header: {size}")
            data_char.value = list(struct.pack('>I', size))
            time.sleep(0.1)
            for i in range(0, size, CHUNK_SIZE):
                chunk = data[i:i+CHUNK_SIZE]
                data_char.value = list(chunk)
                time.sleep(0.05)
                log.info(f"Sent chunk {i//CHUNK_SIZE + 1}/{(size+CHUNK_SIZE-1)//CHUNK_SIZE}")
            log.info("Transfer complete")
        except Exception as e:
            log.error(f"Failed to send: {e}", exc_info=True)

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

log.info("BLE server started, waiting for connections...")
app.publish()