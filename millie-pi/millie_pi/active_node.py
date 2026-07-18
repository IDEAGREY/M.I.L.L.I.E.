from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import __version__
from .phone_client import PhoneClient
from .rf_scanner import RfScanner


class ActiveNode:
    """
    Pi-native RF sensor: scans WiFi/BLE/monitor on the Pi itself,
    pushes events to the phone MILLIE API. Does NOT read ESP32.
    """

    def __init__(self, cfg: dict[str, Any], phone: PhoneClient):
        self.cfg = cfg
        self.phone = phone
        self.scanner = RfScanner(cfg)
        self._stop = threading.Event()
        self._push_thread: threading.Thread | None = None
        self._scan_thread: threading.Thread | None = None
        self._last_push = 0.0
        self._push_ok = 0
        self._push_fail = 0
        self._rf_batches = 0
        self.node_id = cfg.get("node", {}).get("id", "millie-pi")
        self.node_label = cfg.get("node", {}).get("label", "Pi RF node")
        self._scan_seconds = float(cfg.get("rf", {}).get("scan_loop_seconds", 8))

    def start(self) -> None:
        self._scan_thread = threading.Thread(target=self._scan_loop, name="millie-rf-scan", daemon=True)
        self._push_thread = threading.Thread(target=self._push_loop, name="millie-rf-push", daemon=True)
        self._scan_thread.start()
        self._push_thread.start()
        log.info(
            "Active RF node started — iface=%s WiFi:%s BLE:%s → phone %s (scan %ss / push %ss)",
            self.scanner.wifi_iface,
            self.scanner.wifi_enabled,
            self.scanner.ble_enabled,
            self.cfg.get("phone", {}).get("url", ""),
            self._scan_seconds,
            self.cfg.get("phone", {}).get("push_seconds", 2),
        )

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {
            "node": self.node_id,
            "label": self.node_label,
            "phone_url": self.cfg.get("phone", {}).get("url", ""),
            "push_ok": self._push_ok,
            "push_fail": self._push_fail,
            "rf_batches": self._rf_batches,
            "last_push_ts": self._last_push,
            "rf": self.scanner.status(),
        }

    def _scan_loop(self) -> None:
        # Prime immediately so first push can carry data.
        self.scanner.scan_once(force=True)
        while not self._stop.is_set():
            self._stop.wait(self._scan_seconds)
            if self._stop.is_set():
                break
            try:
                n = self.scanner.scan_once(force=False)
                if n == 0 and int(self.scanner.stats.get("wifi", 0)) == 0:
                    self.scanner.scan_once(force=True)
            except Exception:
                log.exception("RF scan loop error")

    def _push_loop(self) -> None:
        push_interval = float(self.cfg.get("phone", {}).get("push_seconds", 2))
        while not self._stop.is_set():
            url = (self.cfg.get("phone", {}).get("url") or "").strip()
            if not url:
                log.warning("phone.url not set — cannot push RF data")
                self._stop.wait(5)
                continue

            self.phone.set_url(url)
            events = self.scanner.drain()
            payload = {
                "node": self.node_id,
                "label": self.node_label,
                "node_ip": PhoneClient.local_ip(),
                "millie_pi_version": __version__,
                "rf": self.scanner.status(),
                "events": events,
            }
            ok, resp, err = self.phone.push_node(payload)
            if ok:
                self._push_ok += 1
                self._last_push = time.time()
                if events:
                    self._rf_batches += 1
                    log.info("pushed %d RF events to phone (batch #%d)", len(events), self._rf_batches)
                elif self._push_ok % 15 == 0:
                    st = self.scanner.stats
                    log.warning(
                        "link ok but 0 RF events — wifi_err=%s ble_err=%s iface=%s",
                        st.get("last_wifi_error", "")[:80],
                        st.get("last_ble_error", "")[:80],
                        self.scanner.wifi_iface,
                    )
            else:
                self._push_fail += 1
                if self._push_fail <= 3 or self._push_fail % 20 == 0:
                    log.warning("phone push failed: %s", err)
            self._stop.wait(push_interval)
