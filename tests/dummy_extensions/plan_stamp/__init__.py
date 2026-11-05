"""A dummy extension that changes plans through app.plans_api.

Used by tests/test_ext_plans_api.py. POST /api/ext/plan-stamp/stamp writes a
note on every plan in one project, and spreads a project-wide value with
apply_project_settings, all inside one transaction.
"""
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("plan_stamp", __name__)
seen = {}


@bp.route("/api/ext/plan-stamp/stamp", methods=["POST"])
def stamp():
    api = current_app.plans_api
    body = request.get_json(silent=True) or {}
    project = body.get("project")

    def change(plans):
        seen["lock_held"] = current_app.plans_lock._is_owned()
        hit = [p for p in plans if api.project_key(p) == project]
        for p in hit:
            p["stamp_note"] = body.get("note")
        changes = api.apply_project_settings(plans, project, {"priority": "high"})
        return {"stamped": len(hit), "changes": sorted(changes)}

    return jsonify(api.transact(change))


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "plans_api" not in app.acp_extension_hooks:
        return
    app.register_blueprint(bp)
