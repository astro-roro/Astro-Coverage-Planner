"""Flask blueprint for the direct-write sync.

Routes (mounted under ``/api/ext/nina-ts-sync``):
    GET  /status          — discovered DB path + schema version + supported versions
    POST /preview         — count what a push *would* do, without writing
    POST /sync            — push ACP plans into TS DB; returns SyncReport
    POST /import/preview  — count what a TS-side import would bring in
    POST /import/apply    — one-shot bootstrap into ACP's data/plans.json
    POST /import/diff     — three-way diff (BASE / ACP / TS) per plan
    POST /import/resolve  — apply per-plan decisions for the diff conflicts
    POST /sync-acquired   — light: refresh `actual_hours` from TS into ACP

The upload routes (``/import/uploads...``) and ``POST /links`` are added to
this blueprint by upload_routes.py. /import/diff and /import/resolve run on
the merge engine in import_merge.py, shared with uploads.

Body for all POSTs (JSON):
    {
        "profile_id": "<NINA profile id>",
        "db_path":    "<optional override; falls back to env+OS default>"
    }
The /import/apply route additionally accepts ``"overwrite": true`` to
replace the contents of data/plans.json wholesale; default refuses if any
plans exist. /import/resolve takes ``"decisions"`` (per-plan-id verdicts:
take_acp / take_ts / skip) and an optional ``"import_ts_only_new"`` list
of TS project ids to bootstrap-import alongside the resolution.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import __version__
from .db import (
    SUPPORTED_USER_VERSIONS,
    SchemaVersionError,
    assert_supported_version,
    open_connection,
    write_transaction,
)
from .paths import resolve_db_path
from .schema import PayloadError

blueprint = Blueprint("nina_ts_sync", __name__, url_prefix="/api/ext/nina-ts-sync")


def _holds_plans_lock(fn):
    """Run a route that reads, changes and writes plans.json under the
    extension's plans lock, so two of them cannot interleave and one write
    back an older copy over the other's."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # Looked up per call: register() may swap in the host's lock.
        from . import import_merge
        with import_merge.PLANS_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def wheel_slots_for(body: dict, profile_id: str) -> list[str]:
    """The profile's wheel slot names, in slot order (gear-templates.md
    section 8). The request's `wheel_slots` when it has them, else the ones
    ACP's fingerprint store holds for the profile, else an empty list, which
    means the wheel is unknown."""
    given = body.get("wheel_slots")
    if isinstance(given, list):
        return [str(s) for s in given if isinstance(s, str)]
    try:
        from app import load_fingerprints  # type: ignore[import-not-found]
        stored = load_fingerprints()
    except (ImportError, AttributeError):
        return []
    # ACP's store is {"version": 1, "profiles": {key: entry}}, the key being
    # the profile id, else its name (app.py `_store_fingerprint`).
    profiles = stored.get("profiles") if isinstance(stored, dict) else None
    entries = profiles if isinstance(profiles, dict) else {}
    # An entry holds the rig under "fingerprint", and the wheel and the
    # profile id live there (ACP gear_templates.py `_profile_entries`).
    def _fp(e: dict) -> dict:
        return e["fingerprint"] if isinstance(e.get("fingerprint"), dict) else {}

    entry = entries.get(profile_id)
    if not isinstance(entry, dict):
        entry = next(
            (e for e in entries.values()
             if isinstance(e, dict)
             and (e.get("profile_id") or _fp(e).get("profile_id")) == profile_id), None)
    slots = _fp(entry).get("wheel_slots") if isinstance(entry, dict) else None
    return [s for s in slots if isinstance(s, str)] if isinstance(slots, list) else []


def _load_acp_state() -> tuple[list[dict], dict]:
    """Read plans + gear from ACP's data files.

    Imports lazily so the extension is loadable even when running tests
    outside the ACP host (where the `app` module isn't available).
    """
    from app import load_plans, load_gear  # type: ignore[import-not-found]
    plans_data = load_plans()
    plans_list = plans_data.get("plans", []) if isinstance(plans_data, dict) else []
    gear = load_gear()
    return plans_list, gear


