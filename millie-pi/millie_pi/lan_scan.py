from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Any

log = logging.getLogger("millie_pi.lan")


def scan_arp_table() -> list[dict[str, str]]:
    """Passive-ish LAN RF adjacency: who's on the wire near the Pi."""
    out: list[dict[str, str]] = []
    try:
        with open("/proc/net/arp", "r", encoding="utf-8") as fh:
            lines = fh.readlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            ip, _typ, _flags, mac = parts[0], parts[1], parts[2], parts[3]
            if mac == "00:00:00:00:00:00":
                continue
            out.append({"ip": ip, "mac": mac.upper()})
    except OSError:
        pass
    return out


def scan_arp_scan(timeout: int = 20) -> list[dict[str, str]]:
    """Active LAN probe when arp-scan is installed (sudo apt install arp-scan)."""
    try:
        proc = subprocess.run(
            ["arp-scan", "--localnet", "--quiet"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return scan_arp_table()
    out: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].count(":") == 5:
            out.append({"ip": parts[0], "mac": parts[1].upper()})
    return out or scan_arp_table()
