"""Three-way field diff between BASE / LOCAL (ACP) / REMOTE (TS).

Pure functions, no I/O. Caller (api.py) supplies the three plan dicts;
this module decides per field which of the four cases applies and bundles
the result into a `PlanDiff`.

Per-plan resolution model: if *any* field is in the conflict quadrant,
the whole plan is conflicted. The user picks `take_acp` / `take_ts` /
`skip` for the plan as a whole.

Tolerance:
    angle  fields → 0.001°
    hour   fields → 0.001h
    count  fields → exact integer
    string fields → exact string

`actual_hours` is deliberately not in the field schema — TS is the
canonical source for it, refreshed continuously by /sync-acquired.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .order import known_rows_of_base

# (path, kind, default). `kind` drives the equality function. `default` is
# used in place of `_MISSING` whenever the owning dict is present but
# doesn't carry the key, because for that field absence itself is a value
# ("state" absent means "active", "minimum_time_min" absent means 0), not
# "no opinion". See the default handling below in `diff_plan`. Most fields
# leave it out: for them, absence really does mean "this side has no
# opinion".
_PLAN_FIELDS: list[tuple[str, str] | tuple[str, str, str]] = [
    # Names are diffed because plans are matched by link and by guid, not by
    # name, so a project or target renamed in TS arrives as a TS-side change.
    ("project_name", "string"),
    ("target.name", "string"),
    ("priority", "string"),
    ("min_altitude_deg", "angle"),
    ("meridian_window_min", "count"),
    # The four TS values: draft, active, inactive, closed. "parked", the
    # name PR #97 used for what is now "inactive", is normalised by the
    # "state" kind below so an old plan still carrying it reads as noise
    # against an inactive TS project.
    ("state", "state", "active"),
    ("minimum_time_min", "count", 0),
    # The shooting-order settings (goal-panel-and-ts-order.md section 4).
    # Absent on a plan means unknown, which takes TS's value: see
    # _UNKNOWN_TAKES_TS below.
    ("filter_switch_frequency", "count"),
    ("smart_exposure_order", "flag"),
    ("target.center_ra_deg", "angle"),
    ("target.center_dec_deg", "angle"),
    ("target.rotation_deg", "angle"),
    ("target.mosaic.rows", "count"),
    ("target.mosaic.cols", "count"),
    ("target.mosaic.overlap_pct", "count"),
]

# Plan fields where an ACP plan without the key does not know the value, as
# opposed to having no opinion. The editor shows an unknown value as the
# default here, so a TS value other than that is brought in whatever the
# base says, and a plan made before ACP could set it learns TS's value on
# the next upload. A TS value equal to the default is no change.
_UNKNOWN_TAKES_TS: dict[str, Any] = {"filter_switch_frequency": 0, "smart_exposure_order": False}

# Per-row fields under filter_rows.<row id> (goal-panel-and-ts-order.md
# section 3). Rows are compared by id. The unit is how ACP shows a goal, not
# a value, so a TS or base row is first put in the local row's unit and only
# the goal itself is compared.
_PER_ROW_FIELDS: list[tuple[str, str]] = [
    ("filter", "string"),
    ("template", "string"),
    ("target_hours", "hours"),
    ("target_frames", "count"),
    ("template_sub_s", "seconds"),
    # actual_hours: NOT diffed.
]

ROWS = "filter_rows"

#: The plan's shooting order: the ids of its rows in list order
#: (goal-panel-and-ts-order.md section 5). Not a stored field: the local
#: and remote values are read off `filter_rows`, the base's from the base
#: snapshot's `exposure_order` for panel 1,1.
ORDER = "exposure_order"
#: The shaped base's `known_rows`: every ACP row id known when the order was
#: last recorded (see `order.known_rows_of_base`).
KNOWN = "known_rows"

ANGLE_TOL = 0.001  # degrees
HOUR_TOL = 0.001   # hours
SECOND_TOL = 0.001  # seconds

# Sentinel — distinguishes "field absent from this dict" from "field is None".
_MISSING = object()


@dataclass
class PlanDiff:
    plan_id: str
    auto_pull: list[str] = field(default_factory=list)  # field paths to take from REMOTE
    auto_keep: list[str] = field(default_factory=list)  # field paths LOCAL changed; will push
    conflict: list[str] = field(default_factory=list)   # field paths both changed
    base_values: dict[str, Any] = field(default_factory=dict)
    local_values: dict[str, Any] = field(default_factory=dict)
    remote_values: dict[str, Any] = field(default_factory=dict)
    # True when there was no base snapshot and the no-base rule made every
    # difference a conflict ("no sync record for this rig").
    no_base: bool = False

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflict)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "auto_pull": list(self.auto_pull),
            "auto_keep": list(self.auto_keep),
            "conflict": list(self.conflict),
            "base":   dict(self.base_values),
            "local":  dict(self.local_values),
            "remote": dict(self.remote_values),
            "no_base": self.no_base,
        }


def split_path(path: str) -> list[str]:
    """Split a dotted field path. A row id can hold a dot (a row derived
    from a goal key such as "Ha 3.5nm"), so filter_rows paths split into
    exactly three parts."""
    if path.startswith(ROWS + "."):
        rest = path[len(ROWS) + 1:]
        if "." in rest:
            key, sub = rest.rsplit(".", 1)
            return [ROWS, key, sub]
        return [ROWS, rest]
    return path.split(".")


def rows_by_id(plan: dict | None) -> dict[str, dict]:
    rows = (plan or {}).get(ROWS)
    if not isinstance(rows, list):
        return {}
    return {str(r["id"]): r for r in rows if isinstance(r, dict) and r.get("id")}


def _get(d: dict | None, path: str) -> Any:
    """Walk a dotted path. Missing or non-dict on the way → _MISSING.
    filter_rows is a list, walked by row id."""
    if d is None:
        return _MISSING
    cur: Any = d
    parts = split_path(path)
    if parts[0] == ROWS:
        cur = rows_by_id(d)
        parts = parts[1:]
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def in_unit(row: dict, unit: str | None) -> dict:
    """A copy of *row* with its goal in *unit*, converted with its own sub.

    Frames to hours uses the writer's inverse, so the hours come back to the
    same `desired`; hours to frames is the writer's `desired`.
    """
    if unit not in ("h", "fr") or row.get("unit") == unit or row.get("unit") not in ("h", "fr"):
        return row
    from .from_ts import _hours_for_desired
    from .rows import desired, sub_s
    out = dict(row)
    if unit == "fr":
        out["target_frames"] = desired(row)
        out.pop("target_hours", None)
    else:
        out["target_hours"] = _hours_for_desired(desired(row), sub_s(row))
        out.pop("target_frames", None)
    out["unit"] = unit
    return out


def align_units(local: dict | None, other: dict | None) -> dict | None:
    """*other* with each row that the local plan also has put in the local
    row's unit. Returns a copy; *other* is not changed."""
    if other is None or local is None:
        return other
    local_rows = rows_by_id(local)
    rows = other.get(ROWS)
    if not isinstance(rows, list):
        return other
    out = dict(other)
    out[ROWS] = [
        in_unit(r, (local_rows.get(str(r.get("id"))) or {}).get("unit")) if isinstance(r, dict) else r
        for r in rows
    ]
    return out


def _eq(kind: str, a: Any, b: Any) -> bool:
    """Tolerance-aware equality. Both _MISSING → equal (consistent absence)."""
    if a is _MISSING and b is _MISSING:
        return True
    if a is _MISSING or b is _MISSING:
        return False
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if kind == "angle":
        try:
            return abs(float(a) - float(b)) <= ANGLE_TOL
        except (TypeError, ValueError):
            return a == b
    if kind == "hours":
        try:
            return abs(float(a) - float(b)) <= HOUR_TOL
        except (TypeError, ValueError):
            return a == b
    if kind == "seconds":
        try:
            return abs(float(a) - float(b)) <= SECOND_TOL
        except (TypeError, ValueError):
            return a == b
    if kind == "count":
        try:
            return int(a) == int(b)
        except (TypeError, ValueError):
            return a == b
    if kind == "flag":
        return bool(a) == bool(b)
    if kind == "state":
        # "parked" is PR #97's old name for what is now "inactive": a plan
        # still carrying it (a hand edit, or an older import) reads the
        # same as "inactive" here, never as a difference on its own.
        norm = lambda v: "inactive" if str(v) == "parked" else str(v)
        return norm(a) == norm(b)
    return a == b  # string, fallback


def _classify(base: Any, local: Any, remote: Any, kind: str) -> str:
    """Return 'unchanged' | 'auto_pull' | 'auto_keep' | 'conflict'.

    `_MISSING` means "this side doesn't carry an opinion on this field" —
    treat it as a non-change, never as a conflict trigger. Practical
    examples: ACP-side `target.mosaic.overlap_pct` absent on a single-
    panel plan, or BASE-side `mosaic.overlap_pct` not stored in the
    snapshot.
    """
    # LOCAL has no opinion — defer to REMOTE.
    if local is _MISSING:
        if remote is _MISSING:
            return "unchanged"
        return "unchanged" if _eq(kind, base, remote) else "auto_pull"

    # REMOTE has no opinion — keep LOCAL.
    if remote is _MISSING:
        return "unchanged" if _eq(kind, base, local) else "auto_keep"

    # Both sides have a value. If BASE is missing (e.g. the snapshot
    # doesn't store this field) we can't tell whether either side
    # "changed" — fall back to local vs remote: agreement = unchanged,
    # disagreement = auto_pull (REMOTE wins absent history).
    if base is _MISSING:
        return "unchanged" if _eq(kind, local, remote) else "auto_pull"

    base_eq_local = _eq(kind, base, local)
    base_eq_remote = _eq(kind, base, remote)
    if base_eq_local and base_eq_remote:
        return "unchanged"
    if base_eq_local and not base_eq_remote:
        return "auto_pull"
    if not base_eq_local and base_eq_remote:
        return "auto_keep"
    # Both differ from base. If LOCAL == REMOTE despite differing from BASE,
    # both ended up at the same value — already in sync, no conflict.
    if _eq(kind, local, remote):
        return "auto_keep"
    return "conflict"


