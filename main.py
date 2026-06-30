"""AutoRCA entrypoint.

Usage:
    python main.py                 # start continuous monitoring (default)
    python main.py --once          # process current files once, then exit
    python main.py --file PATH     # analyze a single file and exit
    python main.py --config PATH   # use an alternate config file
    python main.py --stats         # print processing history stats and exit
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from autorca.config import load_config
from autorca.database import Database
from autorca.engines import active_provider_model
from autorca.processor import Processor
from autorca.service import MonitorService


def _setup_logging(level: str, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")]
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AutoRCA - Automated Log Monitoring & Error Analysis")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--once", action="store_true", help="process existing files once, then exit")
    parser.add_argument("--file", help="analyze a single file and exit")
    parser.add_argument("--stats", action="store_true", help="print history stats and exit")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    _setup_logging(config.log_level, config.log_file)
    log = logging.getLogger("autorca")

    db = Database(config.db_path)
    processor = Processor(config, db)

    active_provider, active_model = active_provider_model(config, db)
    log.info("AutoRCA starting | engine=%s %s | watch=%s",
             active_provider, active_model or "(rules)", config.watch_dir)

    try:
        if args.stats:
            print("Processing history:", db.stats() or "{} (nothing processed yet)")
            return 0

        if args.file:
            target = Path(args.file)
            if not target.exists():
                log.error("File not found: %s", target)
                return 2
            processor.process(target)
            return 0

        if args.once:
            service = MonitorService(config, db, processor)
            count = 0
            for path in service._iter_files():
                if processor.process(path):
                    count += 1
            log.info("One-shot run complete. New analyses: %d", count)
            return 0

        # default: continuous monitoring
        service = MonitorService(config, db, processor)
        service.run()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
