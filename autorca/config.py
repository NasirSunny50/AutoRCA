"""Configuration loading for AutoRCA.

Loads ``config.yaml`` and ``.env`` (for secrets) and exposes a typed
:class:`Config` object. All paths are resolved to absolute paths relative to
the project root so the service behaves identically no matter where it's
launched from.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional at runtime
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    # monitoring
    watch_dir: Path
    recursive: bool
    extensions: List[str]
    stability_seconds: float
    poll_interval_seconds: float
    # processing
    processed_subdir: str
    reports_dir: Path
    db_path: Path
    max_excerpt_chars: int
    # ai
    provider: str
    model: str
    timeout_seconds: float
    fallback_to_heuristic: bool
    gemini_api_key: str = ""
    # local LLM (Ollama) provider
    local_model: str = "qwen2.5-coder:7b"
    local_host: str = "http://localhost:11434"
    local_num_ctx: int = 8192
    local_timeout_seconds: float = 300.0
    # logging
    log_level: str = "INFO"
    log_file: Path = field(default_factory=lambda: PROJECT_ROOT / "autorca_service.log")

    @property
    def processed_dir(self) -> Path:
        """Absolute path to the folder where processed files are moved."""
        return self.watch_dir / self.processed_subdir


def _resolve(path_str: str) -> Path:
    """Resolve a config path: absolute stays absolute, relative is rooted at project."""
    p = Path(path_str)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def load_config(config_path: str | os.PathLike | None = None) -> Config:
    """Load configuration from YAML + environment."""
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")

    cfg_file = Path(config_path) if config_path else (PROJECT_ROOT / "config.yaml")
    if not cfg_file.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_file}")

    with open(cfg_file, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    mon = raw.get("monitoring", {})
    proc = raw.get("processing", {})
    ai = raw.get("ai", {})
    log = raw.get("logging", {})

    extensions = [e.lower() if e.startswith(".") else f".{e.lower()}"
                  for e in mon.get("extensions", [".log", ".txt", ".out", ".trace"])]

    return Config(
        watch_dir=_resolve(mon.get("watch_dir", "Error Log File")),
        recursive=bool(mon.get("recursive", True)),
        extensions=extensions,
        stability_seconds=float(mon.get("stability_seconds", 2)),
        poll_interval_seconds=float(mon.get("poll_interval_seconds", 15)),
        processed_subdir=proc.get("processed_subdir", "processed"),
        reports_dir=_resolve(proc.get("reports_dir", "reports")),
        db_path=_resolve(proc.get("db_path", "autorca.db")),
        max_excerpt_chars=int(proc.get("max_excerpt_chars", 16000)),
        provider=ai.get("provider", "gemini").lower(),
        model=ai.get("model", "gemini-2.0-flash"),
        timeout_seconds=float(ai.get("timeout_seconds", 60)),
        fallback_to_heuristic=bool(ai.get("fallback_to_heuristic", True)),
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        local_model=ai.get("local_model", "qwen2.5-coder:7b"),
        local_host=ai.get("local_host", "http://localhost:11434"),
        local_num_ctx=int(ai.get("local_num_ctx", 8192)),
        local_timeout_seconds=float(ai.get("local_timeout_seconds", 300)),
        log_level=log.get("level", "INFO").upper(),
        log_file=_resolve(log.get("file", "autorca_service.log")),
    )