def _filters_in(plan: dict | None) -> set[str]:
    """The row ids a plan holds."""
    return set(rows_by_id(plan))


def diff_plan(
    plan_id: str,
    base: dict | None,
    local: dict,
    remote: dict | None,
    *,
    no_base_is_conflict: bool = False,
    skip_prefixes: tuple[str, ...] = (),
) -> PlanDiff:
    """Compute the three-way diff for one plan.

    *local* must always be supplied (the current ACP plan dict). *base*
    is None when no prior snapshot exists (first sync). *remote* is None
    when the TS row was deleted.

    First-sync heuristic: when base is None, treat LOCAL ≠ REMOTE as
    auto_pull rather than conflict, since there's no editing history yet, so
    we can't say either side "changed". Practical effect: importing onto
    fresh ACP plans (or freshly-pushed plans) does the right thing.

    ``no_base_is_conflict`` turns that heuristic off, for uploads. With no
    base, any difference is a conflict and the diff is marked ``no_base``,
    because an ACP edit made since the last push would otherwise be
    overwritten without a word. A side that has no value for a field has no
    opinion on it, except for a whole filter present on one side only.

    ``skip_prefixes`` leaves out every path starting with one of them. The
    upload merge uses it for an ACP mosaic whose panels came back from TS as
    separate plans, where the target fields of one panel say nothing about
    the mosaic.
    """
    out = PlanDiff(plan_id=plan_id)
    base = align_units(local, base)
    remote = align_units(local, remote)
    first_sync = base is None
    strict = first_sync and no_base_is_conflict
    local_filters = _filters_in(local)
    remote_filters = _filters_in(remote)

    def _first_sync_class(path: str, kind: str, l: Any, r: Any) -> str:
        if _eq(kind, l, r):
            return "unchanged"
        if not strict:
            return "auto_pull"
        if l is _MISSING or r is _MISSING:
            parts = split_path(path)
            if parts[0] == ROWS and len(parts) == 3:
                row_id = parts[1]
                if (row_id in local_filters) != (row_id in remote_filters):
                    return "conflict"
            return "unchanged"
        return "conflict"

    # Plan-level fields.
    # Pre-compute rows×cols on both sides so we can short-circuit
    # mosaic.overlap_pct for 1×1 plans — overlap is geometrically meaningless
    # when there's no second panel, and the noise (ACP defaults to 15, the
    # importer defaults to 0) would otherwise flood every single-target plan
    # into "incoming auto-pull". See bug reported 2026-05-11.
    def _mosaic_dim(d, axis):
        v = _get(d, f"target.mosaic.{axis}")
        try:
            return int(v) if v is not _MISSING else 1
        except (TypeError, ValueError):
            return 1
    local_is_single = _mosaic_dim(local, "rows") == 1 and _mosaic_dim(local, "cols") == 1
    remote_is_single = _mosaic_dim(remote, "rows") == 1 and _mosaic_dim(remote, "cols") == 1

    for entry in _PLAN_FIELDS:
        path, kind = entry[0], entry[1]
        default = entry[2] if len(entry) > 2 else None
        if path == "target.mosaic.overlap_pct" and local_is_single and remote_is_single:
            continue
        if skip_prefixes and path.startswith(skip_prefixes):
            continue
        b = _get(base, path)
        l = _get(local, path)
        r = _get(remote, path)
        if default is not None:
            # Absence is a value here, not "no opinion", but only when the
            # side itself exists. A genuinely absent side (no base snapshot
            # yet) is still _MISSING, so first-sync handling is unchanged.
            if base is not None and b is _MISSING:
                b = default
            if local is not None and l is _MISSING:
                l = default
            if remote is not None and r is _MISSING:
                r = default
        if path in _UNKNOWN_TAKES_TS and l is _MISSING:
            unknown_default = _UNKNOWN_TAKES_TS[path]
            cls = ("unchanged" if r is _MISSING or _eq(kind, r, unknown_default)
                   else "auto_pull")
        else:
            if path in _UNKNOWN_TAKES_TS and base is not None and b is _MISSING:
                # A base from before these were recorded: ACP could not set
                # them then, so it held the default (as a missing state reads
                # as active in ts-project-settings.md section 3).
                b = _UNKNOWN_TAKES_TS[path]
            cls = _classify(b, l, r, kind) if not first_sync else _first_sync_class(path, kind, l, r)
        if cls == "unchanged":
            continue
        getattr(out, cls).append(path)
        out.base_values[path]   = None if b is _MISSING else b
        out.local_values[path]  = None if l is _MISSING else l
        out.remote_values[path] = None if r is _MISSING else r

    # Per-row fields. Use the union of row ids across all three so adds and
    # drops show up too.
    filters = _filters_in(base) | _filters_in(local) | _filters_in(remote)
    for fname in sorted(filters):
        for sub, kind in _PER_ROW_FIELDS:
            path = f"{ROWS}.{fname}.{sub}"
            if skip_prefixes and path.startswith(skip_prefixes):
                continue
            b = _get(base, path)
            l = _get(local, path)
            r = _get(remote, path)
            cls = _classify(b, l, r, kind) if not first_sync else _first_sync_class(path, kind, l, r)
            if cls == "unchanged":
                continue
            getattr(out, cls).append(path)
            out.base_values[path]   = None if b is _MISSING else b
            out.local_values[path]  = None if l is _MISSING else l
            out.remote_values[path] = None if r is _MISSING else r

    # The order, as the list of row ids both sides hold. A row added or
    # removed on one side is a row change, diffed above, not a change of
    # order, except that a row added in ACP anywhere but the end of the list
    # is the user placing it, so it counts as ACP's reorder (the same rule as
    # `order.same_order`; "added" means an id the base's `known_rows` lacks,
    # so a goal raised from 0 is not one). A base with no `exposure_order` reads as ACP's
    # own order, as a missing state reads as active: ACP could not set an
    # order before, so any difference is a change made in TS.
    if remote is not None and not (skip_prefixes and ORDER.startswith(skip_prefixes)):
        l_ids = [str(r["id"]) for r in (local.get(ROWS) or []) if isinstance(r, dict) and r.get("id")]
        r_ids = [str(r["id"]) for r in (remote.get(ROWS) or []) if isinstance(r, dict) and r.get("id")]
        common = set(l_ids) & set(r_ids)
        raw_base = (base or {}).get(ORDER) if base is not None else None
        if base is not None and isinstance(raw_base, list):
            common &= {str(i) for i in raw_base}
        l = [i for i in l_ids if i in common]
        r = [i for i in r_ids if i in common]
        if base is not None and isinstance(raw_base, list) and not first_sync:
            base_set = {str(i) for i in raw_base}
            raw_known = base.get(KNOWN)
            known_set = base_set | ({str(i) for i in raw_known} if isinstance(raw_known, list) else set())
            r_set = set(r_ids)
            last = max((n for n, i in enumerate(l_ids) if i in base_set), default=-1)
            placed = [i for i in l_ids[:last + 1] if i not in known_set and i not in r_set]
            # Added rows placed before a row the base had stay in the list,
            # so ACP's order differs from the base's.
            if placed:
                l = [i for i in l_ids if i in common or i in placed]
        if first_sync:
            b: Any = _MISSING
            cls = _first_sync_class(ORDER, "order", l, r)
        else:
            b = [str(i) for i in raw_base if str(i) in common] if isinstance(raw_base, list) else list(l)
            cls = _classify(b, l, r, "order")
        if cls != "unchanged":
            getattr(out, cls).append(ORDER)
            out.base_values[ORDER] = None if b is _MISSING else b
            out.local_values[ORDER] = l
            out.remote_values[ORDER] = r

    out.no_base = strict and bool(out.conflict)
    return out


