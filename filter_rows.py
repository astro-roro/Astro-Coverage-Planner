"""A plan's filter rows: the ordered list of goals that replaced `filter_goals`.

See docs/specs/goal-panel-and-ts-order.md section 3. Each row names a filter,
a Target Scheduler exposure template and a goal, in hours or in frames. The
list order is the shooting order. "R at 180 s" and "R Stars at 15 s" are two
rows of one filter.

Pure functions over plan dicts, so the migration, the validator and the
derived `filter_goals` view can be tested without the Flask app.

A row's filter comes from one vocabulary, `row_filter`, which the TS
sync's writer and importer and the NINA plugin share. It is not the archive
scanner's `canon_filter`: that one strips brand names ("Antlia Ha" is Ha),
the writers do not, and a difference between the two would read as a
change of filter made in ACP. tests/fixtures/row_filter_cases.json is the
table all three test against.
"""
from __future__ import annotations

import copy
import datetime
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

# The alias table the writers use (nina_ts_sync.filter_canon.FILTER_CANON
# and the plugin's TsFilterNames). Keys are matched upper-cased.
ROW_FILTER_ALIASES: dict[str, str] = {
    "H": "Ha", "HA": "Ha", "HALPHA": "Ha", "H-ALPHA": "Ha", "H_ALPHA": "Ha",
    "O": "OIII", "O3": "OIII", "OIII": "OIII", "O-III": "OIII", "O_III": "OIII",
    "S": "SII", "S2": "SII", "SII": "SII", "S-II": "SII", "S_II": "SII",
    "L": "L", "LUM": "L", "LUMINANCE": "L", "LIGHT": "L", "CLEAR": "L",
    "R": "R", "RED": "R",
    "G": "G", "GREEN": "G",
    "B": "B", "BLUE": "B",
    "V": "V", "IR": "IR", "UV": "UV",
    "NOFILTER": "NoFilter", "NO FILTER": "NoFilter", "NO_FILTER": "NoFilter",
    "NONE": "NoFilter", "EMPTY": "NoFilter", "OPEN": "NoFilter",
}
_KNOWN = frozenset(ROW_FILTER_ALIASES.values())


def _safe_log(value) -> str:
    """Strip CR/LF and truncate, as app._safe_log does, for CodeQL's log rule."""
    return str(value).replace("\r", "").replace("\n", "")[:200]


def _alias(name: str) -> str:
    trimmed = (name or "").strip()
    return ROW_FILTER_ALIASES.get(trimmed.upper(), trimmed)


def known_filter(name: str | None) -> str | None:
    """The known filter a goal key or TS filtername names, or None. A name
    the alias table does not know is tried on its first word, so "R Stars"
    is R and "Ha 3nm" is Ha; "Antlia Ha" names no known filter."""
    if not name:
        return None
    whole = _alias(name)
    if whole in _KNOWN:
        return whole
    words = str(name).strip().split()
    if len(words) > 1:
        head = _alias(words[0])
        if head in _KNOWN:
            return head
    return None


def row_filter(name: str | None) -> str:
    """A row's filter: the known filter *name* names, else *name* trimmed."""
    return known_filter(name) or (name or "").strip()


def resolve_to_wheel(name: str | None, slots) -> str | None:
    """The wheel slot a filter is written under, or None when no slot is the
    same filter (docs/specs/gear-templates.md section 2).

    An exact match wins, then a match ignoring case and surrounding spaces,
    then the first slot whose alias-table spelling equals the filter's. A
    spelling the table does not know passes through unchanged, so "R Stars"
    and "Antlia Ha" resolve to nothing. Mirrors `resolve_to_wheel` in the
    sync and `TsFilterNames.ResolveToWheel` in the plugin;
    tests/fixtures/wheel_resolve_cases.json drives all three.
    """
    if name is None or not str(name).strip() or not slots:
        return None
    wanted = str(name)
    names = [s for s in slots if isinstance(s, str) and s.strip()]
    for s in names:
        if s == wanted:
            return s
    low = wanted.strip().lower()
    for s in names:
        if s.strip().lower() == low:
            return s
    spelling = _alias(wanted)
    for s in names:
        if _alias(s) == spelling:
            return s
    return None


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


def plan_aac(plan: dict | None) -> dict | None:
    """The plan's `aac` block when it has a usable watermark and floor, else
    None. `filed_through_night` has to be an ISO date and `min_total_frames` a
    whole number of at least 0 (section 6)."""
    b = (plan or {}).get("aac")
    if not isinstance(b, dict):
        return None
    night, floor = b.get("filed_through_night"), b.get("min_total_frames")
    if not isinstance(night, str) or len(night) != 10:
        return None
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
        return None
    try:
        datetime.date.fromisoformat(night)
    except ValueError:
        return None
    return b


def desired_for(row: dict, plan: dict | None) -> int:
    """`desired` with AAC's floor (section 6): a row that has an `aac` block
    on a plan with one never sits below `min_total_frames`. A row with a goal
    of 0 still pushes no plan. The TS sync writers compute the same number
    (tests/fixtures/aac_accepted_cases.json)."""
    d = desired(row)
    if d <= 0 or not isinstance(row.get("aac"), dict):
        return d
    block = plan_aac(plan)
    return max(d, int(block["min_total_frames"])) if block else d


def goal_hours(row: dict) -> float:
    """The row's goal in hours, converting a frame goal with its sub."""
    if row.get("unit") == "fr":
        frames = _num(row.get("target_frames")) or 0
        return frames * sub_s(row) / 3600.0
    return _num(row.get("target_hours")) or 0.0


def panel_count(plan: dict | None) -> int:
    """How many panels the plan's mosaic has: rows x cols, 1 for a plan that
    is not a mosaic or whose mosaic block is junk. The same clamp the TS
    export uses (`_build_ts_export`), so goals and exported targets agree."""
    mosaic = ((plan or {}).get("target") or {}).get("mosaic")
    if not isinstance(mosaic, dict):
        return 1

    def dim(key) -> int:
        v = mosaic.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return 1
        return max(1, int(v))

    return dim("rows") * dim("cols")


def hours_by_filter(plan: dict | None) -> dict[str, dict]:
    """Goal and actual hours per filter, summed over the filter's rows.

    A row's goal is per panel (the TS export gives every panel the full row
    goal), so for a mosaic `target_hours` here is the row goal times the panel
    count: a 2 x 1 mosaic with an 8 h L goal wants 16 h of L. `actual_hours`
    is the plan's single stored figure and is not scaled.

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
    panels = panel_count(plan)
    if panels > 1:
        for entry in out.values():
            entry["target_hours"] *= panels
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
# Gear templates on rows (docs/specs/gear-templates.md sections 3 and 11)
# ---------------------------------------------------------------------------


def camera_templates(camera: dict | None, filt: str | None,
                     templated: bool | None = None) -> list[dict] | None:
    """The gear templates of *camera* on the filter a row names, or None when
    the camera has no gear templates at all (a gear file not yet migrated, or
    no camera). An empty list means the camera has templates but none for
    this filter. *templated* says whether the gear file is in the new shape;
    left out, it is read from the camera's filters, which cannot tell a
    migrated camera with no filters from an old one."""
    if not isinstance(camera, dict) or not isinstance(camera.get("filters"), dict):
        return None
    filters = camera["filters"]
    if templated is None:
        templated = any(isinstance(v, dict) and isinstance(v.get("templates"), list) for v in filters.values())
    if not templated:
        return None
    name = str(filt or "").strip()
    cfg = filters.get(name)
    if not isinstance(cfg, dict):
        want = row_filter(name)
        cfg = next((v for k, v in filters.items() if row_filter(str(k)) == want and isinstance(v, dict)), None)
    tpls = cfg.get("templates") if isinstance(cfg, dict) else None
    return [t for t in tpls if isinstance(t, dict)] if isinstance(tpls, list) else []


def find_template(tpls: list[dict], name) -> dict | None:
    """The template a row's `template` name stands for: the exact name, then
    an earlier name in `aliases`, then a name in `ts_names`."""
    if not isinstance(name, str):
        return None
    for key in ("name", "aliases", "ts_names"):
        for t in tpls:
            held = t.get(key)
            if (held == name) if key == "name" else (isinstance(held, list) and name in held):
                return t
    return None


def template_names(tpl: dict | None) -> set[str]:
    """Every name a row may still carry for this template."""
    if not isinstance(tpl, dict):
        return set()
    out = {tpl.get("name")} if isinstance(tpl.get("name"), str) else set()
    for key in ("aliases", "ts_names"):
        if isinstance(tpl.get(key), list):
            out.update(n for n in tpl[key] if isinstance(n, str))
    return out


def repair_rows(rows, camera: dict | None, plan_id=None, templated: bool | None = None) -> bool:
    """Give every row its `template_id` (section 3). Returns whether any row
    changed.

    A row whose `template_id` names a template of the camera on its filter
    takes that template's name. Otherwise the id is found from `template` by
    name, then `aliases`, then `ts_names`, with no fallback to the starred
    template. A row nothing matches keeps its name and gets
    `template_unresolved`, and is logged. A camera with no gear templates is
    left alone.
    """
    changed = False
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tpls = camera_templates(camera, row.get("filter"), templated)
        if tpls is None:
            continue
        tid = row.get("template_id")
        tpl = next((t for t in tpls if t.get("id") == tid), None) if tid else None
        if tpl is None:
            tpl = find_template(tpls, row.get("template"))
        if tpl is None:
            if row.get("template_unresolved") is not True:
                logging.warning("plan %r: row %r template %r is not in gear for filter %r",
                                _safe_log(plan_id), _safe_log(row.get("id")),
                                _safe_log(row.get("template")), _safe_log(row.get("filter")))
                row["template_unresolved"] = True
                changed = True
            if "template_id" in row:
                del row["template_id"]
                changed = True
            continue
        if row.get("template_id") != tpl.get("id"):
            row["template_id"] = tpl.get("id")
            changed = True
        if row.get("template") != tpl.get("name"):
            row["template"] = tpl.get("name")
            changed = True
        if "template_unresolved" in row:
            del row["template_unresolved"]
            changed = True
    return changed


def _round4(v: float) -> float:
    return round(v + 0.0, 4)


def set_row_sub(row: dict, new_sub: float) -> None:
    """Move a row to *new_sub*, as the editor's `setRowTemplate` does: the
    goal keeps the hours it stood for when the sub crosses 60 s and the row
    changes unit, and otherwise keeps its own number."""
    hours = goal_hours(row)
    row["template_sub_s"] = int(new_sub) if float(new_sub).is_integer() else new_sub
    unit = "fr" if new_sub < FRAME_UNIT_BELOW_S else "h"
    if unit != row.get("unit"):
        if unit == "fr":
            row["target_frames"] = max(0, math.ceil(hours * 3600.0 / new_sub - 1e-9)) if hours > 0 else 0
            row.pop("target_hours", None)
        else:
            row["target_hours"] = _round4(hours)
            row.pop("target_frames", None)
        row["unit"] = unit


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_rows(rows, camera: dict | None, plan_id=None, templated: bool | None = None) -> str | None:
    """The `filter_rows` checks `_validate_plan_payload` makes (section 3).

    Assigns an id to a row that arrives without one. Returns an error string,
    or None when the rows are fine.
    """
    if not isinstance(rows, list):
        return "filter_rows must be a list"
    camera_filters = None
    if isinstance(camera, dict) and isinstance(camera.get("filters"), dict) and camera["filters"]:
        names = [str(k) for k in camera["filters"]]
        camera_filters = set(names) | {row_filter(n) for n in names}
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
        if camera_filters is not None and filt not in camera_filters and row_filter(filt) not in camera_filters:
            return f"{where}.filter {filt!r} is not a filter on this plan's camera"
        tpls = camera_templates(camera, filt, templated)
        if tpls is not None:
            tid = row.get("template_id")
            if tid is not None:
                tpl = next((t for t in tpls if t.get("id") == tid), None) if isinstance(tid, str) else None
                if tpl is None:
                    return f"{where}.template_id {tid!r} is not a template of this plan's camera on filter {filt!r}"
                if template != tpl.get("name"):
                    return f"{where}.template must equal the name of template_id {tid!r}, {tpl.get('name')!r}"
                row.pop("template_unresolved", None)
            else:
                repair_rows([row], camera, plan_id, templated)
                template = row.get("template")
        pair = (filt, template)
        if pair in seen_pairs:
            return f"two rows have filter {filt!r} and template {template!r}"
        seen_pairs.add(pair)
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


def migrate_plan(plan: dict, camera: dict | None) -> dict[str, str]:
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
        filt = known_filter(key)
        if filt is not None and filt == key:
            template, canonical = _usual_template(key, camera), True
        elif filt is not None:
            template, canonical = key, False
        else:
            logging.warning(
                "plan %r: goal %r names no known filter, kept as its own filter",
                _safe_log(plan.get("id")), _safe_log(key),
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
        # The migrated row's own values, over whatever the plugin recorded:
        # a difference between the two is the migration's naming, never an
        # edit, and must not read as a template change on the next push.
        row = rows_by_id.get(row_id) or {}
        entry["filter"] = row.get("filter")
        entry["template"] = row.get("template")

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


# ---------------------------------------------------------------------------
# What AAC needs from ACP (section 6)
# ---------------------------------------------------------------------------

TARGET_CLASSES = ("survey", "deep", "pretty")
DEFAULT_TARGET_CLASS = "survey"


def target_class_of(plan: dict | None) -> str:
    """A plan's target class; absent or any other value reads as survey."""
    v = (plan or {}).get("target_class")
    return v if v in TARGET_CLASSES else DEFAULT_TARGET_CLASS


