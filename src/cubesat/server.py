import bluetooth
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

server_sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
server_sock.bind(("", 1))
server_sock.listen(1)

bluetooth.advertise_service(server_sock, "CameraServer",
    service_classes=[bluetooth.SERIAL_PORT_CLASS],
    profiles=[bluetooth.SERIAL_PORT_PROFILE])

print("Waiting for connection...")
while True:
    try:
        client_sock, address = server_sock.accept()
        print(f"Connected: {address}")
        while True:
            cmd = client_sock.recv(1)
            if cmd == b'C':
                print("Capture triggered!")
                data = capture_image()
                size = len(data)
                client_sock.send(struct.pack('>I', size))
                client_sock.send(data)
                print(f"Sent {size} bytes")
            elif cmd == b'Q' or not cmd:
                print("Disconnected")
                break
        client_sock.close()
    except Exception as e:
        print(f"Error: {e}, retrying in 5 seconds...")
        time.sleep(5)