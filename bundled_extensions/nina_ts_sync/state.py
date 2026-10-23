"""Build `ts_refs` and `ts_base_snapshot` for an ACP plan.

After every write (push or import), we capture two pieces of state inline
on each ACP plan:

- ``ts_refs``: TS-side row Ids we wrote, plus timestamps. Used for the
  rail's "synced N min ago" indicator and for detecting TS-side deletions
  on the next import.
- ``ts_base_snapshot``: the subset of TS state we'll diff against on the
  next import. The "BASE" in the three-way merge.

GUID derivation matches `convert.py` exactly. Panel keys are 1-indexed
tuples stringified as ``"{row},{col}"`` — decoupled from naming so a
TS-side rename doesn't break the mapping.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import rows as fr
from .order import as_rows, effective_plan_order, known_rows_of_base
from .reader import TSSnapshot
from .schema import (
    project_guid,
    row_plan_guid,
    target_guid,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _panel_key(row1: int, col1: int) -> str:
    return f"{row1},{col1}"


def _expected_target_name(base: str, rows: int, cols: int, row1: int, col1: int) -> str:
    """Reproduce convert.py's target naming. Used for guid derivation only;
    if TS rows have been renamed, the guid still works because it was
    stamped at push time and never changes."""
    if rows == 1 and cols == 1:
        return base
    panel_idx = (row1 - 1) * cols + col1
    return f"{base} Panel {panel_idx} (R{row1}C{col1})"


def _resolve_panels(plan: dict) -> tuple[int, int, str]:
    """(rows, cols, base_name) for an ACP plan."""
    tg = plan.get("target") or {}
    mos = tg.get("mosaic") or {}
    rows = max(1, int(mos.get("rows") or 1))
    cols = max(1, int(mos.get("cols") or 1))
    base = tg.get("name") or plan.get("id") or "Untitled"
    return rows, cols, base


def _project_name(plan: dict) -> str:
    explicit = (plan.get("project_name") or "").strip()
    if explicit:
        return explicit
    tg = plan.get("target") or {}
    return (tg.get("name") or "").strip() or plan.get("id") or "Untitled"


def _camera_id(plan: dict) -> str:
    return (plan.get("camera_id") or "").strip()


def _plan_mosaic_snapshot(plan: dict) -> dict | None:
    """Capture the plan's mosaic struct for the BASE snapshot.

    Without this, the diff sees `BASE.mosaic.*` as null and any non-null
    value on LOCAL or REMOTE flips to an `auto_pull` change — which combined
    with bug #4 would silently corrupt the user's overlap_pct on resolve.
    Returns None for 1×1 "mosaics" so the diff can short-circuit cleanly.
    """
    mos = (plan.get("target") or {}).get("mosaic") or {}
    rows = max(1, int(mos.get("rows") or 1))
    cols = max(1, int(mos.get("cols") or 1))
    if rows == 1 and cols == 1:
        return None
    return {
        "rows": rows,
        "cols": cols,
        "overlap_pct": int(mos.get("overlap_pct") or 0),
    }


def _project_snapshot(proj) -> dict:
    """The project fields the BASE snapshot keeps.

    `state` is Target Scheduler's own (0 draft, 1 active, 2 inactive, 3
    closed). ACP's plan model mirrors it as `state`, `priority` and
    `minimum_time_min`; this is the base the next push and import diff
    against.
    """
    return {
        "name": proj.name,
        # `proj.priority or 1` turned low (0) into normal (1), since 0 is
        # falsy in Python. Only None (the column never set) should default.
        "priority": int(proj.priority) if proj.priority is not None else 1,
        "minimumaltitude": float(proj.minimumaltitude or 0),
        "meridianwindow": int(proj.meridianwindow or 0),
        "enablegrader": int(proj.enablegrader or 0),
        "state": int(proj.state if proj.state is not None else 1),
        "minimumtime": int(proj.minimumtime or 0),
        # The shooting-order settings (goal-panel-and-ts-order.md section
        # 4). The importer maps the first two to the plan; `ditherevery` is
        # TS's own, kept for the order writer's dither rows.
        "filterswitchfrequency": int(proj.filterswitchfrequency or 0),
        "smartexposureorder": int(proj.smartexposureorder or 0),
        "ditherevery": int(proj.ditherevery or 0),
    }


#: Base project fields a push writes only when ACP changed them. When the
#: push left one alone, the next base keeps the old value rather than TS's,
#: or ACP would look changed on the next push and overwrite TS's value
#: (ts-project-settings.md section 5, third row).
CARRIED_PROJECT_FIELDS = ("state", "priority", "minimumtime", "filterswitchfrequency", "smartexposureorder")

#: What a base from before state, priority and minimumtime were recorded
#: reads as (ts-project-settings.md section 3). The two order settings have
#: no such default here: a base without them keeps TS's value, as the plugin
#: does.
_BASE_DEFAULTS = {"state": 1, "priority": 1, "minimumtime": 0}

#: The three settings a push never writes on update without a base, so the
#: base leaves them out until it has one (section 5, "No base").
_NEEDS_BASE = ("state", "priority", "minimumtime")


def carry_unwritten_project_fields(
    new_base: dict | None,
    old_project: dict | None,
    written: frozenset[str] | set[str],
    *,
    inserted: bool = False,
) -> None:
    """In *new_base*, put back each of CARRIED_PROJECT_FIELDS that the push
    did not write (not in *written*), from *old_project*, the base before
    the push. Matches the plugin's TsState.BuildBaseSnapshot.

    A field the old base lacks takes the default it reads as. With no old
    base at all, a project this push did not insert keeps no state,
    priority or minimumtime in its base, because the push wrote none of
    them; an inserted project holds exactly ACP's values, so they stay."""
    proj = (new_base or {}).get("project")
    if not proj:
        return
    if not old_project:
        if not inserted:
            for col in _NEEDS_BASE:
                proj.pop(col, None)
        return
    for col in CARRIED_PROJECT_FIELDS:
        if col in written:
            continue
        if col in old_project:
            proj[col] = old_project[col]
        elif col in _BASE_DEFAULTS:
            proj[col] = _BASE_DEFAULTS[col]


