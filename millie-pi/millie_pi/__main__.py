from __future__ import annotations

import argparse
import logging
import sys
import time

from . import __version__
from .active_node import ActiveNode
from .config import load_config
from .phone_client import PhoneClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MILLIE Pi — native RF sensor, pushes to phone (no ESP, no Pi dashboard)"
    )
    parser.add_argument("--config", "-c", help="Path to config.yaml")
    parser.add_argument("--discover", action="store_true", help="Find MILLIE phone and exit")
    parser.add_argument("--version", action="version", version=f"millie-pi {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    cfg = load_config(args.config)
    phone = PhoneClient(cfg.get("phone", {}).get("url", ""))

    if args.discover:
        url = phone.discover(cfg.get("phone", {}).get("scan_subnets", []))
        if url:
            print(url)
            return 0
        print("MILLIE phone not found on scanned subnets", file=sys.stderr)
        return 1

    node = ActiveNode(cfg, phone)
    node.start()

    log = logging.getLogger("millie_pi")
    log.info(
        "Pi RF node running — pushing to %s every %ss (Ctrl+C to stop)",
        cfg.get("phone", {}).get("url", "?"),
        cfg.get("phone", {}).get("push_seconds", 2),
    )
    log.info("Phone dashboard shows PI RF devices with source pi-rf")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        node.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
