"""AutoRCA web portal — a Flask dashboard for browsing RCA results.

Reads the same SQLite history database the monitoring service writes to, so the
portal always reflects the latest analyses (it can run alongside the monitor).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, abort, jsonify, render_template, request, send_file

from ..config import Config, load_config
from ..database import Database
from ..engines import ENGINE_OPTIONS, active_engine, option_by_id
from ..jira_client import (
    JiraClient, JiraError, raise_issue_for_report, match_component,
    decide_assignee as jira_decide_assignee,
    decide_issue_type as jira_decide_type,
)

# Human-friendly metadata for category chips (label + accent colour class).
CATEGORY_META = {
    "application": ("Application Defect", "cat-app"),
    "infrastructure": ("Infrastructure", "cat-infra"),
    "integration": ("Integration", "cat-integ"),
    "configuration": ("Configuration", "cat-config"),
    "no error": ("No Error", "cat-ok"),
    "n/a": ("Uncategorised", "cat-na"),
}

# The levels shown in the "where is the issue" visual: icon + colour class.
LEVELS = [
    ("Application",      "📱", "lv-app"),
    ("Server",           "🖥️", "lv-server"),
    ("Database",         "🗄️", "lv-db"),
    ("Network",          "🌐", "lv-net"),
    ("External Service", "🔌", "lv-ext"),
    ("Configuration",    "⚙️", "lv-cfg"),
]


def _level_icon(level):
    for name, icon, _cls in LEVELS:
        if name.lower() == (level or "").lower():
            return icon
    return "❔"


def _category_meta(category: Optional[str]) -> tuple:
    key = (category or "n/a").lower()
    return CATEGORY_META.get(key, (category or "Uncategorised", "cat-na"))


def _confidence_class(confidence: Optional[str]) -> str:
    return {"high": "conf-high", "medium": "conf-med", "low": "conf-low"}.get(
        (confidence or "").lower(), "conf-na"
    )


def _project_folders(config: Config) -> list:
    """Project names = the immediate sub-folders of the watch dir (minus 'processed')."""
    base = config.watch_dir
    if not base.exists():
        return []
    return [p.name for p in sorted(base.iterdir())
            if p.is_dir() and p.name != config.processed_subdir]


def _all_projects(config: Config, database: Database) -> list:
    """Folder-based projects first (so empty ones still show), then any extra
    projects that exist only in the history DB."""
    projects = _project_folders(config)
    for pr in database.list_projects():
        if pr and pr not in projects:
            projects.append(pr)
    return projects


def _row_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """Augment a DB row with presentation helpers and parsed analysis JSON."""
    view = dict(row)
    cat_label, cat_class = _category_meta(row.get("category"))
    view["category_label"] = cat_label
    view["category_class"] = cat_class
    view["confidence_class"] = _confidence_class(row.get("confidence"))
    analysis = {}
    if row.get("analysis_json"):
        try:
            analysis = json.loads(row["analysis_json"])
        except (json.JSONDecodeError, TypeError):
            analysis = {}
    view["analysis"] = analysis

    incidents = []
    if row.get("incidents_json"):
        try:
            incidents = json.loads(row["incidents_json"]) or []
        except (json.JSONDecodeError, TypeError):
            incidents = []
    view["incidents"] = incidents
    view["groups"] = _group_by_reason(incidents, analysis.get("reason_groups"))

    # short, display-friendly timestamp
    ts = row.get("processed_at") or ""
    view["processed_short"] = ts.replace("T", " ").split(".")[0]
    return view


def _group_by_reason(incidents, reason_groups):
    """Bucket affected endpoints by their error reason and attach the matching
    AI/heuristic explanation for each reason."""
    expl = {}
    for g in (reason_groups or []):
        key = (g.get("reason") or "").strip()
        if key:
            expl[key] = g

    order, bucket = [], {}
    for inc in incidents:
        r = inc.get("reason", "") or "Error"
        if r not in bucket:
            bucket[r] = []
            order.append(r)
        bucket[r].append(inc)

    groups = []
    for r in order:
        g = expl.get(r)
        if not g:  # fuzzy match if exact reason string differs slightly
            for er, ev in expl.items():
                if er and (er in r or r in er):
                    g = ev
                    break
        incs = bucket[r]
        groups.append({
            "reason": r,
            "explanation": g or {},
            "incidents": incs,
            "count": sum(i.get("count", 1) for i in incs),
            "endpoints": len(incs),
        })
    return groups


def create_app(config: Optional[Config] = None) -> Flask:
    config = config or load_config()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["AUTORCA"] = config

    def db() -> Database:
        # One connection per request keeps things simple and process-safe.
        return Database(config.db_path)

    @app.context_processor
    def _inject_helpers():
        database = db()
        try:
            current = active_engine(config, database)
        finally:
            database.close()
        return {
            "LEVELS": LEVELS, "level_icon": _level_icon, "cat_meta": _category_meta,
            "engine_options": ENGINE_OPTIONS, "active_engine": current,
        }

    @app.route("/")
    def dashboard():
        project = request.args.get("project", "").strip() or None
        database = db()
        try:
            stats = database.dashboard_stats(project)
            recent = [_row_view(r) for r in database.list_reports(project=project)[:8]]
            projects = _all_projects(config, database)
        finally:
            database.close()
        return render_template(
            "dashboard.html", stats=stats, recent=recent,
            cat_meta=_category_meta, active="dashboard",
            projects=projects, active_project=project,
        )

    @app.route("/reports")
    def reports():
        search = request.args.get("q", "").strip()
        project = request.args.get("project", "").strip() or None
        database = db()
        try:
            rows = [_row_view(r) for r in database.list_reports(search, project)]
            projects = _all_projects(config, database)
        finally:
            database.close()
        return render_template("reports.html", rows=rows, search=search, active="reports",
                               projects=projects, active_project=project)

    @app.route("/report/<int:report_id>")
    def report_detail(report_id: int):
        database = db()
        try:
            row = database.get_report(report_id)
        finally:
            database.close()
        if not row:
            abort(404)
        view = _row_view(row)
        jira_project = config.jira_project_map.get(view.get("project") or "")
        return render_template(
            "report.html", r=view, active="reports",
            jira_enabled=config.jira_configured, jira_project=jira_project,
        )

    @app.route("/api/report/<int:report_id>/jira/meta")
    def jira_meta(report_id: int):
        """What the Create-Issue form needs: mapped project, auto type/assignee,
        and the project's components (for the dropdown)."""
        database = db()
        try:
            raw = database.get_report(report_id)
        finally:
            database.close()
        if not raw:
            return jsonify({"ok": False, "error": "report not found"}), 404
        row = _row_view(raw)
        project_key = config.jira_project_map.get(row.get("project") or "")
        info = {
            "ok": True,
            "configured": config.jira_configured,
            "project_key": project_key,
            "issue_type": jira_decide_type(row, config),
            "assignee": jira_decide_assignee(row, config),
            "failing_component": row.get("component"),
            "auto_component": None,
            "components": [],
        }
        if project_key and config.jira_configured:
            comps = JiraClient(config).get_components(project_key)
            info["components"] = comps
            info["auto_component"] = match_component(row.get("component"), comps)
        return jsonify(info)

    @app.route("/api/report/<int:report_id>/jira", methods=["POST"])
    def create_jira(report_id: int):
        """Create a Jira issue for a report (or dry-run to preview the payload)."""
        dry = request.args.get("dry") in ("1", "true", "yes")
        body = request.get_json(silent=True) or {}
        components = body.get("components") or ([body["component"]] if body.get("component") else [])
        database = db()
        try:
            raw = database.get_report(report_id)
            if not raw:
                return jsonify({"ok": False, "error": "report not found"}), 404
            # Don't create a second issue for the same report.
            if raw.get("jira_key") and not dry:
                return jsonify({"ok": True, "already": True,
                                "key": raw["jira_key"], "url": raw.get("jira_url")})
            row = _row_view(raw)
            try:
                result = raise_issue_for_report(config, row, dry_run=dry, components=components)
            except JiraError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            if not dry and result.get("key"):
                database.set_jira(report_id, result["key"], result.get("url", ""))
            return jsonify({"ok": True, **result})
        finally:
            database.close()

    @app.route("/download/<int:report_id>")
    def download(report_id: int):
        database = db()
        try:
            row = database.get_report(report_id)
        finally:
            database.close()
        if not row or not row.get("report_path") or not Path(row["report_path"]).exists():
            abort(404)
        return send_file(row["report_path"], as_attachment=True)

    @app.route("/api/provider", methods=["POST"])
    def api_set_provider():
        """Switch the active AI engine (used by the portal's model dropdown).

        Applies to the NEXT file the monitor analyses (the two run as separate
        processes and share this setting via the database).
        """
        engine_id = (request.json or {}).get("engine") if request.is_json else request.form.get("engine")
        option = option_by_id(engine_id or "")
        if not option:
            return jsonify({"ok": False, "error": f"unknown engine '{engine_id}'"}), 400
        database = db()
        try:
            database.set_setting("active_provider", option["provider"])
            database.set_setting("active_model", option["model"])
        finally:
            database.close()
        return jsonify({"ok": True, "engine": option})

    @app.route("/api/stats")
    def api_stats():
        database = db()
        try:
            return jsonify(database.dashboard_stats())
        finally:
            database.close()

    @app.route("/api/reports")
    def api_reports():
        project = request.args.get("project", "").strip() or None
        database = db()
        try:
            rows = database.list_reports(request.args.get("q", "").strip(), project)
        finally:
            database.close()
        # Strip the heavy analysis_json for the list endpoint.
        for r in rows:
            r.pop("analysis_json", None)
        return jsonify(rows)

    return app