@blueprint.get("/status")
def status():
    payload: dict = {
        "extension_version": __version__,
        "supported_user_versions": list(SUPPORTED_USER_VERSIONS),
    }
    try:
        path = resolve_db_path()
    except FileNotFoundError as e:
        payload.update({"db_path": None, "ready": False, "error": str(e)})
        return jsonify(payload), 200

    payload["db_path"] = str(path)
    try:
        conn = open_connection(path, read_only=True)
        try:
            assert_supported_version(conn)
        finally:
            conn.close()
    except SchemaVersionError as e:
        payload.update({"ready": False, "error": str(e), "found_user_version": e.found})
        return jsonify(payload), 200
    payload["ready"] = True
    return jsonify(payload), 200


@blueprint.post("/preview")
def preview_route():
    body = request.get_json(silent=True) or {}
    try:
        path = resolve_db_path(body.get("db_path"))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400

    from .convert import build_payload
    from .upsert import preview as upsert_preview

    plans_list, gear = _load_acp_state()
    try:
        payload = build_payload(
            plans_list, gear, profile_id=profile_id,
            wheel_slots=wheel_slots_for(body, profile_id), profile_name=_profile_name(body))
    except PayloadError as e:
        return jsonify({"error": str(e)}), 400

    conn = open_connection(path, read_only=True)
    try:
        assert_supported_version(conn)
        report = upsert_preview(conn, payload)
        plan_diffs = _compute_plan_diffs(conn, plans_list, payload, profile_id)
    finally:
        conn.close()
    return jsonify({
        "db_path": str(path),
        "report": report.to_dict(),
        "plan_diffs": plan_diffs,
    }), 200


# ---------------------------------------------------------------------------
# Per-plan diff for the preview modal.
#
# Returns a list with one entry per ACP plan, structured for the frontend's
# plan-grouped renderer. Each entry includes:
#
#   - kind: "insert" (no TS counterpart yet) | "update" (matched + at least
#           one field changes) | "unchanged"
#   - project_changes: list of {label, unit, from, to} for project-level
#           fields (min_alt, priority, meridian) that come from the payload's
#           strictest-wins reconciliation, so plans sharing a project_name
#           will report identical project_changes
#   - target_changes: same for the anchor target (R1C1 for mosaics, the
#           lone target otherwise) — RA/Dec/rotation
#   - filter_changes: a row label such as "R · R Stars" → list of {path, label, unit, from, to}
#           for that row's anchor ExposurePlan (desired, in frames)
#
# Frontend dedupes shared project_changes when rendering so a min-alt change
# on a project with N plans is shown once at the project header, not N times.
# ---------------------------------------------------------------------------

_PRIORITY_INT_TO_STR = {0: "low", 1: "normal", 2: "high"}


def _fmt_priority(v) -> str:
    try:
        return _PRIORITY_INT_TO_STR.get(int(v or 1), str(v))
    except Exception:
        return str(v)


def _float_or_none(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v):
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def _compute_plan_diffs(conn, plans_list, payload, profile_id):
    from .rows import rows_of
    from .schema import exposure_plan_guid, project_guid, row_plan_guid, target_guid

    # Read everything once. Profiles are isolated by `profileId`; targets
    # have no profileId column so scope via the projects we just read.
    proj_by_guid: dict[str, dict] = {}
    proj_ids: list[int] = []
    for row in conn.execute(
        "SELECT * FROM project WHERE profileId = ?", (profile_id,)
    ):
        proj_ids.append(int(row["Id"]))
        if row["guid"]:
            proj_by_guid[row["guid"]] = dict(row)

    tgt_by_guid: dict[str, dict] = {}
    if proj_ids:
        placeholders = ",".join("?" for _ in proj_ids)
        for row in conn.execute(
            f"SELECT * FROM target WHERE projectid IN ({placeholders})",
            tuple(proj_ids),
        ):
            if row["guid"]:
                tgt_by_guid[row["guid"]] = dict(row)

    ep_by_guid: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT * FROM exposureplan WHERE profileId = ?", (profile_id,)
    ):
        if row["guid"]:
            ep_by_guid[row["guid"]] = dict(row)

    # Index the just-built payload so we can find each entity's incoming
    # values by guid without traversing nested dicts repeatedly.
    proj_payload_by_guid = {p.guid: p for p in payload.projects}
    tgt_payload_by_guid: dict[str, object] = {}
    for tgts in payload.targets_by_project_guid.values():
        for t in tgts:
            tgt_payload_by_guid[t.guid] = t
    ep_payload_by_guid: dict[str, object] = {}
    for plans in payload.plans_by_target_guid.values():
        for ep in plans:
            ep_payload_by_guid[ep.guid] = ep

    out = []
    for plan in plans_list:
        pname = (plan.get("project_name") or "").strip() or plan.get("id") or "Untitled"
        target = plan.get("target") or {}
        tname_base = target.get("name") or pname
        mos = target.get("mosaic") or {}
        rows = max(1, int(mos.get("rows") or 1))
        cols = max(1, int(mos.get("cols") or 1))
        # Anchor: target convert.py emits as R1C1 for mosaics, plain name
        # otherwise. Matches the upsert+import-side convention.
        if rows == 1 and cols == 1:
            anchor_name = tname_base
        else:
            anchor_name = f"{tname_base} Panel 1 (R1C1)"

        proj_g = project_guid(profile_id, pname)
        anchor_t_g = target_guid(profile_id, pname, anchor_name)
        cur_proj = proj_by_guid.get(proj_g)
        new_proj = proj_payload_by_guid.get(proj_g)
        cur_tgt = tgt_by_guid.get(anchor_t_g)
        new_tgt = tgt_payload_by_guid.get(anchor_t_g)

        project_changes: list[dict] = []
        if cur_proj and new_proj:
            # `path` matches diff.py's _PLAN_FIELDS keys so the frontend can
            # cross-reference push entries against pull entries and dedup
            # any change that's also represented on the pull side. Without
            # this, a TS-side edit shows up in BOTH Outgoing and Incoming
            # sections with mirror-imaged arrows, which confuses users into
            # thinking they have to resolve the same fact twice.
            specs = [
                ("minimumaltitude", "min_altitude_deg", "min altitude", "°",
                 _float_or_none, lambda v: _float_or_none(v), 0.01),
                ("meridianwindow", "meridian_window_min", "meridian window", "min",
                 _int_or_none, lambda v: _int_or_none(v), 0),
                ("priority", "priority", "priority", "",
                 _fmt_priority, _fmt_priority, None),
            ]
            for col, path, label, unit, conv_cur, conv_new, tol in specs:
                cur_v = conv_cur(cur_proj.get(col))
                new_v = conv_new(getattr(new_proj, col, None))
                if isinstance(cur_v, float) and isinstance(new_v, float) and tol is not None:
                    if abs(cur_v - new_v) <= tol:
                        continue
                elif cur_v == new_v:
                    continue
                project_changes.append({
                    "path": path, "label": label, "unit": unit,
                    "from": cur_v, "to": new_v,
                })

        target_changes: list[dict] = []
        if cur_tgt and new_tgt:
            specs = [
                # TS stores RA in hours; users think in degrees. Convert
                # both sides so the diff reads naturally.
                ("ra", "target.center_ra_deg", "RA", "°",
                 lambda v: _float_or_none(v) and round(_float_or_none(v) * 15.0, 4),
                 lambda v: _float_or_none(v) and round(_float_or_none(v) * 15.0, 4),
                 0.001),
                ("dec", "target.center_dec_deg", "Dec", "°",
                 _float_or_none, _float_or_none, 0.001),
                ("rotation", "target.rotation_deg", "rotation", "°",
                 _float_or_none, _float_or_none, 0.05),
            ]
            for col, path, label, unit, conv_cur, conv_new, tol in specs:
                cur_v = conv_cur(cur_tgt.get(col))
                new_v = conv_new(getattr(new_tgt, col, None))
                if cur_v is None or new_v is None:
                    continue
                if abs(cur_v - new_v) <= tol:
                    continue
                target_changes.append({
                    "path": path, "label": label, "unit": unit,
                    "from": round(cur_v, 4), "to": round(new_v, 4),
                })

        # Per row, keyed by filter and template. ACP writes a row's frame goal and
        # never its sub length, so `desired` is the one field that moves.
        filter_changes: dict[str, list[dict]] = {}
        first_of_filter: set[str] = set()
        for row in rows_of(plan):
            if row["filter"] not in first_of_filter:
                first_of_filter.add(row["filter"])
                first = True
            else:
                first = False
            ep_g = row_plan_guid(profile_id, anchor_t_g, row["filter"], row["template"])
            new_ep = ep_payload_by_guid.get(ep_g)
            if not new_ep:
                continue
            cur_ep = ep_by_guid.get(ep_g) or ep_by_guid.get(
                exposure_plan_guid(profile_id, anchor_t_g, row["template"]))
            if cur_ep is None and first:
                cur_ep = ep_by_guid.get(exposure_plan_guid(profile_id, anchor_t_g, row["filter"]))
            new_desired = int(getattr(new_ep, "desired", 0) or 0)
            path = f"filter_rows.{row['id']}.desired"
            changes = []
            if cur_ep is None:
                changes.append({"path": path, "label": "frames", "unit": "fr",
                                "from": None, "to": new_desired})
            elif int(cur_ep.get("desired") or 0) != new_desired:
                changes.append({"path": path, "label": "frames", "unit": "fr",
                                "from": int(cur_ep.get("desired") or 0), "to": new_desired})
            if changes:
                filter_changes[f"{row['filter']} \u00b7 {row['template']}"] = changes

        if cur_proj is None:
            kind = "insert"
        elif not project_changes and not target_changes and not filter_changes:
            kind = "unchanged"
        else:
            kind = "update"

        out.append({
            "plan_id": plan.get("id"),
            "project_name": pname,
            "target_name": tname_base,
            "kind": kind,
            "project_changes": project_changes,
            "target_changes": target_changes,
            "filter_changes": filter_changes,
        })

    return out


def _profile_name(body: dict) -> str | None:
    name = body.get("profile_name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def record_template_results(profile_id: str, results: list[dict]) -> None:
    """Pass the writer's per-template results to ACP's
    `record_template_results(profile_id, results)`, which stores them in
    `ts[P]` under its gear lock. A host that predates it has nothing to
    store them in, so they are dropped."""
    try:
        from app import record_template_results as host_record  # type: ignore[import-not-found]
    except ImportError:
        return
    host_record(profile_id, results)


@blueprint.post("/sync")
def sync_route():
    # ACP's record_template_results takes its GEAR_LOCK, and code that holds
    # PLANS_LOCK never takes GEAR_LOCK (gear-templates.md section 4), so the
    # results are handed over only after the plans lock is released.
    pending: list[tuple[str, list[dict]]] = []
    response = _sync_locked(pending)
    for profile_id, results in pending:
        record_template_results(profile_id, results)
    return response


@_holds_plans_lock
def _sync_locked(pending: list):
    body = request.get_json(silent=True) or {}
    try:
        path = resolve_db_path(body.get("db_path"))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400

    from .convert import build_payload
    from .reader import read_all
    from .state import stamp_plans
    from .upsert import apply as upsert_apply
    from .db import get_user_version

    plans_list, gear = _load_acp_state()
    try:
        payload = build_payload(
            plans_list, gear, profile_id=profile_id,
            wheel_slots=wheel_slots_for(body, profile_id), profile_name=_profile_name(body))
    except PayloadError as e:
        # A plan that cannot be identified, so nothing was opened or copied.
        return jsonify({"error": str(e)}), 400

    try:
        with write_transaction(path) as (conn, backup_path):
            report = upsert_apply(conn, payload)
            # Capture refs+snapshot inside the same transaction so what we
            # stamp on the plans matches exactly what we just wrote.
            user_version = get_user_version(conn)
            snap = read_all(conn, profile_id)
    except SchemaVersionError as e:
        return jsonify({
            "error": str(e),
            "found_user_version": e.found,
            "supported_user_versions": list(SUPPORTED_USER_VERSIONS),
        }), 409
    except PayloadError as e:
        # The backstop check inside apply(). The transaction rolled back.
        return jsonify({"error": str(e)}), 400

    from .links import local_machine, preserve_legacy_links, record_current_link
    from .state import _project_name, carry_unwritten_project_fields
    preserve_legacy_links(plans_list)
    # The conditional project settings this push wrote, per project, and
    # each plan's base before the push, so an unwritten setting keeps its
    # old base, as the plugin's base does (ts-project-settings.md section 5).
    from .state import CARRIED_PROJECT_FIELDS
    from .links import link_for
    written_by_project = {
        proj.name: frozenset(CARRIED_PROJECT_FIELDS)
        - payload.project_update_skip_cols_by_guid.get(proj.guid, frozenset())
        for proj in payload.projects
    }
    inserted_projects = {
        proj.name for proj in payload.projects if proj.guid in report.inserted_project_guids}
    old_projects = [
        ((link_for(p, profile_id) or {}).get("base_snapshot") or {}).get("project")
        for p in plans_list
    ]
    # Each plan's base from before this push, for the sub refresh below.
    old_bases = [(link_for(p, profile_id) or {}).get("base_snapshot") for p in plans_list]
    stamp_plans(
        snap, plans_list,
        profile_id=profile_id,
        user_version=user_version,
        operation="push",
        order_targets=report.order_synced_target_ids,
        gear_data=gear,
    )
    for plan, old_project in zip(plans_list, old_projects):
        carry_unwritten_project_fields(
            plan.get("ts_base_snapshot"), old_project,
            written_by_project.get(_project_name(plan), frozenset()),
            inserted=_project_name(plan) in inserted_projects)
    from .rows import refresh_template_subs
    for plan, old_base in zip(plans_list, old_bases):
        # Each row now carries the sub TS uses for its exposure plan
        # (goal-panel-and-ts-order.md section 3, `template_sub_s`), unless
        # ACP changed the row's sub since the last push: then the push wrote
        # it and the row keeps it (gear-templates.md section 8).
        refresh_template_subs(
            plan, plan.get("ts_base_snapshot"), old_base if isinstance(old_base, dict) else None)
        record_current_link(plan, profile_id, local_machine())
    plans_path, plans_backup = _write_plans_json(plans_list, overwrite=True)
    if report.templates:
        # Hand the per-template results to ACP, which stores them in each
        # gear template's `ts[P]` (gear-templates.md section 4). Recorded by
        # sync_route once the plans lock is released.
        pending.append((profile_id, report.templates))

    return jsonify({
        "db_path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
        "plans_path": str(plans_path),
        "plans_backup_path": str(plans_backup) if plans_backup else None,
        "report": report.to_dict(),
    }), 200


# ---------------------------------------------------------------------------
# TS → ACP read paths
# ---------------------------------------------------------------------------


def _read_snapshot(path, profile_id: str):
    """Common helper — open read-only, version-check, snapshot, close."""
    from .reader import read_all
    conn = open_connection(path, read_only=True)
    try:
        assert_supported_version(conn)
        return read_all(conn, profile_id)
    finally:
        conn.close()


@blueprint.post("/import/preview")
def import_preview_route():
    body = request.get_json(silent=True) or {}
    try:
        path = resolve_db_path(body.get("db_path"))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400

    from .from_ts import import_to_acp_plans

    snap = _read_snapshot(path, profile_id)
    _, gear = _load_acp_state()
    plans, notes, _ = import_to_acp_plans(snap, gear)

    return jsonify({
        "db_path": str(path),
        "counts": {
            "templates": len(snap.templates_by_id),
            "projects": len(snap.projects_by_id),
            "targets": len(snap.targets_by_id),
            "exposureplans": len(snap.plans_by_id),
            "acp_plans_to_create": len(plans),
        },
        "sample_plans": [
            {
                "project_name": p["project_name"],
                "target_name": p["target"]["name"],
                "mosaic": p["target"].get("mosaic", {"rows": 1, "cols": 1}),
                "filters": [r["filter"] for r in p["filter_rows"]],
            } for p in plans[:5]
        ],
        "loss_count": sum(1 for n in notes if n.level == "loss"),
        "notes": [n.to_dict() for n in notes],
    }), 200


@blueprint.post("/import/apply")
@_holds_plans_lock
def import_apply_route():
    """Replace ACP's plans.json with what's currently in TS.

    Refuses by default if plans.json already has plans, to avoid silently
    blowing away existing work. Pass ``{"overwrite": true}`` to confirm.
    """
    body = request.get_json(silent=True) or {}
    try:
        path = resolve_db_path(body.get("db_path"))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400
    overwrite = bool(body.get("overwrite") or False)

    from .from_ts import import_to_acp_plans
    from .state import build_ts_refs_from_ids, build_base_snapshot_from_ids
    from .db import get_user_version
    from .links import local_machine, record_current_link

    snap = _read_snapshot(path, profile_id)
    _, gear = _load_acp_state()
    new_plans, notes, id_maps = import_to_acp_plans(snap, gear)

    # Stamp refs+snapshot on the freshly-imported plans so the very next
    # /import/diff has a base to compare against. Use the explicit-ID
    # variant — TS rows we just read carry NINA-assigned random GUIDs (not
    # our deterministic stamps), so a guid-based lookup would miss every
    # row. The importer already knows which Ids each plan came from.
    conn = open_connection(path, read_only=True)
    try:
        user_version = get_user_version(conn)
    finally:
        conn.close()
    for plan, idmap in zip(new_plans, id_maps):
        plan["ts_refs"] = build_ts_refs_from_ids(
            profile_id=profile_id,
            project_id=idmap["project_id"],
            target_ids_by_panel=idmap["target_ids_by_panel"],
            template_ids_by_filter=idmap["template_ids_by_filter"],
            exposure_plan_ids=idmap["exposure_plan_ids"],
            user_version=user_version,
            operation="import",
        )
        plan["ts_base_snapshot"] = build_base_snapshot_from_ids(
            snap,
            project_id=idmap["project_id"],
            target_ids_by_panel=idmap["target_ids_by_panel"],
            template_ids_by_filter=idmap["template_ids_by_filter"],
            exposure_plan_ids=idmap["exposure_plan_ids"],
            plan=plan,
        )
        record_current_link(plan, profile_id, local_machine())

    plans_path, backup_path = _write_plans_json(new_plans, overwrite=overwrite)
    if plans_path is None:
        return jsonify({
            "error": "data/plans.json already has plans; pass overwrite=true to replace",
        }), 409

    return jsonify({
        "plans_path": str(plans_path),
        "backup_path": str(backup_path) if backup_path else None,
        "imported_plans": len(new_plans),
        "notes": [n.to_dict() for n in notes],
    }), 200


def _panel_target_names(plan: dict) -> list[tuple[str, str]]:
    """[(panel key, target name)] for a mosaic plan, in row-major order, named
    as the push names them: "<target> Panel N (RxCy)". The key is "row,col"
    counted from 1, the key ACP uses in a row's `panels` map and in
    `ts_links.refs.target_ids_by_panel`. Empty for a plan that is not a mosaic."""
    tg = plan.get("target") or {}
    mos = tg.get("mosaic") or {}
    rows = max(1, int(mos.get("rows") or 1))
    cols = max(1, int(mos.get("cols") or 1))
    if rows * cols <= 1:
        return []
    base = tg.get("name") or ""
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append((f"{r + 1},{c + 1}", f"{base} Panel {r * cols + c + 1} (R{r + 1}C{c + 1})"))
    return out


def apply_acquired(plan: dict, profile_id: str, acquired: dict) -> tuple[int, int]:
    """Refresh one plan's `actual_hours` (and, for a mosaic, its per-panel
    `panels` map) from ``read_acquired``'s ``{plan_guid: (subs, exposure_s)}``.

    A mosaic's panels are separate targets in TS, so every panel target is
    read: a row gets ``panels = {"1,1": hours, "2,1": hours, ...}`` and
    ``actual_hours`` is their sum. A panel TS has no plan for keeps the hours
    ACP already had for it. A mosaic pushed as a single target (no valid FOV)
    has no panel targets; it is read by its plain name and the row's
    ``panels`` map is dropped. Returns (rows updated, rows skipped).
    """
    from .rows import rows_of
    from .schema import row_plan_guid, target_guid

    proj_name = (plan.get("project_name") or "").strip() or (
        (plan.get("target") or {}).get("name") or plan.get("id") or "Untitled"
    )
    target_name = (plan.get("target") or {}).get("name") or ""
    panel_targets = [(key, target_guid(profile_id, proj_name, name))
                     for key, name in _panel_target_names(plan)]
    plain_guid = target_guid(profile_id, proj_name, target_name)
    updated = skipped = 0
    for row in rows_of(plan):
        if panel_targets:
            found = {}
            for key, t_guid in panel_targets:
                hit = acquired.get(row_plan_guid(profile_id, t_guid, row["filter"], row["template"]))
                if hit and hit[1] > 0:
                    found[key] = round(hit[0] * hit[1] / 3600.0, 4)
            if found:
                panels = dict(row["panels"]) if isinstance(row.get("panels"), dict) else {}
                panels.update(found)
                row["panels"] = dict(sorted(panels.items()))
                row["actual_hours"] = round(sum(
                    v for v in panels.values() if isinstance(v, (int, float)) and not isinstance(v, bool)), 4)
                updated += 1
                continue
        hit = acquired.get(row_plan_guid(profile_id, plain_guid, row["filter"], row["template"]))
        if hit is None:
            skipped += 1
            continue
        subs, exposure_s = hit
        if exposure_s > 0:
            row["actual_hours"] = round(subs * exposure_s / 3600.0, 4)
            row.pop("panels", None)
            updated += 1
    return updated, skipped


@blueprint.post("/sync-acquired")
@_holds_plans_lock
def sync_acquired_route():
    """Lightweight ongoing sync: refresh actual_hours from TS, nothing else.

    Reads `exposureplan.acquired` for each plan whose guid we recognise
    (from a previous push), maps that back to ``filter_rows[*].actual_hours``
    in ACP's plans.json, writes the file with a sibling .bak. A mosaic's rows
    also get a per-panel ``panels`` map, read from every panel target, with
    ``actual_hours`` as its sum (see apply_acquired).
    """
    body = request.get_json(silent=True) or {}
    try:
        path = resolve_db_path(body.get("db_path"))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400

    from .reader import read_acquired

    conn = open_connection(path, read_only=True)
    try:
        assert_supported_version(conn)
        acquired = read_acquired(conn, profile_id)
    finally:
        conn.close()

    plans_list, _ = _load_acp_state()
    updated = 0
    skipped = 0
    for plan in plans_list:
        u, k = apply_acquired(plan, profile_id, acquired)
        updated += u
        skipped += k

    plans_path, backup_path = _write_plans_json(plans_list, overwrite=True)
    return jsonify({
        "plans_path": str(plans_path),
        "backup_path": str(backup_path) if backup_path else None,
        "updated_rows": updated,
        "skipped_unmatched": skipped,
    }), 200


# ---------------------------------------------------------------------------
# /import/diff and /import/resolve: the three-way merge against the TS
# database on this host. The engine is import_merge.py, shared with uploads.
# ---------------------------------------------------------------------------


@blueprint.post("/import/diff")
def import_diff_route():
    body = request.get_json(silent=True) or {}
    try:
        path = resolve_db_path(body.get("db_path"))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400

    from .import_merge import diff_from, legacy_diff_dict
    from .links import local_machine

    try:
        view, _ = diff_from(path, profile_id, local_machine())
    except SchemaVersionError as e:
        return jsonify({"error": str(e), "found_user_version": e.found}), 409
    return jsonify({"db_path": str(path), **legacy_diff_dict(view)}), 200


@blueprint.post("/import/resolve")
def import_resolve_route():
    """Apply per-plan decisions. Conflicts with no decision are left alone.

    ``import_ts_only_new`` lists the TS plans to add: each item is a
    ``ts_only_new`` entry's ``key``, or, as ACP's sync modal still sends,
    a TS project name, which adds every unmatched plan in that project.
    """
    body = request.get_json(silent=True) or {}
    try:
        path = resolve_db_path(body.get("db_path"))
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"error": "profile_id is required"}), 400
    import_new = [str(x) for x in (body.get("import_ts_only_new") or [])]

    from .import_merge import MergeError, resolve_from
    from .links import local_machine

    try:
        result = resolve_from(
            path, profile_id, local_machine(),
            body.get("decisions") or {}, [], None,
            legacy_names=import_new,
        )
    except SchemaVersionError as e:
        return jsonify({"error": str(e), "found_user_version": e.found}), 409
    except MergeError as e:
        return jsonify(e.to_dict()), e.status
    return jsonify({"db_path": str(path), **result}), 200


