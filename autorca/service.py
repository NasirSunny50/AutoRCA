"""The long-running monitoring service.

Combines:
  * a watchdog observer for *live* filesystem events (new/modified files), and
  * a periodic safety-net rescan that catches anything the OS event stream
    missed (network shares, rapid writes, events during downtime).

Files are only handed to the processor once they are *stable* (their size has
stopped changing for ``stability_seconds``), so we never read a half-written
file. The ``processed/`` folder is always ignored.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .database import Database
from .processor import Processor

log = logging.getLogger("autorca.service")


class _LogEventHandler(FileSystemEventHandler):
    """Feeds candidate paths into the service's pending queue."""

    def __init__(self, service: "MonitorService"):
        self.service = service

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.service.enqueue(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.service.enqueue(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.service.enqueue(Path(event.dest_path))


class MonitorService:
    def __init__(self, config: Config, db: Database, processor: Processor):
        self.config = config
        self.db = db
        self.processor = processor
        self._observer = Observer()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # path -> last-seen (size, mtime); used for the stability check
        self._pending: Dict[str, tuple] = {}

    # ------------------------------------------------------------------ filtering
    def _is_candidate(self, path: Path) -> bool:
        if path.suffix.lower() not in self.config.extensions:
            return False
        # Never (re)process files already inside the processed/ folder.
        try:
            path.resolve().relative_to(self.config.processed_dir.resolve())
            return False
        except ValueError:
            return True

    def enqueue(self, path: Path) -> None:
        if self._is_candidate(path):
            with self._lock:
                self._pending.setdefault(str(path.resolve()), (None, None))

    # ------------------------------------------------------------------ scanning
    def _initial_scan(self) -> None:
        """On startup, queue every existing candidate (restart recovery)."""
        log.info("Performing startup scan of %s ...", self.config.watch_dir)
        for path in self._iter_files():
            self.enqueue(path)

    def _iter_files(self):
        if not self.config.watch_dir.exists():
            return
        globber = self.config.watch_dir.rglob if self.config.recursive else self.config.watch_dir.glob
        for path in globber("*"):
            if path.is_file() and self._is_candidate(path):
                yield path

    def _rescan(self) -> None:
        """Safety-net: re-queue any candidate still sitting in the watch dir."""
        for path in self._iter_files():
            self.enqueue(path)

    # ------------------------------------------------------------------ stability
    def _drain_pending(self) -> None:
        """Process pending files that have become stable."""
        with self._lock:
            items = list(self._pending.items())

        for key, (prev_size, _prev_mtime) in items:
            path = Path(key)
            if not path.exists():
                self._forget(key)
                continue
            try:
                stat = path.stat()
            except OSError:
                continue

            # Stable means: size unchanged since we last looked.
            if prev_size is not None and stat.st_size == prev_size:
                self._forget(key)
                self.processor.process(path)
            else:
                with self._lock:
                    self._pending[key] = (stat.st_size, stat.st_mtime)

    def _forget(self, key: str) -> None:
        with self._lock:
            self._pending.pop(key, None)

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> None:
        self.config.watch_dir.mkdir(parents=True, exist_ok=True)
        self.config.processed_dir.mkdir(parents=True, exist_ok=True)

        self._initial_scan()

        handler = _LogEventHandler(self)
        self._observer.schedule(
            handler, str(self.config.watch_dir), recursive=self.config.recursive
        )
        self._observer.start()
        log.info(
            "Monitoring '%s' (recursive=%s, extensions=%s). Press Ctrl+C to stop.",
            self.config.watch_dir, self.config.recursive, ",".join(self.config.extensions),
        )

        last_rescan = 0.0
        try:
            while not self._stop.is_set():
                # The stability check needs two samples spaced by stability_seconds.
                time.sleep(self.config.stability_seconds)
                self._drain_pending()

                now = time.time()
                if now - last_rescan >= self.config.poll_interval_seconds:
                    self._rescan()
                    last_rescan = now
        except KeyboardInterrupt:
            log.info("Stop requested (Ctrl+C).")
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        except Exception:
            pass
        stats = self.db.stats()
        log.info("Service stopped. Processing history: %s", stats or "{}")