def _template_entry(tpl) -> dict:
    # A gain or offset of 0 is a setting, not an absence: only a missing
    # value reads as -1 (the camera's NINA setting).
    return {
        "name": tpl.name,
        "defaultexposure": float(tpl.defaultexposure or 0),
        "gain": -1 if tpl.gain is None else int(tpl.gain),
        "offset": -1 if tpl.offset is None else int(tpl.offset),
        "bin": int(tpl.bin or 1),
    }


def _plan_entry(pl, tpl, filter_name, template_name, row: dict | None = None,
                plan: dict | None = None) -> dict:
    """One exposure plan in the base. `filter` and `template` say which row
    it was written for, so the next push can tell a template change (the one
    update that writes `exposure`). `template_sub_s` is the sub TS uses,
    which a push copies back onto the row.

    `desired` is the row's goal: when *row* is an AAC-linked row and TS holds
    exactly the floored value, the base records the unfloored goal, so AAC's
    floor never reads as an edit (goal-panel-and-ts-order.md section 6).

    `enabled` is whether ACP wants the plan on: 1 when its row has a goal
    above 0. It is not TS's flag, so a plan the user turned off in TS stays
    off at every later push, not just the next one. With no row it is TS's
    flag."""
    desired = int(pl.desired or 0)
    if row is not None:
        desired = fr.unfloored_goal(row, plan, desired)
        enabled = 1 if fr.desired_for(row, plan) > 0 else 0
    else:
        enabled = int(pl.enabled if pl.enabled is not None else 1)
    entry = {
        "desired": desired,
        "acquired": int(pl.acquired or 0),
        "accepted": int(pl.accepted or 0),
        "enabled": enabled,
        "exposure": float(pl.exposure or 0),
        "filter": filter_name,
        "template": template_name,
        "template_sub_s": fr.plan_sub_s(pl.exposure, tpl.defaultexposure if tpl is not None else 0),
    }
    # The gear template the row used, so the next push can tell a move to
    # another template from a rename (gear-templates.md section 8).
    if row is not None and row.get("template_id"):
        entry["template_id"] = row["template_id"]
    return entry


