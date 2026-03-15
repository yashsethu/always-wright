#!/usr/bin/env python3
"""
Bluetooth RFCOMM client for macOS.
Uses pyserial via the virtual serial port macOS creates when you pair the Pi.

Usage:
    python3 client.py                          # auto-detect the Pi serial port
    python3 client.py /dev/tty.raspberrypi     # use a specific port

Setup:
    1. Pair the Pi in System Settings → Bluetooth (one-time)
    2. pip install pyserial uvloop
    3. Run this script

How it works:
    When you pair a Bluetooth device that supports RFCOMM/SPP on macOS,
    macOS creates a /dev/tty.* device automatically. Writing to that file
    sends bytes over Bluetooth — no Bluetooth API needed.
"""

import asyncio
import glob
import logging
import signal
import sys
import time

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("[boot] uvloop active")
except ImportError:
    print("[boot] WARNING: uvloop not found — install with: pip install uvloop")

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("host")

BAUD_RATE       = 115200
READ_TIMEOUT    = 0.1    # seconds — non-blocking serial read interval
RECONNECT_DELAY = 3.0
WRITE_TIMEOUT   = 2.0


def find_pi_port() -> str | None:
    """
    Look for the Pi's virtual serial port.
    macOS names these /dev/tty.<device-name> where the name comes from
    the Bluetooth device name set on the Pi.
    """
    # Common patterns — sorted so the most specific match wins
    patterns = [
        "/dev/tty.raspberrypi*",
        "/dev/tty.cubesat*",
        "/dev/tty.BTSatellite*",
        "/dev/tty.raspberry*",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]

    # Fallback: list all BT serial ports and let the user pick
    bt_ports = [p.device for p in serial.tools.list_ports.comports()
                if "Bluetooth" in (p.description or "") or p.device.startswith("/dev/tty.")]
    if len(bt_ports) == 1:
        return bt_ports[0]
    if len(bt_ports) > 1:
        print("Multiple Bluetooth serial ports found:")
        for i, p in enumerate(bt_ports):
            print(f"  [{i}] {p}")
        choice = input("Pick one (number): ").strip()
        try:
            return bt_ports[int(choice)]
        except (ValueError, IndexError):
            pass

    return None


class MacClient:
    def __init__(self, port: str):
        self.port    = port
        self.ser     = None
        self.running = False
        self._send_q: asyncio.Queue | None = None

    def _open(self) -> bool:
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=BAUD_RATE,
                timeout=READ_TIMEOUT,         # read() returns after this many seconds
                write_timeout=WRITE_TIMEOUT,
                exclusive=True,               # prevent two processes opening the same port
            )
            log.info(f"Opened {self.port} at {BAUD_RATE} baud")
            return True
        except serial.SerialException as e:
            log.error(f"Could not open {self.port}: {e}")
            return False

    def _close(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    async def run(self):
        self.running = True
        self._send_q = asyncio.Queue()

        input_task = asyncio.create_task(self._input_loop())

        while self.running:
            if not self._open():
                log.info(f"Retrying in {RECONNECT_DELAY}s ...")
                await asyncio.sleep(RECONNECT_DELAY)
                continue

            reader_task = asyncio.create_task(self._reader())
            writer_task = asyncio.create_task(self._writer())

            done, pending = await asyncio.wait(
                [reader_task, writer_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            self._close()

            if self.running:
                log.info(f"Connection lost. Reconnecting in {RECONNECT_DELAY}s ...")
                await asyncio.sleep(RECONNECT_DELAY)

        input_task.cancel()

    async def _reader(self):
        """Read from serial port in executor (serial.readline is blocking)."""
        loop = asyncio.get_running_loop()
        buf  = b""
        while self.running and self.ser and self.ser.is_open:
            try:
                # Run blocking read in a thread so we don't block the event loop
                chunk = await loop.run_in_executor(None, self.ser.read, 4096)
            except (serial.SerialException, OSError) as e:
                log.warning(f"Reader error: {e}")
                break

            if not chunk:
                continue  # timeout, no data — loop again

            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line and line != b"__ping__":
                    print(f"\r\033[K<< {line.decode('utf-8', errors='replace')}")
                    print(">> ", end="", flush=True)

    async def _writer(self):
        """Pull from queue and write to serial port."""
        loop = asyncio.get_running_loop()
        while self.running:
            try:
                msg: bytes = await asyncio.wait_for(self._send_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Check if the port is still alive
                if self.ser is None or not self.ser.is_open:
                    break
                continue
            except asyncio.CancelledError:
                break

            try:
                await loop.run_in_executor(None, self.ser.write, msg)
            except (serial.SerialException, OSError) as e:
                log.warning(f"Writer error: {e}")
                break

    async def _input_loop(self):
        loop = asyncio.get_running_loop()
        print(f"\nConnected via {self.port}. Type a message and press Enter. Ctrl+C to quit.\n")

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

    async def stop(self):
        self.running = False
        self._close()


async def main():
    # Determine port
    if len(sys.argv) >= 2:
        port = sys.argv[1]
        print(f"Using port: {port}")
    else:
        print("Searching for Pi serial port ...")
        port = find_pi_port()
        if not port:
            print()
            print("Could not find a Bluetooth serial port for the Pi.")
            print()
            print("Make sure you have:")
            print("  1. Paired the Pi in System Settings → Bluetooth")
            print("  2. The Pi server is running (sudo python3 pi_server.py)")
            print()
            print("Then check what port appeared:")
            print("  ls /dev/tty.*")
            print()
            print("And pass it explicitly:")
            print("  python3 client.py /dev/tty.raspberrypi")
            sys.exit(1)

    client = MacClient(port)
    loop   = asyncio.get_running_loop()
    main_task = asyncio.current_task()

    def shutdown(*_):
        print("\nShutting down ...")
        async def _stop():
            await client.stop()
            main_task.cancel()
        loop.create_task(_stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    try:
        await client.run()
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass