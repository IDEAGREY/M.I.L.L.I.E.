from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.example.yaml"


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG
    if not cfg_path.exists():
        cfg_path = EXAMPLE_CONFIG
    with cfg_path.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    hub = cfg.setdefault("hub", {})
    hub["data_dir"] = str(_expand(hub.get("data_dir", "~/.millie-pi")))
    hub.setdefault("host", "0.0.0.0")
    hub.setdefault("port", 8780)
    phone = cfg.setdefault("phone", {})
    phone.setdefault("poll_seconds", 2)
    phone.setdefault("discover", True)
    phone.setdefault("url", "")
    phone.setdefault(
        "scan_subnets",
        ["192.168.42.0/24", "192.168.43.0/24", "192.168.0.0/24", "192.168.1.0/24"],
    )
    esp = cfg.setdefault("esp", {})
    esp.setdefault("enabled", False)
    esp.setdefault("port", "")
    esp.setdefault("baud", 115200)
    alerts = cfg.setdefault("alerts", {})
    alerts.setdefault("presence_tripwire", True)
    alerts.setdefault("watchlist_hits", True)
    alerts.setdefault("log_to_file", True)
    return cfg
