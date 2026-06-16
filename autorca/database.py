"""SQLite-backed processing history.

Tracks every file the service has seen so that:
  * already-processed files are never analyzed twice (idempotency), and
  * the service recovers gracefully after a restart/crash.

A file is keyed by ``content_hash`` (sha256 of its bytes), so an edited file
that changes content is treated as new work, while an identical file that
reappears is skipped.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name     TEXT    NOT NULL,
    source_path   TEXT    NOT NULL,
    content_hash  TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    status        TEXT    NOT NULL,          -- 'processed' | 'failed'
    report_path   TEXT,
    error_message TEXT,
    processed_at  TEXT    NOT NULL,
    summary       TEXT,
    error_type    TEXT,
    category      TEXT,
    confidence    TEXT,
    engine        TEXT,
    level         TEXT,
    simple_explanation TEXT,
    endpoint      TEXT,
    request_body  TEXT,
    response_body TEXT,
    response_status TEXT,
    incidents_json TEXT,                      -- all affected endpoints (multi-incident logs)
    analysis_json TEXT                        -- full AnalysisResult as JSON (for the web portal)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_content_hash ON processed_files(content_hash);
CREATE INDEX IF NOT EXISTS idx_status ON processed_files(status);
"""

# Columns added after the initial release; applied to pre-existing databases.
_MIGRATION_COLUMNS = {
    "summary": "TEXT",
    "error_type": "TEXT",
    "category": "TEXT",
    "confidence": "TEXT",
    "engine": "TEXT",
    "level": "TEXT",
    "simple_explanation": "TEXT",
    "endpoint": "TEXT",
    "request_body": "TEXT",
    "response_body": "TEXT",
    "response_status": "TEXT",
    "incidents_json": "TEXT",
    "analysis_json": "TEXT",
}


def hash_file(path: Path) -> str:
    """Return the sha256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add any columns missing from an older database (idempotent)."""
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(processed_files)")}
        for col, decl in _MIGRATION_COLUMNS.items():
            if col not in existing:
                self._conn.execute(f"ALTER TABLE processed_files ADD COLUMN {col} {decl}")

    def is_processed(self, content_hash: str) -> bool:
        """True if a file with this content hash was already processed successfully."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM processed_files WHERE content_hash = ? AND status = 'processed'",
                (content_hash,),
            ).fetchone()
        return row is not None

    def record(
        self,
        *,
        file_name: str,
        source_path: str,
        content_hash: str,
        size_bytes: int,
        status: str,
        report_path: Optional[str] = None,
        error_message: Optional[str] = None,
        summary: Optional[str] = None,
        error_type: Optional[str] = None,
        category: Optional[str] = None,
        confidence: Optional[str] = None,
        engine: Optional[str] = None,
        level: Optional[str] = None,
        simple_explanation: Optional[str] = None,
        endpoint: Optional[str] = None,
        request_body: Optional[str] = None,
        response_body: Optional[str] = None,
        response_status: Optional[str] = None,
        incidents_json: Optional[str] = None,
        analysis_json: Optional[str] = None,
    ) -> None:
        """Insert or replace a processing record (idempotent on content_hash)."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO processed_files
                    (file_name, source_path, content_hash, size_bytes,
                     status, report_path, error_message, processed_at,
                     summary, error_type, category, confidence, engine,
                     level, simple_explanation, endpoint, request_body, response_body,
                     response_status, incidents_json, analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    file_name=excluded.file_name,
                    source_path=excluded.source_path,
                    size_bytes=excluded.size_bytes,
                    status=excluded.status,
                    report_path=excluded.report_path,
                    error_message=excluded.error_message,
                    processed_at=excluded.processed_at,
                    summary=excluded.summary,
                    error_type=excluded.error_type,
                    category=excluded.category,
                    confidence=excluded.confidence,
                    engine=excluded.engine,
                    level=excluded.level,
                    simple_explanation=excluded.simple_explanation,
                    endpoint=excluded.endpoint,
                    request_body=excluded.request_body,
                    response_body=excluded.response_body,
                    response_status=excluded.response_status,
                    incidents_json=excluded.incidents_json,
                    analysis_json=excluded.analysis_json
                """,
                (
                    file_name,
                    source_path,
                    content_hash,
                    size_bytes,
                    status,
                    report_path,
                    error_message,
                    datetime.now(timezone.utc).isoformat(),
                    summary,
                    error_type,
                    category,
                    confidence,
                    engine,
                    level,
                    simple_explanation,
                    endpoint,
                    request_body,
                    response_body,
                    response_status,
                    incidents_json,
                    analysis_json,
                ),
            )
            self._conn.commit()

    def stats(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM processed_files GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------ portal queries
    def list_reports(self, search: str = "") -> list:
        """Return all records (newest first), optionally filtered by a search term."""
        sql = "SELECT * FROM processed_files"
        params: tuple = ()
        if search:
            like = f"%{search}%"
            sql += (" WHERE file_name LIKE ? OR summary LIKE ? OR error_type LIKE ?"
                    " OR category LIKE ?")
            params = (like, like, like, like)
        sql += " ORDER BY datetime(processed_at) DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_report(self, report_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM processed_files WHERE id = ?", (report_id,)
            ).fetchone()
        return dict(row) if row else None

    def dashboard_stats(self) -> dict:
        """Aggregate counts for the dashboard."""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM processed_files").fetchone()["n"]
            by_status = {r["status"]: r["n"] for r in self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM processed_files GROUP BY status")}
            by_level = {(r["level"] or "n/a"): r["n"] for r in self._conn.execute(
                "SELECT level, COUNT(*) AS n FROM processed_files "
                "WHERE status='processed' GROUP BY level ORDER BY n DESC")}
            by_category = {(r["category"] or "n/a"): r["n"] for r in self._conn.execute(
                "SELECT category, COUNT(*) AS n FROM processed_files "
                "WHERE status='processed' GROUP BY category ORDER BY n DESC")}
            by_confidence = {(r["confidence"] or "n/a"): r["n"] for r in self._conn.execute(
                "SELECT confidence, COUNT(*) AS n FROM processed_files "
                "WHERE status='processed' GROUP BY confidence")}
            by_error_type = {(r["error_type"] or "n/a"): r["n"] for r in self._conn.execute(
                "SELECT error_type, COUNT(*) AS n FROM processed_files "
                "WHERE status='processed' GROUP BY error_type ORDER BY n DESC LIMIT 8")}
        return {
            "total": total,
            "by_status": by_status,
            "by_level": by_level,
            "by_category": by_category,
            "by_confidence": by_confidence,
            "by_error_type": by_error_type,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
