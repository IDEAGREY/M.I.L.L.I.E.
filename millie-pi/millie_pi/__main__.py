from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import load_config
from .hub_server import create_app
from .phone_client import PhoneClient
from .storage import Storage
from .sync_worker import SyncWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MILLIE Pi — CIVOPS RF hub")
    parser.add_argument("--config", "-c", help="Path to config.yaml")
    parser.add_argument("--discover", action="store_true", help="Find MILLIE phone and exit")
    parser.add_argument("--version", action="version", version=f"millie-pi {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    cfg = load_config(args.config)
    storage = Storage(cfg["hub"]["data_dir"])
    phone = PhoneClient(cfg.get("phone", {}).get("url", ""))

    if args.discover:
        url = phone.discover(cfg.get("phone", {}).get("scan_subnets", []))
        if url:
            print(url)
            return 0
        print("MILLIE phone not found on scanned subnets", file=sys.stderr)
        return 1

    worker = SyncWorker(cfg, storage, phone)
    worker.start()

    app = create_app(cfg, storage, phone, worker)
    host = cfg["hub"]["host"]
    port = int(cfg["hub"]["port"])
    pi_ip = PhoneClient.local_ip()
    logging.getLogger("millie_pi").info(
        "MILLIE Pi hub on http://%s:%s  (LAN: http://%s:%s)",
        host,
        port,
        pi_ip,
        port,
    )
    app.run(host=host, port=port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