_STATE_NAME = {0: "draft", 1: "active", 2: "inactive", 3: "closed"}


def base_snapshot_to_plan_shape(snap: dict | None) -> dict | None:
    """Project a stored ts_base_snapshot into the ACP-plan shape so
    `diff_plan` can compare it to the LOCAL plan dict directly.

    Prefers the snapshot's `mosaic` block (rows/cols/overlap_pct) when
    present — that's the plan-level mosaic struct captured at push/import
    time. Older snapshots without it fall back to inferring rows/cols from
    the panel-key count and leave overlap_pct missing so the diff can't
    spuriously auto-pull a corrupt inferred value (this is bug #4/#5 from
    FINDINGS-2026-05-11.md — historical context).
    """
    if snap is None:
        return None
    proj = snap.get("project") or {}
    targets = snap.get("targets_by_panel") or {}
    plans_by_pf = snap.get("exposure_plans_by_panel_filter") or {}
    templates = snap.get("templates_by_filter") or {}

    # mosaic block: present on new snapshots (post bug #5 fix), absent on
    # older ones. When absent, fall back to deriving rows/cols from the
    # panel-key shape — overlap_pct stays unset.
    mosaic_snap = snap.get("mosaic")
    if mosaic_snap:
        mosaic_out = {
            "rows": int(mosaic_snap.get("rows") or 1),
            "cols": int(mosaic_snap.get("cols") or 1),
            "overlap_pct": int(mosaic_snap.get("overlap_pct") or 0),
        }
    else:
        keys = [k.split(",") for k in targets.keys()]
        rows = max((int(k[0]) for k in keys), default=1)
        cols = max((int(k[1]) for k in keys), default=1)
        mosaic_out = {"rows": rows, "cols": cols}  # overlap_pct deliberately absent

    # Anchor target (R1C1 if present, else any).
    anchor = targets.get("1,1") or (next(iter(targets.values()), None))
    if anchor is None:
        anchor = {"name": "", "ra": 0.0, "dec": 0.0, "rotation": 0.0}

    # A mosaic's anchor row is "<base> Panel 1 (R1C1)"; the plan holds <base>.
    anchor_name = anchor.get("name") or ""
    if mosaic_out["rows"] > 1 or mosaic_out["cols"] > 1:
        from .from_ts import _split_panel_suffix
        anchor_name = _split_panel_suffix(anchor_name)[0]

    out: dict = {
        "priority": _priority_int_to_name(proj.get("priority")),
        "min_altitude_deg": proj.get("minimumaltitude"),
        "meridian_window_min": proj.get("meridianwindow"),
        "target": {
            "name": anchor_name,
            "center_ra_deg": (anchor.get("ra") or 0) * 15.0,
            "center_dec_deg": anchor.get("dec") or 0,
            "rotation_deg": anchor.get("rotation") or 0,
            "mosaic": mosaic_out,
        },
        "filter_rows": [],
    }
    if proj.get("name"):
        out["project_name"] = proj["name"]
    # Both left absent when the snapshot has no project block at all (or
    # predates this field, for minimum_time_min): the default fill in
    # diff_plan then reads them as "active" and 0.
    proj_state_raw = proj.get("state")
    if proj_state_raw is not None:
        out["state"] = _STATE_NAME.get(int(proj_state_raw), "active")
    proj_min_time = proj.get("minimumtime")
    if proj_min_time is not None:
        out["minimum_time_min"] = int(proj_min_time)
    # Absent on a base from before they were recorded: the diff then has no
    # base for them and falls back to comparing ACP with TS.
    if proj.get("filterswitchfrequency") is not None:
        out["filter_switch_frequency"] = int(proj["filterswitchfrequency"])
    if proj.get("smartexposureorder") is not None:
        out["smart_exposure_order"] = bool(int(proj["smartexposureorder"]))

    # The order the base recorded for the anchor panel, when it has one.
    anchor_order = (targets.get("1,1") or {}).get("exposure_order")
    if isinstance(anchor_order, list):
        out[ORDER] = [str(i) for i in anchor_order]
        out[KNOWN] = known_rows_of_base(targets.get("1,1"), plans_by_pf)

    # Rows from the (1,1) panel's exposure plans, keyed "1,1_<row id>". A
    # base written before rows has no filter or template on an entry, and
    # leaves them out: absent is "no opinion" to the diff.
    from .from_ts import _hours_for_desired
    from .rows import FRAME_UNIT_BELOW_S
    rows = []
    for key, ep in plans_by_pf.items():
        panel, _, row_id = str(key).partition("_")
        if panel != "1,1" or not row_id or not isinstance(ep, dict):
            continue
        tpl = templates.get(row_id) or {}
        sub_s = float(ep.get("template_sub_s") or 0) or float(ep.get("exposure") or 0) \
            or float(tpl.get("defaultexposure") or 0)
        desired = int(ep.get("desired") or 0)
        row: dict = {"id": row_id}
        if ep.get("filter"):
            row["filter"] = ep["filter"]
        if ep.get("template"):
            row["template"] = ep["template"]
        if sub_s > 0:
            row["template_sub_s"] = sub_s
            if sub_s < FRAME_UNIT_BELOW_S:
                row["unit"], row["target_frames"] = "fr", desired
            else:
                row["unit"], row["target_hours"] = "h", _hours_for_desired(desired, sub_s)
        rows.append(row)
    out["filter_rows"] = rows
    return out


_PRIORITY_NAME = {0: "low", 1: "normal", 2: "high"}


def _priority_int_to_name(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return _PRIORITY_NAME.get(int(v), "normal")
    except (TypeError, ValueError):
        return None