def build_ts_refs(
    snap: TSSnapshot,
    plan: dict,
    *,
    profile_id: str,
    user_version: int,
    operation: str,
    camera: dict | None = None,
) -> dict:
    """Build the ts_refs for *plan* using the freshly-read *snap*.

    ``operation`` is "push" or "import" — flips which timestamp updates.
    The opposite timestamp is preserved from any existing ts_refs on the
    plan so we don't lose the older one.
    """
    pname = _project_name(plan)
    rows, cols, base = _resolve_panels(plan)

    proj_g = project_guid(profile_id, pname)
    project_id = snap.project_id_by_guid.get(proj_g)

    target_ids_by_panel: dict[str, int] = {}
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            t_name = _expected_target_name(base, rows, cols, r, c)
            t_g = target_guid(profile_id, pname, t_name)
            tid = snap.target_id_by_guid.get(t_g)
            if tid is not None:
                target_ids_by_panel[_panel_key(r, c)] = tid

    # Keyed by row id: "<target id>_<row id>" for exposure plans, and the
    # row id for the template its plan points at, panel 1,1 first.
    template_ids_by_filter: dict[str, int] = {}
    exposure_plan_ids: dict[str, int] = {}
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            t_name = _expected_target_name(base, rows, cols, r, c)
            t_g = target_guid(profile_id, pname, t_name)
            for row in fr.rows_of(plan, camera):
                pid = snap.plan_id_by_guid.get(
                    row_plan_guid(profile_id, t_g, row["filter"], row["template"]))
                if pid is None:
                    continue
                key = f"{target_ids_by_panel.get(_panel_key(r, c), 0)}_{row['id']}"
                exposure_plan_ids[key] = pid
                template_ids_by_filter.setdefault(row["id"], snap.plans_by_id[pid].exposureTemplateId)

    prior = plan.get("ts_refs") or {}
    now = _now_iso()
    refs = {
        "profile_id": profile_id,
        "project_id": project_id,
        "target_ids_by_panel": target_ids_by_panel,
        "template_ids_by_filter": template_ids_by_filter,
        "exposure_plan_ids": exposure_plan_ids,
        "last_pushed_iso": prior.get("last_pushed_iso"),
        "last_pushed_user_version": prior.get("last_pushed_user_version"),
        "last_imported_iso": prior.get("last_imported_iso"),
    }
    if operation == "push":
        refs["last_pushed_iso"] = now
        refs["last_pushed_user_version"] = user_version
    elif operation == "import":
        refs["last_imported_iso"] = now
    return refs


def _order_fields(
    snap: TSSnapshot, target_id: int, row_id_by_plan: dict[int, str]
) -> tuple[list[str], list[list[int]]]:
    """`exposure_order` and `override_rows` for one target as TS has them:
    the row ids in the order TS shoots their plans, and the override rows
    as [action, referenceIdx] pairs (goal-panel-and-ts-order.md section 5).
    A plan no ACP row stands for is left out of the order."""
    rows = snap.override_rows_by_target.get(target_id, [])
    order, _ = effective_plan_order(snap.enabled_plan_ids(target_id), rows)
    return [row_id_by_plan[p] for p in order if p in row_id_by_plan], [[a, r] for a, r in rows]


ANCHOR_PANEL = "1,1"


