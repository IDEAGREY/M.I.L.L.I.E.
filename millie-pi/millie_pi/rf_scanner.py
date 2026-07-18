from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
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
    """Native Pi RF collection — wpa_cli / iwlist / iw / nmcli with sudo fallbacks."""

    MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

    def __init__(self, cfg: dict[str, Any]):
        rf = cfg.get("rf", {})
        iface = (rf.get("wifi_iface") or "auto").strip()
        self.wifi_iface = self._detect_wifi_iface() if iface in ("auto", "") else iface
        self.monitor_iface = (rf.get("monitor_iface") or "").strip()
        self.wifi_enabled = bool(rf.get("wifi_scan", True))
        self.ble_enabled = bool(rf.get("ble_scan", True))
        self.monitor_enabled = bool(rf.get("monitor_scan", False)) and bool(self.monitor_iface)
        self.use_sudo = bool(rf.get("use_sudo", True))
        self.scan_script = os.environ.get(
            "MILLIE_RF_SCAN",
            str(Path(__file__).resolve().parent / "rf-scan.sh"),
        )
        self._seen: dict[str, float] = {}
        self._dedupe_s = float(rf.get("dedupe_seconds", 6))
        self._last_wifi = 0.0
        self._last_ble = 0.0
        self._last_monitor = 0.0
        self.wifi_interval = float(rf.get("wifi_interval", 5))
        self.ble_interval = float(rf.get("ble_interval", 10))
        self.monitor_interval = float(rf.get("monitor_interval", 10))
        self._pending: list[dict[str, Any]] = []
        self._pending_lock = threading.Lock()
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
            "wifi_iface": self.wifi_iface,
        }
        log.info(
            "RF scanner on %s (sudo=%s) wifi every %ss ble every %ss",
            self.wifi_iface,
            self.use_sudo,
            self.wifi_interval,
            self.ble_interval,
        )

    def _detect_wifi_iface(self) -> str:
        out, _, _ = self._run(["iw", "dev"], timeout=5)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Interface "):
                iface = line.split()[1]
                if iface != "lo":
                    return iface
        for path in Path("/sys/class/net").iterdir():
            if path.name.startswith("wl"):
                return path.name
        return "wlan0"

    def _dedupe(self, key: str) -> bool:
        now = time.time()
        last = self._seen.get(key, 0)
        if now - last < self._dedupe_s:
            return True
        self._seen[key] = now
        return False

    def _add_events(self, rf_events: list[RfEvent], counter_key: str) -> list[dict[str, Any]]:
        added: list[dict[str, Any]] = []
        for ev in rf_events:
            key = f"w:{ev.mac}:{ev.ssid}" if ev.kind == "wifi" else f"b:{ev.mac}"
            if self._dedupe(key):
                continue
            js = ev.to_json()
            added.append(js)
            self.stats[counter_key] = int(self.stats[counter_key]) + 1
        return added

    def scan_once(self, force: bool = False) -> int:
        """Run due scans and queue new events. Returns count queued this call."""
        now = time.time()
        queued = 0
        if self.wifi_enabled and (force or now - self._last_wifi >= self.wifi_interval):
            self._last_wifi = now
            wifi_events, err, method = self._wifi_scan()
            self.stats["last_wifi_found"] = len(wifi_events)
            self.stats["last_wifi_method"] = method or ""
            if wifi_events:
                self.stats["last_wifi_error"] = ""
                added = self._add_events(wifi_events, "wifi")
                queued += len(added)
                if added:
                    log.info("WiFi scan (%s): %d new APs", method, len(added))
                    with self._pending_lock:
                        self._pending.extend(added)
            elif err:
                self.stats["wifi_failures"] = int(self.stats["wifi_failures"]) + 1
                self.stats["last_wifi_error"] = err[:200]
                if int(self.stats["wifi_failures"]) <= 5 or int(self.stats["wifi_failures"]) % 8 == 0:
                    log.warning("WiFi scan failed: %s", err[:160])
        if self.ble_enabled and (force or now - self._last_ble >= self.ble_interval):
            self._last_ble = now
            ble_events, err, method = self._ble_scan()
            self.stats["last_ble_found"] = len(ble_events)
            self.stats["last_ble_method"] = method or ""
            if ble_events:
                self.stats["last_ble_error"] = ""
                added = self._add_events(ble_events, "ble")
                queued += len(added)
                if added:
                    log.info("BLE scan (%s): %d new devices", method, len(added))
                    with self._pending_lock:
                        self._pending.extend(added)
            elif err:
                self.stats["ble_failures"] = int(self.stats["ble_failures"]) + 1
                self.stats["last_ble_error"] = err[:200]
                if int(self.stats["ble_failures"]) <= 5 or int(self.stats["ble_failures"]) % 8 == 0:
                    log.warning("BLE scan failed: %s", err[:160])
        if self.monitor_enabled and now - self._last_monitor >= self.monitor_interval:
            self._last_monitor = now
            mon = self._monitor_airodump()
            added = self._add_events(mon, "monitor")
            queued += len(added)
            if added:
                with self._pending_lock:
                    self._pending.extend(added)
        return queued

    def collect(self) -> list[dict[str, Any]]:
        """Scan if due, then return and clear all pending events for push."""
        force = int(self.stats["wifi"]) == 0 and int(self.stats["wifi_failures"]) < 30
        if force:
            self.scan_once(force=True)
        else:
            self.scan_once(force=False)
        return self.drain()

    def drain(self) -> list[dict[str, Any]]:
        with self._pending_lock:
            batch = self._pending
            self._pending = []
        return batch

    def test_scan(self) -> dict[str, Any]:
        """Run immediate full scan for diagnostics (--test-scan)."""
        self._last_wifi = 0
        self._last_ble = 0
        self._seen.clear()
        wifi_events, wifi_err, wifi_method = self._wifi_scan()
        ble_events, ble_err, ble_method = self._ble_scan()
        return {
            "iface": self.wifi_iface,
            "wifi_method": wifi_method,
            "wifi_found": len(wifi_events),
            "wifi_error": wifi_err,
            "wifi_sample": [e.to_json() for e in wifi_events[:5]],
            "ble_method": ble_method,
            "ble_found": len(ble_events),
            "ble_error": ble_err,
            "ble_sample": [e.to_json() for e in ble_events[:5]],
        }

    def status(self) -> dict[str, Any]:
        with self._pending_lock:
            pending = len(self._pending)
        return {
            "wifi_iface": self.wifi_iface,
            "monitor_iface": self.monitor_iface or None,
            "wifi_scan": self.wifi_enabled,
            "ble_scan": self.ble_enabled,
            "monitor_scan": self.monitor_enabled,
            "use_sudo": self.use_sudo,
            "pending_events": pending,
            "stats": dict(self.stats),
        }

    def _cmds(self, base: list[str], use_sudo: bool | None = None) -> list[list[str]]:
        sudo = self.use_sudo if use_sudo is None else use_sudo
        out: list[list[str]] = [base]
        if sudo:
            out.insert(0, ["sudo", "-n"] + base)
        return out

    def _run(self, cmd: list[str], timeout: int = 25) -> tuple[str, str, int]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            combined = (r.stdout or "") + (r.stderr or "")
            err_tail = combined.strip()[-240:] if r.returncode not in (0, 1, 124) else ""
            return combined, err_tail, r.returncode
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            self.stats["errors"] = int(self.stats["errors"]) + 1
            return "", str(exc), -1

    def _run_first(self, variants: list[list[str]], timeout: int = 25) -> tuple[str, str, int, list[str]]:
        last_err = ""
        for cmd in variants:
            out, err, rc = self._run(cmd, timeout=timeout)
            if out.strip() and rc in (0, 1, 124):
                return out, err, rc, cmd
            last_err = err or out.strip()[-200:] or f"rc={rc}"
        return "", last_err, -1, variants[-1] if variants else []

    def _wifi_scan(self) -> tuple[list[RfEvent], str, str]:
        attempts: list[tuple[str, callable]] = [
            ("shell", self._wifi_shell_script),
            ("wpa_cli", self._wifi_wpa_cli),
            ("iwlist", self._wifi_iwlist),
            ("iw-trigger", self._wifi_iw_trigger_dump),
            ("nmcli", self._wifi_nmcli),
            ("iw-scan", self._wifi_iw_blocking),
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
            errors.append(f"{name}: 0 APs")
        return [], "; ".join(errors[-4:]), ""

    def _wifi_shell_script(self) -> list[RfEvent]:
        script = self.scan_script
        if not Path(script).exists():
            raise RuntimeError(f"missing scan script: {script}")
        out, err, rc = self._run(
            ["sudo", "-n", "bash", script, self.wifi_iface, "wifi"],
            timeout=50,
        )
        if not out.strip():
            raise RuntimeError(err or f"shell scan empty rc={rc}")
        events: list[RfEvent] = []
        if "===WPA===" in out:
            events.extend(self._parse_wpa_results(out.split("===WPA===", 1)[1].split("===", 1)[0]))
        if not events and "===IWLIST===" in out:
            events.extend(self._parse_iwlist_section(out.split("===IWLIST===", 1)[1].split("===", 1)[0]))
        if not events and "===IW===" in out:
            events.extend(self._parse_iw_scan(out.split("===IW===", 1)[1].split("===", 1)[0]))
        return events

    def _parse_wpa_results(self, block: str) -> list[RfEvent]:
        events: list[RfEvent] = []
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("bssid"):
                continue
            parts = re.split(r"\t+", line)
            if len(parts) < 3:
                parts = line.split()
            if len(parts) < 3:
                continue
            mac = parts[0].upper()
            if not self.MAC_RE.fullmatch(mac):
                continue
            try:
                freq = int(parts[1])
                rssi = int(parts[2])
            except ValueError:
                freq = None
                rssi = None
            ssid = parts[4] if len(parts) > 4 else ""
            events.append(
                RfEvent(
                    kind="wifi",
                    mac=mac,
                    rssi=rssi,
                    ssid=ssid,
                    channel=self._freq_to_ch(freq) if freq else None,
                    ftype="beacon",
                    extra={"pi_scan": "shell-wpa"},
                )
            )
        return events

    def _parse_iwlist_section(self, block: str) -> list[RfEvent]:
        return self._parse_iwlist_text(block)

    def _wifi_wpa_cli(self) -> list[RfEvent]:
        variants = self._cmds(["wpa_cli", "-i", self.wifi_iface, "scan"], False)
        variants += self._cmds(["wpa_cli", "-i", self.wifi_iface, "scan"], True)
        for cmd in variants:
            self._run(cmd, timeout=6)
        time.sleep(2.5)
        out, err, rc, _ = self._run_first(
            self._cmds(["wpa_cli", "-i", self.wifi_iface, "scan_results"], False)
            + self._cmds(["wpa_cli", "-i", self.wifi_iface, "scan_results"], True),
            timeout=12,
        )
        if not out.strip():
            raise RuntimeError(err or "wpa_cli empty")
        return self._parse_wpa_results(out)

    def _parse_iwlist_text(self, out: str) -> list[RfEvent]:
        mac = ""
        ssid = ""
        rssi: int | None = None
        ch: int | None = None
        for line in out.splitlines():
            line = line.strip()
            m = re.search(r"Address:\s*([0-9A-Fa-f:]{17})", line)
            if m:
                if mac:
                    events.append(
                        RfEvent(
                            kind="wifi",
                            mac=mac.upper(),
                            rssi=rssi,
                            ssid=ssid,
                            channel=ch,
                            ftype="beacon",
                            extra={"pi_scan": "iwlist"},
                        )
                    )
                mac = m.group(1)
                ssid = ""
                rssi = None
                ch = None
            elif "Channel:" in line and "Frequency" not in line:
                try:
                    ch = int(line.split("Channel:")[1].split()[0])
                except (ValueError, IndexError):
                    pass
            elif "Signal level" in line:
                m2 = re.search(r"-?\d+", line.split("Signal level")[-1])
                if m2:
                    rssi = int(m2.group())
            elif line.startswith("ESSID:"):
                ssid = line.split("ESSID:")[1].strip().strip('"')
        if mac:
            events.append(
                RfEvent(
                    kind="wifi",
                    mac=mac.upper(),
                    rssi=rssi,
                    ssid=ssid,
                    channel=ch,
                    ftype="beacon",
                    extra={"pi_scan": "iwlist"},
                )
            )
        return events

    def _wifi_iwlist(self) -> list[RfEvent]:
        out, err, rc, cmd = self._run_first(
            self._cmds(["iwlist", self.wifi_iface, "scan"], False)
            + self._cmds(["iwlist", self.wifi_iface, "scan"], True),
            timeout=45,
        )
        if not out.strip():
            raise RuntimeError(err or "iwlist empty")
        return self._parse_iwlist_text(out)

    def _wifi_nmcli(self) -> list[RfEvent]:
        for cmd in self._cmds(["nmcli", "dev", "wifi", "rescan", "ifname", self.wifi_iface], False):
            self._run(cmd, timeout=8)
        time.sleep(2.0)
        out, err, rc, _ = self._run_first(
            self._cmds(
                ["nmcli", "-t", "-f", "BSSID,SSID,CHAN,SIGNAL", "dev", "wifi", "list", "ifname", self.wifi_iface],
                False,
            )
            + self._cmds(
                ["nmcli", "-t", "-f", "BSSID,SSID,CHAN,SIGNAL", "dev", "wifi", "list", "ifname", self.wifi_iface],
                True,
            ),
            timeout=15,
        )
        if not out.strip():
            raise RuntimeError(err or "nmcli empty")
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
                rssi = -100 + int(parts[-1])
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

    def _wifi_iw_trigger_dump(self) -> list[RfEvent]:
        for cmd in self._cmds(["iw", "dev", self.wifi_iface, "scan", "trigger"], False) + self._cmds(
            ["iw", "dev", self.wifi_iface, "scan", "trigger"], True
        ):
            self._run(cmd, timeout=8)
        time.sleep(2.5)
        out, err, rc, _ = self._run_first(
            self._cmds(["iw", "dev", self.wifi_iface, "scan", "dump", "-u"], False)
            + self._cmds(["iw", "dev", self.wifi_iface, "scan", "dump", "-u"], True),
            timeout=20,
        )
        if not out.strip():
            raise RuntimeError(err or "iw dump empty")
        return self._parse_iw_scan(out)

    def _wifi_iw_blocking(self) -> list[RfEvent]:
        out, err, rc, _ = self._run_first(
            self._cmds(["iw", "dev", self.wifi_iface, "scan", "-u"], False)
            + self._cmds(["iw", "dev", self.wifi_iface, "scan", "-u"], True),
            timeout=35,
        )
        if not out.strip():
            raise RuntimeError(err or "iw scan empty")
        return self._parse_iw_scan(out)

    def _parse_iw_scan(self, out: str) -> list[RfEvent]:
        if not out:
            return []
        events: list[RfEvent] = []
        mac = ""
        ssid = ""
        rssi: int | None = None
        freq: int | None = None

        def flush() -> None:
            nonlocal mac, ssid, rssi, freq
            if mac and self.MAC_RE.fullmatch(mac.upper()):
                events.append(
                    RfEvent(
                        kind="wifi",
                        mac=mac.upper(),
                        rssi=rssi,
                        ssid=ssid,
                        channel=self._freq_to_ch(freq) if freq else None,
                        ftype="beacon",
                        extra={"pi_scan": "iw"},
                    )
                )
            mac = ""
            ssid = ""
            rssi = None
            freq = None

        for line in out.splitlines():
            line = line.strip()
            if line.startswith("BSS "):
                flush()
                parts = line.split()
                if parts:
                    mac = parts[1].split("(")[0].lower()
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
        flush()
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
            ("shell", self._ble_shell_script),
            ("hcitool", self._ble_hcitool),
            ("bluetoothctl", self._ble_bluetoothctl),
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

    def _ble_shell_script(self) -> list[RfEvent]:
        script = self.scan_script
        if not Path(script).exists():
            raise RuntimeError(f"missing scan script: {script}")
        out, err, rc = self._run(
            ["sudo", "-n", "bash", script, self.wifi_iface, "ble"],
            timeout=20,
        )
        if not out.strip():
            raise RuntimeError(err or "ble shell empty")
        events: list[RfEvent] = []
        block = out.split("===BLE===", 1)[-1]
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("LE Scan"):
                continue
            if line.startswith("Device "):
                parts = line.split(None, 2)
                if len(parts) >= 2 and self.MAC_RE.fullmatch(parts[1]):
                    name = parts[2] if len(parts) > 2 else ""
                    events.append(
                        RfEvent(kind="ble", mac=parts[1].upper(), name=name, extra={"pi_scan": "shell-ble"})
                    )
                continue
            parts = line.split()
            if parts and self.MAC_RE.fullmatch(parts[0]):
                name = " ".join(parts[1:]) if len(parts) > 1 else ""
                events.append(
                    RfEvent(kind="ble", mac=parts[0].upper(), name=name, extra={"pi_scan": "shell-ble"})
                )
        return events

    def _ble_hcitool(self) -> list[RfEvent]:
        out, err, rc, _ = self._run_first(
            self._cmds(["timeout", "6", "hcitool", "lescan", "--duplicates"], False)
            + self._cmds(["timeout", "6", "hcitool", "lescan", "--duplicates"], True),
            timeout=12,
        )
        if not out.strip():
            raise RuntimeError(err or "hcitool empty")
        events: list[RfEvent] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("LE Scan"):
                continue
            parts = line.split()
            if len(parts) >= 1 and self.MAC_RE.fullmatch(parts[0]):
                name = " ".join(parts[1:]) if len(parts) > 1 else ""
                events.append(
                    RfEvent(kind="ble", mac=parts[0].upper(), name=name, extra={"pi_scan": "hci"})
                )
        return events

    def _ble_bluetoothctl(self) -> list[RfEvent]:
        for cmd in self._cmds(["bluetoothctl", "power", "on"], False) + self._cmds(
            ["bluetoothctl", "power", "on"], True
        ):
            self._run(cmd, timeout=5)
        for cmd in self._cmds(["timeout", "7", "bluetoothctl", "scan", "on"], False) + self._cmds(
            ["timeout", "7", "bluetoothctl", "scan", "on"], True
        ):
            self._run(cmd, timeout=12)
        out, err, rc, _ = self._run_first(
            self._cmds(["bluetoothctl", "devices"], False)
            + self._cmds(["bluetoothctl", "devices"], True),
            timeout=10,
        )
        if not out.strip():
            raise RuntimeError(err or "bluetoothctl empty")
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
        out_dir = "/tmp/millie-airodump"
        self._run(["mkdir", "-p", out_dir], timeout=2)
        self._run(
            [
                "sudo",
                "-n",
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
            ],
            timeout=12,
        )
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
                        extra={"pi_scan": "monitor"},
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
