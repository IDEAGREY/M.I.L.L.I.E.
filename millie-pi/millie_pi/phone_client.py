from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urljoin

import requests


class PhoneClient:
    """HTTP client for the MILLIE Android app API (port 8770)."""

    HANDSHAKE_PATH = "/api/hub/handshake"
    TIMEOUT = 3.0

    def __init__(self, base_url: str = ""):
        self.base_url = base_url.rstrip("/") if base_url else ""

    def set_url(self, url: str) -> None:
        self.base_url = url.rstrip("/")

    def _get(self, path: str) -> tuple[bool, dict[str, Any] | None, float, str]:
        if not self.base_url:
            return False, None, 0.0, "no phone url configured"
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        t0 = time.time()
        try:
            resp = requests.get(url, timeout=self.TIMEOUT)
            latency = (time.time() - t0) * 1000
            if resp.status_code != 200:
                return False, None, latency, f"HTTP {resp.status_code}"
            return True, resp.json(), latency, ""
        except requests.RequestException as exc:
            return False, None, (time.time() - t0) * 1000, str(exc)

    def _post(self, path: str, body: dict[str, Any]) -> tuple[bool, dict[str, Any] | None, str]:
        if not self.base_url:
            return False, None, "no phone url configured"
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        try:
            resp = requests.post(url, json=body, timeout=self.TIMEOUT)
            if resp.status_code != 200:
                return False, None, f"HTTP {resp.status_code}"
            return True, resp.json(), ""
        except requests.RequestException as exc:
            return False, None, str(exc)

    def handshake(self) -> tuple[bool, dict[str, Any] | None, float, str]:
        return self._get(self.HANDSHAKE_PATH)

    def probe_url(self, base_url: str) -> tuple[bool, dict[str, Any] | None]:
        old = self.base_url
        self.base_url = base_url.rstrip("/")
        ok, data, _, _ = self.handshake()
        if not ok:
            self.base_url = old
            return False, None
        return True, data

    @staticmethod
    def _hosts_in_subnet(cidr: str, limit: int = 64) -> list[str]:
        net = ipaddress.ip_network(cidr, strict=False)
        hosts = []
        for i, host in enumerate(net.hosts()):
            if i >= limit:
                break
            hosts.append(str(host))
        return hosts

    def discover(self, subnets: list[str], port: int = 8770) -> str | None:
        candidates: list[str] = []
        for cidr in subnets:
            candidates.extend(f"http://{h}:{port}" for h in self._hosts_in_subnet(cidr))

        def try_url(url: str) -> str | None:
            ok, _ = self.probe_url(url)
            return url if ok else None

        with ThreadPoolExecutor(max_workers=24) as pool:
            futures = [pool.submit(try_url, u) for u in candidates]
            for fut in as_completed(futures):
                found = fut.result()
                if found:
                    return found
        return None

    def fetch_state(self) -> tuple[bool, dict[str, Any] | None, float, str]:
        return self._get("/api/state")

    def fetch_civops(self) -> tuple[bool, dict[str, Any] | None, float, str]:
        return self._get("/api/civops")

    def fetch_wallsense(self) -> tuple[bool, dict[str, Any] | None, float, str]:
        return self._get("/api/civops/wallsense")

    def fetch_devices(self) -> tuple[bool, dict[str, Any] | None, float, str]:
        return self._get("/api/devices")

    def fetch_radar(self) -> tuple[bool, dict[str, Any] | None, float, str]:
        return self._get("/api/radar")

    def wallsense_command(self, action: str, arg: str = "") -> tuple[bool, dict[str, Any] | None, str]:
        return self._post("/api/civops/wallsense", {"action": action, "arg": arg})

    def civops_command(self, cmd: str, arg1: str = "", arg2: str = "") -> tuple[bool, dict[str, Any] | None, str]:
        return self._post("/api/civops/command", {"cmd": cmd, "arg1": arg1, "arg2": arg2})

    @staticmethod
    def local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"
