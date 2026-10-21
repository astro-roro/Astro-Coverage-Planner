"""ACP plan dicts → SyncPayload (the dataclass tree upsert.py consumes).

Mirrors the project-grouping + mosaic-panel-expansion logic of ACP's
existing `_build_ts_export` so direct-write produces the same logical row
set as the legacy zip flow. Re-implemented here (rather than imported from
app) so the extension stays independent of ACP's internals.

Inputs (loaded by the caller, typically from `data/plans.json` /
`data/gear.json`):
    plans_list: list of plan dicts in ACP's native shape — see
        ``data/plans.example.json`` or app.py:newEmptyPlan for the schema.
    gear_data:  dict with ``telescopes`` and ``cameras`` lists.
    profile_id: a string identifying the NINA profile these plans target.
                Currently the user passes this in; future work could pull
                it from gear data or a settings file.

Strictest-wins resolution per project group:
    - min_altitude_deg → max across the group's plans
    - meridian_window_min → min of the nonzero values, else 0
    - priority → max priority across the group
    - state → the most-running value: active, inactive, draft, closed
    - minimum_time_min → max across the group's plans

`state`, `priority` and `minimum_time_min` are ACP-owned but synced both
ways (docs/specs/ts-project-settings.md section 5): the resolved group
value only reaches the UPDATE when it differs from the last base snapshot
this rig took, so a change made in Target Scheduler since then survives
the push. See `project_update_skip_cols_by_guid` on `SyncPayload`.

`filter_switch_frequency` and `smart_exposure_order` follow the same rule
(goal-panel-and-ts-order.md section 4), with one difference: absent on a plan
means unknown, and an unknown value is never written on update. Across a
group the first plan with a value wins, and a disagreement is a push note.
"""
from __future__ import annotations

import math
import time
from datetime import datetime

from . import rows as fr
from .filter_canon import canonical_filter_name, resolve_to_wheel
from .links import link_for
from .order import as_rows, known_rows_of_base
from .schema import (
    AacProject,
    ExposurePlan,
    ExposureTemplate,
    GearWork,
    PayloadError,
    PlanLookup,
    Project,
    RemovedPlan,
    RuleWeight,
    SyncPayload,
    Target,
    TargetOrder,
    exposure_plan_guid,
    gear_template_guid,
    project_guid,
    row_plan_guid,
    row_template_guid,
    target_guid,
)

PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2}
_PRIORITY_NAME = {0: "low", 1: "normal", 2: "high"}

# The four TS project states, and the group-consistency order from
# docs/specs/ts-project-settings.md section 1: the most-running value wins,
# so one active plan keeps the whole project imaging.
_STATE_TO_INT = {"draft": 0, "active": 1, "inactive": 2, "closed": 3}
_STATE_NAME = {0: "draft", 1: "active", 2: "inactive", 3: "closed"}
_STATE_RANK = {"active": 0, "inactive": 1, "draft": 2, "closed": 3}


def _normalize_state(v) -> str:
    """A plan's raw `state` value, folded onto one of the four TS names.

    ``None``/absent means active. ``"parked"`` (PR #97's name for what is
    now "inactive") and anything else unrecognised also read as their
    closest TS meaning rather than raising.
    """
    if not v:
        return "active"
    if v == "parked":
        return "inactive"
    return v if v in _STATE_TO_INT else "active"

# Rule weights seeded on a freshly inserted Project. Target Scheduler looks a
# project's weights up by each scoring rule's DISPLAY name (ScoringRule.Name,
# e.g. "Meridian Flip Penalty") and throws if one is missing, so the planner
# fails for every target until NINA restarts and TS repairs the rows. These are
# the eight rules TS 5 defines, at its own DEFAULT_WEIGHT x 100. Users tune them
# in the TS UI afterwards; a sync never overwrites a tuned weight.
DEFAULT_RULE_WEIGHTS: dict[str, float] = {
    "Percent Complete": 50.0,
    "Project Priority": 50.0,
    "Setting Soonest": 50.0,
    "Meridian Window Priority": 75.0,
    "Target Switch Penalty": 67.0,
    "Mosaic Completion": 0.0,
    "Smart Exposure Order": 0.0,
    "Meridian Flip Penalty": 0.0,
}

