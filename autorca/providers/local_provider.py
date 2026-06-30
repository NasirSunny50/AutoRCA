"""Local LLM analysis provider via Ollama.

Runs a model entirely on this machine (no API key, no internet, no data leaves
the host) through Ollama's REST API. Recommended model for this task:
``qwen2.5-coder:7b`` (strong code/log reasoning + JSON output for its size).

It asks the model for strict JSON matching :class:`AnalysisResult` using exactly
the same prompt/schema as the Gemini provider (shared in ``_rca_shared``), so the
reports are structurally identical regardless of backend. On any failure (Ollama
not running, model not pulled, slow timeout, bad JSON) it falls back to the
offline :class:`HeuristicProvider` if configured, so a file is never dropped.

Setup on the target machine:
    1. Install Ollama        -> https://ollama.com/download  (or winget)
    2. ollama pull qwen2.5-coder:7b
    3. set ai.provider: "local" in config.yaml
"""
from __future__ import annotations

import logging

import requests

from ..config import Config
from ..log_parser import ErrorDigest
from .base import AnalysisProvider, AnalysisResult
from .heuristic_provider import HeuristicProvider
from ._rca_shared import (
    SYSTEM_INSTRUCTION,
    build_prompt,
    json_to_result,
    parse_json_object,
)

log = logging.getLogger("autorca.local")


class LocalProvider(AnalysisProvider):
    name = "local"

    def __init__(self, config: Config):
        self.config = config
        self.model = config.local_model
        self.host = config.local_host.rstrip("/")
        self.num_ctx = config.local_num_ctx
        self.timeout = config.local_timeout_seconds
        self._fallback = HeuristicProvider(config) if config.fallback_to_heuristic else None

    def analyze(self, digest: ErrorDigest, file_name: str) -> AnalysisResult:
        try:
            data = self._call_ollama(digest, file_name)
            result = json_to_result(data, digest)
            result.engine = self.name
            result.model = self.model
            return result
        except Exception as exc:  # broad: connection, model-missing, timeout, parse
            log.error("Local model analysis failed (%s); falling back if enabled.", exc)
            return self._fallback_or_raise(digest, file_name, str(exc))

    # ------------------------------------------------------------------ internals
    def _fallback_or_raise(self, digest: ErrorDigest, file_name: str, reason: str) -> AnalysisResult:
        if self._fallback is not None:
            res = self._fallback.analyze(digest, file_name)
            res.engine = f"heuristic (local fallback: {reason})"
            return res
        raise RuntimeError(f"Local model unavailable and no fallback configured: {reason}")

    def _call_ollama(self, digest: ErrorDigest, file_name: str) -> dict:
        prompt = build_prompt(digest, file_name)
        body = {
            "model": self.model,
            "system": SYSTEM_INSTRUCTION,
            "prompt": prompt,
            "stream": False,
            # Ollama constrains output to valid JSON when format="json".
            "format": "json",
            "options": {
                "temperature": 0.2,
                # Bigger context so the full log digest isn't truncated
                # (Ollama defaults to a small 2k window otherwise).
                "num_ctx": self.num_ctx,
                "num_predict": 4096,
            },
        }
        try:
            resp = requests.post(
                f"{self.host}/api/generate", json=body, timeout=self.timeout
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"cannot reach Ollama at {self.host} — is it running? "
                f"(install: https://ollama.com/download, then `ollama serve`)"
            ) from exc

        if resp.status_code == 404:
            raise RuntimeError(
                f"model '{self.model}' not found on Ollama — run `ollama pull {self.model}`"
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        text = resp.json().get("response", "")
        if not text.strip():
            raise RuntimeError("empty response from local model")
        return parse_json_object(text)
