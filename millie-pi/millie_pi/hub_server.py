from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

from .phone_client import PhoneClient
from .storage import Storage
from .sync_worker import SyncWorker

log = logging.getLogger("millie_pi.hub")


def create_app(cfg: dict[str, Any], storage: Storage, phone: PhoneClient, worker: SyncWorker) -> Flask:
    app = Flask(__name__, static_folder=str(Path(__file__).parent.parent / "static"))
    hub_cfg = cfg.get("hub", {})

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "hub.html")

    @app.get("/api/hub/status")
    def hub_status():
        return jsonify(storage.hub_status())

    @app.get("/api/hub/handshake")
    def hub_handshake():
        return jsonify(
            {
                "millie_pi": True,
                "version": "1.0.0",
                "phone_linked": storage.get_meta("phone_linked", False),
                "port": hub_cfg.get("port", 8780),
            }
        )

    @app.get("/api/hub/history/wallsense")
    def wallsense_history():
        limit = request.args.get("limit", 500, type=int)
        source = request.args.get("source")
        return jsonify({"samples": storage.wallsense_history(limit, source)})

    @app.get("/api/hub/alerts")
    def alerts():
        limit = request.args.get("limit", 50, type=int)
        return jsonify({"alerts": storage.recent_alerts(limit)})

    @app.get("/api/hub/snapshots/<kind>")
    def snapshot(kind: str):
        data = storage.latest_snapshot(kind)
        if data is None:
            return jsonify({"error": "no snapshot"}), 404
        return jsonify(data)

    @app.post("/api/hub/phone/wallsense")
    def proxy_wallsense():
        body = request.get_json(silent=True) or {}
        ok, data, err = phone.wallsense_command(
            body.get("action", ""),
            body.get("arg", ""),
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), 502
        return jsonify(data or {"ok": True})

    @app.post("/api/hub/phone/command")
    def proxy_command():
        body = request.get_json(silent=True) or {}
        ok, data, err = phone.civops_command(
            body.get("cmd", ""),
            body.get("arg1", ""),
            body.get("arg2", ""),
        )
        if not ok:
            return jsonify({"ok": False, "error": err}), 502
        return jsonify(data or {"ok": True})

    @app.post("/api/hub/esp/command")
    def esp_command():
        if not worker._esp or not worker._esp.connected:
            return jsonify({"ok": False, "error": "ESP not connected on Pi"}), 503
        body = request.get_json(silent=True) or {}
        worker._esp.send_cmd(body.get("cmd", ""), body.get("arg1", ""), body.get("arg2", ""))
        return jsonify({"ok": True})

    @app.get("/api/hub/export/alerts.csv")
    def export_alerts():
        rows = storage.recent_alerts(5000)
        lines = ["timestamp,source,severity,title,body,mac\n"]
        for r in reversed(rows):
            lines.append(
                f"{r['ts']},{r['source']},{r['severity']},{json_escape(r['title'])},{json_escape(r['body'])},{r.get('mac') or ''}\n"
            )
        return Response("".join(lines), mimetype="text/csv")

    return app


def json_escape(s: str) -> str:
    return '"' + (s or "").replace('"', '""') + '"'
