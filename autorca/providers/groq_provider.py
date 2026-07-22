"""Groq analysis provider (OpenAI-compatible REST, fast free tier).

Uses Groq's OpenAI-compatible chat-completions endpoint with JSON response mode.
Requires ``GROQ_API_KEY`` in the environment / .env. Get a free key at
https://console.groq.com/keys.

Same strict-JSON schema as the other backends (shared in ``_rca_shared``), so the
reports are structurally identical regardless of engine. On any failure (no key,
network, rate limit, bad JSON) it falls back to the offline heuristic engine if
configured, so a file is never dropped silently.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

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

log = logging.getLogger("autorca.groq")

_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(AnalysisProvider):
    name = "groq"

    def __init__(self, config: Config):
        self.config = config
        self.api_key = config.groq_api_key
        self.model = config.groq_model
        self._fallback = HeuristicProvider(config) if config.fallback_to_heuristic else None

    def analyze(self, digest: ErrorDigest, file_name: str) -> AnalysisResult:
        if not self.api_key:
            log.warning("No GROQ_API_KEY set; using offline heuristic engine.")
            return self._fallback_or_raise(digest, file_name, "missing API key")

        try:
            data = self._call_groq(digest, file_name)
            result = json_to_result(data, digest)
            result.engine = self.name
            result.model = self.model
            return result
        except Exception as exc:  # broad: network, rate-limit, parse, etc.
            log.error("Groq analysis failed (%s); falling back if enabled.", exc)
            return self._fallback_or_raise(digest, file_name, str(exc))

    # ------------------------------------------------------------------ internals
    def _fallback_or_raise(self, digest: ErrorDigest, file_name: str, reason: str) -> AnalysisResult:
        if self._fallback is not None:
            res = self._fallback.analyze(digest, file_name)
            res.engine = f"heuristic (Groq fallback: {reason})"
            return res
        raise RuntimeError(f"Groq unavailable and no fallback configured: {reason}")

    def _call_groq(self, digest: ErrorDigest, file_name: str) -> Dict[str, Any]:
        prompt = build_prompt(digest, file_name)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 8000,
            # Force valid JSON output (the prompt already asks for a JSON object).
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(
            _ENDPOINT,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=self.config.timeout_seconds,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        payload = resp.json()
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Groq response shape: {payload}") from exc

        return parse_json_object(text)
