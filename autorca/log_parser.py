"""Log parsing & error-extraction.

Raw log files can be huge and mostly noise (DEBUG/INFO lines, repeated stack
frames). This module distills a file down to an *error digest*: the lines that
actually matter for root-cause analysis. The digest is what we feed to the AI,
which keeps each request small enough to stay comfortably inside Gemini's free
tier while preserving the cause-and-effect chain.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

# A fully-qualified exception class, e.g. io.jsonwebtoken.ExpiredJwtException
_EXCEPTION_RE = re.compile(
    r"\b((?:[a-zA-Z_$][\w$]*\.){2,}[A-Z][\w$]*(?:Exception|Error|Throwable|Failure))\b"
)
# "Caused by:" chain markers in JVM stack traces
_CAUSED_BY_RE = re.compile(r"^\s*Caused by:\s*(.+)$")
# A stack frame line: "    at com.foo.Bar.method(Bar.java:42)"
_STACK_FRAME_RE = re.compile(r"^\s*at\s+[\w$.<>]+\(")
# Severity tokens commonly found in log lines
_SEVERITY_RE = re.compile(r"\b(FATAL|ERROR|WARN|SEVERE|CRITICAL|Exception)\b")
# Correlation / trace id patterns (CID=..., traceId=..., request-id ...)
_CORRELATION_RE = re.compile(r"\b(?:CID|correlationId|traceId|requestId|request-id)[=:]\s*([\w-]+)", re.I)
# Bracketed ids used by the multi-component logs:  CID[...] / TID[...] / MID[...]
_CID_BRACKET_RE = re.compile(r"\bCID\[([\w-]+)\]")
_TID_BRACKET_RE = re.compile(r"\bTID\[([\w-]+)\]")
# The leading component tag every line carries, e.g. "[apigw] ..." / "[spg] ..."
_COMPONENT_RE = re.compile(r"^\[([a-z0-9][a-z0-9_-]*)\]")
# Header line listing every component that took part in this correlation id.
_COMPONENTS_HEADER_RE = re.compile(r"^#\s*Components involved:\s*(.+)$", re.I)
# HTTP request/response markers, e.g. [REQ] POST /path  or  [RES] POST /path 401
_HTTP_RE = re.compile(r"\[(REQ|RES)\]\s+(\w+)\s+(\S+)(?:\s+(\d{3}))?")
# Full req/res line with an optional JSON/text body after the path/status.
_HTTP_BODY_RE = re.compile(
    r"\[(REQ|RES)\]\s+(\w+)\s+(\S+)(?:\s+(\d{3}))?(?:\s+(\d+)ms)?\s*(\{.*\}|\[.*\])?\s*$"
)


def _compact_body(raw: str, max_len: int = 1400) -> str:
    """Pretty-print a JSON body for display, truncating very long string values
    (e.g. encrypted/base64 payloads) so the readable fields stay visible."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw if len(raw) <= max_len else raw[:max_len] + " …(truncated)"

    def _trim(v):
        if isinstance(v, str) and len(v) > 160:
            return v[:160] + f" …(+{len(v) - 160} chars)"
        if isinstance(v, dict):
            return {k: _trim(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_trim(x) for x in v[:20]]
        return v

    try:
        return json.dumps(_trim(obj), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return raw[:max_len]


@dataclass
class Incident:
    """One affected endpoint: an HTTP call that came back with an error status."""
    method: str = ""
    path: str = ""
    status: str = ""
    reason: str = ""
    request_body: str = ""
    response_body: str = ""
    count: int = 1            # how many times this same (endpoint, reason) occurred

    @property
    def endpoint(self) -> str:
        return f"{self.method} {self.path}".strip()


def _reason_from_body(body: str, status: str) -> str:
    """Pull a human reason out of a response body, falling back to the status."""
    body = (body or "").strip()
    if body:
        try:
            obj = json.loads(body)
            if isinstance(obj, dict):
                for k in ("reason", "errorCode", "code", "error", "devMessage", "message"):
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()[:140]
        except (json.JSONDecodeError, ValueError):
            pass
    sev = {"4": "Client error", "5": "Server error"}.get(status[:1] if status else "", "Error")
    return f"HTTP {status} ({sev})" if status else "Error"


def parse_incidents(lines: List[str], max_raw: int = 600) -> List[Incident]:
    """Find every error response (4xx/5xx) and pair it with its request body.

    Works whether or not the log uses correlation ids: requests are matched to
    responses by path (the most recent request to that path). Identical
    (endpoint, reason) pairs are merged with a count so a flood of the same
    failure shows up once.
    """
    last_req_by_path: dict = {}
    last_req_any = ""
    raw: List[Incident] = []

    for line in lines:
        m = _HTTP_BODY_RE.search(line)
        if not m:
            continue
        kind, method, path, status, _ms, body = m.groups()
        if kind == "REQ":
            compact = _compact_body(body or "")
            if path:
                last_req_by_path[path] = compact
            last_req_any = compact or last_req_any
        elif kind == "RES":
            if not (status and status[:1] in ("4", "5")):
                continue
            raw.append(Incident(
                method=method or "", path=path or "", status=status or "",
                reason=_reason_from_body(body or "", status or ""),
                request_body=last_req_by_path.get(path, last_req_any),
                response_body=_compact_body(body or ""),
            ))
            if len(raw) >= max_raw:
                break

    merged: dict = {}
    order: List[tuple] = []
    for inc in raw:
        key = (inc.endpoint, inc.reason)
        if key in merged:
            merged[key].count += 1
        else:
            merged[key] = inc
            order.append(key)
    incidents = [merged[k] for k in order]
    incidents.sort(key=lambda i: (-i.count, i.endpoint))
    return incidents


@dataclass
class ErrorDigest:
    """Structured, condensed view of the noteworthy parts of a log file."""
    exception_classes: List[str] = field(default_factory=list)
    root_cause_exception: Optional[str] = None
    caused_by_chain: List[str] = field(default_factory=list)
    severities: Counter = field(default_factory=Counter)
    components: List[str] = field(default_factory=list)
    correlation_ids: List[str] = field(default_factory=list)
    transaction_ids: List[str] = field(default_factory=list)
    # multi-component logs: every component seen, and the one that raised the error
    components_involved: List[str] = field(default_factory=list)
    failing_component: str = ""
    http_events: List[str] = field(default_factory=list)
    error_lines: List[str] = field(default_factory=list)
    # HTTP request/response context
    request_line: str = ""          # e.g. "POST /secure-filter/api/.../fund-transfer"
    request_body: str = ""
    response_line: str = ""         # e.g. "POST /...fund-transfer  ->  401  (20ms)"
    response_status: str = ""
    response_body: str = ""
    incidents: List[Incident] = field(default_factory=list)  # all affected endpoints
    total_lines: int = 0
    excerpt: str = ""

    @property
    def has_errors(self) -> bool:
        return bool(self.exception_classes or self.error_lines
                    or self.severities.get("ERROR") or self.severities.get("FATAL"))

    @property
    def primary_exception(self) -> Optional[str]:
        """Best single label for the failure: the deepest 'Caused by', else first seen."""
        if self.root_cause_exception:
            return self.root_cause_exception
        return self.exception_classes[0] if self.exception_classes else None


def _short_class(fqcn: str) -> str:
    """com.foo.BarException -> BarException (for component inference)."""
    return fqcn.rsplit(".", 1)[-1]


def _infer_components(exception_classes: List[str], frames: List[str]) -> List[str]:
    """Infer the affected service/component from package names in app frames.

    We prefer the application's own packages (heuristic: the most frequent
    multi-segment package prefix that isn't a well-known framework) so the report
    points at *your* code rather than at Spring/Netflix internals.
    """
    framework_prefixes = (
        "org.springframework", "com.netflix", "io.undertow", "javax.", "java.",
        "jakarta.", "org.apache", "io.jsonwebtoken", "sun.", "org.hibernate",
    )
    pkg_counter: Counter = Counter()
    for frame in frames:
        m = re.search(r"at\s+([\w$.]+)\(", frame)
        if not m:
            continue
        fqmethod = m.group(1)
        if fqmethod.startswith(framework_prefixes):
            continue
        parts = fqmethod.split(".")
        if len(parts) >= 3:
            pkg_counter[".".join(parts[:3])] += 1
    return [pkg for pkg, _ in pkg_counter.most_common(3)]


def parse_log(text: str, max_excerpt_chars: int = 16000) -> ErrorDigest:
    """Distill raw log text into an :class:`ErrorDigest`."""
    lines = text.splitlines()
    digest = ErrorDigest(total_lines=len(lines))

    seen_exceptions: List[str] = []
    frames: List[str] = []
    context_window: List[str] = []  # rolling buffer for context before an error
    comp_order: List[str] = []           # components seen (in first-seen order)
    header_components: List[str] = []    # authoritative list from the file header
    error_components: List[str] = []     # components that logged an ERROR/FATAL
    exception_components: List[str] = [] # components on an exception line
    error_res_component = ""             # component that returned a 4xx/5xx

    for raw in lines:
        line = raw.rstrip("\n")

        # leading component tag, e.g. "[spg] ..." ; and the header component list
        cmatch = _COMPONENT_RE.match(line)
        comp = cmatch.group(1) if cmatch else None
        if comp and comp not in comp_order:
            comp_order.append(comp)
        hdr = _COMPONENTS_HEADER_RE.match(line)
        if hdr:
            header_components = [c.strip() for c in hdr.group(1).split(",") if c.strip()]

        # bracketed correlation / transaction ids (CID[...] / TID[...])
        for cid in _CID_BRACKET_RE.findall(line):
            if cid and cid not in digest.correlation_ids:
                digest.correlation_ids.append(cid)
        for tid in _TID_BRACKET_RE.findall(line):
            if tid and tid not in digest.transaction_ids:
                digest.transaction_ids.append(tid)

        # collect exception class names
        for m in _EXCEPTION_RE.finditer(line):
            fqcn = m.group(1)
            if fqcn not in seen_exceptions:
                seen_exceptions.append(fqcn)

        # caused-by chain (deepest one is usually the real root cause)
        cb = _CAUSED_BY_RE.match(line)
        if cb:
            digest.caused_by_chain.append(cb.group(1).strip())

        # stack frames (kept separately, not all dumped to the excerpt)
        if _STACK_FRAME_RE.match(line):
            frames.append(line)

        # severities
        sevs = _SEVERITY_RE.findall(line)
        for sev in sevs:
            digest.severities[sev.upper() if sev != "Exception" else "EXCEPTION"] += 1

        # which component is at fault: first one to log ERROR/FATAL or an exception
        if comp:
            if "ERROR" in sevs or "FATAL" in sevs:
                if comp not in error_components:
                    error_components.append(comp)
            if _EXCEPTION_RE.search(line) and comp not in exception_components:
                exception_components.append(comp)

        # correlation ids
        for cid in _CORRELATION_RE.findall(line):
            if cid and cid not in digest.correlation_ids:
                digest.correlation_ids.append(cid)

        # http req/res events (+ capture bodies for display/analysis)
        hm = _HTTP_RE.search(line)
        if hm:
            kind, method, path, status = hm.groups()
            digest.http_events.append(
                f"[{kind}] {method} {path}" + (f" -> {status}" if status else "")
            )
            bm = _HTTP_BODY_RE.search(line)
            body = bm.group(6) if bm else ""
            ms = bm.group(5) if bm else None
            if kind == "REQ" and not digest.request_line:
                digest.request_line = f"{method} {path}"
                digest.request_body = _compact_body(body)
            elif kind == "RES":
                digest.response_line = (
                    f"{method} {path}  ->  {status or '?'}" + (f"  ({ms}ms)" if ms else "")
                )
                digest.response_status = status or ""
                digest.response_body = _compact_body(body)
                if status and status[:1] in ("4", "5") and comp:
                    error_res_component = comp

        # capture error-relevant lines + a little preceding context
        is_error_line = bool(_SEVERITY_RE.search(line)) or bool(cb) \
            or bool(_EXCEPTION_RE.search(line))
        if is_error_line:
            for ctx in context_window:
                if ctx not in digest.error_lines:
                    digest.error_lines.append(ctx)
            digest.error_lines.append(line)
            context_window.clear()
        else:
            context_window.append(line)
            if len(context_window) > 2:
                context_window.pop(0)

    digest.exception_classes = seen_exceptions
    # Component chain + the component that actually failed (drives Jira component).
    digest.components_involved = header_components or comp_order
    digest.failing_component = (
        (error_components[0] if error_components else "")
        or (exception_components[0] if exception_components else "")
        or error_res_component
        or (digest.components_involved[-1] if digest.components_involved else "")
    )
    # The deepest "Caused by" exception class is the most probable root cause.
    if digest.caused_by_chain:
        last_cause = digest.caused_by_chain[-1]
        m = _EXCEPTION_RE.search(last_cause)
        digest.root_cause_exception = m.group(1) if m else last_cause.split(":")[0]
    elif seen_exceptions:
        digest.root_cause_exception = seen_exceptions[-1]

    digest.components = _infer_components(seen_exceptions, frames)

    # All affected endpoints (multi-incident logs). If we found error responses,
    # use the most significant one as the "primary" request/response shown up top.
    digest.incidents = parse_incidents(lines)
    if digest.incidents:
        top = digest.incidents[0]
        digest.request_line = top.endpoint
        digest.request_body = top.request_body
        digest.response_line = f"{top.endpoint}  ->  {top.status}"
        digest.response_status = top.status
        digest.response_body = top.response_body

    digest.excerpt = _build_excerpt(digest, frames, max_excerpt_chars)
    return digest


def _build_excerpt(digest: ErrorDigest, frames: List[str], limit: int) -> str:
    """Build a compact, information-dense excerpt for the AI prompt.

    We include the error/exception lines in full, plus a *trimmed* set of stack
    frames (application frames preferred, framework noise collapsed), staying
    under ``limit`` characters.
    """
    parts: List[str] = []

    if digest.http_events:
        parts.append("HTTP EVENTS:\n" + "\n".join(digest.http_events[:20]))

    if digest.error_lines:
        parts.append("ERROR / EXCEPTION LINES:\n" + "\n".join(digest.error_lines))

    # Prefer application frames; keep at most ~40 frames so a giant trace
    # doesn't blow the budget.
    app_frames = [f for f in frames if not f.lstrip().startswith(
        ("at org.springframework", "at com.netflix", "at io.undertow",
         "at javax.", "at java.", "at jakarta.", "at sun.", "at org.apache"))]
    selected = (app_frames or frames)[:40]
    if selected:
        parts.append("KEY STACK FRAMES:\n" + "\n".join(selected))

    excerpt = "\n\n".join(parts).strip()
    if len(excerpt) > limit:
        excerpt = excerpt[:limit] + "\n...[truncated]"
    return excerpt
