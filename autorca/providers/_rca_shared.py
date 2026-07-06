"""Shared RCA prompt + JSON-to-result logic.

Both the cloud (Gemini) and the local (Ollama) providers ask an LLM to fill the
exact same strict-JSON schema and parse it back into an :class:`AnalysisResult`.
That prompt and the parsing live here so there is a single source of truth for
the schema — change it once and every LLM backend stays in sync.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from ..log_parser import ErrorDigest
from .base import AnalysisResult

SYSTEM_INSTRUCTION = (
    "You are a senior site-reliability engineer performing root-cause analysis (RCA) "
    "on application error logs. You correlate related log lines, follow exception "
    "'Caused by' chains to the deepest root cause, detect cascading failures, and "
    "classify issues as application defects, infrastructure problems, integration "
    "failures, or configuration errors. Be precise, technical, and actionable. "
    "Base every claim on evidence in the provided log; never invent stack frames."
)

# JSON schema we ask the model to fill. Kept flat & explicit for reliable parsing.
PROMPT_TEMPLATE = """Analyze the following error-log digest from file "{file_name}".

Return ONLY a JSON object (no markdown, no commentary) with EXACTLY these keys:
{{
  "summary": "ONE short, plain sentence: what is the problem? (a non-engineer should get it)",
  "simple_explanation": "1-2 sentences in very simple, non-technical language explaining the problem and why it happened, as if to a beginner",
  "level": "the SINGLE level where the issue lives: 'Application' | 'Server' | 'Database' | 'Network' | 'External Service' | 'Configuration'",
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
      "level": "Application | Server | Database | Network | External Service | Configuration",
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

Guidance for "level" (pick the SINGLE best-fitting one):
- Application      = a bug/defect or logic issue in the app's own code (e.g. NullPointerException, unhandled case, wrong logic).
- Server           = the host/runtime/process itself (e.g. out-of-memory, disk full, JVM/startup/thread failure).
- Database         = the datastore (e.g. connection refused, SQL error, deadlock, pool exhausted).
- Network          = raw connectivity between services (e.g. socket timeouts, connection refused, DNS resolution).
- External Service = a third-party / upstream dependency that itself failed or returned an error (payment gateway, partner API, SMS/email provider returning 5xx or a bad/contract-breaking response).
- Configuration    = missing or wrong configuration (absent env var/secret, bad property, unresolved placeholder, misconfigured bean/feature flag).

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


def build_prompt(digest: ErrorDigest, file_name: str) -> str:
    """Render the RCA prompt for one log digest."""
    incident_lines, distinct = [], []
    for inc in digest.incidents[:14]:
        incident_lines.append(
            f"- {inc.endpoint}  | {inc.status} | x{inc.count} | reason: {inc.reason}"
        )
        if inc.reason not in distinct:
            distinct.append(inc.reason)
    incidents_block = "\n".join(incident_lines) or "(no error responses captured)"
    distinct_reasons = "\n".join(f"- {r}" for r in distinct[:8]) or "(none)"

    return PROMPT_TEMPLATE.format(
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


def parse_json_object(text: str) -> Dict[str, Any]:
    """Extract the outermost JSON object from a model's text response."""
    text = text.strip()
    # Strip accidental markdown fences if the model added them.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def json_to_result(data: Dict[str, Any], digest: ErrorDigest) -> AnalysisResult:
    """Map a parsed JSON object onto an AnalysisResult, backfilling blanks."""
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
        result.level = {
            "infrastructure": "Server", "configuration": "Configuration",
            "integration": "External Service", "application": "Application",
        }.get((result.category or "").lower(), "Application")
    result.level = _normalise_level(result.level)
    return result


# Canonical level names + common synonyms the model might return.
_LEVEL_SYNONYMS = {
    "app": "Application", "application": "Application",
    "server": "Server", "host": "Server", "runtime": "Server", "infrastructure": "Server",
    "database": "Database", "db": "Database", "datastore": "Database",
    "network": "Network", "connectivity": "Network",
    "external": "External Service", "external service": "External Service",
    "third party": "External Service", "third-party": "External Service",
    "3rd party": "External Service", "upstream": "External Service", "integration": "External Service",
    "security": "Application", "auth": "Application", "authentication": "Application",
    "authorization": "Application", "authn": "Application", "authz": "Application",
    "config": "Configuration", "configuration": "Configuration",
}


def _normalise_level(level: str) -> str:
    key = (level or "").strip().lower()
    return _LEVEL_SYNONYMS.get(key, (level or "Application").strip().title())
