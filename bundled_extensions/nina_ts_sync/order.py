"""The shooting order, as Target Scheduler reads it from override rows.

docs/specs/goal-panel-and-ts-order.md section 5. TS has no column for plan
order: a target's enabled exposure plans in `Id` order are its natural
order, and `overrideexposureorderitem` rows, when a target has any, say what
it shoots instead. Each row is an (action, referenceIdx) pair: action 0 is
an exposure of the enabled plan at that index in natural order, action 1 a
dither with referenceIdx -1 (Database/Schema/OverrideExposureOrderItem.cs:
12-14).

The plugin's `TsOrder` mirrors this module. Both writers have to produce the
same rows.
"""
from __future__ import annotations

EXPOSURE = 0
DITHER = 1

Rows = list[tuple[int, int]]


_INT_MAX = 2**31 - 1


def _as_int(value) -> int | None:
    """*value* when it is a stored integer (not a bool, a float or a
    string) that fits 32 bits, as the plugin's reader requires; else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if -_INT_MAX - 1 <= value <= _INT_MAX else None


def as_rows(raw) -> Rows | None:
    """A stored list of [action, referenceIdx] pairs as tuples, or None
    when *raw* is not such a list. A token that is not a pair of integers
    makes the whole list absent, the same as in the plugin's `AsRows`."""
    if not isinstance(raw, list):
        return None
    out: Rows = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        action, ref = _as_int(item[0]), _as_int(item[1])
        if action is None or ref is None:
            return None
        out.append((action, ref))
    return out


def effective_plan_order(enabled: list[int], rows: Rows) -> tuple[list[int], list[int]]:
    """The plans of one target in the order TS shoots them, and the plans
    it never shoots.

    *enabled* is the target's enabled plan ids in natural order. With
    override rows, the order is each plan's first exposure row; a plan no
    row names is never shot (PlanningTarget.cs:80-97) and goes at the end.
    Without, it is the natural order."""
    if not rows:
        return list(enabled), []
    out: list[int] = []
    for action, ref in rows:
        if action == EXPOSURE and 0 <= ref < len(enabled) and enabled[ref] not in out:
            out.append(enabled[ref])
    missing = [p for p in enabled if p not in out]
    return out + missing, missing


def inject_dithers(items: Rows, filter_of: list[str], dither_every: int) -> Rows:
    """Dither rows inserted by Target Scheduler's own rule,
    `DitherInjector.Inject` (Planning/DitherInjector.cs:31-72, the count in
    FindNextDither at 124-141).

    A dither goes before the item where any filter has been seen
    `dither_every + 1` times since the last dither, with the list treated
    as a circle: the first item is put on the end while walking and taken
    off again after. *filter_of* gives the filter name of each referenceIdx,
    which is how TS counts (the plan's template filter name)."""
    if dither_every <= 0 or not items:
        return list(items)
    work = list(items) + [items[0]]
    unique = list(dict.fromkeys(filter_of[r] for a, r in work if a == EXPOSURE))
    out: Rows = []
    pos = 0
    while pos < len(work):
        counts = dict.fromkeys(unique, 0)
        found = -1
        for i in range(pos, len(work)):
            action, ref = work[i]
            if action == EXPOSURE:
                name = filter_of[ref]
                counts[name] += 1
                if counts[name] == dither_every + 1:
                    found = i
                    break
        if found < 0:
            out.extend(work[pos:])
            break
        out.extend(work[pos:found])
        out.append((DITHER, -1))
        pos = found
    out.pop()
    return out


def known_rows_of_base(entry, plans_by_panel_filter) -> list[str]:
    """The ACP row ids a base knew at the last link or push: `known_rows`,
    which includes rows with no plan (goal 0) and disabled ones. A base from
    before `known_rows` falls back to the ids in `exposure_order` and in
    `exposure_plans_by_panel_filter` ("<panel>_<row id>" keys), which is
    every row that had a plan."""
    raw = entry.get("known_rows") if isinstance(entry, dict) else None
    if isinstance(raw, list):
        return [str(i) for i in raw]
    out = [str(i) for i in ((entry or {}).get("exposure_order") or [])] if isinstance(entry, dict) else []
    if isinstance(plans_by_panel_filter, dict):
        for key in plans_by_panel_filter:
            _, _, row_id = str(key).partition("_")
            if row_id:
                out.append(row_id)
    return list(dict.fromkeys(out))


def same_order(acp: list[str], base: list[str], known: list[str] | None = None) -> bool:
    """Whether ACP's order equals the base's, over the rows both have, and
    no row new since the base sits before one the base has.

    A row removed since the base is not a change of order: its plan is
    disabled. A row added at the end is not one either: its plan is
    appended. A row added anywhere else is the user placing it, so it is a
    reorder (goal-panel-and-ts-order.md section 5). "Added" means a row id
    the base did not know at all: *known* lists every id the base knew,
    including rows with no plan, so a goal raised from 0 keeps its place
    without counting. Without *known*, only the base order's own ids are
    known."""
    base_set = set(base)
    known_set = base_set | set(known or ())
    acp_set = set(acp)
    common = acp_set & base_set
    if [r for r in acp if r in common] != [r for r in base if r in common]:
        return False
    last = max((i for i, r in enumerate(acp) if r in common), default=-1)
    return not any(r not in known_set for r in acp[:last + 1])