def keep_aac(payload: dict, existing: dict | None) -> None:
    """Put AAC's figures back on a plan a client is saving.

    AAC is the only writer of a row's `aac` block and of the plan's `aac`
    block, so whatever a client echoes is replaced by what is stored, matched
    by row id. A row the client added has none, and a row whose filter or
    template differs from the stored row's loses its block: AAC reported it
    for the old pair (section 6).
    """
    stored = {r.get("id"): r for r in rows_of(existing) if isinstance(r.get("aac"), dict)}
    rows = payload.get("filter_rows")
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            r.pop("aac", None)
            old = stored.get(r.get("id"))
            if old is not None and same_identity(old, r):
                r["aac"] = copy.deepcopy(old["aac"])
    payload.pop("aac", None)
    if isinstance(existing, dict) and isinstance(existing.get("aac"), dict):
        payload["aac"] = copy.deepcopy(existing["aac"])


def same_identity(a: dict, b: dict) -> bool:
    """Whether two rows name the same filter and template, the pair AAC's
    figures are reported against."""
    return row_filter(a.get("filter")) == row_filter(b.get("filter")) \
        and a.get("template") == b.get("template")


def goal_of(row: dict) -> float:
    """The row's goal in its own unit: hours for "h", frames for "fr"."""
    if row.get("unit") == "fr":
        return float(max(0, int(_num(row.get("target_frames")) or 0)))
    return _num(row.get("target_hours")) or 0.0


_ACCEPTED_NUMBERS = ("accepted_seconds", "seen_seconds")
_ACCEPTED_COUNTS = ("accepted_frames", "seen_frames")


def _nonneg(v) -> bool:
    n = _num(v)
    return n is not None and n >= 0