def _share_anchor_order(targets: dict) -> None:
    """The base order is one per target: the anchor panel's (1,1). Every
    other panel records the anchor's order as its `exposure_order`, never its
    own, so a hand-made order on one panel is not mistaken for ACP's order
    at the next push (goal-panel-and-ts-order.md section 5). `known_rows`
    travels with it. Without an anchor order, no panel records either."""
    anchor = targets.get(ANCHOR_PANEL)
    order = anchor.get("exposure_order") if isinstance(anchor, dict) else None
    known = anchor.get("known_rows") if isinstance(anchor, dict) else None
    for key, entry in targets.items():
        if key == ANCHOR_PANEL:
            continue
        if isinstance(order, list):
            entry["exposure_order"] = list(order)
        else:
            entry.pop("exposure_order", None)
        if isinstance(order, list) and isinstance(known, list):
            entry["known_rows"] = list(known)
        else:
            entry.pop("known_rows", None)


def _old_panel(old_base: dict | None, panel_key: str) -> dict:
    entry = ((old_base or {}).get("targets_by_panel") or {}).get(panel_key)
    return entry if isinstance(entry, dict) else {}


def build_base_snapshot(
    snap: TSSnapshot,
    plan: dict,
    *,
    profile_id: str,
    old_base: dict | None = None,
    order_targets: set[int] | None = None,
    camera: dict | None = None,
) -> dict:
    """Build the ts_base_snapshot for *plan*.

    Each panel's target records `exposure_order` and `override_rows`.
    *order_targets* are the targets whose override rows are ACP's after
    the push (`SyncReport.order_synced_target_ids`): those record what TS
    now has. Every other target keeps what *old_base* recorded, or nothing,
    because a push that left TS's rows alone keeps the old base order
    (goal-panel-and-ts-order.md section 5). None means there is no push
    outcome: each target records the order TS shoots, and its
    `override_rows` only when TS has none, as an import does, so rows set
    in TS are never taken for rows the sync wrote. The base order is the
    anchor panel's for every panel.

    *camera* names the templates of rows derived from filter goals.

    Captures the current TS state as the "BASE" for the next three-way
    merge. Anything missing in *snap* (e.g. user already deleted the TS
    row) is omitted, and diff treats absent fields as "no remote".
    """
    pname = _project_name(plan)
    rows, cols, base = _resolve_panels(plan)

    snapshot: dict = {
        "captured_iso": _now_iso(),
        "project": None,
        "mosaic": _plan_mosaic_snapshot(plan),
        "targets_by_panel": {},
        "templates_by_filter": {},
        "exposure_plans_by_panel_filter": {},
    }

    proj_g = project_guid(profile_id, pname)
    proj_id = snap.project_id_by_guid.get(proj_g)
    if proj_id is not None:
        snapshot["project"] = _project_snapshot(snap.projects_by_id[proj_id])

    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            t_name = _expected_target_name(base, rows, cols, r, c)
            t_g = target_guid(profile_id, pname, t_name)
            tid = snap.target_id_by_guid.get(t_g)
            if tid is None:
                continue
            tgt = snap.targets_by_id[tid]
            entry = {
                "name": tgt.name,
                "ra": float(tgt.ra or 0),  # hours
                "dec": float(tgt.dec or 0),
                "rotation": float(tgt.rotation or 0),
            }
            if order_targets is None or tid in order_targets:
                row_id_by_plan: dict[int, str] = {}
                for row in fr.rows_of(plan, camera):
                    pid = snap.plan_id_by_guid.get(
                        row_plan_guid(profile_id, t_g, row["filter"], row["template"]))
                    if pid is not None:
                        row_id_by_plan.setdefault(pid, row["id"])
                entry["exposure_order"], ts_rows = _order_fields(snap, tid, row_id_by_plan)
                entry["known_rows"] = list(dict.fromkeys(
                    str(row["id"]) for row in fr.rows_of(plan, camera)))
                if order_targets is not None or not ts_rows:
                    entry["override_rows"] = ts_rows
            else:
                old = _old_panel(old_base, _panel_key(r, c))
                for key in ("exposure_order", "override_rows", "known_rows"):
                    if key in old:
                        entry[key] = old[key]
            snapshot["targets_by_panel"][_panel_key(r, c)] = entry
    _share_anchor_order(snapshot["targets_by_panel"])

    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            t_name = _expected_target_name(base, rows, cols, r, c)
            t_g = target_guid(profile_id, pname, t_name)
            for row in fr.rows_of(plan, camera):
                pid = snap.plan_id_by_guid.get(
                    row_plan_guid(profile_id, t_g, row["filter"], row["template"]))
                if pid is None:
                    continue
                pl = snap.plans_by_id[pid]
                tpl = snap.templates_by_id.get(pl.exposureTemplateId)
                if tpl is not None:
                    snapshot["templates_by_filter"].setdefault(row["id"], _template_entry(tpl))
                snapshot["exposure_plans_by_panel_filter"][f"{_panel_key(r, c)}_{row['id']}"] = \
                    _plan_entry(pl, tpl, row["filter"], row["template"], row, plan)

    return snapshot