def _plans_document(plans: list) -> dict:
    """The full plans.json document to write.

    ACP core owns top-level keys this extension doesn't know about (e.g.
    `settings_migrated`, its one-shot draft-to-active migration flag). Every
    write path loads the current document and keeps every key but `plans`,
    rather than rebuilding `{"version": 1, "plans": ...}` from scratch,
    which would drop them and make ACP re-run whatever they gate.
    """
    from app import load_plans  # type: ignore[import-not-found]
    try:
        doc = load_plans()
    except Exception:
        doc = None
    if not isinstance(doc, dict):
        doc = {"version": 1}
    return {**doc, "plans": plans}


def _write_plans_json(plans: list, *, overwrite: bool):
    """Write a new ACP data/plans.json via the host's save_plans helper.

    Refuses to clobber if `overwrite=False` and existing file already has
    plans. Returns (path, backup_path) on success, (None, None) if refused.
    """
    import json
    import shutil
    from datetime import datetime, timezone
    from app import PLANS_PATH, save_plans  # type: ignore[import-not-found]

    plans_path = PLANS_PATH
    if plans_path.exists() and not overwrite:
        try:
            existing = json.loads(plans_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("plans"):
                return None, None
        except Exception:
            pass  # corrupt — overwrite is fine

    backup_path = None
    if plans_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = plans_path.with_name(
            f"{plans_path.stem}-acpsync-{stamp}-backup{plans_path.suffix}"
        )
        # Two writes in one second must not share a backup, or the second
        # overwrites the only copy of the state before the first.
        n = 2
        while backup_path.exists():
            backup_path = plans_path.with_name(
                f"{plans_path.stem}-acpsync-{stamp}-{n}-backup{plans_path.suffix}"
            )
            n += 1
        shutil.copy2(plans_path, backup_path)

    save_plans(_plans_document(plans))
    return plans_path, backup_path


# ---------------------------------------------------------------------------
# Config + profile-picker endpoints — used by the core ACP UI on first
# button click to prompt for and persist a profile_id.
# ---------------------------------------------------------------------------


@blueprint.get("/config")
def config_get():
    """Return the current persisted config (just ``profile_id`` today)."""
    from . import config
    cfg = config.load()
    return jsonify({
        "profile_id": cfg.get("profile_id"),
        "config_path": str(config.config_path()),
    }), 200


@blueprint.post("/config")
def config_set():
    """Persist a new ``profile_id`` (and any future config keys).

    Body: ``{"profile_id": "<GUID>"}``. Empty/missing profile_id clears it.
    """
    from . import config
    body = request.get_json(silent=True) or {}
    updates: dict = {}
    if "profile_id" in body:
        pid = (body.get("profile_id") or "").strip() or None
        updates["profile_id"] = pid
    if not updates:
        return jsonify({"error": "no recognised keys in body"}), 400
    merged = config.save(updates)
    return jsonify({
        "profile_id": merged.get("profile_id"),
        "config_path": str(config.config_path()),
    }), 200


@blueprint.get("/profiles")
def profiles_list():
    """List distinct ``profileId`` values found in the live DB.

    Used by the frontend profile picker on first click. Includes a small
    sample of project names per profile so the user can recognise which
    profile is which without consulting NINA.
    """
    try:
        path = resolve_db_path()
    except FileNotFoundError as e:
        return jsonify({"error": str(e), "profiles": []}), 200

    from collections import defaultdict
    conn = open_connection(path, read_only=True)
    try:
        assert_supported_version(conn)
        rows = list(conn.execute(
            "SELECT profileId, name FROM project ORDER BY profileId, name"
        ))
        # profileprefence is the authoritative list of *known* profiles even
        # when they don't yet own any projects, so merge those too.
        try:
            pref_rows = list(conn.execute(
                "SELECT DISTINCT profileId FROM profilepreference"
            ))
        except Exception:
            pref_rows = []
    except SchemaVersionError as e:
        return jsonify({
            "error": str(e),
            "found_user_version": e.found,
            "supported_user_versions": list(SUPPORTED_USER_VERSIONS),
            "profiles": [],
        }), 200
    finally:
        conn.close()

    seen = defaultdict(list)
    for r in rows:
        seen[r["profileId"]].append(r["name"])
    for r in pref_rows:
        seen.setdefault(r["profileId"], [])

    # NINA stores the human profile name in %LOCALAPPDATA%\NINA\Profiles\
    # <guid>.profile. The TS DB only has the GUID, which is meaningless to
    # the user. Best-effort merge — if we can't read the file (Linux dev,
    # nuked profiles dir, etc.) the picker falls back to a short id.
    from .paths import read_profile_names
    name_by_id = {k.lower(): v for k, v in read_profile_names().items()}

    return jsonify({
        "db_path": str(path),
        "profiles": [
            {
                "profile_id": pid,
                "name": name_by_id.get((pid or "").lower(), ""),
                "project_count": len(names),
                "sample_projects": names[:5],
            }
            for pid, names in seen.items()
        ],
    }), 200
