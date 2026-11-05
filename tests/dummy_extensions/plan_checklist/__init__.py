"""A dummy extension that adds a section to the plan editor.

Used by tests/test_ext_editor_sections.py. The section is a small page that
shows a checklist stored on the plan as ``checklist`` and ticks items through
plans_api, then asks ACP to reload the plan with ``acp:plan:reload``.
"""
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("plan_checklist", __name__)

PAGE = """<!doctype html><meta charset="utf-8"><title>Checklist</title>
<ul id="items"></ul>
<script>
const planId = new URLSearchParams(location.search).get("plan_id");
async function tick(item) {
  await fetch("/api/ext/plan-checklist/tick", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({plan_id: planId, item})});
  window.parent.postMessage({type: "acp:plan:reload", plan_id: planId}, location.origin);
}
</script>"""


@bp.route("/api/ext/plan-checklist/section")
def section():
    return PAGE


@bp.route("/api/ext/plan-checklist/tick", methods=["POST"])
def tick():
    body = request.get_json(silent=True) or {}
    pid, item = body.get("plan_id"), body.get("item")

    def change(plans):
        for p in plans:
            if p.get("id") == pid:
                p.setdefault("checklist", {})[str(item)] = True
                return True
        return False

    found = current_app.plans_api.transact(change)
    return (jsonify({"ok": True}), 200) if found else (jsonify({"error": "not found"}), 404)


def register(app):
    if getattr(app, "acp_extension_api", None) != 1 or "editor_sections" not in app.acp_extension_hooks:
        return
    app.register_blueprint(bp)
    app.extensions_manifest.append({
        "extension": "plan_checklist",
        "name": "Plan checklist",
        "editor_sections": [{"id": "checklist", "label": "Checklist",
                             "page": "/api/ext/plan-checklist/section", "height": 120}],
    })
