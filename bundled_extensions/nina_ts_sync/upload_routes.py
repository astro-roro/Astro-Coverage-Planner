"""Routes for TS uploads from the NINA plugin, per-rig links, and the review page.

Under ``/api/ext/nina-ts-sync``:
    POST   /import/uploads               take an upload, answer 202
    GET    /import/uploads/pending       every upload still waiting on a choice
    GET    /import/uploads/<id>          status, and the merge preview once ready
    POST   /import/uploads/<id>/preview  re-run the preview for another profile
    POST   /import/uploads/<id>/apply    merge, then delete the upload
    DELETE /import/uploads/<id>          discard the upload
    POST   /links                        push state from the plugin

And the review page at ``GET /ext/nina-ts-sync/import/uploads/<id>``.

ACP core's access gate covers every path. The one check here is that uploads
are refused while ACP_API_TOKEN is unset, because an open server would take
a database file from anyone on the network.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from . import activity, uploads
from .api import blueprint

pages = Blueprint("nina_ts_sync_pages", __name__, url_prefix=uploads.PAGE_PREFIX)

_REVIEW_HTML = Path(__file__).with_name("review.html")


def _token_set() -> bool:
    return bool((os.environ.get("ACP_API_TOKEN") or "").strip())


def _too_large(limit: int):
    return jsonify({
        "error": f"upload larger than {limit} bytes; raise ACP_TS_UPLOAD_MAX_BYTES on the server to allow it",
        "max_bytes": limit,
    }), 413


def _parse_meta(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(meta, dict):
        return None
    pid = meta.get("profile_id")
    sha = meta.get("sha256")
    if not isinstance(pid, str) or not pid.strip():
        return None
    if not isinstance(sha, str) or not uploads.SHA_RE.match(sha.strip().lower()):
        return None
    profiles = meta.get("profiles")
    clean_profiles = []
    if isinstance(profiles, list):
        for p in profiles[:100]:
            if isinstance(p, dict) and isinstance(p.get("id"), str):
                clean_profiles.append({"id": p["id"][:200], "name": str(p.get("name") or "")[:200]})

    def _s(key):
        v = meta.get(key)
        return str(v)[:200] if v is not None else None

    uv = meta.get("user_version")
    return {
        "profile_id": pid.strip()[:200],
        "profiles": clean_profiles,
        "sha256": sha.strip().lower(),
        "user_version": uv if isinstance(uv, int) else None,
        "plugin_version": _s("plugin_version"),
        "machine": _s("machine"),
    }


@blueprint.post("/import/uploads")
def upload_create():
    # 1. Uploads need a token on the server.
    if not _token_set():
        return jsonify({"error": "ACP needs an access token set before it accepts uploads"}), 403

    # 2. This route's own body limit, above the 1 MB every other route has.
    limit = uploads.max_bytes()
    request.max_content_length = limit
    if request.content_length is not None and request.content_length > limit:
        return _too_large(limit)
    try:
        files = request.files
        form = request.form
    except RequestEntityTooLarge:
        return _too_large(limit)

    # 3. Both parts, and meta parses.
    f = files.get("file")
    meta = _parse_meta(form.get("meta"))
    if meta is None and "meta" in files:
        try:
            meta = _parse_meta(files["meta"].read(64 * 1024).decode("utf-8"))
        except UnicodeDecodeError:
            meta = None
    if f is None:
        return jsonify({"error": "the upload needs a 'file' part"}), 400
    if meta is None:
        return jsonify({"error": "the upload needs a 'meta' part: JSON with profile_id and sha256"}), 400

    uploads.sweep()
    upload_id = uuid.uuid4().hex
    # 4 and 5. SQLite header, then the SHA-256 of what arrived.
    try:
        size, reason = uploads.receive(f.stream, upload_id, meta["sha256"])
    except RequestEntityTooLarge:
        return _too_large(limit)
    if size is None:
        if reason == "not_sqlite":
            return jsonify({"error": "not a SQLite database"}), 415
        return jsonify({"error": "upload incomplete or damaged"}), 400

    state = uploads.new_state(upload_id, size=size, meta=meta)
    uploads.save_state(state)
    superseded = uploads.supersede_older(upload_id, meta["profile_id"])
    uploads.enqueue(upload_id, meta["profile_id"])
    activity.upload_received(upload_id, meta, uploads.profile_name_for(state))
    return jsonify({
        "upload_id": upload_id,
        "state": "checking",
        "status_url": uploads.status_url(upload_id),
        "review_url": uploads.review_url(upload_id),
        "superseded": superseded,
    }), 202


def _missing():
    return jsonify({
        "error": "This upload has expired or was already applied. Send it again from NINA.",
        "state": None,
    }), 404


@blueprint.get("/import/uploads/pending")
def upload_pending():
    """Every upload still waiting on a choice, newest first. ACP core polls
    this to show a banner on every page when a rig has sent something."""
    out = []
    for state in uploads.list_pending():
        preview = state.get("preview") if isinstance(state.get("preview"), dict) else None
        c = (preview or {}).get("counts") or {}
        out.append({
            "upload_id": state["upload_id"],
            "state": state.get("state"),
            "machine": (state.get("meta") or {}).get("machine"),
            "profile_name": uploads.profile_name_for(state),
            "summary": (preview or {}).get("summary"),
            "counts": {
                "new": int(c.get("new") or 0),
                "updated": int(c.get("updated") or 0),
                "conflicts": int(c.get("conflicts") or 0),
            },
            "review_url": uploads.review_url(state["upload_id"]),
            "created_iso": state.get("created_iso"),
            "expires_iso": state.get("expires_iso"),
        })
    return jsonify({"uploads": out}), 200


@blueprint.get("/import/uploads/<upload_id>")
def upload_status(upload_id: str):
    state = uploads.load_state(upload_id)
    if state is None:
        return _missing()
    return jsonify(uploads.public_status(state)), 200


@blueprint.post("/import/uploads/<upload_id>/preview")
def upload_repreview(upload_id: str):
    state = uploads.load_state(upload_id)
    if state is None or state.get("state") not in ("ready", "failed", "checking"):
        return _missing()
    if not uploads.db_path(upload_id).exists():
        return _missing()
    body = request.get_json(silent=True) or {}
    profile_id = (body.get("profile_id") or state.get("profile_id") or "").strip()
    known = {p.get("profile_id") for p in state.get("profiles") or []}
    if known and profile_id not in known:
        return jsonify({"error": "that profile is not in this upload"}), 400
    uploads.update_state(upload_id, state="checking", error=None, preview=None,
                         profile_id=profile_id)
    uploads.enqueue(upload_id, profile_id)
    return jsonify({
        "upload_id": upload_id,
        "state": "checking",
        "status_url": uploads.status_url(upload_id),
        "review_url": uploads.review_url(upload_id),
    }), 202


@blueprint.post("/import/uploads/<upload_id>/apply")
def upload_apply(upload_id: str):
    """Held under the plans lock from start to finish, so a second apply of
    the same upload (a double click) waits, then finds it gone: 404."""
    from flask import g
    from .import_merge import PLANS_LOCK
    with PLANS_LOCK:
        resp = _upload_apply(upload_id)
    # Recorded once the lock is let go, so the host's SQLite write never
    # holds up a plans.json save.
    changed = g.pop("activity_applied", None)
    if changed is not None:
        activity.upload_applied(upload_id, changed)
    return resp


def _upload_apply(upload_id: str):
    from .db import SchemaVersionError
    from .import_merge import MergeError, resolve_from

    state = uploads.load_state(upload_id)
    if state is None or state.get("state") in ("superseded", "applied"):
        return _missing()
    path = uploads.db_path(upload_id)
    if not path.exists():
        return _missing()
    if state.get("state") != "ready" or not isinstance(state.get("preview"), dict):
        return jsonify({"error": "ACP is still reading this upload", "state": state.get("state")}), 409

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "the body must be JSON"}), 400
    preview = state["preview"]
    if (body.get("sha256") or "").strip().lower() != state.get("sha256"):
        return jsonify({"error": "This upload changed since the page loaded. Reload the page.",
                        "reason": "sha_mismatch"}), 409
    profile_id = (body.get("profile_id") or "").strip()
    if profile_id != preview.get("profile_id"):
        return jsonify({"error": "The preview is for another profile. Reload the page.",
                        "reason": "profile_mismatch"}), 409
    if body.get("preview_hash") != preview.get("preview_hash"):
        return jsonify({"error": "This page shows an older preview. Reload the page.",
                        "reason": "stale_preview"}), 409
    add = body.get("add")
    if add is not None and not isinstance(add, list):
        return jsonify({"error": "add must be a list of keys"}), 400
    decisions = body.get("decisions") or {}
    if not isinstance(decisions, dict):
        return jsonify({"error": "decisions must be an object"}), 400

    try:
        result = resolve_from(
            path, profile_id, (state.get("meta") or {}).get("machine"),
            decisions, add, body.get("new_plan_gear"),
            strict=True, immutable=True, require_decisions=True,
            expected_hash=preview.get("preview_hash"),
        )
    except MergeError as e:
        return jsonify(e.to_dict()), e.status
    except SchemaVersionError as e:
        return jsonify({"error": str(e)}), 409
    except sqlite3.Error:
        # The file went while this ran, swept or discarded. Nothing was written.
        return _missing()
    uploads.delete_upload(upload_id)
    applied = result.get("applied") or {}
    from flask import g
    g.activity_applied = sum(len(applied.get(k) or []) for k in ("take_ts", "auto_pull", "added"))
    return jsonify({"state": "applied", **result}), 200


@blueprint.delete("/import/uploads/<upload_id>")
def upload_discard(upload_id: str):
    if uploads.load_state(upload_id) is None:
        return _missing()
    uploads.delete_upload(upload_id)
    activity.upload_discarded(upload_id)
    return jsonify({"upload_id": upload_id, "state": "discarded"}), 200


@blueprint.post("/links")
def links_post():
    """Push state from the plugin: ``{profile_id, machine, states: [{plan_id,
    refs, base_snapshot}], templates, capabilities}``. Writes
    ``ts_links[profile_id]`` on each named plan that exists, and each row's
    ``template_sub_s`` from the base unless ACP changed it since the last
    push; unknown plan ids are ignored.

    ``templates`` are the gear template results of the push, handed to ACP's
    ``record_template_results``. ``capabilities`` says what the plugin can
    do with template links, and ACP keeps ``links_capable[profile_id]`` from
    it (gear-templates.md section 4): a post that lacks it clears the entry.
    """
    from .import_merge import PLANS_LOCK, load_acp_state
    from .links import link_for, set_link_only
    from .rows import refresh_template_subs

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "the body must be JSON"}), 400
    profile_id = (body.get("profile_id") or "").strip() if isinstance(body.get("profile_id"), str) else ""
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400
    machine = body.get("machine") if isinstance(body.get("machine"), str) else None
    states = body.get("states")
    if not isinstance(states, list):
        return jsonify({"error": "states must be a list"}), 400

    updated, unknown = [], []
    with PLANS_LOCK:
        plans, _ = load_acp_state()
        plans = [dict(p) for p in plans if isinstance(p, dict)]
        by_id = {p.get("id"): p for p in plans}
        for st in states:
            if not isinstance(st, dict) or not isinstance(st.get("refs"), dict):
                continue
            plan = by_id.get(st.get("plan_id"))
            if plan is None:
                unknown.append(st.get("plan_id"))
                continue
            base = st.get("base_snapshot") if isinstance(st.get("base_snapshot"), dict) else None
            old_base = (link_for(plan, profile_id) or {}).get("base_snapshot")
            set_link_only(plan, profile_id, machine=machine, refs=st["refs"], base_snapshot=base)
            # The plugin's base records the sub TS uses for each row's
            # exposure plan; the row takes it as its `template_sub_s`,
            # unless ACP changed the row's sub since the last push.
            refresh_template_subs(plan, base, old_base if isinstance(old_base, dict) else None)
            updated.append(plan.get("id"))
        if updated:
            from app import save_plans  # type: ignore[import-not-found]
            from .api import _plans_document
            # No backup here: this only records link state after a push, on
            # every sync, and a backup each time would bury the ones that
            # matter.
            save_plans(_plans_document(plans))

    caps = body.get("capabilities")
    _set_links_capable(profile_id, [c for c in caps if isinstance(c, str)] if isinstance(caps, list) else [])
    templates = body.get("templates")
    if isinstance(templates, list) and templates:
        from .api import record_template_results
        record_template_results(profile_id, [t for t in templates if isinstance(t, dict)])
    activity.rig_synced(profile_id, machine, len(updated))
    return jsonify({"updated": updated, "unknown": unknown}), 200


def _set_links_capable(profile_id: str, capabilities: list[str]) -> None:
    """Tell ACP which template-link abilities the plugin reported for the
    profile, via its ``set_links_capable(profile_id, capabilities)``. An empty
    list clears the entry. A host without it has no gear templates to guard."""
    try:
        from app import set_links_capable  # type: ignore[import-not-found]
    except ImportError:
        return
    set_links_capable(profile_id, capabilities)


@pages.get("/import/uploads/<upload_id>")
def review_page(upload_id: str):
    html = _REVIEW_HTML.read_text(encoding="utf-8")
    safe_id = upload_id if uploads.ID_RE.match(upload_id or "") else ""
    html = html.replace("__UPLOAD_ID__", safe_id).replace("__API_PREFIX__", uploads.API_PREFIX)
    resp = Response(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp
