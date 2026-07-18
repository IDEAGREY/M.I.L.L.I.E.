from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("millie_pi.rf")


@dataclass
class RfEvent:
    kind: str  # wifi | ble
    mac: str
    rssi: int | None = None
    ssid: str = ""
    name: str = ""
    channel: int | None = None
    ftype: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        obj: dict[str, Any] = {"t": self.kind, "mac": self.mac, "source": "pi-rf"}
        if self.rssi is not None:
            obj["rssi"] = self.rssi
        if self.ssid:
            obj["ssid"] = self.ssid
        if self.name:
            obj["name"] = self.name
        if self.channel is not None:
            obj["ch"] = self.channel
        if self.ftype:
            obj["ftype"] = self.ftype
        obj.update(self.extra)
        return obj


class RfScanner:
    """Native Pi RF collection — no ESP32. Uses Pi WiFi/BLE/monitor dongles."""

    MAC_RE = re.compile(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}")

    def __init__(self, cfg: dict[str, Any]):
        rf = cfg.get("rf", {})
        self.wifi_iface = rf.get("wifi_iface", "wlan0")
        self.monitor_iface = (rf.get("monitor_iface") or "").strip()
        self.wifi_enabled = bool(rf.get("wifi_scan", True))
        self.ble_enabled = bool(rf.get("ble_scan", True))
        self.monitor_enabled = bool(rf.get("monitor_scan", False)) and bool(self.monitor_iface)
        self._seen: dict[str, float] = {}
        self._dedupe_s = float(rf.get("dedupe_seconds", 8))
        self._last_wifi = 0.0
        self._last_ble = 0.0
        self._last_monitor = 0.0
        self.wifi_interval = float(rf.get("wifi_interval", 15))
        self.ble_interval = float(rf.get("ble_interval", 20))
        self.monitor_interval = float(rf.get("monitor_interval", 10))
        self.stats = {"wifi": 0, "ble": 0, "monitor": 0, "errors": 0}

    def _dedupe(self, key: str) -> bool:
        now = time.time()
        last = self._seen.get(key, 0)
        if now - last < self._dedupe_s:
            return True
        self._seen[key] = now
        return False

    def collect(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        now = time.time()
        if self.wifi_enabled and now - self._last_wifi >= self.wifi_interval:
            self._last_wifi = now
            for ev in self._wifi_iw_scan():
                if not self._dedupe(f"w:{ev.mac}:{ev.ssid}"):
                    events.append(ev.to_json())
                    self.stats["wifi"] += 1
        if self.ble_enabled and now - self._last_ble >= self.ble_interval:
            self._last_ble = now
            for ev in self._ble_hcitool():
                if not self._dedupe(f"b:{ev.mac}"):
                    events.append(ev.to_json())
                    self.stats["ble"] += 1
        if self.monitor_enabled and now - self._last_monitor >= self.monitor_interval:
            self._last_monitor = now
            for ev in self._monitor_airodump():
                if not self._dedupe(f"m:{ev.mac}:{ev.ftype}"):
                    events.append(ev.to_json())
                    self.stats["monitor"] += 1
        return events

    def status(self) -> dict[str, Any]:
        return {
            "wifi_iface": self.wifi_iface,
            "monitor_iface": self.monitor_iface or None,
            "wifi_scan": self.wifi_enabled,
            "ble_scan": self.ble_enabled,
            "monitor_scan": self.monitor_enabled,
            "stats": dict(self.stats),
        }

    def _run(self, cmd: list[str], timeout: int = 25) -> str:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode not in (0, 1):
                log.debug("cmd %s rc=%s stderr=%s", cmd[0], r.returncode, r.stderr[:200])
            return (r.stdout or "") + (r.stderr or "")
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            self.stats["errors"] += 1
            log.debug("cmd failed: %s", exc)
            return ""

    def _wifi_iw_scan(self) -> list[RfEvent]:
        out = self._run(["iw", "dev", self.wifi_iface, "scan", "-u"])
        if not out:
            out = self._run(["sudo", "iw", "dev", self.wifi_iface, "scan", "-u"])
        if not out:
            return []
        events: list[RfEvent] = []
        mac = ""
        ssid = ""
        rssi: int | None = None
        freq: int | None = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("BSS "):
                parts = line.split()
                if parts:
                    mac = parts[1].split("(")[0].lower()
                    ssid = ""
                    rssi = None
            elif line.startswith("signal:"):
                try:
                    rssi = int(float(line.split("signal:")[1].split()[0].strip()))
                except (ValueError, IndexError):
                    pass
            elif line.startswith("freq:"):
                try:
                    freq = int(line.split("freq:")[1].split()[0])
                except (ValueError, IndexError):
                    pass
            elif line.startswith("SSID:"):
                ssid = line.split("SSID:", 1)[1].strip()
                if mac and self.MAC_RE.fullmatch(mac):
                    ch = self._freq_to_ch(freq) if freq else None
                    events.append(
                        RfEvent(
                            kind="wifi",
                            mac=mac.upper(),
                            rssi=rssi,
                            ssid=ssid,
                            channel=ch,
                            ftype="beacon",
                            extra={"pi_scan": "iw"},
                        )
                    )
        return events

    @staticmethod
    def _freq_to_ch(freq: int) -> int | None:
        if 2412 <= freq <= 2484:
            return (freq - 2407) // 5
        if 5170 <= freq <= 5885:
            return (freq - 5000) // 5
        return None

    def _ble_hcitool(self) -> list[RfEvent]:
        out = self._run(["timeout", "6", "hcitool", "lescan", "--duplicates"], timeout=10)
        if not out:
            out = self._run(["sudo", "timeout", "6", "hcitool", "lescan", "--duplicates"], timeout=10)
        events: list[RfEvent] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("LE Scan"):
                continue
            parts = line.split()
            if len(parts) >= 2 and self.MAC_RE.fullmatch(parts[0]):
                name = " ".join(parts[1:]) if len(parts) > 1 else ""
                events.append(RfEvent(kind="ble", mac=parts[0].upper(), name=name, extra={"pi_scan": "hci"}))
        return events

    def _monitor_airodump(self) -> list[RfEvent]:
        """Promiscuous probe/beacon capture via airodump-ng (USB monitor dongle)."""
        out_dir = "/tmp/millie-airodump"
        self._run(["mkdir", "-p", out_dir], timeout=2)
        cmd = [
            "sudo", "timeout", "8", "airodump-ng",
            self.monitor_iface,
            "--write", f"{out_dir}/snap",
            "--output-format", "csv",
            "--write-interval", "1",
        ]
        self._run(cmd, timeout=12)
        csv_files = sorted(Path(out_dir).glob("snap-*.csv"))
        if not csv_files:
            return []
        try:
            text = csv_files[-1].read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []
        events: list[RfEvent] = []
        section = "ap"
        for line in text.splitlines():
            if line.startswith("BSSID"):
                section = "ap"
                continue
            if line.startswith("Station MAC"):
                section = "sta"
                continue
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            if section == "ap" and len(parts) >= 14:
                mac = parts[0].upper()
                if not self.MAC_RE.fullmatch(mac):
                    continue
                ssid = parts[13].strip()
                try:
                    ch = int(parts[3]) if parts[3].strip().isdigit() else None
                except ValueError:
                    ch = None
                try:
                    rssi = int(parts[8]) if parts[8].strip().lstrip("-").isdigit() else None
                except ValueError:
                    rssi = None
                events.append(
                    RfEvent(
                        kind="wifi",
                        mac=mac,
                        rssi=rssi,
                        ssid=ssid,
                        channel=ch,
                        ftype="monitor_beacon",
                        extra={"pi_scan": "monitor", "oui_flock": False},
                    )
                )
            elif section == "sta" and len(parts) >= 6:
                mac = parts[0].upper()
                if not self.MAC_RE.fullmatch(mac):
                    continue
                try:
                    rssi = int(parts[3]) if parts[3].strip().lstrip("-").isdigit() else None
                except ValueError:
                    rssi = None
                events.append(
                    RfEvent(
                        kind="wifi",
                        mac=mac,
                        rssi=rssi,
                        ftype="probe_req",
                        extra={"pi_scan": "monitor"},
                    )
                )
        return events


# removed duplicate Path import