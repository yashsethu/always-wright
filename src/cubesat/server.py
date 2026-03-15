#!/usr/bin/env python3
"""
Bluetooth RFCOMM server for Raspberry Pi (satellite side).
Run with: sudo python3 server.py
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
    print("[boot] Install with: pip install uvloop")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/bt_satellite.log"),
    ],
)
log = logging.getLogger("satellite")

RFCOMM_CHANNEL = 1
READ_SIZE = 4096
KEEP_ALIVE_INTERVAL = 5.0   # seconds between keep-alive pings when idle
KEEP_ALIVE_TIMEOUT = 15.0   # seconds before declaring client dead


class SatelliteServer:
    def __init__(self):
        self.server_sock = None
        self.active_connections: list[ClientHandler] = []
        self.running = False

    def setup_socket(self):
        """Create and bind the RFCOMM server socket."""
        sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
        sock.setblocking(False)

        # SO_REUSEADDR equivalent for BT — avoids "address already in use" on restart
        sock.setsockopt(bluetooth.SOL_HCI, bluetooth.HCI_FILTER, b"\x00" * 14)

        try:
            sock.bind(("", RFCOMM_CHANNEL))
        except OSError:
            # Channel busy — try binding to any available channel
            sock.bind(("", bluetooth.PORT_ANY))

        sock.listen(1)

        local_addr = bluetooth.read_local_bdaddr()
        local_channel = sock.getsockname()[1]
        log.info(f"Listening on {local_addr} channel {local_channel}")

        # Advertise via SDP so the host can discover us by name
        bluetooth.advertise_service(
            sock,
            "BTSatellite",
            service_classes=[bluetooth.SERIAL_PORT_CLASS],
            profiles=[bluetooth.SERIAL_PORT_PROFILE],
        )
        log.info("SDP service 'BTSatellite' advertised")
        self.server_sock = sock
        return sock

    async def accept_loop(self):
        """Accept incoming connections in a non-blocking loop."""
        loop = asyncio.get_running_loop()
        log.info("Waiting for connections...")

        while self.running:
            try:
                client_sock, addr = await loop.sock_accept(self.server_sock)
                client_sock.setblocking(False)
                log.info(f"Connected: {addr}")
                handler = ClientHandler(client_sock, addr, self)
                self.active_connections.append(handler)
                asyncio.create_task(handler.run(), name=f"client-{addr[0]}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.running:
                    log.error(f"Accept error: {e}")
                    await asyncio.sleep(0.5)

    def remove_connection(self, handler):
        try:
            self.active_connections.remove(handler)
        except ValueError:
            pass

    async def start(self):
        self.running = True
        self.setup_socket()
        await self.accept_loop()

    async def stop(self):
        self.running = False
        for conn in list(self.active_connections):
            await conn.close()
        if self.server_sock:
            try:
                self.server_sock.close()
            except Exception:
                pass
        log.info("Server stopped")


class ClientHandler:
    def __init__(self, sock, addr, server: SatelliteServer):
        self.sock = sock
        self.addr = addr
        self.server = server
        self.last_recv = time.monotonic()
        self._closed = False

    async def run(self):
        loop = asyncio.get_running_loop()
        reader_task = asyncio.create_task(self._reader(loop))
        keepalive_task = asyncio.create_task(self._keepalive(loop))

        try:
            await asyncio.gather(reader_task, keepalive_task)
        except asyncio.CancelledError:
            pass
        finally:
            reader_task.cancel()
            keepalive_task.cancel()
            await self.close()
            self.server.remove_connection(self)

    async def _reader(self, loop):
        """Read messages and echo them back immediately."""
        buf = b""
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

            self.last_recv = time.monotonic()
            buf += chunk

            # Process all complete newline-terminated messages in the buffer
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                await self._handle_message(loop, line.strip())

    async def _handle_message(self, loop, raw: bytes):
        """Process one message and send response. Customize your logic here."""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")

        log.info(f"RX [{self.addr[0]}]: {text!r}")

        # ── Your application logic goes here ──────────────────────────────
        response = f"received: {text}\n"
        # ──────────────────────────────────────────────────────────────────

        await self._send(loop, response.encode("utf-8"))

    async def _keepalive(self, loop):
        """Send periodic pings; disconnect if host goes silent."""
        while not self._closed:
            await asyncio.sleep(KEEP_ALIVE_INTERVAL)
            idle = time.monotonic() - self.last_recv
            if idle > KEEP_ALIVE_TIMEOUT:
                log.warning(f"Client {self.addr} timed out after {idle:.1f}s idle")
                break
            try:
                await self._send(loop, b"__ping__\n")
            except Exception:
                break

    async def _send(self, loop, data: bytes):
        try:
            await loop.sock_sendall(self.sock, data)
        except (BrokenPipeError, OSError) as e:
            log.warning(f"Send failed to {self.addr}: {e}")
            raise

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.close()
        except Exception:
            pass
        log.info(f"Connection to {self.addr} closed")


async def main():
    server = SatelliteServer()

    loop = asyncio.get_running_loop()

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