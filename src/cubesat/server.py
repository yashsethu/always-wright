#!/usr/bin/env python3
"""
Bluetooth RFCOMM server for Raspberry Pi (satellite side).
No third-party Bluetooth library required — uses Python stdlib socket.

Run with: sudo python3 pi_server.py
Install:  pip install uvloop
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
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/bt_satellite.log"),
    ],
)
log = logging.getLogger("satellite")

RFCOMM_CHANNEL    = 1
READ_SIZE         = 4096
KEEPALIVE_EVERY   = 5.0   # seconds between keep-alive pings when idle
KEEPALIVE_TIMEOUT = 15.0  # seconds of silence before dropping the client


class SatelliteServer:
    def __init__(self):
        self.server_sock = None
        self.active: list["ClientHandler"] = []
        self.running = False

    def _make_server_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        # empty string = BDADDR_ANY, bind to whichever BT adapter is present
        sock.bind(("", RFCOMM_CHANNEL))
        sock.listen(5)
        return sock

    async def accept_loop(self):
        loop = asyncio.get_running_loop()
        log.info(f"Listening on RFCOMM channel {RFCOMM_CHANNEL} ...")

        while self.running:
            try:
                client_sock, addr = await loop.sock_accept(self.server_sock)
                client_sock.setblocking(False)
                log.info(f"Connection from {addr}")
                handler = ClientHandler(client_sock, addr, self)
                self.active.append(handler)
                asyncio.create_task(handler.run(), name=f"client-{addr}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.running:
                    log.error(f"Accept error: {e}")
                    await asyncio.sleep(0.5)

    def remove(self, handler):
        try:
            self.active.remove(handler)
        except ValueError:
            pass

    async def start(self):
        self.server_sock = self._make_server_socket()
        self.running = True
        await self.accept_loop()

    async def stop(self):
        self.running = False
        for c in list(self.active):
            await c.close()
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        log.info("Server stopped")


class ClientHandler:
    def __init__(self, sock: socket.socket, addr, server: SatelliteServer):
        self.sock    = sock
        self.addr    = addr
        self.server  = server
        self.last_rx = time.monotonic()
        self._closed = False

    async def run(self):
        reader = asyncio.create_task(self._reader())
        keeper = asyncio.create_task(self._keepalive())
        try:
            await asyncio.gather(reader, keeper)
        except asyncio.CancelledError:
            pass
        finally:
            reader.cancel()
            keeper.cancel()
            await self.close()
            self.server.remove(self)

    async def _reader(self):
        loop = asyncio.get_running_loop()
        buf  = b""
        while not self._closed:
            try:
                chunk = await loop.sock_recv(self.sock, READ_SIZE)
            except (ConnectionResetError, BrokenPipeError, OSError):
                log.info(f"Client {self.addr} disconnected")
                break
            except asyncio.CancelledError:
                break

            if not chunk:
                log.info(f"Client {self.addr} closed connection")
                break

            self.last_rx = time.monotonic()
            buf += chunk

            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                await self._handle(line.strip())

    async def _handle(self, raw: bytes):
        """Process one message. Put your application logic here."""
        if raw == b"__ping__":
            return  # silently drop keep-alive pings from the client

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        log.info(f"RX: {text!r}")

        # ── Your application logic goes here ──────────────────────────
        response = f"received: {text}\n"
        # ──────────────────────────────────────────────────────────────

        await self._send(response.encode())

    async def _keepalive(self):
        while not self._closed:
            await asyncio.sleep(KEEPALIVE_EVERY)
            idle = time.monotonic() - self.last_rx
            if idle > KEEPALIVE_TIMEOUT:
                log.warning(f"Client {self.addr} timed out ({idle:.1f}s idle)")
                break
            try:
                await self._send(b"__ping__\n")
            except Exception:
                break

    async def _send(self, data: bytes):
        loop = asyncio.get_running_loop()
        try:
            await loop.sock_sendall(self.sock, data)
        except (BrokenPipeError, OSError) as e:
            log.warning(f"Send failed: {e}")
            raise

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.close()
        except Exception:
            pass
        log.info(f"Closed connection from {self.addr}")


async def main():
    server = SatelliteServer()
    loop   = asyncio.get_running_loop()

    def shutdown(*_):
        log.info("Shutdown signal received")
        loop.create_task(server.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass