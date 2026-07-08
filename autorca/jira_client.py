"""Jira integration — turn an AutoRCA report into a Jira issue.

Targets Jira Server / Data Center (REST API v2, HTTP basic auth with
username + password, wiki-markup description, assignee by username). Given a
report row (as the portal builds it), this:
  * picks the Jira project from the AutoRCA project mapping,
  * decides Bug vs Improvement (an actual error is a Bug; a clean/no-error log
    is an Improvement),
  * routes the assignee by level (Application -> app owner, everything else ->
    the service owner), resolving the person by name/username,
  * writes a clean, QA-style description, and
  * attaches the original log file.

Credentials come from the environment (JIRA_BASE_URL / JIRA_USERNAME /
JIRA_PASSWORD); project map + assignees come from config.yaml.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import requests

from .config import Config

log = logging.getLogger("autorca.jira")


def _norm(s: str) -> str:
    """Normalise a name for matching: lower-case, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_component(code: str, project_components: list) -> Optional[str]:
    """Map a log component code (e.g. 'spg', 'apigw') to the Jira component whose
    name matches it, ignoring case / separators ('spg'->'SPG', 'apigw'->'API-GW')."""
    if not code:
        return None
    target = _norm(code)
    if not target:
        return None
    # exact normalised match first, then a startswith fallback
    for c in project_components:
        if _norm(c) == target:
            return c
    for c in project_components:
        n = _norm(c)
        if n and (n.startswith(target) or target.startswith(n)):
            return c
    return None

# Levels routed to the "application" assignee; everything else is a service issue.
_APPLICATION_LEVELS = {"application"}


class JiraError(RuntimeError):
    """Raised when Jira rejects a request (surfaced to the portal user)."""


# --------------------------------------------------------- issue spec from a row
def project_key_for(row: dict, config: Config) -> Optional[str]:
    return config.jira_project_map.get(row.get("project") or "")


def decide_issue_type(row: dict, config: Config) -> str:
    cat = (row.get("category") or "").lower()
    etype = (row.get("error_type") or "").lower()
    if cat == "no error" or etype == "no error" or (cat in ("", "n/a") and etype in ("", "no error")):
        return config.jira_improvement_type
    return config.jira_bug_type


def decide_assignee(row: dict, config: Config) -> str:
    level = (row.get("level") or "").lower()
    return (config.jira_assignee_application if level in _APPLICATION_LEVELS
            else config.jira_assignee_service)


def build_summary(row: dict) -> str:
    a = row.get("analysis") or {}
    level = row.get("level") or a.get("level") or "Issue"
    etype = a.get("error_type") or row.get("error_type") or "Error"
    headline = row.get("summary") or a.get("summary") or "AutoRCA detected an issue"
    return f"[{level}] {etype}: {headline}"[:250]


def build_description(row: dict, config: Config) -> str:
    """A tidy, QA-style issue description in Jira wiki markup."""
    a = row.get("analysis") or {}
    sections = []

    def kv(label, value):
        return f"* *{label}:* {value if value not in (None, '') else 'n/a'}"

    headline = a.get("summary") or row.get("summary")
    if headline:
        sections.append("{panel:title=Summary|borderStyle=solid}\n" + headline + "\n{panel}")
    if a.get("simple_explanation"):
        sections.append(a["simple_explanation"])

    sections.append("h3. Environment\n" + "\n".join([
        kv("Project", row.get("project")),
        kv("Where (level)", row.get("level") or a.get("level")),
        kv("Category", row.get("category")),
        kv("Confidence", row.get("confidence")),
        kv("Detected at", row.get("processed_short")),
        kv("Source log", row.get("file_name")),
        kv("Analysis engine", a.get("engine") or row.get("engine")),
    ]))

    if a.get("what_happened"):
        sections.append("h3. What happened\n" + a["what_happened"])

    steps = []
    if a.get("probable_trigger"):
        steps.append("Trigger: " + a["probable_trigger"])
    steps += [str(s) for s in (a.get("sequence_of_events") or [])]
    if a.get("failure_point"):
        steps.append("Fails at: " + a["failure_point"])
    if steps:
        sections.append("h3. Steps / sequence\n" + "\n".join("* " + s for s in steps))

    if a.get("root_cause") or a.get("exception_class"):
        rc = ["h3. Root cause"]
        if a.get("root_cause"):
            rc.append(a["root_cause"])
        extra = []
        if a.get("exception_class"):
            extra.append(kv("Exception", a["exception_class"]))
        if a.get("affected_component"):
            extra.append(kv("Component", a["affected_component"]))
        if extra:
            rc.append("\n".join(extra))
        sections.append("\n".join(rc))

    if a.get("impact"):
        sections.append("h3. Impact\n" + a["impact"])

    incidents = row.get("incidents") or []
    if incidents:
        lines = ["h3. Affected endpoints"]
        for inc in incidents[:15]:
            ep = inc.get("endpoint") or f"{inc.get('method', '')} {inc.get('path', '')}".strip()
            cnt = inc.get("count", 1)
            suffix = f" (x{cnt})" if cnt and cnt > 1 else ""
            lines.append(f"* {ep} - HTTP {inc.get('status', '')} - {inc.get('reason', '')}{suffix}")
        sections.append("\n".join(lines))

    fixes = a.get("resolution_steps") or []
    if fixes:
        sections.append("h3. Recommended resolution\n" + "\n".join("* " + str(f) for f in fixes))

    sections.append(
        "h3. Reference\n"
        + kv("Content hash", (row.get("content_hash") or "")[:16])
        + "\n_Auto-generated by AutoRCA · original log attached._"
    )
    return "\n\n".join(sections)


def locate_log_file(config: Config, row: dict) -> Optional[Path]:
    """Find the original log on disk (it has usually been moved to processed/)."""
    name = row.get("file_name") or ""
    project = row.get("project")
    bases = []
    if project:
        bases.append(config.watch_dir / project / config.processed_subdir)
    bases.append(config.watch_dir / config.processed_subdir)

    for base in bases:
        cand = base / name
        if cand.exists():
            return cand
    sp = row.get("source_path")
    if sp and Path(sp).exists():
        return Path(sp)
    stem = Path(name).stem
    for base in bases:
        if base.exists():
            for f in sorted(base.glob(f"{stem}*")):
                return f
    return None