def stamp_plans(
    snap: TSSnapshot,
    plans: list[dict],
    *,
    profile_id: str,
    user_version: int,
    operation: str,
    order_targets: set[int] | None = None,
    gear_data: dict | None = None,
) -> None:
    """Mutate each plan in-place, writing fresh ts_refs and ts_base_snapshot.

    *order_targets* as for `build_base_snapshot`; each plan's old base is
    its link for *profile_id*, read before it is replaced. *gear_data* gives
    each plan's camera, which names the templates of rows derived from
    filter goals, as the push does."""
    from .links import link_for
    cameras_by_id = {c.get("id"): c for c in (gear_data or {}).get("cameras", []) if isinstance(c, dict)}
    for plan in plans:
        old_base = (link_for(plan, profile_id) or {}).get("base_snapshot")
        camera = cameras_by_id.get(plan.get("camera_id") or "")
        plan["ts_refs"] = build_ts_refs(
            snap, plan,
            profile_id=profile_id,
            user_version=user_version,
            operation=operation,
            camera=camera,
        )
        plan["ts_base_snapshot"] = build_base_snapshot(
            snap, plan, profile_id=profile_id,
            old_base=old_base if isinstance(old_base, dict) else None,
            order_targets=order_targets,
            camera=camera,
        )


# ---------------------------------------------------------------------------
# Explicit-ID variants — used by the import path, where TS rows carry
# NINA-assigned random GUIDs (not our deterministic stamps), so the
# guid-based lookup in build_ts_refs would miss every row. The importer knows
# exactly which TS Ids each plan came from and passes them in directly.
# ---------------------------------------------------------------------------


def build_ts_refs_from_ids(
    *,
    profile_id: str,
    project_id: int | None,
    target_ids_by_panel: dict[str, int],
    template_ids_by_filter: dict[str, int],
    exposure_plan_ids: dict[str, int],
    user_version: int,
    operation: str,
    prior_refs: dict | None = None,
) -> dict:
    prior = prior_refs or {}
    now = _now_iso()
    refs = {
        "profile_id": profile_id,
        "project_id": project_id,
        "target_ids_by_panel": dict(target_ids_by_panel),
        "template_ids_by_filter": dict(template_ids_by_filter),
        "exposure_plan_ids": dict(exposure_plan_ids),
        "last_pushed_iso": prior.get("last_pushed_iso"),
        "last_pushed_user_version": prior.get("last_pushed_user_version"),
        "last_imported_iso": prior.get("last_imported_iso"),
    }
    if operation == "push":
        refs["last_pushed_iso"] = now
        refs["last_pushed_user_version"] = user_version
    elif operation == "import":
        refs["last_imported_iso"] = now
    return refs


