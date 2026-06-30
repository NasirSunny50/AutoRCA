"""Google Gemini analysis provider (free tier, via REST).

Uses the public Generative Language REST endpoint directly so there is no
dependency on a fast-moving Python SDK. Requires ``GEMINI_API_KEY`` in the
environment / .env. Get a free key at https://aistudio.google.com/app/apikey.

The model is asked to return strict JSON matching :class:`AnalysisResult`, which
we parse defensively. On any failure (no key, network, quota, bad JSON) it falls
back to the offline :class:`HeuristicProvider` if configured, so a file is never
dropped silently.
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

log = logging.getLogger("autorca.gemini")

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider(AnalysisProvider):
    name = "gemini"

    def __init__(self, config: Config):
        self.config = config
        self.api_key = config.gemini_api_key
        self.model = config.model
        self._fallback = HeuristicProvider(config) if config.fallback_to_heuristic else None

    def analyze(self, digest: ErrorDigest, file_name: str) -> AnalysisResult:
        if not self.api_key:
            log.warning("No GEMINI_API_KEY set; using offline heuristic engine.")
            return self._fallback_or_raise(digest, file_name, "missing API key")

        try:
            data = self._call_gemini(digest, file_name)
            result = json_to_result(data, digest)
            result.engine = self.name
            result.model = self.model
            return result
        except Exception as exc:  # broad: network, quota, parse, etc.
            log.error("Gemini analysis failed (%s); falling back if enabled.", exc)
            return self._fallback_or_raise(digest, file_name, str(exc))

    # ------------------------------------------------------------------ internals
    def _fallback_or_raise(self, digest: ErrorDigest, file_name: str, reason: str) -> AnalysisResult:
        if self._fallback is not None:
            res = self._fallback.analyze(digest, file_name)
            res.engine = f"heuristic (Gemini fallback: {reason})"
            return res
        raise RuntimeError(f"Gemini unavailable and no fallback configured: {reason}")

    def _call_gemini(self, digest: ErrorDigest, file_name: str) -> Dict[str, Any]:
        prompt = build_prompt(digest, file_name)

        body = {
            "system_instruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "maxOutputTokens": 8192,
                # Gemini 2.5 models "think" by default, which consumes the output
                # budget and can truncate the JSON. We want structured extraction,
                # not chain-of-thought, so disable thinking. (Ignored by 2.0 models.)
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        url = _ENDPOINT.format(model=self.model)
        resp = requests.post(
            url,
            params={"key": self.api_key},
            json=body,
            timeout=self.config.timeout_seconds,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        payload = resp.json()
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Gemini response shape: {payload}") from exc

        return parse_json_object(text)
