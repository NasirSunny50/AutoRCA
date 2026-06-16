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

import json
import logging
from typing import Any, Dict, List

import requests

from ..config import Config
from ..log_parser import ErrorDigest
from .base import AnalysisProvider, AnalysisResult
from .heuristic_provider import HeuristicProvider

log = logging.getLogger("autorca.gemini")

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM_INSTRUCTION = (
    "You are a senior site-reliability engineer performing root-cause analysis (RCA) "
    "on application error logs. You correlate related log lines, follow exception "
    "'Caused by' chains to the deepest root cause, detect cascading failures, and "
    "classify issues as application defects, infrastructure problems, integration "
    "failures, or configuration errors. Be precise, technical, and actionable. "
    "Base every claim on evidence in the provided log; never invent stack frames."
)

# JSON schema we ask Gemini to fill. Kept flat & explicit for reliable parsing.
_PROMPT_TEMPLATE = """Analyze the following error-log digest from file "{file_name}".

Return ONLY a JSON object (no markdown, no commentary) with EXACTLY these keys:
{{
  "summary": "ONE short, plain sentence: what is the problem? (a non-engineer should get it)",
  "simple_explanation": "1-2 sentences in very simple, non-technical language explaining the problem and why it happened, as if to a beginner",
  "level": "the SINGLE level where the issue lives: 'Application' | 'Server' | 'Database' | 'Network'",
  "level_reason": "one short clause on why it's that level",
  "error_type": "short label, e.g. 'Authentication / Session Expiry'",
  "exception_class": "fully-qualified primary exception class",
  "affected_component": "the service/component/module at fault",
  "failure_point": "the specific class.method(file:line) where it failed",
  "probable_trigger": "what action/event set this off",
  "category": "one of: application | infrastructure | integration | configuration",
  "what_happened": "2-4 sentence factual description",
  "why_it_happened": "the mechanism of failure",
  "root_cause": "the deepest underlying cause",
  "impact": "user/business/system impact",
  "resolution_steps": ["ordered, concrete remediation steps"],
  "sequence_of_events": ["chronological steps reconstructed from the log"],
  "cascading_failures": ["secondary failures this triggered, if any"],
  "confidence": "High | Medium | Low",
  "confidence_reason": "why this confidence level",
  "reason_groups": [
    {{
      "reason": "COPY the exact reason string given in DISTINCT REASONS below",
      "title": "short plain headline for this specific reason",
      "level": "Application | Server | Database | Network",
      "simple_explanation": "1-2 sentences, plain non-technical language",
      "what_happened": "what failed for this reason",
      "root_cause": "the underlying cause of THIS reason",
      "fix": "the single most important fix for this reason"
    }}
  ]
}}

IMPORTANT about reason_groups: the log may contain SEVERAL different failures on
DIFFERENT endpoints. Produce ONE entry in "reason_groups" for EACH distinct reason
listed under DISTINCT REASONS below — explain each one separately. If there is only
one reason, return a single entry. Always copy the "reason" value verbatim so it can
be matched back to its endpoints.

Guidance for "level":
- Application = a bug/defect or logic issue in the app's own code (e.g. NullPointerException, bad auth handling, unhandled case).
- Server     = the host/runtime/process or its configuration (e.g. out-of-memory, disk full, JVM/startup/config failure).
- Database   = the datastore (e.g. connection refused, SQL error, deadlock, pool exhausted).
- Network    = connectivity between services (e.g. timeouts, connection refused to a remote host, DNS, integration calls).

Log statistics: total_lines={total_lines}, severities={severities}, correlation_ids={correlation_ids}.
Exception classes seen (first->last): {exception_classes}
Caused-by chain (outer->root): {caused_by}

PRIMARY HTTP REQUEST: {request_line}
REQUEST BODY: {request_body}
PRIMARY HTTP RESPONSE: {response_line}
RESPONSE BODY: {response_body}

AFFECTED ENDPOINTS (endpoint | status | count | reason):
{incidents_block}

DISTINCT REASONS (one reason_groups entry required for each):
{distinct_reasons}

--- LOG DIGEST START ---
{excerpt}
--- LOG DIGEST END ---
"""

_LIST_FIELDS = {"resolution_steps", "sequence_of_events", "cascading_failures"}
_STR_FIELDS = {
    "summary", "simple_explanation", "level", "level_reason",
    "error_type", "exception_class", "affected_component",
    "failure_point", "probable_trigger", "category", "what_happened",
    "why_it_happened", "root_cause", "impact", "confidence", "confidence_reason",
}


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
            result = self._to_result(data, digest)
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
        # Build the affected-endpoints block + the distinct reasons to explain.
        incident_lines, distinct = [], []
        for inc in digest.incidents[:14]:
            incident_lines.append(
                f"- {inc.endpoint}  | {inc.status} | x{inc.count} | reason: {inc.reason}"
            )
            if inc.reason not in distinct:
                distinct.append(inc.reason)
        incidents_block = "\n".join(incident_lines) or "(no error responses captured)"
        distinct_reasons = "\n".join(f"- {r}" for r in distinct[:8]) or "(none)"

        prompt = _PROMPT_TEMPLATE.format(
            file_name=file_name,
            total_lines=digest.total_lines,
            severities=dict(digest.severities),
            correlation_ids=digest.correlation_ids[:5],
            exception_classes=digest.exception_classes[:10] or ["(none)"],
            caused_by=digest.caused_by_chain[:10] or ["(none)"],
            request_line=digest.request_line or "(none)",
            request_body=digest.request_body or "(none)",
            response_line=digest.response_line or "(none)",
            response_body=digest.response_body or "(none)",
            incidents_block=incidents_block,
            distinct_reasons=distinct_reasons,
            excerpt=digest.excerpt or "(no excerpt extracted)",
        )

        body = {
            "system_instruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
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

        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        text = text.strip()
        # Strip accidental markdown fences if the model added them.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
        # Grab the outermost JSON object.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
        return json.loads(text)

    def _to_result(self, data: Dict[str, Any], digest: ErrorDigest) -> AnalysisResult:
        result = AnalysisResult()
        for field in _STR_FIELDS:
            val = data.get(field)
            if isinstance(val, str):
                setattr(result, field, val.strip())
        for field in _LIST_FIELDS:
            val = data.get(field)
            if isinstance(val, list):
                setattr(result, field, [str(x).strip() for x in val if str(x).strip()])
            elif isinstance(val, str) and val.strip():
                setattr(result, field, [val.strip()])

        # reason_groups is a list of dicts (one per distinct error reason).
        rg = data.get("reason_groups")
        if isinstance(rg, list):
            result.reason_groups = [g for g in rg if isinstance(g, dict)]

        # Backfill anything the model left blank from the deterministic digest.
        if not result.exception_class and digest.primary_exception:
            result.exception_class = digest.primary_exception
        if not result.affected_component and digest.components:
            result.affected_component = digest.components[0]
        if not result.confidence:
            result.confidence = "Medium"
        if not result.level:
            # Map the broad category onto a level as a fallback.
            result.level = {
                "infrastructure": "Server", "configuration": "Server",
                "integration": "Network", "application": "Application",
            }.get((result.category or "").lower(), "Application")
        result.level = result.level.strip().title()
        return result
