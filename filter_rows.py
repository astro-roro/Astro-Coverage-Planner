"""A plan's filter rows: the ordered list of goals that replaced `filter_goals`.

See docs/specs/goal-panel-and-ts-order.md section 3. Each row names a filter,
a Target Scheduler exposure template and a goal, in hours or in frames. The
list order is the shooting order. "R at 180 s" and "R Stars at 15 s" are two
rows of one filter.

Pure functions over plan dicts, so the migration, the validator and the
derived `filter_goals` view can be tested without the Flask app. The filter
vocabulary comes in as a `canon` callable (the scanner's `canon_filter`),
because importing the scanner pulls in numpy and astropy.
"""
from __future__ import annotations

import copy
import logging
import math
import uuid
from typing import Callable

# The drop-down order and the migration's row order. Filters outside this
# list come after it, by name.
FILTER_ORDER = ("L", "R", "G", "B", "Ha", "OIII", "SII")

UNITS = ("h", "fr")

# A sub shorter than this is a star or calibration-style row, counted in
# frames rather than hours.
FRAME_UNIT_BELOW_S = 60

Canon = Callable[[str], "str | None"]


def filter_sort_key(name: str) -> tuple[int, str]:
    try:
        return (FILTER_ORDER.index(name), "")
    except ValueError:
        return (len(FILTER_ORDER), name)


def rows_of(plan: dict | None) -> list[dict]:
    """The plan's rows, skipping anything that is not a row object."""
    rows = (plan or {}).get("filter_rows")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _num(value) -> float | None:
    """float(value) for a real finite number, else None. A string is not a
    number here, whatever it spells."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def sub_s(row: dict) -> float:
    """The row's sub length, 300 when it has none, as the writers read it."""
    v = _num(row.get("template_sub_s"))
    return v if v and v > 0 else 300.0


def desired(row: dict) -> int:
    """How many frames the writers ask Target Scheduler for (section 3).

    0 means the row has no goal and pushes no exposure plan.
    """
    if row.get("unit") == "fr":
        frames = _num(row.get("target_frames")) or 0
        return max(0, int(frames))
    hours = _num(row.get("target_hours")) or 0
    if hours <= 0:
        return 0
    return max(1, math.ceil(hours * 3600.0 / sub_s(row)))


def goal_hours(row: dict) -> float:
    """The row's goal in hours, converting a frame goal with its sub."""
    if row.get("unit") == "fr":
        frames = _num(row.get("target_frames")) or 0
        return frames * sub_s(row) / 3600.0
    return _num(row.get("target_hours")) or 0.0


def hours_by_filter(plan: dict | None) -> dict[str, dict]:
    """Goal and actual hours per filter, summed over the filter's rows.

    The one place readers that want a total per filter (coverage, the live
    page, plan matching) get it from.
    """
    out: dict[str, dict] = {}
    for row in rows_of(plan):
        name = str(row.get("filter") or "").strip()
        if not name:
            continue
        entry = out.setdefault(name, {"target_hours": 0.0, "actual_hours": 0.0})
        entry["target_hours"] += goal_hours(row)
        entry["actual_hours"] += _num(row.get("actual_hours")) or 0.0
    return out


