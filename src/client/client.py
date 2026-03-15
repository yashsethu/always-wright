#!/usr/bin/env python3
"""
Bluetooth RFCOMM client for the host computer.
No third-party Bluetooth library required — uses Python stdlib socket.

Usage:
    python3 computer_client.py AA:BB:CC:DD:EE:FF    # Pi's BT MAC address

How to find the Pi's BT address:
    On the Pi, run:  hciconfig hci0 | grep "BD Address"

Install: pip install uvloop
"""

import asyncio
import logging
import signal
import socket
import sys
import time

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("[boot] uvloop active")
except ImportError:
    print("[boot] WARNING: uvloop not found — install with: pip install uvloop")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("host")

RFCOMM_CHANNEL  = 1
READ_SIZE       = 4096
RECONNECT_DELAY = 3.0   # seconds between reconnect attempts
CONNECT_TIMEOUT = 10.0  # seconds before giving up on a connection attempt


class HostClient:
    def __init__(self, pi_addr: str):
        self.pi_addr    = pi_addr
        self.sock       = None
        self.running    = False
        self._send_q: asyncio.Queue | None = None

    async def connect(self) -> bool:
        """Open a non-blocking RFCOMM socket to the Pi. Returns True on success."""
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.setblocking(True)  # connect() itself must be blocking

        log.info(f"Connecting to {self.pi_addr}:{RFCOMM_CHANNEL} ...")
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, sock.connect, (self.pi_addr, RFCOMM_CHANNEL)),
                timeout=CONNECT_TIMEOUT,
            )
            sock.setblocking(False)
            self.sock = sock
            log.info("Connected!")
            return True
        except asyncio.TimeoutError:
            log.error(f"Timed out after {CONNECT_TIMEOUT}s")
        except OSError as e:
            log.error(f"Connection failed: {e}")
        except Exception as e:
            log.error(f"Unexpected error: {e}")

        try:
            sock.close()
        except Exception:
            pass
        return False

    async def run(self):
        self.running  = True
        self._send_q  = asyncio.Queue()

        # Read stdin in a background thread so it never blocks the event loop
        input_task = asyncio.create_task(self._input_loop())

        while self.running:
            if not await self.connect():
                log.info(f"Retrying in {RECONNECT_DELAY}s ...")
                await asyncio.sleep(RECONNECT_DELAY)
                continue

            reader_task = asyncio.create_task(self._reader())
            writer_task = asyncio.create_task(self._writer())

            # Run until either side drops
            done, pending = await asyncio.wait(
                [reader_task, writer_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            self._close_socket()

            if self.running:
                log.info(f"Connection lost. Reconnecting in {RECONNECT_DELAY}s ...")
                await asyncio.sleep(RECONNECT_DELAY)

        input_task.cancel()

    async def _reader(self):
        loop = asyncio.get_running_loop()
        buf  = b""
        while True:
            try:
                chunk = await loop.sock_recv(self.sock, READ_SIZE)
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
                    # Clear the current "> " prompt, print response, redraw prompt
                    print(f"\r\033[K<< {line.decode('utf-8', errors='replace')}")
                    print(">> ", end="", flush=True)

    async def _writer(self):
        while True:
            try:
                msg: bytes = await asyncio.wait_for(self._send_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            loop = asyncio.get_running_loop()
            try:
                await loop.sock_sendall(self.sock, msg)
            except (BrokenPipeError, OSError) as e:
                log.warning(f"Writer: send failed: {e}")
                break

    async def _input_loop(self):
        """Read lines from stdin and push them onto the send queue."""
        loop = asyncio.get_running_loop()
        print(f"\nReady. Type a message and press Enter. Ctrl+C to quit.\n")

        while self.running:
            try:
                print(">> ", end="", flush=True)
                line = await loop.run_in_executor(None, sys.stdin.readline)
            except Exception:
                break

            if not line:
                break

            line = line.strip()
            if line:
                await self._send_q.put((line + "\n").encode("utf-8"))

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
    if len(sys.argv) != 2:
        print("Usage: python3 computer_client.py AA:BB:CC:DD:EE:FF")
        print()
        print("To find the Pi's BT address, run on the Pi:")
        print("  hciconfig hci0 | grep 'BD Address'")
        sys.exit(1)

    pi_addr = sys.argv[1].upper().strip()
    client  = HostClient(pi_addr)
    loop    = asyncio.get_running_loop()

    def shutdown(*_):
        print("\nShutting down ...")
        loop.create_task(client.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    await client.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass