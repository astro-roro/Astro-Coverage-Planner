"""TS rows → ACP plan dicts.

Inverse of convert.py. The interesting bit is **mosaic reconstruction**:
ACP stores a mosaic as ``{rows, cols, overlap_pct}`` on one plan; TS stores
N×M individual Target rows. To round-trip, we have to detect that the N
targets in a project actually form a regular grid.

Detection is **geometric**, not name-based. We project each target into a
local tangent plane around the group centroid, rotate into the camera
frame using each target's ``rotation`` (every panel of a mosaic shares the
same rotation), and look for a regular rows×cols pattern with uniform
stride. This works regardless of:
    - whether the panels were named with ACP's "Panel N (RxCy)" suffix
    - whether someone renamed them in TS's UI
    - whether the mosaic was built in NINA's Framing Assistant directly

Name suffixes are still parsed when present — they give us a clean base
name for the resulting ACP plan. When absent we use the longest common
prefix of the panel target names, falling back to the project name.

A detected grid only becomes an ACP mosaic when some telescope and camera in
ACP's gear list rebuilds it exactly: same panel names, same centres. ACP
writes panels from gear and overlap, so any other grid would be renamed and
moved on the next push. Those panels come in as separate plans instead, with
a loss note.

Round-trip caveats (documented in the README):
    - Targets within a project that don't form a regular grid get emitted
      as single-target ACP plans, so a project mixing one mosaic and one
      single target arrives as several ACP plans, not one.
    - Plans that are not a rebuilt mosaic get the first telescope and camera
      in the gear list. TS does not record gear, and a single target's
      position does not depend on it.
    - Anything the import cannot carry exactly (a mosaic kept as panels, a
      plan repeating a filter and template) is reported as a note at level "loss" naming
      the project and target.
    - A TS project's ``state``, ``priority`` and ``minimumtime`` columns
      carry into ACP as the plan's own ``state``, ``priority`` and
      ``minimum_time_min``. State is always written explicitly, as one of
      the four TS values: draft, active, inactive or closed.
"""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass

from .convert import _fov_arcmin, _mosaic_panel_centers
from .order import effective_plan_order
from .reader import TSSnapshot
from .rows import FRAME_UNIT_BELOW_S, plan_sub_s, row_filter
from .schema import ExposurePlan, ExposureTemplate, Project, Target

# Matches the suffix convert.py stamps: " Panel 4 (R2C2)".
_PANEL_RE = re.compile(r"^(?P<base>.+?) Panel \d+ \(R(?P<row>\d+)C(?P<col>\d+)\)$")

PRIORITY_NAMES = {0: "low", 1: "normal", 2: "high"}


@dataclass
class ImportNote:
    level: str  # "info" | "warn" | "loss"
    message: str
    project: str = ""  # the TS project the note is about
    target: str = ""  # the TS target, or a mosaic's base name, where there is one

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "message": self.message,
            "project": self.project,
            "target": self.target,
        }


def _split_panel_suffix(name: str) -> tuple[str, int | None, int | None]:
    """Return (base_name, 1-indexed row, 1-indexed col), or (name, None, None)."""
    m = _PANEL_RE.match(name)
    if not m:
        return name, None, None
    return m.group("base"), int(m.group("row")), int(m.group("col"))


def _common_base_name(targets: list[Target], project_name: str) -> str:
    """Pick a clean base name for a reconstructed mosaic plan.

    Preference order: panel-suffix base if present and consistent →
    longest common prefix of names → project name.
    """
    suffix_bases = set()
    for t in targets:
        base, r, c = _split_panel_suffix(t.name)
        if r is not None and c is not None:
            suffix_bases.add(base)
    if len(suffix_bases) == 1:
        return next(iter(suffix_bases))

    names = [t.name for t in targets]
    prefix = names[0]
    for n in names[1:]:
        i = 0
        while i < min(len(prefix), len(n)) and prefix[i] == n[i]:
            i += 1
        prefix = prefix[:i]
    prefix = prefix.rstrip(" -_")
    return prefix or project_name


def _angular_sep_deg(t1: Target, t2: Target) -> float:
    """Approximate angular separation (deg) between two targets.

    Small-angle flat-sky approximation — accurate to ~0.1% for separations
    under 5°, more than enough for the clustering threshold. Doesn't handle
    RA wrap at 0/360h, but ACP mosaics are tiny relative to the wrap point.
    """
    avg_dec = (t1.dec + t2.dec) / 2.0
    cosD = math.cos(math.radians(avg_dec))
    d_ra_deg = abs((t1.ra - t2.ra) * 15.0) * cosD
    d_dec = abs(t1.dec - t2.dec)
    return math.hypot(d_ra_deg, d_dec)


