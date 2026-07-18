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
        self.stats: dict[str, Any] = {
            "wifi": 0,
            "ble": 0,
            "monitor": 0,
            "errors": 0,
            "wifi_failures": 0,
            "ble_failures": 0,
            "last_wifi_error": "",
            "last_ble_error": "",
            "last_wifi_found": 0,
            "last_ble_found": 0,
            "last_wifi_method": "",
            "last_ble_method": "",
        }

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
            wifi_events, err, method = self._wifi_scan()
            self.stats["last_wifi_found"] = len(wifi_events)
            self.stats["last_wifi_method"] = method or ""
            if wifi_events:
                self.stats["last_wifi_error"] = ""
            elif err:
                self.stats["wifi_failures"] = int(self.stats["wifi_failures"]) + 1
                self.stats["last_wifi_error"] = err[:160]
                if int(self.stats["wifi_failures"]) <= 3 or int(self.stats["wifi_failures"]) % 10 == 0:
                    log.warning("WiFi scan failed (%s): %s", method or "none", err[:120])
            for ev in wifi_events:
                if not self._dedupe(f"w:{ev.mac}:{ev.ssid}"):
                    events.append(ev.to_json())
                    self.stats["wifi"] = int(self.stats["wifi"]) + 1
        if self.ble_enabled and now - self._last_ble >= self.ble_interval:
            self._last_ble = now
            ble_events, err, method = self._ble_scan()
            self.stats["last_ble_found"] = len(ble_events)
            self.stats["last_ble_method"] = method or ""
            if ble_events:
                self.stats["last_ble_error"] = ""
            elif err:
                self.stats["ble_failures"] = int(self.stats["ble_failures"]) + 1
                self.stats["last_ble_error"] = err[:160]
                if int(self.stats["ble_failures"]) <= 3 or int(self.stats["ble_failures"]) % 10 == 0:
                    log.warning("BLE scan failed (%s): %s", method or "none", err[:120])
            for ev in ble_events:
                if not self._dedupe(f"b:{ev.mac}"):
                    events.append(ev.to_json())
                    self.stats["ble"] = int(self.stats["ble"]) + 1
        if self.monitor_enabled and now - self._last_monitor >= self.monitor_interval:
            self._last_monitor = now
            for ev in self._monitor_airodump():
                if not self._dedupe(f"m:{ev.mac}:{ev.ftype}"):
                    events.append(ev.to_json())
                    self.stats["monitor"] = int(self.stats["monitor"]) + 1
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

    def _run(self, cmd: list[str], timeout: int = 25) -> tuple[str, str, int]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = (r.stdout or "") + (r.stderr or "")
            return out, out.strip()[-200:] if r.returncode not in (0, 1) else "", r.returncode
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            self.stats["errors"] = int(self.stats["errors"]) + 1
            return "", str(exc), -1

    def _wifi_scan(self) -> tuple[list[RfEvent], str, str]:
        """Try several methods — connected wlan0 often blocks plain `iw scan`."""
        attempts: list[tuple[str, callable]] = [
            ("nmcli", self._wifi_nmcli),
            ("iw-trigger", self._wifi_iw_trigger_dump),
            ("iw-scan", self._wifi_iw_blocking),
            ("sudo-nmcli", lambda: self._wifi_nmcli(use_sudo=True)),
            ("sudo-iw-trigger", lambda: self._wifi_iw_trigger_dump(use_sudo=True)),
            ("sudo-iw-scan", lambda: self._wifi_iw_blocking(use_sudo=True)),
        ]
        errors: list[str] = []
        for name, fn in attempts:
            try:
                events = fn()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            if events:
                return events, "", name
            errors.append(f"{name}: 0 networks")
        return [], "; ".join(errors[-3:]), ""

    def _wifi_nmcli(self, use_sudo: bool = False) -> list[RfEvent]:
        prefix = ["sudo"] if use_sudo else []
        self._run(prefix + ["nmcli", "dev", "wifi", "rescan", "ifname", self.wifi_iface], timeout=8)
        time.sleep(2.0)
        out, err, rc = self._run(
            prefix
            + [
                "nmcli",
                "-t",
                "-f",
                "BSSID,SSID,CHAN,SIGNAL",
                "dev",
                "wifi",
                "list",
                "ifname",
                self.wifi_iface,
            ],
            timeout=15,
        )
        if rc not in (0, 1) or not out.strip():
            if err:
                raise RuntimeError(err)
            return []
        events: list[RfEvent] = []
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 8:
                continue
            mac = ":".join(parts[0:6]).upper()
            if not self.MAC_RE.fullmatch(mac):
                continue
            ssid = ":".join(parts[6:-2])
            try:
                ch = int(parts[-2]) if parts[-2].strip().isdigit() else None
            except ValueError:
                ch = None
            try:
                pct = int(parts[-1])
                rssi = -100 + pct  # nmcli 0–100 → rough dBm
            except ValueError:
                rssi = None
            events.append(
                RfEvent(
                    kind="wifi",
                    mac=mac,
                    rssi=rssi,
                    ssid=ssid,
                    channel=ch,
                    ftype="beacon",
                    extra={"pi_scan": "nmcli"},
                )
            )
        return events

    def _wifi_iw_trigger_dump(self, use_sudo: bool = False) -> list[RfEvent]:
        prefix = ["sudo"] if use_sudo else []
        _, err, rc = self._run(prefix + ["iw", "dev", self.wifi_iface, "scan", "trigger"], timeout=8)
        if rc not in (0, 1) and err:
            raise RuntimeError(err)
        time.sleep(2.5)
        out, err, rc = self._run(prefix + ["iw", "dev", self.wifi_iface, "scan", "dump", "-u"], timeout=15)
        if rc not in (0, 1) and not out.strip():
            raise RuntimeError(err or "empty dump")
        return self._parse_iw_scan(out)

    def _wifi_iw_blocking(self, use_sudo: bool = False) -> list[RfEvent]:
        prefix = ["sudo"] if use_sudo else []
        out, err, rc = self._run(prefix + ["iw", "dev", self.wifi_iface, "scan", "-u"], timeout=30)
        if rc not in (0, 1) and not out.strip():
            raise RuntimeError(err or f"rc={rc}")
        return self._parse_iw_scan(out)

    def _parse_iw_scan(self, out: str) -> list[RfEvent]:
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

    def _ble_scan(self) -> tuple[list[RfEvent], str, str]:
        attempts: list[tuple[str, callable]] = [
            ("hcitool", self._ble_hcitool),
            ("bluetoothctl", self._ble_bluetoothctl),
            ("sudo-hcitool", lambda: self._ble_hcitool(use_sudo=True)),
            ("sudo-bluetoothctl", lambda: self._ble_bluetoothctl(use_sudo=True)),
        ]
        errors: list[str] = []
        for name, fn in attempts:
            try:
                events = fn()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            if events:
                return events, "", name
            errors.append(f"{name}: 0 devices")
        return [], "; ".join(errors[-3:]), ""

    def _ble_hcitool(self, use_sudo: bool = False) -> list[RfEvent]:
        prefix = ["sudo"] if use_sudo else []
        out, err, rc = self._run(
            prefix + ["timeout", "6", "hcitool", "lescan", "--duplicates"], timeout=10
        )
        if rc not in (0, 1, 124) and not out.strip():
            raise RuntimeError(err or f"rc={rc}")
        events: list[RfEvent] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("LE Scan"):
                continue
            parts = line.split()
            if len(parts) >= 2 and self.MAC_RE.fullmatch(parts[0]):
                name = " ".join(parts[1:]) if len(parts) > 1 else ""
                events.append(
                    RfEvent(kind="ble", mac=parts[0].upper(), name=name, extra={"pi_scan": "hci"})
                )
        return events

    def _ble_bluetoothctl(self, use_sudo: bool = False) -> list[RfEvent]:
        prefix = ["sudo"] if use_sudo else []
        self._run(prefix + ["bluetoothctl", "power", "on"], timeout=5)
        self._run(prefix + ["timeout", "7", "bluetoothctl", "scan", "on"], timeout=10)
        out, err, rc = self._run(prefix + ["bluetoothctl", "devices"], timeout=8)
        if rc not in (0, 1) and not out.strip():
            raise RuntimeError(err or f"rc={rc}")
        events: list[RfEvent] = []
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("Device "):
                continue
            parts = line.split(None, 2)
            if len(parts) >= 2 and self.MAC_RE.fullmatch(parts[1]):
                name = parts[2] if len(parts) > 2 else ""
                events.append(
                    RfEvent(kind="ble", mac=parts[1].upper(), name=name, extra={"pi_scan": "btctl"})
                )
        return events

    def _monitor_airodump(self) -> list[RfEvent]:
        """Promiscuous probe/beacon capture via airodump-ng (USB monitor dongle)."""
        out_dir = "/tmp/millie-airodump"
        self._run(["mkdir", "-p", out_dir], timeout=2)
        cmd = [
            "sudo",
            "timeout",
            "8",
            "airodump-ng",
            self.monitor_iface,
            "--write",
            f"{out_dir}/snap",
            "--output-format",
            "csv",
            "--write-interval",
            "1",
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
