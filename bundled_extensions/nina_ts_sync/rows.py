"""A plan's filter rows, as the writers and the importer read them.

docs/specs/goal-panel-and-ts-order.md section 3. A plan holds `filter_rows`,
an ordered list; each row names a filter, a Target Scheduler template and a
goal in hours ("h") or frames ("fr"). The plugin's `TsRows` mirrors this
module, and both writers have to agree on every number here.

A plan from an ACP that predates filter rows carries `filter_goals` only.
`rows_of` derives rows from it the way ACP's own one-shot migration does,
with each row's id set to its goal key. That keeps a push from the new
plugin against an old ACP finding its exposure plans through `ts_links`,
whose keys are "<target id>_<goal key>", and the migration later moves
those keys onto real row ids.
"""
from __future__ import annotations

import math

from .filter_canon import FILTER_CANON, canonical_filter_name

FILTER_ORDER = ("L", "R", "G", "B", "Ha", "OIII", "SII")
FRAME_UNIT_BELOW_S = 60
DEFAULT_SUB_S = 300.0

_KNOWN = frozenset(FILTER_CANON.values())


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def sub_s(row: dict) -> float:
    """The row's `template_sub_s`, 300 when it has none."""
    v = _num(row.get("template_sub_s"))
    return v if v and v > 0 else DEFAULT_SUB_S


def desired(row: dict) -> int:
    """Frames the writers ask TS for. 0 means the row pushes no plan.

    h: max(1, ceil(target_hours x 3600 / template_sub_s)); fr: target_frames.
    """
    if row.get("unit") == "fr":
        return max(0, int(_num(row.get("target_frames")) or 0))
    hours = _num(row.get("target_hours")) or 0.0
    if hours <= 0:
        return 0
    return max(1, math.ceil(hours * 3600.0 / sub_s(row)))


def aac_block(row: dict) -> dict | None:
    """The row's `aac` block, AAC's figures, or None."""
    b = row.get("aac")
    return b if isinstance(b, dict) else None


def plan_aac(plan: dict | None) -> dict | None:
    """The plan's `aac` block with a usable watermark and floor, or None.

    `filed_through_night` has to be an ISO date and `min_total_frames` a whole
    number of at least 0; anything else reads as no block, so a malformed
    block can never open a write (goal-panel-and-ts-order.md section 6).
    """
    b = (plan or {}).get("aac")
    if not isinstance(b, dict):
        return None
    night, floor = b.get("filed_through_night"), b.get("min_total_frames")
    if not isinstance(night, str) or len(night) != 10:
        return None
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
        return None
    try:
        import datetime
        datetime.date.fromisoformat(night)
    except ValueError:
        return None
    return b


def desired_for(row: dict, plan: dict | None) -> int:
    """`desired` with AAC's floor (section 6): a row that has an `aac` block
    on a plan with one never sits below `min_total_frames`. A row with a goal
    of 0 still pushes no plan."""
    d = desired(row)
    if d <= 0 or aac_block(row) is None:
        return d
    block = plan_aac(plan)
    return max(d, int(block["min_total_frames"])) if block else d


def goal_of(row: dict) -> float:
    """The row's goal in its own unit: hours for "h", frames for "fr"."""
    if row.get("unit") == "fr":
        return float(max(0, int(_num(row.get("target_frames")) or 0)))
    return _num(row.get("target_hours")) or 0.0


def complete_counts(row: dict) -> bool:
    """Whether the row's `aac.complete` still stands. AAC judged it against
    the goal the row had when it sent the figures (`goal_at_send`, in the
    row's unit); a goal raised since means AAC has not seen the new goal, so
    the row reads as incomplete until AAC reports again. A block with no
    `goal_at_send` has nothing to compare and stands."""
    block = aac_block(row)
    if block is None or block.get("complete") is not True:
        return False
    sent = _num(block.get("goal_at_send"))
    if sent is None:
        return True
    return goal_of(row) <= sent + 1e-9


