from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .esp_reader import EspReader
from .phone_client import PhoneClient
from .storage import Storage

log = logging.getLogger("millie_pi.sync")


class SyncWorker:
    """Background sync from phone API + optional direct ESP ingest."""

    def __init__(self, cfg: dict[str, Any], storage: Storage, phone: PhoneClient):
        self.cfg = cfg
        self.storage = storage
        self.phone = phone
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._esp: EspReader | None = None
        self._last_presence: bool | None = None
        self._last_watchlist: set[str] = set()

    def start(self) -> None:
        esp_cfg = self.cfg.get("esp", {})
        if esp_cfg.get("enabled"):
            self._esp = EspReader(
                port=esp_cfg.get("port", ""),
                baud=int(esp_cfg.get("baud", 115200)),
                on_line=self._on_esp_line,
            )
            self._esp.start()
            self.storage.set_meta("esp_direct", True)
            log.info("direct ESP reader started")

        self._thread = threading.Thread(target=self._loop, name="millie-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._esp:
            self._esp.stop()

    def _on_esp_line(self, obj: dict[str, Any]) -> None:
        if obj.get("t") == "wallsense":
            self.storage.insert_wallsense("esp", obj)
            presence = bool(obj.get("presence"))
            if self.cfg.get("alerts", {}).get("presence_tripwire"):
                self._maybe_presence_alert(presence, "esp")
            return
        kind = obj.get("t")
        mac = obj.get("mac", "")
        if kind and mac:
            self.storage.insert_alert(
                "esp",
                "ESP hit",
                f"{kind.upper()} {mac} rssi={obj.get('rssi')}",
                severity="LOW",
                mac=mac,
                dedupe_key=f"esp:{mac}",
            )

    def _maybe_presence_alert(self, presence: bool, source: str) -> None:
        if self._last_presence is None:
            self._last_presence = presence
            return
        if presence == self._last_presence:
            return
        self._last_presence = presence
        if presence:
            self.storage.insert_alert(
                source,
                "Wall Sense PRESENCE",
                "Motion detected through walls",
                severity="HIGH",
                dedupe_key=f"{source}:presence:on",
            )
        else:
            self.storage.insert_alert(
                source,
                "Wall Sense CLEAR",
                "Area clear — motion stopped",
                severity="MEDIUM",
                dedupe_key=f"{source}:presence:off",
            )

    def _ensure_phone_url(self) -> bool:
        phone_cfg = self.cfg.get("phone", {})
        url = (phone_cfg.get("url") or "").strip()
        if url:
            self.phone.set_url(url)
            ok, _ = self.phone.probe_url(url)
            if ok:
                self.storage.set_meta("phone_url", url)
                self.storage.set_meta("phone_linked", True)
                return True
        if not phone_cfg.get("discover", True):
            self.storage.set_meta("phone_linked", False)
            return False
        found = self.phone.discover(phone_cfg.get("scan_subnets", []))
        if found:
            self.phone.set_url(found)
            self.storage.set_meta("phone_url", found)
            self.storage.set_meta("phone_linked", True)
            log.info("discovered MILLIE phone at %s", found)
            return True
        self.storage.set_meta("phone_linked", False)
        return False

    def _sync_endpoint(
        self,
        name: str,
        fetch_fn,
        store_kind: str | None = None,
        on_ok=None,
    ) -> None:
        ok, data, latency, detail = fetch_fn()
        self.storage.log_sync(name, ok, latency, detail)
        if ok and data is not None:
            if store_kind:
                self.storage.save_snapshot(store_kind, data)
            if on_ok:
                on_ok(data)

    def _loop(self) -> None:
        poll = float(self.cfg.get("phone", {}).get("poll_seconds", 2))
        while not self._stop.is_set():
            linked = self._ensure_phone_url()
            if linked:
                t0 = time.time()
                self._sync_endpoint("state", self.phone.fetch_state, "state")
                self._sync_endpoint("civops", self.phone.fetch_civops, "civops")
                self._sync_endpoint(
                    "wallsense",
                    self.phone.fetch_wallsense,
                    "wallsense",
                    self._on_phone_wallsense,
                )
                self._sync_endpoint(
                    "devices",
                    self.phone.fetch_devices,
                    "devices",
                    self._on_phone_devices,
                )
                self._sync_endpoint("radar", self.phone.fetch_radar, "radar")
                self.storage.set_meta("last_sync_ts", time.time())
                self.storage.set_meta("phone_linked", True)
                log.debug("sync ok in %.0fms", (time.time() - t0) * 1000)
            else:
                self.storage.set_meta("phone_linked", False)
                log.debug("phone not linked — waiting")
            self._stop.wait(poll)

    def _on_phone_wallsense(self, data: dict[str, Any]) -> None:
        if not data.get("enabled"):
            return
        self.storage.insert_wallsense("phone", data)
        if self.cfg.get("alerts", {}).get("presence_tripwire"):
            self._maybe_presence_alert(bool(data.get("presence")), "phone")

    def _on_phone_devices(self, data: dict[str, Any]) -> None:
        self.storage.record_device_counts(data)
        if not self.cfg.get("alerts", {}).get("watchlist_hits"):
            return
        civops = self.storage.latest_snapshot("civops") or {}
        watchlist = set(civops.get("watchlist") or [])
        if not watchlist:
            return
        for dev in data.get("all") or []:
            mac = (dev.get("mac") or "").upper()
            if mac in watchlist and mac not in self._last_watchlist:
                self.storage.insert_alert(
                    "phone",
                    "Watchlist hit",
                    f"{mac} seen on watchlist",
                    severity="HIGH",
                    mac=mac,
                    dedupe_key=f"watch:{mac}",
                )
        self._last_watchlist = watchlist