# The internal names earlier versions seeded by mistake (2026-10-09: TS planning
# threw "The given key 'Meridian Flip Penalty' was not present in the
# dictionary"). A sync that finds them on a project it touches removes them and
# adds whichever of the real names are missing.
LEGACY_RULE_NAMES: frozenset[str] = frozenset({
    "PercentComplete", "MeridianWindow", "MosaicCompletion", "SettingSoonest", "SmartExposure",
})


def _fov_arcmin(telescope: dict | None, camera: dict | None) -> tuple[float, float]:
    """Compute on-sky FOV (width, height) in arcmin from gear metadata.

    Mirrors app.py:_fov_arcmin. Returns ``(0, 0)`` if either piece is missing
    or the maths underflows — caller treats that as "single-panel only".
    """
    if not telescope or not camera:
        return (0.0, 0.0)
    fl = float(telescope.get("focal_length_mm") or 0)
    px = float(camera.get("pixel_size_um") or 0)
    dims = camera.get("sensor_px") or [0, 0]
    if fl <= 0 or px <= 0 or len(dims) < 2:
        return (0.0, 0.0)
    arcsec_per_px = 206.265 * px / fl
    return (
        round(dims[0] * arcsec_per_px / 60, 2),
        round(dims[1] * arcsec_per_px / 60, 2),
    )


def _mosaic_panel_centers(
    ra_c: float,
    dec_c: float,
    fov_w_arcmin: float,
    fov_h_arcmin: float,
    rot_deg: float,
    rows: int,
    cols: int,
    overlap_pct: float,
) -> list[dict]:
    """Per-panel centers [{row, col, ra_deg, dec_deg}] for a rows×cols mosaic.

    Identical formula to app.py:_mosaic_panel_centers — kept in sync because
    the panel suffix in target names ("Panel N (RxCy)") feeds into the
    target_guid, which is the dedup key.
    """
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    overlap = max(0.0, min(0.99, float(overlap_pct) / 100.0))
    stride_w = (fov_w_arcmin / 60.0) * (1.0 - overlap)
    stride_h = (fov_h_arcmin / 60.0) * (1.0 - overlap)
    R = math.radians(rot_deg or 0)
    cosR, sinR = math.cos(R), math.sin(R)
    cosD = max(1e-6, math.cos(math.radians(dec_c)))
    panels: list[dict] = []
    for i in range(rows):
        for j in range(cols):
            cx = (j - (cols - 1) / 2.0) * stride_w
            cy = ((rows - 1) / 2.0 - i) * stride_h
            de = cx * cosR + cy * sinR
            dn = -cx * sinR + cy * cosR
            panels.append({
                "row": i, "col": j,
                "ra_deg": ra_c + de / cosD,
                "dec_deg": dec_c + dn,
            })
    return panels


def _plan_label(plan: dict) -> str:
    """How to name one ACP plan in a message the user has to act on.

    The plan id first, because the messages that use this are about two plans
    that share a target name, where naming them by target name would say
    nothing.
    """
    plan_id = (plan.get("id") or "").strip()
    if plan_id:
        return repr(plan_id)
    target_name = ((plan.get("target") or {}).get("name") or "").strip()
    return repr(target_name) if target_name else "an unnamed plan"


def _group_plans_by_project(plans_list: list[dict]) -> dict[str, list[dict]]:
    """Mirrors the grouping rule in app.py:_build_ts_export."""
    groups: dict[str, list[dict]] = {}
    for pl in plans_list:
        explicit = (pl.get("project_name") or "").strip()
        if explicit:
            pname = explicit
        else:
            tg = pl.get("target") or {}
            pname = (tg.get("name") or "").strip() or pl.get("id") or "Untitled"
        groups.setdefault(pname, []).append(pl)
    return groups


def _project_constraints(group: list[dict]) -> tuple[float, int, str, str, int]:
    """(min_alt, meridian, priority, state, minimum_time_min) for a project
    group, each resolved by the group-consistency rule in section 1 of
    docs/specs/ts-project-settings.md."""
    min_alt = max(float(p.get("min_altitude_deg") or 0) for p in group)
    merid_vals = [float(p.get("meridian_window_min") or 0) for p in group]
    nonzero = [v for v in merid_vals if v > 0]
    meridian = int(min(nonzero)) if nonzero else 0
    pri_name = max(
        group,
        key=lambda p: PRIORITY_RANK.get(p.get("priority", "normal"), 1),
    ).get("priority", "normal")
    state_name = min(
        (_normalize_state(p.get("state")) for p in group),
        key=lambda s: _STATE_RANK.get(s, 0),
    )
    min_time = max(int(p.get("minimum_time_min") or 0) for p in group)
    return min_alt, meridian, pri_name, state_name, min_time


#: ACP plan field -> TS project column, for the two shooting-order settings
#: (goal-panel-and-ts-order.md section 4). Absent on a plan means unknown.
ORDER_SETTINGS = (
    ("filter_switch_frequency", "filterswitchfrequency"),
    ("smart_exposure_order", "smartexposureorder"),
)


def _order_setting_value(field: str, value) -> int | None:
    """A plan's value for one order setting as the TS integer, or None when
    the plan has none. A switch frequency outside 0 to 99 reads as none."""
    if value is None:
        return None
    if field == "smart_exposure_order":
        return 1 if value is True or value == 1 else 0
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 99 and n == value else None


def _group_order_settings(pname: str, group: list[dict]) -> tuple[dict[str, int | None], list[str]]:
    """The switch frequency and smart order for a project group, by TS
    column, and a push result line for each one the plans disagree on.

    Both settings belong to the TS project. When plans in one project
    disagree, the first plan in plans.json order that has a value wins
    (goal-panel-and-ts-order.md section 4). None means no plan has one, so
    the writers never write it on update."""
    out: dict[str, int | None] = {}
    notes: list[str] = []
    for field, col in ORDER_SETTINGS:
        values = [
            (p.get("id"), v) for p in group
            if (v := _order_setting_value(field, p.get(field))) is not None
        ]
        out[col] = values[0][1] if values else None
        if len({v for _, v in values}) > 1:
            label = "filter switch frequency" if col == "filterswitchfrequency" else "smart order"
            shown = values[0][1] if col == "filterswitchfrequency" else ("on" if values[0][1] else "off")
            notes.append(
                f"{pname}: plans disagree on the {label}, so the push used {shown} "
                f"from plan {values[0][0]}, the first in the list."
            )
    return out, notes


def _group_base_project(group: list[dict]) -> dict | None:
    """The first plan in the group carrying a base snapshot's project block,
    representative of what this rig last saw for the whole group. `None`
    means no signal at all: section 5's "no base" rule then leaves state,
    priority and minimumtime untouched on an update."""
    for p in group:
        snap = (p.get("ts_base_snapshot") or {}).get("project")
        if snap:
            return snap
    return None


def build_payload(
    plans_list: list[dict],
    gear_data: dict,
    *,
    profile_id: str,
    now: datetime | None = None,
    wheel_slots: list[str] | None = None,
    profile_name: str | None = None,
) -> SyncPayload:
    """Convert ACP plan model into the SyncPayload upsert.py applies.

    *now* is the moment the AAC freshness rule is judged at (section 6);
    the real time when left out. It must carry a time zone.

    *wheel_slots* are the slot names of the NINA profile's filter wheel. A
    camera filter that carries gear templates is written by the gear
    template writer (gear-templates.md section 8), which writes each
    template's filtername as the slot and skips a filter that has none. An
    empty list, or None, means the wheel is unknown there: existing
    templates are updated except their filtername, and a missing one is not
    inserted. A filter with no gear templates is written as before, and
    ignores the wheel. *profile_name* is for the notes."""
    telescopes_by_id = {t["id"]: t for t in gear_data.get("telescopes", [])}
    cameras_by_id = {c["id"]: c for c in gear_data.get("cameras", [])}
    payload = SyncPayload(profile_id=profile_id, profile_name=profile_name)
    profile_label = (profile_name or "").strip() or "this profile"
    wheel_known = bool(wheel_slots) and any(isinstance(s, str) and s.strip() for s in wheel_slots)
    gear_work_by_key: dict[str, GearWork] = {}
    # (camera, filter) to the rows skipped because the filter is not on the
    # wheel, in the order met.
    off_wheel_rows: dict[tuple[str, str], list[str]] = {}
    off_wheel_owner: dict[tuple[str, str], tuple[str, str]] = {}
    payload.aac_now = now if now is not None else datetime.now().astimezone()
    now_unix = int(time.time())

    seen_template_names: set[str] = set()
    template_guid_by_name: dict[str, str] = {}
    # Target identity to the ACP plan that produced it, so a collision can say
    # which two plans need looking at rather than just that one happened.
    plan_id_by_target_guid: dict[str, str] = {}

    for pname, group in _group_plans_by_project(plans_list).items():
        min_alt, meridian, pri_name, state_name, min_time = _project_constraints(group)
        base_proj = _group_base_project(group)
        order, order_notes = _group_order_settings(pname, group)
        payload.notes.extend(order_notes)

        write_state = write_priority = write_min_time = False
        # The two order settings follow the same rule (section 4 of
        # goal-panel-and-ts-order.md): written on update only when the
        # group has a value and it differs from the base. A base from before
        # they were recorded reads as 0 and off, because ACP could not set
        # either then.
        write_order = {
            col: base_proj is not None and val is not None
            and int(base_proj.get(col) or 0) != val
            for col, val in order.items()
        }
        if base_proj is not None:
            base_state = _STATE_NAME.get(int(base_proj.get("state", 1)), "active")
            base_priority = _PRIORITY_NAME.get(int(base_proj.get("priority", 1)), "normal")
            base_min_time = int(base_proj.get("minimumtime") or 0)
            write_state = base_state != state_name
            write_priority = base_priority != pri_name
            write_min_time = base_min_time != min_time

        skip_cols: set[str] = set()
        if not write_state:
            skip_cols.add("state")
        if not write_priority:
            skip_cols.add("priority")
        if not write_min_time:
            skip_cols.add("minimumtime")
        for col, write in write_order.items():
            if not write:
                skip_cols.add(col)
        # activedate/inactivedate only move when this push is actually the
        # one writing the state that earns them (section 4).
        if not (write_state and state_name == "active"):
            skip_cols.add("activedate")
        if not (write_state and state_name in ("inactive", "closed")):
            skip_cols.add("inactivedate")

        proj = Project(
            profileId=profile_id,
            name=pname,
            guid=project_guid(profile_id, pname),
            state=_STATE_TO_INT[state_name],
            priority=PRIORITY_RANK.get(pri_name, 1),
            createdate=now_unix,
            activedate=now_unix if state_name == "active" else None,
            inactivedate=now_unix if state_name in ("inactive", "closed") else None,
            minimumaltitude=min_alt,
            meridianwindow=meridian,
            minimumtime=min_time,
            # On insert, ACP's value, or 0 and off when the plan has none.
            filterswitchfrequency=order["filterswitchfrequency"] or 0,
            smartexposureorder=order["smartexposureorder"] or 0,
        )
        payload.projects.append(proj)
        payload.project_update_skip_cols_by_guid[proj.guid] = frozenset(skip_cols)
        payload.rule_weights_by_project_guid[proj.guid] = [
            RuleWeight(projectid=0, name=name, weight=w)
            for name, w in DEFAULT_RULE_WEIGHTS.items()
        ]
        payload.targets_by_project_guid.setdefault(proj.guid, [])
        aac = payload.aac_by_project_guid.setdefault(proj.guid, AacProject(name=pname))
        watermarks: list[str] = []

        for pl in group:
            tg = pl.get("target") or {}
            ra_deg = float(tg.get("center_ra_deg") or 0)
            dec_deg = float(tg.get("center_dec_deg") or 0)
            rot_deg = float(tg.get("rotation_deg") or 0)
            telescope = telescopes_by_id.get(pl.get("telescope_id") or "")
            camera = cameras_by_id.get(pl.get("camera_id") or "")
            fov_w, fov_h = _fov_arcmin(telescope, camera)
            mosaic = tg.get("mosaic") or {"rows": 1, "cols": 1, "overlap_pct": 0}
            rows = max(1, int(mosaic.get("rows") or 1))
            cols = max(1, int(mosaic.get("cols") or 1))
            overlap = float(mosaic.get("overlap_pct") or 0)

            cam_id = (camera or {}).get("id") or ""
            for name in ((camera or {}).get("filters") or {}):
                payload.camera_filters.add(canonical_filter_name(name))
            if camera is not None:
                _register_gear_templates(payload, camera, profile_id, wheel_slots, wheel_known, gear_work_by_key)
            plan_rows = fr.rows_of(pl, camera)
            pushed = [r for r in plan_rows if fr.desired(r) > 0]
            plan_aac = fr.plan_aac(pl)
            if plan_aac is None:
                aac.blocked = aac.blocked or f"{_plan_label(pl)} has no AAC figures yet"
            else:
                watermarks.append(plan_aac["filed_through_night"])
            if isinstance(pl.get("aac"), dict) or any(fr.aac_block(r) is not None for r in plan_rows):
                aac.linked = True

            # One template per name per profile. The push looks each one up
            # by name and inserts it only when TS has none
            # (goal-panel-and-ts-order.md section 3, "Templates"). A filter
            # that carries gear templates is written by the gear template
            # writer instead, one template per gear template
            # (gear-templates.md section 8).
            owner = _row_owner(pl)
            usable: list[dict] = []
            gear_of: dict[str, GearWork] = {}
            for row in pushed:
                gear_templates = (((camera or {}).get("filters") or {}).get(row["filter"]) or {}).get("templates")
                if isinstance(gear_templates, list) and gear_templates:
                    found = resolve_gear_template(gear_templates, row)
                    work = gear_work_by_key.get(f"{cam_id}|{found.get('id')}") if found else None
                    if work is None:
                        payload.notes.append(
                            f"{owner} {row['filter']}: template '{row.get('template')}' is not in gear, row skipped")
                        continue
                    if work.off_wheel:
                        key = (cam_id, row["filter"])
                        if key not in off_wheel_rows:
                            off_wheel_rows[key] = []
                            off_wheel_owner[key] = ((camera or {}).get("name") or "The camera", row["filter"])
                        off_wheel_rows[key].append(f"{owner} {row['filter']}")
                        continue
                    work.row_labels.append(f"{owner} {row['filter']}")
                    gear_of[row["id"]] = work
                    usable.append(row)
                    continue
                usable.append(row)
                tpl_uuid = row_template_guid(profile_id, row["filter"], cam_id, row["template"])
                if row["template"] in seen_template_names:
                    continue
                seen_template_names.add(row["template"])
                filt_cfg = ((camera or {}).get("filters") or {}).get(row["filter"]) or {}
                payload.templates.append(ExposureTemplate(
                    profileId=profile_id,
                    name=row["template"],
                    filtername=row["filter"],
                    guid=tpl_uuid,
                    defaultexposure=fr.sub_s(row),
                    gain=int(filt_cfg.get("gain", -1)),
                    offset=int(filt_cfg.get("offset", -1)),
                    bin=int(filt_cfg.get("bin", 1)),
                ))
                template_guid_by_name[row["template"]] = tpl_uuid

            link = link_for(pl, profile_id) or {}
            refs = link.get("refs") if isinstance(link.get("refs"), dict) else {}
            base = link.get("base_snapshot") if isinstance(link.get("base_snapshot"), dict) else {}
            pinned_targets = refs.get("target_ids_by_panel") or {}
            pinned_plans = refs.get("exposure_plan_ids") or {}
            # Rows derived from filter_goals (an ACP before filter rows) never
            # move an existing plan to another template.
            derived = not isinstance(pl.get("filter_rows"), list)
            base_plans = base.get("exposure_plans_by_panel_filter") or {}
            base_targets = base.get("targets_by_panel") or {}
            first_of_filter: set[str] = set()
            seen_filters: set[str] = set()
            for row in plan_rows:
                if row["filter"] not in seen_filters:
                    seen_filters.add(row["filter"])
                    first_of_filter.add(row["id"])

            panels = _mosaic_panel_centers(
                ra_deg, dec_deg, fov_w, fov_h, rot_deg, rows, cols, overlap
            ) if (rows > 1 or cols > 1) and fov_w > 0 and fov_h > 0 else [
                {"row": 0, "col": 0, "ra_deg": ra_deg, "dec_deg": dec_deg}
            ]

            base_name = tg.get("name") or pl.get("id") or "Untitled"
            if not base_name.strip():
                raise PayloadError(
                    f"The plan {_plan_label(pl)} has a target with no name, so Target "
                    "Scheduler would have nothing to show and the next push nothing to "
                    "match. Give the target a name and push again. Nothing was written."
                )
            multi = len(panels) > 1
            if multi:
                aac.blocked = aac.blocked or f"{_plan_label(pl)} is a mosaic, and AAC's counts are per row"

            for panel in panels:
                if multi:
                    panel_idx = panel["row"] * cols + panel["col"] + 1
                    suffix = f" Panel {panel_idx} (R{panel['row']+1}C{panel['col']+1})"
                else:
                    suffix = ""
                t_name = f"{base_name}{suffix}"
                t_guid = target_guid(profile_id, pname, t_name)
                # Two plans in one project with the same target name used to
                # produce two payload entries under one guid, which collapsed
                # into a single row while the log reported both as loaded.
                # Refuse instead, and say which plans need renaming.
                if t_guid in plan_id_by_target_guid:
                    raise PayloadError(
                        f"The plans {plan_id_by_target_guid[t_guid]} and "
                        f"{_plan_label(pl)} both produce the target {t_name!r} in the "
                        f"project {pname!r}, so one would overwrite the other. Rename "
                        "one of them, or put them in different projects, and push "
                        "again. Nothing was written."
                    )
                plan_id_by_target_guid[t_guid] = _plan_label(pl)
                target = Target(
                    projectid=0,  # filled in by upsert.py
                    name=t_name,
                    guid=t_guid,
                    ra=panel["ra_deg"] / 15.0,  # TS stores RA in hours
                    dec=panel["dec_deg"],
                    rotation=rot_deg,
                )
                payload.targets_by_project_guid[proj.guid].append(target)

                plan_list = payload.plans_by_target_guid.setdefault(t_guid, [])
                panel_key = f"{panel['row'] + 1},{panel['col'] + 1}"
                pinned_tid = pinned_targets.get(panel_key)
                # The base order is the target's, which is the anchor panel's
                # (1,1): ACP's order has changed when it differs from that,
                # for every panel. Each panel's base rows are its own
                # (section 5).
                anchor_target = base_targets.get("1,1")
                anchor_order = anchor_target.get("exposure_order") if isinstance(anchor_target, dict) else None
                seen_target = base_targets.get(panel_key)
                if isinstance(anchor_order, list):
                    payload.order_by_target_guid[t_guid] = TargetOrder(
                        base_order=[str(r) for r in anchor_order],
                        base_rows=as_rows(seen_target.get("override_rows")) if isinstance(seen_target, dict) else None,
                        known_rows=known_rows_of_base(anchor_target, base_plans),
                    )
                for row in usable:
                    plan_uuid = row_plan_guid(profile_id, t_guid, row["filter"], row["template"])
                    acquired = fr.acquired_frames(row)
                    wanted = fr.desired_for(row, pl)
                    row_work = gear_of.get(row["id"])
                    row_sub = fr.sub_s(row)
                    # A row on a gear template inserts its plan on the
                    # template's default (-1) when it follows it, and with
                    # its own sub when it deviates, so TS never shoots a sub
                    # other than the row's.
                    follows = row_work is not None and _same_sub(row_sub, _num(row_work.template.get("sub_s")))
                    plan_list.append(ExposurePlan(
                        profileId=profile_id,
                        targetid=0,
                        exposureTemplateId=0,
                        guid=plan_uuid,
                        exposure=-1.0 if row_work is None or follows else float(row_sub),
                        desired=wanted,
                        acquired=acquired,
                        accepted=acquired,
                    ))
                    counted = fr.aac_accepted(row, pl, wanted)
                    if counted is None:
                        aac.blocked = aac.blocked or f"{fr.row_label(row)} has no AAC figures yet"
                    else:
                        aac.accepted_by_plan_guid[plan_uuid] = counted
                    payload.template_guid_by_plan_guid[plan_uuid] = (
                        row_work.guid if row_work is not None else template_guid_by_name[row["template"]])
                    old = [exposure_plan_guid(profile_id, t_guid, row["template"])]
                    if row["id"] in first_of_filter:
                        old.append(exposure_plan_guid(profile_id, t_guid, row["filter"]))
                    pin = pinned_plans.get(f"{pinned_tid}_{row['id']}") if pinned_tid is not None else None
                    seen = base_plans.get(f"{panel_key}_{row['id']}") or {}
                    lookup = PlanLookup(
                        label=f"{t_name} {fr.row_label(row)}",
                        pinned_id=int(pin) if isinstance(pin, int) and not isinstance(pin, bool) else None,
                        old_guids=old,
                        template_changed=False,
                        row_id=row["id"],
                        in_base=seen.get("enabled") == 1,
                    )
                    if row_work is None:
                        lookup.template_changed = not derived and bool(seen.get("template")) and (
                            not fr.same_filter(seen.get("filter"), row["filter"])
                            or seen.get("template") != row["template"])
                    else:
                        lookup.gear_mode = True
                        lookup.row_sub_s = float(row_sub)
                        lookup.row_follows = follows
                        seen_sub = _num(seen.get("template_sub_s"))
                        lookup.row_sub_changed = seen_sub is not None and not _same_sub(seen_sub, row_sub)
                        lookup.template_changed = _gear_row_moved(seen, row_work.template, profile_id)
                    payload.plan_lookup_by_guid[plan_uuid] = lookup

                # Rows removed in ACP: their exposure plans are disabled,
                # never deleted, and only when they carry ACP's stamp.
                if pinned_tid is not None:
                    # A row with a goal of 0 pushes no plan, so its plan is
                    # disabled as if the row were gone.
                    row_ids = {r["id"] for r in pushed}
                    prefix = f"{pinned_tid}_"
                    for key, plan_id in pinned_plans.items():
                        if not str(key).startswith(prefix) or not isinstance(plan_id, int):
                            continue
                        row_id = str(key)[len(prefix):]
                        if row_id in row_ids:
                            continue
                        seen = base_plans.get(f"{panel_key}_{row_id}") or {}
                        if not seen.get("filter") or not seen.get("template"):
                            continue
                        payload.removed_by_target_guid.setdefault(t_guid, []).append(RemovedPlan(
                            plan_id=plan_id,
                            guids=frozenset({
                                row_plan_guid(profile_id, t_guid, seen["filter"], seen["template"]),
                                exposure_plan_guid(profile_id, t_guid, seen["template"]),
                                exposure_plan_guid(profile_id, t_guid, seen["filter"]),
                            }),
                        ))

        aac.filed_through_night = min(watermarks) if watermarks else None
        if not aac.accepted_by_plan_guid:
            aac.blocked = aac.blocked or "no row has AAC figures yet"

    # The rows a filter that is not on the wheel kept out, said once per
    # camera and filter (gear-templates.md section 5).
    for key, rows_here in off_wheel_rows.items():
        camera_name, filt = off_wheel_owner[key]
        n = len(rows_here)
        payload.notes.append(
            f"{camera_name}: {filt} is not on the wheel of {profile_label} yet, "
            f"so {n} {'row was' if n == 1 else 'rows were'} skipped: {', '.join(rows_here)}.")

    return payload


# -- Gear templates (gear-templates.md section 8) ----------------------------


def _num(value) -> float | None:
    return fr._num(value)


def _same_sub(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9


def _row_owner(plan: dict) -> str:
    """The plan's target name for a note, the plan id when it has none."""
    name = str((plan.get("target") or {}).get("name") or "").strip()
    return name or str(plan.get("id") or "").strip() or "Untitled"


def _register_gear_templates(
    payload: SyncPayload, camera: dict, profile_id: str, wheel_slots, wheel_known: bool,
    work_by_key: dict[str, GearWork],
) -> None:
    """Add the camera's gear templates to the payload's work list, once per
    camera, in gear order. A filter is on the wheel when the profile's slots
    hold one for it. The template is registered either way: the gear template
    writer reports it, and skips it when the filter is off the wheel."""
    cam_id = camera.get("id")
    filters = camera.get("filters")
    if not cam_id or not isinstance(filters, dict):
        return
    for filt, cfg in filters.items():
        templates = (cfg or {}).get("templates") if isinstance(cfg, dict) else None
        if not isinstance(templates, list):
            continue
        slot = None
        off_wheel = False
        if wheel_known:
            slot = resolve_to_wheel(filt, wheel_slots)
            off_wheel = slot is None
        for tpl in templates:
            if not isinstance(tpl, dict) or not tpl.get("id"):
                continue
            key = f"{cam_id}|{tpl['id']}"
            if key in work_by_key:
                continue
            work = GearWork(
                camera_id=cam_id, camera_name=camera.get("name"), filter=filt, template=tpl,
                slot=slot, off_wheel=off_wheel, guid=gear_template_guid(profile_id, tpl["id"]),
            )
            work_by_key[key] = work
            payload.gear_works.append(work)
            payload.templates.append(ExposureTemplate(
                profileId=profile_id,
                name=tpl.get("name"),
                filtername=slot or "",
                guid=work.guid,
                defaultexposure=_num(tpl.get("sub_s")) or 60.0,
                gain=int(tpl.get("gain", -1)),
                offset=int(tpl.get("offset", -1)),
                bin=int(tpl.get("bin", 1)),
            ))


def resolve_gear_template(templates: list, row: dict) -> dict | None:
    """The gear template a row uses: its template_id, else its template
    name, else an alias or a ts_names entry. None when none matches, and then
    the row is skipped with a note. There is no fallback to the starred
    template."""
    templates = [t for t in templates if isinstance(t, dict)]
    tid = row.get("template_id")
    if tid:
        for t in templates:
            if t.get("id") == tid:
                return t
    name = row.get("template")
    if not name:
        return None
    for t in templates:
        if t.get("name") == name:
            return t
    for t in templates:
        if name in (t.get("aliases") or []):
            return t
    for t in templates:
        if name in (t.get("ts_names") or []):
            return t
    return None


def _gear_row_moved(seen: dict, template: dict, profile_id: str) -> bool:
    """Whether ACP moved the row to another template since the last push
    (gear-templates.md section 8, "Which template a plan is on"). The base
    entry's template_id is compared with the row's. A base from before gear
    templates has the template's name only: the row is unchanged when that
    name is one the template is known by. No base entry at all is not a
    change."""
    if not seen:
        return False
    seen_id = seen.get("template_id")
    if seen_id:
        return seen_id != template.get("id")
    seen_name = seen.get("template")
    if not seen_name:
        return False
    if seen_name == template.get("name"):
        return False
    if seen_name in (template.get("aliases") or []) or seen_name in (template.get("ts_names") or []):
        return False
    link = _link_of(template, profile_id)
    base_name = ((link or {}).get("base") or {}).get("name")
    return not (base_name is not None and seen_name == base_name)


def _link_of(template: dict, profile_id: str) -> dict | None:
    ts = template.get("ts")
    if not isinstance(ts, dict):
        return None
    for pid, link in ts.items():
        if isinstance(pid, str) and pid.lower() == profile_id.lower() and isinstance(link, dict):
            return link
    return None