def filter_goals_view(plan: dict | None) -> dict[str, dict]:
    """The `filter_goals` object GET /api/plans still serves to older plugins.

    Built from the first row of each filter and never stored. An older plugin
    pushes one exposure plan per filter, so it gets each filter's first hour
    row; a filter whose only rows are frame rows gets its first frame row in
    hours. Star rows therefore reach Target Scheduler only once the new
    plugin is installed.
    """
    first_hour: dict[str, dict] = {}
    first_any: dict[str, dict] = {}
    for row in rows_of(plan):
        name = str(row.get("filter") or "").strip()
        if not name:
            continue
        first_any.setdefault(name, row)
        if row.get("unit") != "fr":
            first_hour.setdefault(name, row)
    out: dict[str, dict] = {}
    for name, any_row in first_any.items():
        row = first_hour.get(name, any_row)
        entry = {
            "target_hours": round(goal_hours(row), 4),
            "sub_exposure_s": sub_s(row),
        }
        actual = _num(row.get("actual_hours"))
        if actual is not None:
            entry["actual_hours"] = actual
        out[name] = entry
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_rows(rows, camera: dict | None, canon: Canon) -> str | None:
    """The `filter_rows` checks `_validate_plan_payload` makes (section 3).

    Assigns an id to a row that arrives without one. Returns an error string,
    or None when the rows are fine.
    """
    if not isinstance(rows, list):
        return "filter_rows must be a list"
    camera_filters = None
    if isinstance(camera, dict) and isinstance(camera.get("filters"), dict) and camera["filters"]:
        names = [str(k) for k in camera["filters"]]
        camera_filters = set(names) | {canon(n) or n for n in names}
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for i, row in enumerate(rows):
        where = f"filter_rows[{i}]"
        if not isinstance(row, dict):
            return f"{where} must be an object"
        rid = row.get("id")
        if rid is None or rid == "":
            rid = row["id"] = str(uuid.uuid4())
        if not isinstance(rid, str):
            return f"{where}.id must be a string"
        if rid in seen_ids:
            return f"two rows share the id {rid!r}"
        seen_ids.add(rid)
        filt = row.get("filter")
        if not isinstance(filt, str) or not filt.strip():
            return f"{where}.filter must be a non-empty string"
        template = row.get("template")
        if not isinstance(template, str) or not template.strip():
            return f"{where}.template must be a non-empty string"
        pair = (filt, template)
        if pair in seen_pairs:
            return f"two rows have filter {filt!r} and template {template!r}"
        seen_pairs.add(pair)
        if camera_filters is not None and filt not in camera_filters and (canon(filt) or filt) not in camera_filters:
            return f"{where}.filter {filt!r} is not a filter on this plan's camera"
        sub = row.get("template_sub_s")
        sub_v = _num(sub)
        if isinstance(sub, bool) or sub_v is None or sub_v <= 0:
            return f"{where}.template_sub_s must be a number above 0"
        unit = row.get("unit")
        if unit not in UNITS:
            return f"{where}.unit must be \"h\" or \"fr\""
        if unit == "h":
            if "target_hours" not in row:
                return f"{where}.target_hours is required when unit is \"h\""
            th = row["target_hours"]
            th_v = _num(th)
            if isinstance(th, bool) or th_v is None or th_v < 0:
                return f"{where}.target_hours must be a number of at least 0"
        else:
            if "target_frames" not in row:
                return f"{where}.target_frames is required when unit is \"fr\""
            tf = row["target_frames"]
            if isinstance(tf, bool) or not isinstance(tf, (int, float)) or not math.isfinite(tf) \
                    or tf < 0 or int(tf) != tf:
                return f"{where}.target_frames must be a whole number of at least 0"
        if "actual_hours" in row and row["actual_hours"] is not None:
            ah = row["actual_hours"]
            ah_v = _num(ah)
            if isinstance(ah, bool) or ah_v is None or ah_v < 0:
                return f"{where}.actual_hours must be a number of at least 0"
    return None


# ---------------------------------------------------------------------------
# The one-shot migration from filter_goals (section 3)
# ---------------------------------------------------------------------------


def known_filter(name: str | None, canon: Canon, known: set[str]) -> str | None:
    """The canonical filter a goal key names, or None.

    `canon_filter` maps aliases ("Lum", "Antlia Ha") but passes a key such as
    "R Stars" through unchanged, so a key the vocabulary does not know is
    tried again on its first word: "R Stars" is filter R, "Ha 3nm" is Ha.
    """
    if not name:
        return None
    whole = canon(name)
    if whole in known:
        return whole
    first = str(name).strip().split()
    if len(first) > 1:
        head = canon(first[0])
        if head in known:
            return head
    return None


def _usual_template(key: str, camera: dict | None) -> str:
    """The template name the old writer pushed for a canonical goal key."""
    cfg = ((camera or {}).get("filters") or {}).get(key) or {}
    name = cfg.get("ts_template_name") if isinstance(cfg, dict) else None
    if name:
        return name
    if camera and camera.get("name"):
        return f"{key} ({camera['name']})"
    return key


def _row_from_goal(filt: str, template: str, goal: dict) -> dict:
    raw_sub = _num(goal.get("sub_exposure_s"))
    sub = raw_sub if raw_sub and raw_sub > 0 else 300
    if float(sub).is_integer():
        sub = int(sub)
    hours = _num(goal.get("target_hours")) or 0.0
    row: dict = {
        "id": str(uuid.uuid4()),
        "filter": filt,
        "template": template,
        "template_sub_s": sub,
    }
    if sub < FRAME_UNIT_BELOW_S:
        # The old writer divided by the whole-second sub, floored at 1.
        div = max(1, int(sub))
        row["unit"] = "fr"
        row["target_frames"] = max(1, math.ceil(hours * 3600.0 / div)) if hours > 0 else 0
    else:
        row["unit"] = "h"
        row["target_hours"] = hours
    if "actual_hours" in goal:
        row["actual_hours"] = goal["actual_hours"]
    if isinstance(goal.get("aac"), dict):
        row["aac"] = copy.deepcopy(goal["aac"])
    return row


def _sub_row_entries(goal: dict) -> list[tuple[str, dict]]:
    """`sub_rows` from an interim build, as (key, entry) pairs. Accepts the
    object form keyed by template and the list form with a name field."""
    raw = goal.get("sub_rows")
    out: list[tuple[str, dict]] = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                out.append((str(k), v))
    elif isinstance(raw, list):
        for v in raw:
            if not isinstance(v, dict):
                continue
            k = v.get("key") or v.get("template") or v.get("name")
            if k:
                out.append((str(k), v))
    return out