def unfloored_goal(row: dict, plan: dict | None, ts_desired: int) -> int:
    """TS's `desired` read as ACP's goal. AAC's floor lifts what the writer
    sends (`desired_for`), so a `desired` of exactly that is the row's own
    goal, never a change to take; anything else is a real edit in TS."""
    if aac_block(row) is not None and desired(row) > 0 and ts_desired == desired_for(row, plan):
        return desired(row)
    return ts_desired


def unfloored(local: dict | None, remote: dict | None) -> dict | None:
    """*remote* with each row whose `desired` is the floored value of the
    local row it stands for set back to that row's goal (unit and amount).
    Returns *remote* itself when nothing changes, else a copy."""
    if local is None or remote is None:
        return remote
    remote_rows = remote.get("filter_rows")
    if not isinstance(remote_rows, list):
        return remote
    mine = {str(r["id"]): r for r in rows_of(local) if r.get("id")}
    out_rows, changed = [], False
    for r in remote_rows:
        l = mine.get(str(r.get("id"))) if isinstance(r, dict) else None
        if (l is not None and desired_for(l, local) != desired(l)
                and desired(r) == desired_for(l, local)):
            r = dict(r)
            r["unit"] = l.get("unit")
            r.pop("target_hours", None)
            r.pop("target_frames", None)
            key = "target_frames" if l.get("unit") == "fr" else "target_hours"
            r[key] = l.get(key)
            changed = True
        out_rows.append(r)
    if not changed:
        return remote
    return {**remote, "filter_rows": out_rows}


def aac_accepted(row: dict, plan: dict | None, wanted: int) -> int | None:
    """The `accepted` count to write for an AAC-linked row, or None when the
    row has no usable figures. *wanted* is the row's `desired` after the
    floor. Section 6, "The accepted write to TS"."""
    block, plan_block = aac_block(row), plan_aac(plan)
    if block is None or plan_block is None:
        return None
    seconds, frames = _num(block.get("accepted_seconds")), _num(block.get("accepted_frames"))
    if seconds is None or seconds < 0 or frames is None or frames < 0:
        return None
    count = math.floor(seconds / sub_s(row) + 0.000001)
    if complete_counts(row):
        return max(count, wanted)
    short = max(1, int(plan_block["min_total_frames"]) - int(frames))
    return max(0, min(count, wanted - short))


def acquired_frames(row: dict) -> int:
    """The row's `actual_hours` in frames, for an exposure plan's insert."""
    return int(round((_num(row.get("actual_hours")) or 0.0) * 3600.0 / max(1.0, sub_s(row))))


def known_filter(name: str | None) -> str | None:
    """The canonical filter a goal key names, or None. "R Stars" is R: the
    alias table passes it through unchanged, so its first word is tried."""
    if not name:
        return None
    whole = canonical_filter_name(name)
    if whole in _KNOWN:
        return whole
    words = str(name).strip().split()
    if len(words) > 1:
        head = canonical_filter_name(words[0])
        if head in _KNOWN:
            return head
    return None


def row_filter(name: str | None) -> str:
    """A row's filter for a goal key or a TS filtername: the one vocabulary
    the migration, both writers and the importer share
    (tests/fixtures/row_filter_cases.json, copied into ACP and the plugin).
    The known filter it names, else the trimmed name as it is."""
    return known_filter(name) or (name or "").strip()


def same_filter(a: str | None, b: str | None) -> bool:
    """Whether two filter names are the same row filter. A difference that
    only canonicalisation makes ("Antlia Ha" and "Ha" are not one, "H" and
    "Ha" are) is never an edit."""
    return row_filter(a) == row_filter(b)


def _filter_rank(name: str) -> tuple[int, str]:
    return (FILTER_ORDER.index(name), "") if name in FILTER_ORDER else (len(FILTER_ORDER), name)


