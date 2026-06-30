"""The catalogue of selectable AI engines + the active-engine resolver.

A single source of truth shared by:
  * the web portal  — renders the "Model selection" dropdown and writes the
    chosen engine into the settings table, and
  * the monitor     — reads the active engine before each file and builds the
    matching provider.

Because the portal and the monitor are separate processes, the selection is
persisted in the ``settings`` table (keys ``active_provider`` / ``active_model``)
rather than held in memory. A switch therefore applies to the next file the
monitor analyses.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .config import Config
from .database import Database

# Each option maps a friendly label to a (provider, model) pair.
# `kind` is just for grouping/badge display in the UI.
ENGINE_OPTIONS: List[Dict[str, str]] = [
    {"id": "gemini-flash",      "label": "Gemini 2.5 Flash",         "provider": "gemini",    "model": "gemini-2.5-flash",      "kind": "Cloud"},
    {"id": "gemini-flash-lite", "label": "Gemini 2.5 Flash-Lite",    "provider": "gemini",    "model": "gemini-2.5-flash-lite", "kind": "Cloud"},
    {"id": "local-qwen7b",      "label": "Qwen2.5-Coder 7B (local)", "provider": "local",     "model": "qwen2.5-coder:7b",      "kind": "Local"},
    {"id": "local-qwen3b",      "label": "Qwen2.5-Coder 3B (local)", "provider": "local",     "model": "qwen2.5-coder:3b",      "kind": "Local"},
    {"id": "heuristic",         "label": "Offline heuristic (no AI)","provider": "heuristic", "model": "",                      "kind": "Offline"},
]


def option_by_id(engine_id: str) -> Optional[Dict[str, str]]:
    return next((o for o in ENGINE_OPTIONS if o["id"] == engine_id), None)


def active_provider_model(config: Config, db: Database) -> Tuple[str, str]:
    """The (provider, model) currently in effect: the portal selection if any,
    otherwise the config.yaml defaults."""
    provider = (db.get_setting("active_provider") or config.provider).lower()
    if provider == "gemini":
        model = db.get_setting("active_model") or config.model
    elif provider == "local":
        model = db.get_setting("active_model") or config.local_model
    else:
        provider, model = "heuristic", ""
    return provider, model


def active_engine(config: Config, db: Database) -> Dict[str, str]:
    """The currently-active option dict (synthesised if it isn't in the catalogue)."""
    provider, model = active_provider_model(config, db)
    for o in ENGINE_OPTIONS:
        if o["provider"] == provider and (o["provider"] == "heuristic" or o["model"] == model):
            return o
    label = provider.title() if provider == "heuristic" else f"{provider} · {model}"
    return {"id": "custom", "label": label, "provider": provider, "model": model, "kind": "Custom"}