def build_base_snapshot_from_ids(
    snap: TSSnapshot,
    *,
    project_id: int | None,
    target_ids_by_panel: dict[str, int],
    template_ids_by_filter: dict[str, int],
    exposure_plan_ids: dict[str, int],
    plan: dict | None = None,
    old_base: dict | None = None,
) -> dict:
    """The base an import leaves, from the TS ids it read.

    Every panel records `exposure_order` as the anchor panel's (1,1) order
    in TS now, not its own: the order decision is per target, and a hand
    order on another panel must not look like ACP's. A panel records
    `known_rows` is what *old_base* knew plus the row ids behind TS's plans
    (ACP's rows now when there is no old base), so a row added in ACP since
    the last push still counts as new. A panel records
    `override_rows` only when they are no order of TS's own: none at all, or
    the rows *old_base* recorded, which the sync wrote. Rows set in TS are
    left out, so the next push sees them differ from the base and leaves them
    in TS until ACP's order changes (goal-panel-and-ts-order.md section 5).
    """
    snapshot: dict = {
        "captured_iso": _now_iso(),
        "project": None,
        "mosaic": _plan_mosaic_snapshot(plan) if plan else None,
        "targets_by_panel": {},
        "templates_by_filter": {},
        "exposure_plans_by_panel_filter": {},
    }
    if project_id is not None and project_id in snap.projects_by_id:
        snapshot["project"] = _project_snapshot(snap.projects_by_id[project_id])
    row_id_by_plan_by_target: dict[int, dict[int, str]] = {}
    for key, pid in exposure_plan_ids.items():
        tid_str, _, row_id = str(key).partition("_")
        try:
            row_id_by_plan_by_target.setdefault(int(tid_str), {}).setdefault(pid, row_id)
        except ValueError:
            continue
    for panel_key, tid in target_ids_by_panel.items():
        tgt = snap.targets_by_id.get(tid)
        if tgt is None:
            continue
        entry = {
            "name": tgt.name,
            "ra": float(tgt.ra or 0),  # hours, matching build_base_snapshot
            "dec": float(tgt.dec or 0),
            "rotation": float(tgt.rotation or 0),
        }
        order, ts_rows = _order_fields(snap, tid, row_id_by_plan_by_target.get(tid, {}))
        entry["exposure_order"] = order
        # What the old base knew, not ACP's rows now: a row added in ACP since
        # the last push must still count as new at the next one.
        if isinstance(old_base, dict) and old_base.get("targets_by_panel"):
            known = known_rows_of_base(
                _old_panel(old_base, panel_key), old_base.get("exposure_plans_by_panel_filter"))
        else:
            known = [str(r["id"]) for r in fr.rows_of(plan)]
        known += list(row_id_by_plan_by_target.get(tid, {}).values())
        entry["known_rows"] = list(dict.fromkeys(known))
        old_rows = as_rows(_old_panel(old_base, panel_key).get("override_rows"))
        if not ts_rows or (old_rows is not None and [tuple(r) for r in ts_rows] == old_rows):
            entry["override_rows"] = ts_rows
        snapshot["targets_by_panel"][panel_key] = entry
    _share_anchor_order(snapshot["targets_by_panel"])
    for row_id, tpl_id in template_ids_by_filter.items():
        tpl = snap.templates_by_id.get(tpl_id)
        if tpl is None:
            continue
        snapshot["templates_by_filter"][row_id] = _template_entry(tpl)
    # exposure_plan_ids is keyed "<target_id>_<row id>"; the snapshot wants
    # "<panel_key>_<row id>". Translate via the inverse of target_ids_by_panel.
    target_id_to_panel = {tid: panel_key for panel_key, tid in target_ids_by_panel.items()}
    acp_rows = {str(r["id"]): r for r in fr.rows_of(plan)} if plan else {}
    for key, pid in exposure_plan_ids.items():
        pl = snap.plans_by_id.get(pid)
        if pl is None:
            continue
        try:
            tid_str, row_id = key.split("_", 1)
            tid = int(tid_str)
        except (ValueError, IndexError):
            continue
        panel_key = target_id_to_panel.get(tid)
        if panel_key is None:
            continue
        tpl = snap.templates_by_id.get(pl.exposureTemplateId)
        filt = fr.row_filter(tpl.filtername) if tpl is not None else None
        snapshot["exposure_plans_by_panel_filter"][f"{panel_key}_{row_id}"] = _plan_entry(
            pl, tpl, filt, tpl.name if tpl is not None else None,
            acp_rows.get(row_id), plan)
    return snapshot