# ------------------------------------------------------------------- API client
class JiraClient:
    """Minimal Jira Server/DC REST v2 client (basic auth)."""

    def __init__(self, config: Config):
        self.config = config
        self.base = config.jira_base_url.rstrip("/")
        self.auth = (config.jira_user, config.jira_password)
        self.verify = config.jira_verify_ssl
        self._cache: dict = {}
        if not self.verify:
            try:
                requests.packages.urllib3.disable_warnings()  # type: ignore
            except Exception:
                pass

    def _get(self, path, timeout=25, **kw):
        return requests.get(self.base + path, auth=self.auth, verify=self.verify, timeout=timeout, **kw)

    def myself(self) -> dict:
        r = self._get("/rest/api/2/myself")
        r.raise_for_status()
        return r.json()

    def get_components(self, project_key: str) -> list:
        """List component names for a project (for the create dropdown)."""
        try:
            r = self._get(f"/rest/api/2/project/{project_key}/components", timeout=40)
            r.raise_for_status()
            return [c["name"] for c in r.json() if c.get("name")]
        except Exception as exc:
            log.warning("Jira: could not list components for %s: %s", project_key, exc)
            return []

    def resolve_assignee(self, who: str) -> Optional[str]:
        """Search by name/username/email; return the Jira username to assign."""
        who = (who or "").strip()
        if not who:
            return None
        if who in self._cache:
            return self._cache[who]
        name = None
        try:
            r = self._get("/rest/api/2/user/search", params={"username": who})
            r.raise_for_status()
            users = r.json()
            if users:
                name = users[0].get("name") or users[0].get("key")
            else:
                log.warning("Jira: no user matched '%s'", who)
        except Exception as exc:
            log.warning("Jira user search failed for '%s': %s", who, exc)
        self._cache[who] = name
        return name

    def create_issue(self, *, project_key, issue_type, summary, description,
                     assignee_name=None, components=None) -> tuple:
        fields = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": summary[:250],
            "description": description,
        }
        if assignee_name:
            fields["assignee"] = {"name": assignee_name}
        if components:
            fields["components"] = [{"name": c} for c in components]
        r = requests.post(self.base + "/rest/api/2/issue", json={"fields": fields},
                          auth=self.auth, verify=self.verify, timeout=30)
        if r.status_code >= 300:
            raise JiraError(f"HTTP {r.status_code}: {r.text[:400]}")
        key = r.json()["key"]
        return key, f"{self.base}/browse/{key}"

    def attach_file(self, issue_key: str, file_path: Optional[Path]) -> bool:
        if not file_path or not Path(file_path).exists():
            log.info("Jira: no log file found to attach for %s", issue_key)
            return False
        try:
            with open(file_path, "rb") as fh:
                r = requests.post(
                    f"{self.base}/rest/api/2/issue/{issue_key}/attachments",
                    auth=self.auth, verify=self.verify,
                    headers={"X-Atlassian-Token": "no-check"},
                    files={"file": (Path(file_path).name, fh, "text/plain")},
                    timeout=60,
                )
            if r.status_code >= 300:
                log.warning("Jira attach failed for %s: HTTP %s %s",
                            issue_key, r.status_code, r.text[:200])
                return False
            return True
        except Exception as exc:
            log.warning("Jira attach error for %s: %s", issue_key, exc)
            return False


def raise_issue_for_report(config: Config, row: dict, dry_run: bool = False,
                           components: Optional[list] = None) -> dict:
    """Build + (optionally) create the Jira issue for a report row."""
    project_key = project_key_for(row, config)
    if not project_key:
        raise JiraError(f"No Jira project mapped for '{row.get('project') or 'unassigned'}'.")

    issue_type = decide_issue_type(row, config)
    assignee_who = decide_assignee(row, config)
    summary = build_summary(row)
    description = build_description(row, config)
    log_path = locate_log_file(config, row)
    components = [c for c in (components or []) if str(c).strip()]

    # Auto-select the Jira component from the failing component (no user input).
    auto_component = None
    if not components and row.get("component") and config.jira_configured:
        proj_comps = JiraClient(config).get_components(project_key)
        auto_component = match_component(row["component"], proj_comps)
        if auto_component:
            components = [auto_component]

    spec = {
        "project_key": project_key,
        "issue_type": issue_type,
        "summary": summary,
        "assignee": assignee_who or "(unassigned)",
        "components": components,
        "auto_component": auto_component,
        "failing_component": row.get("component"),
        "log_attached": bool(log_path),
        "log_file": str(log_path) if log_path else None,
    }

    if dry_run:
        spec["dry_run"] = True
        spec["configured"] = config.jira_configured
        return spec

    if not config.jira_configured:
        raise JiraError("Jira is not configured — add JIRA_BASE_URL, JIRA_USERNAME "
                        "and JIRA_PASSWORD to .env, then restart the portal.")

    client = JiraClient(config)
    assignee_name = client.resolve_assignee(assignee_who) if assignee_who else None
    key, url = client.create_issue(
        project_key=project_key, issue_type=issue_type, summary=summary,
        description=description, assignee_name=assignee_name, components=components,
    )
    attached = client.attach_file(key, log_path)
    spec.update({"key": key, "url": url, "log_attached": attached, "dry_run": False,
                 "assignee_resolved": assignee_name or None})
    return spec