def _derived_rows(goals: dict, camera: dict | None) -> list[dict]:
    """ACP's migration table, with each row's id set to its goal key."""
    staged = []
    for key, goal in goals.items():
        if not isinstance(goal, dict):
            continue
        key = str(key)
        filt = known_filter(key)
        if filt is not None and filt == key:
            cfg = ((camera or {}).get("filters") or {}).get(key) or {}
            template = cfg.get("ts_template_name") or (
                f"{key} ({camera['name']})" if camera and camera.get("name") else key)
            canonical = True
        elif filt is not None:
            template, canonical = key, False
        else:
            filt, template, canonical = key, key, True
        raw = _num(goal.get("sub_exposure_s"))
        sub = raw if raw and raw > 0 else DEFAULT_SUB_S
        hours = _num(goal.get("target_hours")) or 0.0
        row = {"id": key, "filter": filt, "template": template, "template_sub_s": sub}
        if sub < FRAME_UNIT_BELOW_S:
            row["unit"] = "fr"
            row["target_frames"] = max(1, math.ceil(hours * 3600.0 / max(1, int(sub)))) if hours > 0 else 0
        else:
            row["unit"] = "h"
            row["target_hours"] = hours
        if "actual_hours" in goal:
            row["actual_hours"] = goal["actual_hours"]
        staged.append((_filter_rank(filt), not canonical, template, row))
    staged.sort(key=lambda s: s[:3])
    return [s[3] for s in staged]


def rows_of(plan: dict | None, camera: dict | None = None) -> list[dict]:
    """The plan's filter rows, in shooting order."""
    raw = (plan or {}).get("filter_rows")
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict) and r.get("id")]
    goals = (plan or {}).get("filter_goals")
    if isinstance(goals, dict):
        return _derived_rows(goals, camera)
    return []


def row_label(row: dict) -> str:
    """How a push result names one row: "R Stars", or the filter when the
    template is named after it."""
    filt, template = row.get("filter") or "", row.get("template") or ""
    return template if template and template != filt else filt


def plan_sub_s(exposure, template_default) -> float:
    """The sub TS uses for an exposure plan: its own `exposure` when above 0,
    else its template's default (PlanningExposure.cs:50)."""
    e = _num(exposure)
    if e and e > 0:
        return e
    return _num(template_default) or 0.0


def _subs_by_row(base_snapshot: dict | None) -> dict[str, float]:
    """Each row's `template_sub_s` as a base recorded it, panel 1,1 first,
    else any panel."""
    entries = (base_snapshot or {}).get("exposure_plans_by_panel_filter") or {}
    by_row: dict[str, float] = {}
    for key, entry in entries.items():
        panel, _, row_id = str(key).partition("_")
        sub = _num((entry or {}).get("template_sub_s"))
        if not row_id or not sub or sub <= 0:
            continue
        if panel == "1,1" or row_id not in by_row:
            by_row[row_id] = sub
    return by_row


def refresh_template_subs(
    plan: dict, base_snapshot: dict | None, previous_base: dict | None = None
) -> bool:
    """Set each row's `template_sub_s` to the sub TS uses for its exposure
    plan, as the base just captured it. Panel 1,1 first, else any panel.
    Returns whether anything changed.

    With *previous_base*, the base from before this push, a row is changed
    only when its sub still equals the one that base recorded for it, that
    is, when ACP did not change it since the last push. A row ACP changed
    keeps its value and the push writes it, so the refresh can never undo an
    ACP change (gear-templates.md section 8, "The sub length"). A row the
    previous base has no sub for is changed as before."""
    by_row = _subs_by_row(base_snapshot)
    before = _subs_by_row(previous_base)
    changed = False
    rows = plan.get("filter_rows")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        sub = by_row.get(row.get("id"))
        if sub is None:
            continue
        seen = before.get(row.get("id"))
        mine = _num(row.get("template_sub_s"))
        if seen is not None and mine is not None and abs(mine - seen) > 1e-9:
            continue
        value = int(sub) if float(sub).is_integer() else sub
        if row.get("template_sub_s") != value:
            row["template_sub_s"] = value
            changed = True
    return changed
