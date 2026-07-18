from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None


OnLine = Callable[[dict[str, Any]], None]


class EspReader:
    """Read CYD JSON lines from USB serial (same protocol as Android UsbReader)."""

    CP210X_VIDS = {0x10C4}

    def __init__(
        self,
        port: str = "",
        baud: int = 115200,
        on_line: OnLine | None = None,
    ):
        self.port = port
        self.baud = baud
        self.on_line = on_line or (lambda _o: None)
        self._running = False
        self._thread: threading.Thread | None = None
        self._ser = None

    @staticmethod
    def find_port() -> str | None:
        if list_ports is None:
            return None
        for p in list_ports.comports():
            if p.vid in EspReader.CP210X_VIDS or "CP210" in (p.description or "").upper():
                return p.device
        return None

    @property
    def connected(self) -> bool:
        return self._ser is not None and getattr(self._ser, "is_open", False)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="millie-esp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    def send_cmd(self, sub: str, arg1: str = "", arg2: str = "") -> None:
        if not self.connected:
            return
        if arg2:
            line = f"CMD|{sub}|{arg1}|{arg2}\n"
        elif arg1:
            line = f"CMD|{sub}|{arg1}\n"
        else:
            line = f"CMD|{sub}\n"
        self._ser.write(line.encode("ascii"))

    def _open(self) -> None:
        if serial is None:
            raise RuntimeError("pyserial not installed")
        port = self.port or self.find_port()
        if not port:
            raise RuntimeError("no CYD serial port found")
        self._ser = serial.Serial(port, self.baud, timeout=0.5)
        self.port = port

    def _loop(self) -> None:
        buf = ""
        while self._running:
            try:
                if not self.connected:
                    self._open()
                    time.sleep(0.2)
                chunk = self._ser.read(512).decode("utf-8", errors="ignore")
                if not chunk:
                    continue
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.on_line(obj)
            except Exception:
                if self._ser:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                    self._ser = None
                time.sleep(3)
