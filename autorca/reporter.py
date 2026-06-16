"""Render an :class:`AnalysisResult` into a human-readable Markdown report."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .log_parser import ErrorDigest
from .providers.base import AnalysisResult

_CONFIDENCE_BADGE = {"high": "🟢 High", "medium": "🟡 Medium", "low": "🔴 Low"}


_LEVELS = ["Application", "Server", "Database", "Network"]


def _level_bar(level: str) -> str:
    """Render the 4 levels inline, marking the affected one."""
    lvl = (level or "").strip().lower()
    parts = []
    for name in _LEVELS:
        if name.lower() == lvl:
            parts.append(f"**[ ✅ {name} ]**")
        else:
            parts.append(f"{name}")
    return "  →  ".join(parts)


def _req_res_section(digest: ErrorDigest) -> str:
    """Markdown block showing the captured endpoint, request & response, if any."""
    if not (digest.request_body or digest.response_body or digest.request_line):
        return ""
    endpoint = digest.request_line or digest.response_line.split("  ->")[0].strip()
    out = []
    if endpoint:
        out.append(f"## 🎯 Endpoint\n`{endpoint}`"
                   + (f"  →  **{digest.response_status}**" if digest.response_status else "")
                   + "\n\n---\n")
    out.append("## 🔁 Request &amp; Response\n")
    if digest.request_line:
        out.append(f"**➡️ Request:** `{digest.request_line}`")
        if digest.request_body:
            out.append(f"```json\n{digest.request_body}\n```")
    if digest.response_line:
        out.append(f"**⬅️ Response:** `{digest.response_line}`")
        if digest.response_body:
            out.append(f"```json\n{digest.response_body}\n```")
    out.append("\n---\n")
    return "\n".join(out) + "\n"


def _affected_section(digest: ErrorDigest, result: AnalysisResult) -> str:
    """Markdown for affected endpoints, grouped by reason (multi-incident aware)."""
    incidents = digest.incidents
    if not incidents:
        return _req_res_section(digest)

    expl = {(g.get("reason") or "").strip(): g for g in (result.reason_groups or [])}

    # group incidents by reason, preserving order
    order, bucket = [], {}
    for inc in incidents:
        bucket.setdefault(inc.reason, []).append(inc)
        if inc.reason not in order:
            order.append(inc.reason)

    out = [f"## 🎯 Affected Endpoints ({len(incidents)} endpoint(s), "
           f"{len(order)} distinct reason(s))\n"]
    out.append("| Endpoint | Status | Count | Reason |")
    out.append("|----------|--------|-------|--------|")
    for inc in incidents:
        out.append(f"| `{inc.endpoint}` | {inc.status} | {inc.count} | {inc.reason} |")
    out.append("")

    for reason in order:
        g = expl.get(reason) or {}
        title = g.get("title") or reason
        out.append(f"### 🧩 {title}")
        if g.get("level"):
            out.append(f"**Level:** {g['level']}")
        if g.get("simple_explanation"):
            out.append(f"\n{g['simple_explanation']}")
        if g.get("root_cause"):
            out.append(f"\n**Why:** {g['root_cause']}")
        if g.get("fix"):
            out.append(f"\n**Fix:** {g['fix']}")
        out.append(f"\n_Reason code:_ `{reason}` · _Endpoints:_ "
                   + ", ".join(f"`{i.endpoint}`" for i in bucket[reason][:6]))
        # show request/response for the first endpoint of this reason
        rep = bucket[reason][0]
        if rep.request_body:
            out.append(f"\n**➡️ Request** (`{rep.endpoint}`)\n```json\n{rep.request_body}\n```")
        if rep.response_body:
            out.append(f"**⬅️ Response** ({rep.status})\n```json\n{rep.response_body}\n```")
        out.append("")
    out.append("---\n")
    return "\n".join(out) + "\n"


def _numbered(items: list[str]) -> str:
    if not items:
        return "_None identified._"
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, start=1))


def render_markdown(
    result: AnalysisResult,
    digest: ErrorDigest,
    *,
    source_file: str,
    content_hash: str,
) -> str:
    badge = _CONFIDENCE_BADGE.get(result.confidence.lower(), result.confidence)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_line = _level_bar(result.level)
    req_res = _affected_section(digest, result)

    md = f"""# 🔍 Root-Cause Analysis Report

**Source file:** `{source_file}`
**Generated:** {generated}
**Analysis engine:** {result.engine or 'n/a'}{f' ({result.model})' if result.model else ''}
**Confidence:** {badge}

---

## ⚡ The Problem (at a glance)
> **{result.summary or 'n/a'}**

{result.simple_explanation or ''}

**Where is the issue?**  {level_line}
{f'_{result.level_reason}_' if result.level_reason else ''}

---
{req_res}
## 🧭 Classification

| Field | Value |
|-------|-------|
| **Level** | {result.level or 'n/a'} |
| **Error type** | {result.error_type or 'n/a'} |
| **Exception class** | `{result.exception_class or 'n/a'}` |
| **Affected component** | {result.affected_component or 'n/a'} |
| **Failure point** | `{result.failure_point or 'n/a'}` |
| **Probable trigger** | {result.probable_trigger or 'n/a'} |
| **Category** | {result.category or 'n/a'} |

---

## 📖 What Happened
{result.what_happened or '_n/a_'}

## ❓ Why It Happened
{result.why_it_happened or '_n/a_'}

## 💥 Impact
{result.impact or '_n/a_'}

## 🎯 Root Cause
{result.root_cause or '_n/a_'}

---

## 🛠️ Recommended Resolution Steps
{_numbered(result.resolution_steps)}

---

## 🔗 Probable Sequence of Events
{_numbered(result.sequence_of_events)}

## 🌊 Cascading / Secondary Failures
{_numbered(result.cascading_failures)}

---

## 📊 Evidence & Diagnostics

- **Total log lines:** {digest.total_lines}
- **Severity counts:** {dict(digest.severities) or 'n/a'}
- **Exception chain (outer → root):** {' → '.join(digest.exception_classes) or 'n/a'}
- **Correlation IDs:** {', '.join(digest.correlation_ids[:5]) or 'n/a'}
- **HTTP events:** {'; '.join(digest.http_events[:5]) or 'n/a'}
- **Confidence rationale:** {result.confidence_reason or 'n/a'}
- **Content hash (sha256):** `{content_hash[:16]}…`

<details>
<summary>Error digest sent to the analysis engine</summary>

```
{digest.excerpt or '(none)'}
```
</details>

---
*Generated by AutoRCA — Automated Log Monitoring & Error Analysis System.*
"""
    return md


def write_report(reports_dir: Path, source_file_name: str, content: str) -> Path:
    """Write the report and return its path. Names are timestamped + collision-safe."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source_file_name).stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"RCA_{stem}_{ts}"
    path = reports_dir / f"{base}.md"
    counter = 1
    while path.exists():
        path = reports_dir / f"{base}_{counter}.md"
        counter += 1
    path.write_text(content, encoding="utf-8")
    return path