def _cluster_targets_spatially(
    targets: list[Target], max_link_deg: float
) -> list[list[Target]]:
    """Group targets by spatial proximity using single-link clustering.

    Two targets are linked if their angular separation is below
    ``max_link_deg``; clusters are the connected components of that graph.
    Chain-linking means a 4×3 mosaic with 25′ stride and a 1.5° link
    threshold still merges into one cluster even though opposite corners
    are >1.5° apart.

    Returned in input order of each cluster's first target.
    """
    n = len(targets)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _angular_sep_deg(targets[i], targets[j]) < max_link_deg:
                union(i, j)

    groups: dict[int, list[Target]] = {}
    for i, t in enumerate(targets):
        groups.setdefault(find(i), []).append(t)
    return list(groups.values())


def _detect_grid(
    targets: list[Target],
    *,
    tol_arcmin: float = 2.0,
) -> tuple[int, int, dict[tuple[int, int], Target]] | None:
    """Detect a regular rows×cols grid in target positions.

    Projects each target's (RA, Dec) into a local tangent plane around
    the group centroid, rotates by ``-rotation_deg`` (assumes all panels
    share the same rotation, which a mosaic does by construction), then
    bins by Y to find rows and verifies each row has the same column count
    with uniform stride.

    Returns ``(rows, cols, {(row, col): target})`` 1-indexed where row 1 is
    north and col 1 is west, matching convert.py's ACP→TS convention. None
    if the points don't form a regular grid.
    """
    if len(targets) < 2:
        return None

    # Tangent-plane projection around the centroid. RA is stored in hours
    # in TS — multiply by 15 to get degrees, then by cos(dec) for the
    # local east/west scaling.
    avg_dec = sum(t.dec for t in targets) / len(targets)
    cosD = max(1e-6, math.cos(math.radians(avg_dec)))
    avg_ra_deg = sum(t.ra * 15.0 for t in targets) / len(targets)
    rot_rad = math.radians(targets[0].rotation or 0)
    cosR = math.cos(rot_rad)
    sinR = math.sin(rot_rad)

    # Sky-frame offset (de east, dn north) in degrees, then rotate INTO the
    # camera frame so the grid is axis-aligned regardless of the mosaic's PA.
    pts: list[tuple[Target, float, float]] = []
    for t in targets:
        de = ((t.ra * 15.0) - avg_ra_deg) * cosD
        dn = t.dec - avg_dec
        cx = de * cosR - dn * sinR  # camera-frame east-of-center
        cy = de * sinR + dn * cosR  # camera-frame north-of-center
        pts.append((t, cx, cy))

    tol_deg = tol_arcmin / 60.0

    # Bin by camera-Y (north) into rows. Sort north-first so row index 1 = north.
    pts_by_y = sorted(pts, key=lambda p: -p[2])
    rows_y: list[float] = []
    rows_pts: list[list[tuple[Target, float, float]]] = []
    for p in pts_by_y:
        if rows_y and abs(rows_y[-1] - p[2]) < tol_deg:
            rows_pts[-1].append(p)
        else:
            rows_y.append(p[2])
            rows_pts.append([p])

    cols = len(rows_pts[0])
    if cols == 0:
        return None
    if not all(len(r) == cols for r in rows_pts):
        return None
    rows = len(rows_pts)

    # Within each row, sort by camera-X (east). col 0 (west = lowest x) → col 1.
    grid: dict[tuple[int, int], Target] = {}
    for ri, row in enumerate(rows_pts, start=1):
        row.sort(key=lambda p: p[1])
        for ci, (t, _, _) in enumerate(row, start=1):
            grid[(ri, ci)] = t

    # Stride uniformity check — if rows-stride or cols-stride varies by more
    # than tolerance, it's not a regular grid (could be a curved mosaic, two
    # adjacent objects, or genuinely scattered targets).
    if rows > 1:
        row_strides = [abs(rows_y[i] - rows_y[i + 1]) for i in range(rows - 1)]
        if max(row_strides) - min(row_strides) > tol_deg:
            return None
    if cols > 1:
        for row in rows_pts:
            xs = sorted(p[1] for p in row)
            col_strides = [xs[i + 1] - xs[i] for i in range(cols - 1)]
            if max(col_strides) - min(col_strides) > tol_deg:
                return None

    return rows, cols, grid


def _camera_frame_xy(t: Target, avg_dec: float, avg_ra_deg: float, rot_deg: float) -> tuple[float, float]:
    """Project a target onto a local tangent plane around (avg_ra, avg_dec) and
    rotate by ``-rot_deg`` into the camera frame. Returns (cx, cy) in degrees,
    where +cx = camera-east-of-center, +cy = camera-north-of-center.

    Mirrors the projection used inside _detect_grid so overlap inference reads
    panel stride along the *camera* axes — the only axes where convert.py's
    `(j - (cols-1)/2) * stride_w` math is geometrically meaningful regardless
    of mosaic rotation.
    """
    cosD = max(1e-6, math.cos(math.radians(avg_dec)))
    rot_rad = math.radians(rot_deg or 0)
    cosR = math.cos(rot_rad)
    sinR = math.sin(rot_rad)
    de = ((t.ra * 15.0) - avg_ra_deg) * cosD
    dn = t.dec - avg_dec
    cx = de * cosR - dn * sinR
    cy = de * sinR + dn * cosR
    return cx, cy


def _overlap_pct_from_grid(
    rows: int, cols: int,
    grid: dict[tuple[int, int], Target],
    fov_w_arcmin: float,
    fov_h_arcmin: float,
) -> int:
    """Infer overlap_pct from the actual panel stride against the gear FOV.

    Earlier versions read stride directly from sky-frame RA/Dec deltas, which
    silently broke for any non-zero mosaic rotation: a 92°-rotated 4×3 had
    `cos(92°)·stride ≈ 0`, the inferred overlap blew up to 90%+, and a
    subsequent `/import/resolve` auto-pull would corrupt the user's ACP
    plan. The right axes are the **camera** axes — the same projection
    `_detect_grid` already uses — so project every panel into that frame
    and read the stride between adjacent (col→col, row→row) panels there.

    Returns 0 if FOV isn't available or it's a 1×1 "mosaic" (i.e. not).
    """
    if fov_w_arcmin <= 0 or fov_h_arcmin <= 0:
        return 0
    if rows <= 1 and cols <= 1:
        return 0

    # Compute the projection center from all panels in the grid so the
    # camera-frame origin is stable regardless of which corner we anchor on.
    panels = list(grid.values())
    avg_dec = sum(t.dec for t in panels) / len(panels)
    avg_ra_deg = sum(t.ra * 15.0 for t in panels) / len(panels)
    rot_deg = float(panels[0].rotation or 0)

    if cols > 1:
        # Use the (1,1) → (1,2) pair: same row, adjacent cols. In the camera
        # frame this is a pure +cx displacement of magnitude stride_w (deg).
        tl_cx, _ = _camera_frame_xy(grid[(1, 1)], avg_dec, avg_ra_deg, rot_deg)
        tr_cx, _ = _camera_frame_xy(grid[(1, 2)], avg_dec, avg_ra_deg, rot_deg)
        stride_arcmin = abs(tr_cx - tl_cx) * 60.0
        ratio = stride_arcmin / fov_w_arcmin
        return max(0, round((1.0 - ratio) * 100))
    # rows > 1
    _, top_cy = _camera_frame_xy(grid[(1, 1)], avg_dec, avg_ra_deg, rot_deg)
    _, bot_cy = _camera_frame_xy(grid[(2, 1)], avg_dec, avg_ra_deg, rot_deg)
    stride_arcmin = abs(top_cy - bot_cy) * 60.0
    ratio = stride_arcmin / fov_h_arcmin
    return max(0, round((1.0 - ratio) * 100))


def _center_of_panels(panels: list[Target]) -> tuple[float, float]:
    """Mean RA (deg) and Dec (deg) of a panel grid — good enough for the
    visualised plan-centre. RA averaging ignores the wrap-at-360 corner case;
    very wide mosaics straddling 0h would be wrong, but a typical 2-3 panel
    mosaic spans much less than that."""
    n = len(panels)
    ra_deg = sum(p.ra * 15.0 for p in panels) / n  # ra column is hours
    dec_deg = sum(p.dec for p in panels) / n
    return ra_deg, dec_deg


# Target Scheduler's ProjectState. Anything but active is something an ACP
# plan cannot say.
STATE_NAMES = {0: "draft", 1: "active", 2: "inactive", 3: "closed"}

# How far a rebuilt mosaic panel may land from where TS has it. ACP writes
# panel centres from gear and overlap, so a mosaic it made itself comes back
# to within floating point; anything further away was made or moved by hand.
_PANEL_TOL_ARCSEC = 0.1

# Names an exposure plan whose target is in no project of this profile.
_NO_PROJECT = "(no project)"


@dataclass
class _RowPlan:
    """One enabled TS exposure plan, as the filter row it becomes."""
    ep_id: int
    plan: ExposurePlan
    template: ExposureTemplate
    filter: str
    sub_s: float  # the length TS uses: the plan's own, or its template's default

    @property
    def pair(self) -> tuple[str, str]:
        return (self.filter, self.template.name or "")


def _row_plans(
    ep_ids: list[int], snap: TSSnapshot, target_id: int | None = None
) -> tuple[list[_RowPlan], list[int], list[int], list[int]]:
    """A target's enabled exposure plans in its effective order, each as a
    filter row (goal-panel-and-ts-order.md sections 3 and 5, "What the
    importer does"). A disabled plan is a row removed in ACP, so it is not
    read.

    The effective order is the order of each plan's first exposure row when
    the target has override rows, with plans no row names at the end, and
    the natural order (Id) otherwise.

    Returns the rows, the Ids of plans whose template is missing, the Ids
    of plans that repeat a filter and template already on the target, which
    one ACP plan cannot hold twice, and the Ids of plans the override rows
    leave out, which TS never shoots.
    """
    out: list[_RowPlan] = []
    missing: list[int] = []
    repeated: list[int] = []
    seen: set[tuple[str, str]] = set()
    natural = sorted(ep_ids)
    enabled = [i for i in natural
               if int(snap.plans_by_id[i].enabled if snap.plans_by_id[i].enabled is not None else 1) != 0]
    rows = snap.override_rows_by_target.get(target_id, []) if target_id is not None else []
    ordered, never_shot = effective_plan_order(enabled, rows)
    for ep_id in ordered:
        ep = snap.plans_by_id[ep_id]
        if int(ep.enabled if ep.enabled is not None else 1) == 0:
            continue
        tpl = snap.templates_by_id.get(ep.exposureTemplateId)
        if tpl is None:
            missing.append(ep_id)
            continue
        # The filter comes from the template's filtername, never its name,
        # through the vocabulary the writers share: "R Stars (ZWO ASI6200MM
        # Pro)" on filtername R, or an older writer's filtername "R Stars",
        # is filter R.
        rp = _RowPlan(ep_id, ep, tpl, row_filter(tpl.filtername),
                      plan_sub_s(ep.exposure, tpl.defaultexposure))
        if rp.pair in seen:
            repeated.append(ep_id)
            continue
        seen.add(rp.pair)
        out.append(rp)
    return out, missing, repeated, never_shot


def _hours_for_desired(desired: int, sub_s: float) -> float:
    """Goal hours that the writer turns back into exactly *desired* subs.

    The writer computes ``max(1, ceil(hours * 3600 / template_sub_s))``.
    Rounding hours to four places pushed 50 x 300 s to 4.1667 h, which comes
    back as 51. Keep the tidy four-place value when it survives the trip,
    else round down to four places, else the unrounded value, else step down
    until it fits.
    """
    if desired <= 0:
        return 0.0
    div = sub_s if sub_s and sub_s > 0 else 300.0

    def back(h: float) -> int:
        return max(1, math.ceil(h * 3600.0 / div))

    exact = desired * div / 3600.0
    for h in (round(exact, 4), math.floor(exact * 1e4) / 1e4, exact):
        if h > 0 and back(h) == desired:
            return h
    h = exact
    while back(h) > desired:
        h = math.nextafter(h, 0.0)
    return h


def _row(rp: _RowPlan, row_id: str, *, grader_enabled: bool) -> dict:
    """The ACP filter row for one exposure plan.

    `actual_hours` uses `accepted` when the parent project has the grader
    enabled (rejected frames don't count); else `acquired` (raw capture).
    """
    sub = int(rp.sub_s) if float(rp.sub_s).is_integer() else rp.sub_s
    good_count = rp.plan.accepted if grader_enabled else rp.plan.acquired
    row = {
        "id": row_id,
        "filter": rp.filter,
        "template": rp.template.name or "",
        "template_sub_s": sub,
    }
    desired = int(rp.plan.desired or 0)
    if rp.sub_s < FRAME_UNIT_BELOW_S:
        row["unit"] = "fr"
        row["target_frames"] = max(0, desired)
    else:
        row["unit"] = "h"
        row["target_hours"] = _hours_for_desired(desired, rp.sub_s)
    row["actual_hours"] = round((good_count or 0) * rp.sub_s / 3600.0, 4)
    return row


def _rows_signature(rps: list[_RowPlan]) -> dict:
    """What a panel asks for, to tell whether two panels ask for the same."""
    return {rp.pair: (int(rp.plan.desired or 0), rp.sub_s) for rp in rps}


def _ra_deg_exact(ra_hours: float) -> float:
    """Right ascension in degrees that the writer divides back to *ra_hours*.

    ``ra * 15 / 15`` misses the original in the last bit for some values, so
    a pinned push would move the target by 1e-15 of an hour. Nudge the
    degrees by an ulp or two until the division lands.
    """
    deg = float(ra_hours) * 15.0
    if deg / 15.0 == ra_hours:
        return deg
    for direction in (math.inf, -math.inf):
        cand = deg
        for _ in range(4):
            cand = math.nextafter(cand, direction)
            if cand / 15.0 == ra_hours:
                return cand
    return deg


def _panel_name(base: str, cols: int, row0: int, col0: int) -> str:
    """convert.py's name for a mosaic panel, rows and columns from 0."""
    return f"{base} Panel {row0 * cols + col0 + 1} (R{row0 + 1}C{col0 + 1})"


def _gear_that_rebuilds(
    panels: list[Target],
    rows: int,
    cols: int,
    base: str,
    gear: dict,
) -> tuple[dict, dict, int] | None:
    """Find the telescope and camera that reproduce this mosaic exactly.

    TS keeps no record of the gear a project used. What it does keep is the
    panel layout, and an ACP mosaic's layout follows from the gear's field of
    view and the overlap. So try every telescope and camera in ACP's gear
    list: infer the overlap from the panel stride, rebuild the panels the way
    the writer would, and accept the pair only if every panel comes back with
    its TS name and within a tenth of an arcsecond of its TS centre.

    Stride alone cannot always tell gear apart: a field four times as wide at
    80% overlap lays panels exactly where the real one did at 20%. When more
    than one pair fits, take the smallest overlap, which is the one a person
    would have set. The panels land in the same places whichever is chosen.

    Returns ``(telescope, camera, overlap_pct)`` or None. None means pushing
    this as an ACP mosaic would rename or move panels, so the caller keeps
    them as separate targets instead.
    """
    rotations = {float(p.rotation or 0) for p in panels}
    if len(rotations) != 1:
        return None
    rot = rotations.pop()
    ra_c, dec_c = _center_of_panels(panels)
    cos_d = math.cos(math.radians(dec_c))
    grid = {(r + 1, c + 1): panels[r * cols + c] for r in range(rows) for c in range(cols)}

    best: tuple[dict, dict, int] | None = None
    for scope in gear.get("telescopes", []) or []:
        for cam in gear.get("cameras", []) or []:
            fov_w, fov_h = _fov_arcmin(scope, cam)
            if fov_w <= 0 or fov_h <= 0:
                continue
            overlap = _overlap_pct_from_grid(rows, cols, grid, fov_w, fov_h)
            centres = _mosaic_panel_centers(ra_c, dec_c, fov_w, fov_h, rot, rows, cols, overlap)
            for c in centres:
                t = panels[c["row"] * cols + c["col"]]
                if t.name != _panel_name(base, cols, c["row"], c["col"]):
                    break
                d_ra = (c["ra_deg"] - float(t.ra) * 15.0) * cos_d * 3600.0
                d_dec = (c["dec_deg"] - float(t.dec)) * 3600.0
                if math.hypot(d_ra, d_dec) > _PANEL_TOL_ARCSEC:
                    break
            else:
                if best is None or overlap < best[2]:
                    best = (scope, cam, overlap)
    return best


def _id_map_for_target_set(
    *,
    project_id: int,
    panels_by_key: dict[str, int],
    rows_by_target: dict[int, list[_RowPlan]],
    row_ids: dict[tuple[str, str], str],
) -> dict:
    """Build the per-plan ts_refs ID map from the TS rows we just read.

    ``panels_by_key`` maps panel-key strings (e.g. ``"1,1"``) to the TS
    target Id at that panel. For a single-target plan this is just
    ``{"1,1": tid}``; for an N×M mosaic it's the full grid.

    Exposure plans are keyed ``"<target id>_<row id>"`` and templates by row
    id, the keys the writers look a row's plan up by. Every panel's plan for
    one filter and template shares that row's id.
    """
    template_ids_by_filter: dict[str, int] = {}
    exposure_plan_ids: dict[str, int] = {}
    for tid in panels_by_key.values():
        for rp in rows_by_target.get(tid, []):
            row_id = row_ids.get(rp.pair)
            if row_id is None:
                continue
            template_ids_by_filter.setdefault(row_id, rp.plan.exposureTemplateId)
            exposure_plan_ids[f"{tid}_{row_id}"] = rp.ep_id
    return {
        "project_id": project_id,
        "target_ids_by_panel": dict(panels_by_key),
        "template_ids_by_filter": template_ids_by_filter,
        "exposure_plan_ids": exposure_plan_ids,
    }


def _new_row_ids(rps: list[_RowPlan]) -> dict[tuple[str, str], str]:
    """A fresh id for each filter and template. The merge swaps in the id of
    the ACP row with the same filter and template when there is one."""
    return {rp.pair: str(uuid.uuid4()) for rp in rps}


