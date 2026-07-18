from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Storage:
    """Local SQLite archive for synced MILLIE data and Pi-native ESP ingest."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "millie_pi.db"
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_ts REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    endpoint TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    latency_ms REAL,
                    detail TEXT
                );
                CREATE TABLE IF NOT EXISTS wallsense_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    source TEXT NOT NULL,
                    motion REAL,
                    variance REAL,
                    presence INTEGER NOT NULL,
                    anchor TEXT,
                    rssi INTEGER,
                    calibrated INTEGER,
                    payload TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ws_ts ON wallsense_samples(ts);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    mac TEXT,
                    dedupe_key TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
                CREATE TABLE IF NOT EXISTS device_counts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    total INTEGER NOT NULL,
                    suspicious INTEGER NOT NULL,
                    trackers INTEGER NOT NULL,
                    payload TEXT
                );
                CREATE TABLE IF NOT EXISTS phone_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def set_meta(self, key: str, value: Any) -> None:
        now = time.time()
        blob = json.dumps(value)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta(key, value, updated_ts) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts
                """,
                (key, blob, now),
            )

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row["value"])

    def log_sync(self, endpoint: str, ok: bool, latency_ms: float | None = None, detail: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sync_log(ts, endpoint, ok, latency_ms, detail) VALUES(?,?,?,?,?)",
                (time.time(), endpoint, 1 if ok else 0, latency_ms, detail[:500]),
            )

    def insert_wallsense(
        self,
        source: str,
        payload: dict[str, Any],
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO wallsense_samples(
                    ts, source, motion, variance, presence, anchor, rssi, calibrated, payload
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    now,
                    source,
                    payload.get("motion"),
                    payload.get("variance"),
                    1 if payload.get("presence") else 0,
                    payload.get("anchor") if payload.get("anchor") else None,
                    payload.get("anchor_rssi"),
                    1 if payload.get("calibrated") else 0,
                    json.dumps(payload),
                ),
            )

    def wallsense_history(self, limit: int = 500, source: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM wallsense_samples"
        args: list[Any] = []
        if source:
            q += " WHERE source=?"
            args.append(source)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(q, args).fetchall()
        out = []
        for row in reversed(rows):
            out.append(
                {
                    "ts": row["ts"],
                    "source": row["source"],
                    "motion": row["motion"],
                    "variance": row["variance"],
                    "presence": bool(row["presence"]),
                    "anchor": row["anchor"],
                    "rssi": row["rssi"],
                    "calibrated": bool(row["calibrated"]),
                }
            )
        return out

    def insert_alert(
        self,
        source: str,
        title: str,
        body: str,
        severity: str = "MEDIUM",
        mac: str = "",
        dedupe_key: str = "",
    ) -> bool:
        if dedupe_key:
            with self._lock, self._connect() as conn:
                recent = conn.execute(
                    "SELECT id FROM alerts WHERE dedupe_key=? AND ts > ?",
                    (dedupe_key, time.time() - 120),
                ).fetchone()
            if recent:
                return False
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts(ts, source, title, body, severity, mac, dedupe_key)
                VALUES(?,?,?,?,?,?,?)
                """,
                (time.time(), source, title, body, severity, mac, dedupe_key),
            )
        return True

    def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def save_snapshot(self, kind: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO phone_snapshots(ts, kind, payload) VALUES(?,?,?)",
                (time.time(), kind, json.dumps(payload)),
            )

    def latest_snapshot(self, kind: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM phone_snapshots WHERE kind=? ORDER BY id DESC LIMIT 1",
                (kind,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def record_device_counts(self, payload: dict[str, Any]) -> None:
        all_devs = payload.get("all") or []
        suspicious = sum(1 for d in all_devs if d.get("suspicious"))
        trackers = sum(1 for d in all_devs if d.get("tracker"))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO device_counts(ts, total, suspicious, trackers, payload)
                VALUES(?,?,?,?,?)
                """,
                (time.time(), len(all_devs), suspicious, trackers, json.dumps({"total": len(all_devs)})),
            )

    def hub_status(self) -> dict[str, Any]:
        phone_url = self.get_meta("phone_url", "")
        phone_linked = bool(self.get_meta("phone_linked", False))
        last_sync = self.get_meta("last_sync_ts", 0)
        wallsense = self.latest_snapshot("wallsense") or {}
        civops = self.latest_snapshot("civops") or {}
        state = self.latest_snapshot("state") or {}
        return {
            "hub": "millie-pi",
            "version": "1.0.0",
            "phone_url": phone_url,
            "phone_linked": phone_linked,
            "last_sync_ts": last_sync,
            "last_sync_ago_s": round(time.time() - last_sync, 1) if last_sync else None,
            "wallsense": wallsense,
            "civops_active": civops.get("active", False),
            "usb_connected": state.get("usb_enabled", False),
            "scanning": state.get("scanning", False),
            "gps": state.get("gps"),
            "esp_direct": self.get_meta("esp_direct", False),
            "alerts_recent": len(self.recent_alerts(10)),
        }
