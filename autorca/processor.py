"""The processing pipeline for a single log file.

Steps, in order:
  1. Read the file & compute its content hash.
  2. Skip if already processed (idempotency / restart-safe).
  3. Parse into an error digest.
  4. Run AI/heuristic analysis.
  5. Render & write a Markdown report.
  6. Record the result in the history DB.
  7. Move the source file into the ``processed/`` folder.

Every step is defensive: a failure is recorded and the file is moved aside so
the service keeps running and never loops forever on a bad file.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Dict, Tuple

from .config import Config
from .database import Database, hash_file
from .engines import active_provider_model
from .log_parser import parse_log
from .providers import build_provider
from .providers.base import AnalysisProvider, AnalysisResult
from .reporter import render_markdown, write_report

log = logging.getLogger("autorca.processor")


class Processor:
    def __init__(self, config: Config, db: Database, provider: AnalysisProvider = None):
        self.config = config
        self.db = db
        # Providers are resolved per file from the active engine (settings table),
        # so a switch made in the portal takes effect on the next analysis.
        self._provider_cache: Dict[Tuple[str, str], AnalysisProvider] = {}

    def _active_provider(self) -> AnalysisProvider:
        provider, model = active_provider_model(self.config, self.db)
        key = (provider, model)
        cached = self._provider_cache.get(key)
        if cached is None:
            if provider == "gemini":
                cfg = replace(self.config, provider="gemini", model=model)
            elif provider == "local":
                cfg = replace(self.config, provider="local", local_model=model)
            else:
                cfg = replace(self.config, provider="heuristic")
            cached = build_provider(cfg)
            self._provider_cache[key] = cached
            log.info("Active engine: %s (%s)", provider, model or "rules")
        return cached

    def process(self, path: Path) -> bool:
        """Process one file. Returns True if a (new) analysis was produced."""
        path = path.resolve()
        if not path.exists():
            return False
        project = self._project_for(path)

        try:
            content_hash = hash_file(path)
        except OSError as exc:
            log.warning("Cannot read %s yet (%s); will retry on next scan.", path.name, exc)
            return False

        if self.db.is_processed(content_hash):
            log.debug("Skipping already-processed file: %s", path.name)
            # Tidy up: if an identical file is sitting in the watch dir, move it aside.
            self._move_to_processed(path)
            return False

        log.info("Analyzing: %s", path.name)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            digest = parse_log(text, self.config.max_excerpt_chars)

            if not digest.has_errors:
                log.info("No errors detected in %s; recording as processed.", path.name)
                result = self._no_error_result()
            else:
                result = self._active_provider().analyze(digest, path.name)

            report = render_markdown(
                result, digest, source_file=path.name, content_hash=content_hash
            )
            report_path = write_report(self.config.reports_dir, path.name, report)
            log.info("Report written: %s", report_path.name)

            self.db.record(
                file_name=path.name,
                source_path=str(path),
                project=project,
                content_hash=content_hash,
                size_bytes=path.stat().st_size,
                status="processed",
                report_path=str(report_path),
                summary=result.summary,
                error_type=result.error_type,
                category=result.category,
                confidence=result.confidence,
                engine=result.engine,
                level=result.level,
                simple_explanation=result.simple_explanation,
                endpoint=digest.request_line or digest.response_line.split("  ->")[0].strip(),
                request_body=digest.request_body,
                response_body=digest.response_body,
                response_status=digest.response_status,
                incidents_json=json.dumps([asdict(i) for i in digest.incidents], ensure_ascii=False),
                analysis_json=json.dumps(asdict(result), ensure_ascii=False),
                component=digest.failing_component or None,
                components_involved=",".join(digest.components_involved) or None,
                transaction_id=(digest.transaction_ids[0] if digest.transaction_ids else None),
            )
            self._move_to_processed(path)
            return True

        except Exception as exc:  # never let one bad file kill the service
            log.exception("Failed to process %s: %s", path.name, exc)
            try:
                self.db.record(
                    file_name=path.name,
                    source_path=str(path),
                    project=project,
                    content_hash=content_hash,
                    size_bytes=path.stat().st_size if path.exists() else 0,
                    status="failed",
                    error_message=str(exc),
                )
                # Move aside so we don't reprocess the same broken file forever.
                self._move_to_processed(path)
            except Exception:
                log.exception("Also failed to record failure for %s", path.name)
            return False

    # ------------------------------------------------------------------ helpers
    def _project_for(self, path: Path) -> str:
        """The project a log belongs to = the top-level sub-folder under the
        watch dir (e.g. 'Nagad'). Files dropped directly in the watch dir, or
        outside it, have no project."""
        try:
            rel = path.resolve().relative_to(self.config.watch_dir.resolve())
        except ValueError:
            return None
        # rel.parts[-1] is the file name; a leading folder (if any) is the project.
        return rel.parts[0] if len(rel.parts) >= 2 else None

    def _processed_dir_for(self, path: Path) -> Path:
        """Processed files are moved into a 'processed' folder inside their own
        project folder, so each project's history stays separate."""
        project = self._project_for(path)
        base = self.config.watch_dir / project if project else self.config.watch_dir
        return base / self.config.processed_subdir

    def _move_to_processed(self, path: Path) -> None:
        if not path.exists():
            return
        dest_dir = self._processed_dir_for(path)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        # Avoid clobbering an existing file of the same name.
        counter = 1
        while dest.exists():
            dest = dest_dir / f"{path.stem}_{counter}{path.suffix}"
            counter += 1
        try:
            shutil.move(str(path), str(dest))
            log.debug("Moved %s -> %s", path.name, dest)
        except OSError as exc:
            log.warning("Could not move %s to processed/: %s", path.name, exc)

    @staticmethod
    def _no_error_result() -> AnalysisResult:
        return AnalysisResult(
            summary="No errors or exceptions were detected in this log file.",
            what_happened="The file was scanned but contained no ERROR/FATAL severities or "
                          "exception stack traces.",
            why_it_happened="n/a", impact="None.", root_cause="n/a",
            error_type="No Error", category="n/a", confidence="High",
            confidence_reason="Deterministic scan found no error signatures.",
            engine="scanner",
        )