def migrate_plan(plan: dict, camera: dict | None, canon: Canon, known: set[str]) -> dict[str, str]:
    """Turn one plan's `filter_goals` into `filter_rows`, in place.

    Returns the old goal key to new row id map, for rekeying `ts_links`.
    A plan with `filter_rows` already, or no `filter_goals`, is left alone
    apart from dropping a stray `filter_goals`.
    """
    goals = plan.get("filter_goals")
    if isinstance(plan.get("filter_rows"), list):
        plan.pop("filter_goals", None)
        return {}
    if not isinstance(goals, dict):
        plan.pop("filter_goals", None)
        plan["filter_rows"] = []
        return {}

    # (filter, from a canonical key, template, row, old key)
    staged: list[tuple[str, bool, str, dict, str]] = []
    for key, goal in goals.items():
        if not isinstance(goal, dict):
            continue
        key = str(key)
        filt = known_filter(key, canon, known)
        if filt is not None and filt == key:
            template, canonical = _usual_template(key, camera), True
        elif filt is not None:
            template, canonical = key, False
        else:
            logging.warning(
                "plan %r: goal %r names no known filter, kept as its own filter",
                plan.get("id"), key,
            )
            filt, template, canonical = key, key, True
        staged.append((filt, canonical, template, _row_from_goal(filt, template, goal), key))
        for sub_key, entry in _sub_row_entries(goal):
            staged.append((filt, False, sub_key, _row_from_goal(filt, sub_key, entry), sub_key))

    # Two goals that land on one filter and template would make two rows the
    # validator refuses. The first keeps the pair; a later one keeps its key
    # as the template, which is what the old writer named it by.
    used: set[tuple[str, str]] = set()
    for i, (filt, canonical, template, row, key) in enumerate(staged):
        if (filt, template) in used and template != key:
            row["template"] = template = key
            staged[i] = (filt, canonical, template, row, key)
        used.add((filt, template))

    staged.sort(key=lambda s: (filter_sort_key(s[0]), not s[1], s[2]))
    plan["filter_rows"] = [s[3] for s in staged]
    plan.pop("filter_goals", None)
    return {s[4]: s[3]["id"] for s in staged}


def _rekey(mapping: dict, sep: str, id_by_key: dict[str, str], extra: Callable[[str, dict], None] | None = None) -> dict:
    """Rewrite "<prefix><sep><goal key>" keys to "<prefix><sep><row id>"."""
    out = {}
    for k, v in mapping.items():
        prefix, found, key = str(k).partition(sep)
        if found and key in id_by_key:
            new_key = f"{prefix}{sep}{id_by_key[key]}"
            if extra is not None and isinstance(v, dict):
                v = dict(v)
                extra(id_by_key[key], v)
            out[new_key] = v
        else:
            out[k] = v
    return out


def rekey_links(plan: dict, id_by_key: dict[str, str]) -> None:
    """Move every per-goal key in `ts_links`, `ts_refs` and `ts_base_snapshot`
    onto the row ids, so the next push still finds its exposure plans and a
    base entry still describes the same row. Each moved base entry records
    the row's filter and template, so the writers see no change of template
    and leave `exposure` alone."""
    if not id_by_key:
        return
    rows_by_id = {r["id"]: r for r in rows_of(plan)}

    def note_row(row_id: str, entry: dict) -> None:
        row = rows_by_id.get(row_id) or {}
        entry.setdefault("filter", row.get("filter"))
        entry.setdefault("template", row.get("template"))

    def fix_refs(refs) -> None:
        if not isinstance(refs, dict):
            return
        if isinstance(refs.get("exposure_plan_ids"), dict):
            refs["exposure_plan_ids"] = _rekey(refs["exposure_plan_ids"], "_", id_by_key)
        if isinstance(refs.get("template_ids_by_filter"), dict):
            refs["template_ids_by_filter"] = {
                id_by_key.get(k, k): v for k, v in refs["template_ids_by_filter"].items()
            }

    def fix_base(base) -> None:
        if not isinstance(base, dict):
            return
        if isinstance(base.get("exposure_plans_by_panel_filter"), dict):
            base["exposure_plans_by_panel_filter"] = _rekey(
                base["exposure_plans_by_panel_filter"], "_", id_by_key, note_row)
        if isinstance(base.get("templates_by_filter"), dict):
            base["templates_by_filter"] = {
                id_by_key.get(k, k): v for k, v in base["templates_by_filter"].items()
            }

    links = plan.get("ts_links")
    if isinstance(links, dict):
        for link in links.values():
            if isinstance(link, dict):
                fix_refs(link.get("refs"))
                fix_base(link.get("base_snapshot"))
    fix_refs(plan.get("ts_refs"))
    fix_base(plan.get("ts_base_snapshot"))