def import_to_acp_plans(
    snap: TSSnapshot,
    gear: dict,
) -> tuple[list[dict], list[ImportNote], list[dict]]:
    """Convert a TS snapshot back into ACP plan dicts.

    Returns ``(plans, notes, id_maps)``. ``id_maps[i]`` is a dict
    ``{"project_id", "target_ids_by_panel", "template_ids_by_filter",
    "exposure_plan_ids"}`` corresponding to ``plans[i]``. The import path
    uses it to build ts_refs and ts_base_snapshot straight from known TS Ids,
    because the GUID lookup only finds rows ACP pushed, not rows made in the
    TS UI.

    Notes are user-facing, for the API response. A note at level "loss"
    names something the import could not carry into ACP exactly: the
    project, the target where there is one, and what is lost.
    """
    notes: list[ImportNote] = []
    plans_out: list[dict] = []
    id_maps_out: list[dict] = []

    telescopes = gear.get("telescopes", []) or []
    cameras = gear.get("cameras", []) or []
    default_scope = telescopes[0] if telescopes else None
    default_cam = cameras[0] if cameras else None
    fov_w, fov_h = _fov_arcmin(default_scope, default_cam)

    # Group targets by their parent project.
    targets_by_project: dict[int, list[Target]] = {}
    for tid, tgt in snap.targets_by_id.items():
        if tgt.projectid:
            targets_by_project.setdefault(tgt.projectid, []).append(tgt)

    ep_ids_by_target: dict[int, list[int]] = {}
    for ep_id, plan in snap.plans_by_id.items():
        if plan.targetid in snap.targets_by_id:
            ep_ids_by_target.setdefault(plan.targetid, []).append(ep_id)
            continue
        notes.append(ImportNote(
            "loss",
            f"Exposure plan {ep_id} {_NO_PROJECT} points at target {plan.targetid}, "
            "which is in no project of this profile, so it is not imported.",
            project=_NO_PROJECT,
        ))

    target_id_by_dataclass = {id(t): tid for tid, t in snap.targets_by_id.items()}

    # Cluster threshold: 3× the largest FOV dimension, fallback 1.5° when
    # FOV unknown. Just needs to exceed the per-panel STRIDE; chain-linking
    # captures the full mosaic even when opposite corners are far apart.
    cluster_threshold_deg = 3.0 * max(fov_w, fov_h, 30.0) / 60.0

    for proj_id, proj in snap.projects_by_id.items():
        pname = proj.name
        proj_targets = targets_by_project.get(proj_id, [])
        if not proj_targets:
            notes.append(ImportNote(
                "loss",
                f"Target Scheduler project '{pname}' has no targets, so there is no "
                "ACP plan to make of it and it is not imported.",
                project=pname,
            ))
            continue

        proj_priority = int(proj.priority) if proj.priority is not None else 1
        priority = PRIORITY_NAMES.get(proj_priority, "normal")
        grader_enabled = bool(int(proj.enablegrader or 0) == 1)
        min_alt = float(proj.minimumaltitude or 0)
        meridian = int(proj.meridianwindow or 0)
        min_time = int(proj.minimumtime or 0)
        switch_freq = int(proj.filterswitchfrequency or 0)
        smart_order = bool(int(proj.smartexposureorder or 0))

        state = int(proj.state if proj.state is not None else 1)
        plan_state = STATE_NAMES.get(state)
        if plan_state is None:
            plan_state = "active"
            notes.append(ImportNote(
                "loss",
                f"Target Scheduler has the project '{pname}' in an unrecognised "
                f"state ({state}). ACP shows it as active, and a push from ACP "
                "leaves the state in Target Scheduler as it is.",
                project=pname,
            ))

        rows_by_target: dict[int, list[_RowPlan]] = {}
        for t in proj_targets:
            tid = target_id_by_dataclass[id(t)]
            rps, missing, repeated, never_shot = _row_plans(ep_ids_by_target.get(tid, []), snap, tid)
            rows_by_target[tid] = rps
            for ep_id in never_shot:
                tpl = snap.templates_by_id.get(snap.plans_by_id[ep_id].exposureTemplateId)
                notes.append(ImportNote(
                    "warn",
                    f"TS never shoots {tpl.name if tpl is not None else f'exposure plan {ep_id}'} "
                    f"on {t.name}, because its override order leaves it out.",
                    project=pname, target=t.name,
                ))
            for ep_id in missing:
                notes.append(ImportNote(
                    "loss",
                    f"Exposure plan {ep_id} on '{t.name}' in '{pname}' uses template "
                    f"{snap.plans_by_id[ep_id].exposureTemplateId}, which no longer "
                    "exists, so it is not imported.",
                    project=pname, target=t.name,
                ))
            for ep_id in repeated:
                tpl = snap.templates_by_id[snap.plans_by_id[ep_id].exposureTemplateId]
                notes.append(ImportNote(
                    "loss",
                    f"Exposure plan {ep_id} on '{t.name}' in '{pname}' uses template "
                    f"'{tpl.name}' again, and an ACP plan holds each filter and template "
                    "once, so it is not imported.",
                    project=pname, target=t.name,
                ))

        def _emit_single(tgt: Target) -> None:
            tid = target_id_by_dataclass[id(tgt)]
            row_ids = _new_row_ids(rows_by_target[tid])
            plan_out = {
                "id": f"acp-imp-{tgt.guid or tgt.name}",
                "guid": tgt.guid or "",
                "project_name": pname,
                "priority": priority,
                "min_altitude_deg": min_alt,
                "meridian_window_min": meridian,
                "minimum_time_min": min_time,
                "filter_switch_frequency": switch_freq,
                "smart_exposure_order": smart_order,
                "state": plan_state,
                "telescope_id": (default_scope or {}).get("id") or "",
                "camera_id": (default_cam or {}).get("id") or "",
                "target": {
                    "name": tgt.name,
                    "center_ra_deg": _ra_deg_exact(float(tgt.ra or 0)),
                    "center_dec_deg": float(tgt.dec or 0),
                    "rotation_deg": float(tgt.rotation or 0),
                    "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 0},
                },
                "filter_rows": [
                    _row(rp, row_ids[rp.pair], grader_enabled=grader_enabled)
                    for rp in rows_by_target[tid]
                ],
            }
            plans_out.append(plan_out)
            id_maps_out.append(_id_map_for_target_set(
                project_id=proj_id,
                panels_by_key={"1,1": tid},
                rows_by_target=rows_by_target,
                row_ids=row_ids,
            ))

        # Group into clusters that *should* form a single ACP plan. Two-pass:
        # (1) Targets carrying our pushed-by-ACP "<base> Panel N (RxCy)"
        #     suffix get bucketed by base name. This handles the common case
        #     of multiple mosaics in one TS project (which spatial clustering
        #     misclusters when the mosaics are nearby on sky).
        # (2) Anything without our suffix (renamed in TS, built in Framing
        #     Assistant, or single-target plans) falls back to spatial
        #     single-link clustering.
        suffix_buckets: dict[str, list[Target]] = {}
        unsuffixed: list[Target] = []
        for t in proj_targets:
            base, r, c = _split_panel_suffix(t.name)
            if r is not None and c is not None:
                suffix_buckets.setdefault(base, []).append(t)
            else:
                unsuffixed.append(t)
        clusters: list[list[Target]] = list(suffix_buckets.values())
        if unsuffixed:
            clusters.extend(_cluster_targets_spatially(unsuffixed, cluster_threshold_deg))

        mosaic_noted = False
        for cluster in clusters:
            if len(cluster) == 1:
                _emit_single(cluster[0])
                continue

            found = _detect_grid(cluster)
            if found is None:
                # Cluster of >1 targets that aren't a regular grid (curved
                # mosaic, hand-perturbed panels, or a coincidental cluster
                # of unrelated targets that happened to be close on sky).
                # Per the README caveat, emit each as its own plan.
                for tgt in cluster:
                    _emit_single(tgt)
                continue

            rows, cols, grid = found
            panels = [grid[(r, c)] for r in range(1, rows + 1) for c in range(1, cols + 1)]
            base = _common_base_name(panels, pname)
            match = _gear_that_rebuilds(panels, rows, cols, base, gear)
            panel_ids = [target_id_by_dataclass[id(p)] for p in panels]
            anchor_goals = _rows_signature(rows_by_target[panel_ids[0]])
            differing = any(
                _rows_signature(rows_by_target[tid]) != anchor_goals
                for tid in panel_ids[1:]
            )

            if match is None or differing:
                # Pushing this as an ACP mosaic would rebuild the panels from
                # gear and overlap, which renames and moves them, or would
                # give every panel the first panel's goals. Keep the panels
                # exactly as TS has them instead.
                why = (
                    "their panels ask for different exposures, and an ACP mosaic "
                    "gives every panel the same goals"
                    if match is not None else
                    "no telescope and camera in ACP's gear list rebuilds these "
                    "panel names and centres, so pushing them as an ACP mosaic "
                    "would rename and move them"
                )
                in_ts_order = sorted(panels, key=lambda p: target_id_by_dataclass[id(p)])
                notes.append(ImportNote(
                    "loss",
                    f"'{pname}' has {len(panels)} targets laid out as a {rows}x{cols} "
                    f"mosaic ({', '.join(p.name for p in in_ts_order)}). They come into "
                    f"ACP as {len(panels)} separate plans, not one mosaic, because "
                    f"{why}.",
                    project=pname, target=base,
                ))
                mosaic_noted = True
                for tgt in in_ts_order:
                    _emit_single(tgt)
                continue

            scope, cam, overlap = match
            mosaic_noted = True
            row_ids = _new_row_ids(rows_by_target[panel_ids[0]])
            anchor = panels[0]
            ra_deg, dec_deg = _center_of_panels(panels)
            panels_by_key = {
                f"{r},{c}": target_id_by_dataclass[id(grid[(r, c)])]
                for r in range(1, rows + 1) for c in range(1, cols + 1)
            }
            plan_id_seed = anchor.guid or f"{pname}-{base}"
            mosaic_plan = {
                "id": f"acp-imp-{plan_id_seed}",
                "guid": proj.guid or "",
                "project_name": pname,
                "priority": priority,
                "min_altitude_deg": min_alt,
                "meridian_window_min": meridian,
                "minimum_time_min": min_time,
                "filter_switch_frequency": switch_freq,
                "smart_exposure_order": smart_order,
                "state": plan_state,
                "telescope_id": scope.get("id") or "",
                "camera_id": cam.get("id") or "",
                "target": {
                    "name": base,
                    "center_ra_deg": ra_deg,
                    "center_dec_deg": dec_deg,
                    "rotation_deg": float(anchor.rotation or 0),
                    "mosaic": {"rows": rows, "cols": cols, "overlap_pct": overlap},
                },
                "filter_rows": [
                    _row(rp, row_ids[rp.pair], grader_enabled=grader_enabled)
                    for rp in rows_by_target[panel_ids[0]]
                ],
            }
            plans_out.append(mosaic_plan)
            id_maps_out.append(_id_map_for_target_set(
                project_id=proj_id,
                panels_by_key=panels_by_key,
                rows_by_target=rows_by_target,
                row_ids=row_ids,
            ))

        if int(proj.isMosaic or 0) == 1 and not mosaic_noted:
            notes.append(ImportNote(
                "loss",
                f"Target Scheduler marks '{pname}' as a mosaic, but its targets do "
                "not form a regular grid, so they come into ACP as separate plans "
                "and ACP will not show them as one mosaic.",
                project=pname,
            ))

    return plans_out, notes, id_maps_out
