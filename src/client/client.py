#!/usr/bin/env python3
"""
Bluetooth RFCOMM client for the host computer.
Usage:
    python3 client.py                      # auto-discover Pi
    python3 client.py AA:BB:CC:DD:EE:FF    # connect to known address
Requires: pip install uvloop PyBluez2
"""

import asyncio
import bluetooth
import logging
import signal
import sys
import time

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("[boot] uvloop active")
except ImportError:
    print("[boot] WARNING: uvloop not found, falling back to default asyncio loop")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("host")

RFCOMM_CHANNEL = 1
SERVICE_NAME = "BTSatellite"
RECONNECT_DELAY = 3.0    # seconds between reconnect attempts
READ_SIZE = 4096
CONNECT_TIMEOUT = 10.0   # seconds to wait for initial connection


def discover_pi() -> str | None:
    """Scan for a device advertising BTSatellite and return its address."""
    print("Scanning for BTSatellite service... (this takes ~10s)")
    try:
        services = bluetooth.find_service(name=SERVICE_NAME)
        if services:
            addr = services[0]["host"]
            print(f"Found BTSatellite at {addr}")
            return addr
    except Exception as e:
        log.error(f"Discovery failed: {e}")
    return None


class HostClient:
    def __init__(self, pi_addr: str):
        self.pi_addr = pi_addr
        self.sock = None
        self.running = False
        self._send_queue: asyncio.Queue = None
        self._loop: asyncio.AbstractEventLoop = None

    async def connect(self) -> bool:
        """Attempt to connect to the Pi. Returns True on success."""
        sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
        sock.setblocking(False)
        self._loop = asyncio.get_running_loop()

        log.info(f"Connecting to {self.pi_addr}:{RFCOMM_CHANNEL}...")
        try:
            # BluetoothSocket.connect() is not truly async — run in executor
            # to avoid blocking the event loop during the BT handshake
            await asyncio.wait_for(
                self._loop.run_in_executor(
                    None, sock.connect, (self.pi_addr, RFCOMM_CHANNEL)
                ),
                timeout=CONNECT_TIMEOUT,
            )
            sock.setblocking(False)
            self.sock = sock
            log.info("Connected!")
            return True
        except asyncio.TimeoutError:
            log.error(f"Connection timed out after {CONNECT_TIMEOUT}s")
        except bluetooth.btcommon.BluetoothError as e:
            log.error(f"Bluetooth error: {e}")
        except Exception as e:
            log.error(f"Connection failed: {e}")

        try:
            sock.close()
        except Exception:
            pass
        return False

    async def run(self):
        """Main loop: connect, run reader+writer, reconnect on drop."""
        self.running = True
        self._send_queue = asyncio.Queue()

        # Start interactive input in a background thread (so it doesn't block)
        input_task = asyncio.create_task(self._input_loop())

        while self.running:
            if not await self.connect():
                log.info(f"Retrying in {RECONNECT_DELAY}s...")
                await asyncio.sleep(RECONNECT_DELAY)
                continue

            reader_task = asyncio.create_task(self._reader())
            writer_task = asyncio.create_task(self._writer())

            # Run until either reader or writer exits (connection dropped)
            done, pending = await asyncio.wait(
                [reader_task, writer_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            self._close_socket()

            if self.running:
                log.info(f"Connection lost. Reconnecting in {RECONNECT_DELAY}s...")
                await asyncio.sleep(RECONNECT_DELAY)

        input_task.cancel()

    async def _reader(self):
        """Receive data from Pi and print to stdout."""
        buf = b""
        while True:
            try:
                chunk = await self._loop.sock_recv(self.sock, READ_SIZE)
            except (ConnectionResetError, BrokenPipeError, OSError):
                log.warning("Reader: connection lost")
                break

            if not chunk:
                break

            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line and line != b"__ping__":
                    print(f"\n<< {line.decode('utf-8', errors='replace')}")
                    print(">> ", end="", flush=True)

    async def _writer(self):
        """Pull messages from the queue and send them to the Pi."""
        while True:
            try:
                msg: bytes = await asyncio.wait_for(
                    self._send_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                await self._loop.sock_sendall(self.sock, msg)
            except (BrokenPipeError, OSError) as e:
                log.warning(f"Writer: send failed: {e}")
                break

    async def _input_loop(self):
        """Read stdin in a thread and push messages to the send queue."""
        loop = asyncio.get_running_loop()
        print(f"Connected to {self.pi_addr}. Type messages and press Enter.")
        print("Ctrl+C to quit.\n")

        while self.running:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception:
                break

            if not line:
                break

            line = line.strip()
            if line:
                print(">> ", end="", flush=True)
                await self._send_queue.put((line + "\n").encode("utf-8"))

    def _close_socket(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    async def stop(self):
        self.running = False
        self._close_socket()


async def main():
    # Determine Pi address
    if len(sys.argv) > 1:
        pi_addr = sys.argv[1]
        print(f"Using provided address: {pi_addr}")
    else:
        pi_addr = discover_pi()
        if not pi_addr:
            print("Could not find BTSatellite service.")
            print("Make sure pi_server.py is running on your Pi.")
            print("Or provide the address manually: python3 computer_client.py AA:BB:CC:DD:EE:FF")
            sys.exit(1)

    client = HostClient(pi_addr)
    loop = asyncio.get_running_loop()

    def shutdown(*_):
        print("\nShutting down...")
        loop.create_task(client.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    await client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass