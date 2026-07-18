from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .phone_client import PhoneClient
from .rf_scanner import RfScanner

log = logging.getLogger("millie_pi.active_node")


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
        self._thread: threading.Thread | None = None
        self._last_push = 0.0
        self._push_ok = 0
        self._push_fail = 0
        self.node_id = cfg.get("node", {}).get("id", "millie-pi")
        self.node_label = cfg.get("node", {}).get("label", "Pi RF node")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="millie-active-node", daemon=True)
        self._thread.start()
        log.info(
            "Active RF node started — WiFi:%s BLE:%s Monitor:%s → phone %s",
            self.scanner.wifi_enabled,
            self.scanner.ble_enabled,
            self.scanner.monitor_enabled,
            self.cfg.get("phone", {}).get("url", ""),
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
            "last_push_ts": self._last_push,
            "rf": self.scanner.status(),
        }

    def _loop(self) -> None:
        push_interval = float(self.cfg.get("phone", {}).get("push_seconds", 2))
        while not self._stop.is_set():
            url = (self.cfg.get("phone", {}).get("url") or "").strip()
            if not url:
                log.warning("phone.url not set — cannot push RF data")
                self._stop.wait(5)
                continue

            self.phone.set_url(url)
            events = self.scanner.collect()
            payload = {
                "node": self.node_id,
                "label": self.node_label,
                "node_ip": PhoneClient.local_ip(),
                "rf": self.scanner.status(),
                "events": events,
            }
            ok, resp, err = self.phone.push_node(payload)
            if ok:
                self._push_ok += 1
                self._last_push = time.time()
                if events:
                    log.info("pushed %d RF events to phone", len(events))
            else:
                self._push_fail += 1
                if self._push_fail <= 3 or self._push_fail % 20 == 0:
                    log.warning("phone push failed: %s", err)
            self._stop.wait(push_interval)