def validate_accepted(body) -> str | None:
    """The shape checks for POST /api/plans/<id>/accepted (section 6)."""
    if not isinstance(body, dict):
        return "body must be a JSON object"
    night = body.get("filed_through_night")
    if not isinstance(night, str):
        return "filed_through_night must be a date string such as 2026-10-11"
    try:
        datetime.date.fromisoformat(night)
    except ValueError:
        return "filed_through_night must be a date string such as 2026-10-11"
    floor = body.get("min_total_frames")
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
        return "min_total_frames must be a whole number of at least 0"
    for key in ("source", "at"):
        if body.get(key) is not None and not isinstance(body[key], str):
            return f"{key} must be a string"
    rows = body.get("rows")
    if not isinstance(rows, list):
        return "rows must be a list"
    for i, r in enumerate(rows):
        where = f"rows[{i}]"
        if not isinstance(r, dict):
            return f"{where} must be an object"
        if not isinstance(r.get("id"), str) or not r["id"]:
            return f"{where}.id must be a non-empty string"
        for key in ("filter", "template"):
            if not isinstance(r.get(key), str):
                return f"{where}.{key} must be a string"
        for key in _ACCEPTED_NUMBERS:
            if not _nonneg(r.get(key)):
                return f"{where}.{key} must be a number of at least 0"
        for key in _ACCEPTED_COUNTS:
            v = r.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
                    or v < 0 or int(v) != v:
                return f"{where}.{key} must be a whole number of at least 0"
        if not isinstance(r.get("complete"), bool):
            return f"{where}.complete must be true or false"
        est = r.get("estimated_hours")
        if est is not None and not _nonneg(est):
            return f"{where}.estimated_hours must be null or a number of at least 0"
        meas = r.get("measurement")
        if meas is not None and not isinstance(meas, dict):
            return f"{where}.measurement must be null or an object"
    return None


def apply_accepted(plan: dict, body: dict, received_at: str,
                   canon_filter: Callable[[str], str | None],
                   names_for_row: Callable[[dict], set[str]] | None = None) -> list[dict]:
    """Store AAC's figures on *plan* and return the `unknown_rows`.

    A row is stored only when its id is on the plan, its echoed filter reaches
    the row's filter, and its echoed template equals the row's. A row AAC sent
    that comes back unknown loses its stored block, which was reported for a
    filter and template the row no longer has. Each stored block also records
    `goal_at_send`, the row's goal in its own unit at that moment, so a
    `complete` flag stops counting once the goal is raised. Values may go
    down. `actual_hours` is never touched. The caller has run
    `validate_accepted`. *names_for_row* gives the earlier names a row's
    template went by, so a rename between AAC reading the plans and writing
    back does not drop the block (gear templates spec section 7).
    """
    by_id = {r.get("id"): r for r in rows_of(plan)}
    unknown: list[dict] = []
    for entry in body["rows"]:
        row = by_id.get(entry["id"])
        if row is None:
            unknown.append({"id": entry["id"], "reason": "no such row"})
            continue
        echoed = entry["filter"]
        filter_ok = echoed == row.get("filter") or row_filter(echoed) == row.get("filter") \
            or (canon_filter(echoed) or None) == row.get("filter")
        template_ok = entry["template"] == row.get("template") or (
            names_for_row is not None and entry["template"] in names_for_row(row))
        if not filter_ok or not template_ok:
            row.pop("aac", None)
            unknown.append({"id": entry["id"], "reason":
                            f"row now has filter {row.get('filter')} and template {row.get('template')}"})
            continue
        row["aac"] = {
            "accepted_seconds": float(entry["accepted_seconds"]),
            "accepted_frames": int(entry["accepted_frames"]),
            "seen_seconds": float(entry["seen_seconds"]),
            "seen_frames": int(entry["seen_frames"]),
            "complete": entry["complete"],
            "estimated_hours": entry.get("estimated_hours"),
            "measurement": entry.get("measurement"),
            "goal_at_send": goal_of(row),
        }
    plan["aac"] = {
        "filed_through_night": body["filed_through_night"],
        "min_total_frames": body["min_total_frames"],
        "received_at": received_at,
    }
    return unknown
