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

# Human-friendly metadata for category chips (label + accent colour class).
CATEGORY_META = {
    "application": ("Application Defect", "cat-app"),
    "infrastructure": ("Infrastructure", "cat-infra"),
    "integration": ("Integration", "cat-integ"),
    "configuration": ("Configuration", "cat-config"),
    "no error": ("No Error", "cat-ok"),
    "n/a": ("Uncategorised", "cat-na"),
}

# The 4 levels shown in the "where is the issue" visual: icon + colour class.
LEVELS = [
    ("Application", "📱", "lv-app"),
    ("Server",      "🖥️", "lv-server"),
    ("Database",    "🗄️", "lv-db"),
    ("Network",     "🌐", "lv-net"),
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

    @app.context_processor
    def _inject_helpers():
        return {"LEVELS": LEVELS, "level_icon": _level_icon, "cat_meta": _category_meta}

    def db() -> Database:
        # One connection per request keeps things simple and process-safe.
        return Database(config.db_path)

    @app.route("/")
    def dashboard():
        database = db()
        try:
            stats = database.dashboard_stats()
            recent = [_row_view(r) for r in database.list_reports()[:8]]
        finally:
            database.close()
        return render_template(
            "dashboard.html", stats=stats, recent=recent,
            cat_meta=_category_meta, active="dashboard",
        )

    @app.route("/reports")
    def reports():
        search = request.args.get("q", "").strip()
        database = db()
        try:
            rows = [_row_view(r) for r in database.list_reports(search)]
        finally:
            database.close()
        return render_template("reports.html", rows=rows, search=search, active="reports")

    @app.route("/report/<int:report_id>")
    def report_detail(report_id: int):
        database = db()
        try:
            row = database.get_report(report_id)
        finally:
            database.close()
        if not row:
            abort(404)
        return render_template("report.html", r=_row_view(row), active="reports")

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

    @app.route("/api/stats")
    def api_stats():
        database = db()
        try:
            return jsonify(database.dashboard_stats())
        finally:
            database.close()

    @app.route("/api/reports")
    def api_reports():
        database = db()
        try:
            rows = database.list_reports(request.args.get("q", "").strip())
        finally:
            database.close()
        # Strip the heavy analysis_json for the list endpoint.
        for r in rows:
            r.pop("analysis_json", None)
        return jsonify(rows)

    return app
