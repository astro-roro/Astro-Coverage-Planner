"""Keep coverage target numbers the same from one scan to the next.

The scanner clusters masters and sub folders into targets afresh on every
run. Before this module it numbered them 1..N in cluster order, so a rescan
after new data arrived renumbered everything, and anything the user had
keyed by ``target_id`` (finished marks, hidden marks) silently landed on a
different target.

Now each new target takes the id of the previous target it matches on the
sky. The previous targets come from the manifest the scan is about to
replace, plus a small ledger (``data/target_ids.json``) that remembers every
id ever issued, where it sat and what it was called. The ledger lets
matching work when the previous manifest is missing or was swapped by hand.

A match needs the two targets to sit on the same patch of sky: centres
within half the smaller field, or footprints overlapping over most of the
smaller one. If both targets carry object names and share none, the match
also needs the centres close and the footprints mostly the same (so a small
galaxy inside an old wide field is not read as the same target). Object
names then break ties.

Splits and merges:

- One previous target matching several new ones keeps its id on the new
  part with the most integration. The other parts get new ids.
- Several previous targets matching one new target: the new target keeps
  the id of the previous one with the most integration. The others are
  recorded as merged into it, and per-target user state moves across.

New ids count up from the highest id ever issued, so an id is never handed
to a different target. A retired id can come back only for the same patch
of sky, for example after a scan that ran with a drive unplugged.

Per-target user state lives in JSON files keyed by target id. A file that
should follow merges registers itself in ``TARGET_STORES`` below with a
function that combines two entries. ``carry_over_merges`` then moves the
retired id's entry onto the surviving id in every registered file.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

LEDGER_VERSION = 1

# A target with no known field size is treated as this wide, the same
# radius the scanner clusters with.
DEFAULT_FIELD_ARCMIN = 30.0

# Share of the smaller footprint that must overlap for two targets to match.
OVERLAP_MATCH = 0.5

# When both targets are named and share no name, the footprints must also
# agree this closely (intersection over union) to count as the same target.
UNNAMED_RENAME_IOU = 0.5


# --------------------------------------------------------------------------
# Atomic JSON write (same pattern as app._atomic_write_json)

def atomic_write_json(path: Path, data, **dumps_kwargs) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, **dumps_kwargs)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path | None):
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Sky geometry

def _unit(ra: float, dec: float) -> tuple[float, float, float]:
    a, d = math.radians(ra), math.radians(dec)
    return (math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d))


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Great-circle distance, safe at RA 0/360 and at the poles."""
    x1, y1, z1 = _unit(ra1, dec1)
    x2, y2, z2 = _unit(ra2, dec2)
    cross = math.sqrt((y1 * z2 - z1 * y2) ** 2 + (z1 * x2 - x1 * z2) ** 2
                      + (x1 * y2 - y1 * x2) ** 2)
    dot = x1 * x2 + y1 * y2 + z1 * z2
    return math.degrees(math.atan2(cross, dot))


def _project(ra0: float, dec0: float, ra: float, dec: float):
    """Gnomonic projection of (ra, dec) onto the plane tangent at (ra0, dec0).

    Returns (east, north) in degrees, or None for a point on the far side.
    """
    a0, d0 = math.radians(ra0), math.radians(dec0)
    a, d = math.radians(ra), math.radians(dec)
    cos_c = (math.sin(d0) * math.sin(d)
             + math.cos(d0) * math.cos(d) * math.cos(a - a0))
    if cos_c <= 1e-6:
        return None
    x = math.cos(d) * math.sin(a - a0) / cos_c
    y = (math.cos(d0) * math.sin(d)
         - math.sin(d0) * math.cos(d) * math.cos(a - a0)) / cos_c
    return (math.degrees(x), math.degrees(y))


def _deproject(ra0: float, dec0: float, east: float, north: float):
    """Inverse of ``_project``: tangent-plane offsets in degrees to (ra, dec)."""
    x, y = math.radians(east), math.radians(north)
    d0 = math.radians(dec0)
    rho = math.hypot(x, y)
    if rho == 0:
        return (ra0 % 360.0, dec0)
    c = math.atan(rho)
    dec = math.asin(math.cos(c) * math.sin(d0) + y * math.sin(c) * math.cos(d0) / rho)
    ra = math.radians(ra0) + math.atan2(
        x * math.sin(c), rho * math.cos(d0) * math.cos(c) - y * math.sin(d0) * math.sin(c))
    return (math.degrees(ra) % 360.0, math.degrees(dec))


def _box_corners(ra: float, dec: float, w_arcmin: float, h_arcmin: float,
                 rot_deg: float | None) -> list[list[float]]:
    """Corners of a w x h box, laid out the way the scanner's fov_corners does."""
    r = math.radians(rot_deg or 0.0)
    out = []
    for dx, dy in [(-1, -1), (-1, 1), (1, 1), (1, -1)]:
        lx, ly = dx * w_arcmin / 2.0, dy * h_arcmin / 2.0
        east = lx * math.cos(r) + ly * math.sin(r)
        north = -lx * math.sin(r) + ly * math.cos(r)
        out.append(list(_deproject(ra, dec, east / 60.0, north / 60.0)))
    return out


def _polygon_area(poly) -> float:
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _ccw(poly):
    return poly if _polygon_area(poly) >= 0 else list(reversed(poly))


def _clip(subject, clipper):
    """Sutherland-Hodgman: the part of ``subject`` inside convex ``clipper``."""
    def inside(p, a, b):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0

    def cross_point(p, q, a, b):
        x1, y1, x2, y2 = p[0], p[1], q[0], q[1]
        x3, y3, x4, y4 = a[0], a[1], b[0], b[1]
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if den == 0:
            return q
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    out = list(subject)
    for i in range(len(clipper)):
        a, b = clipper[i], clipper[(i + 1) % len(clipper)]
        src, out = out, []
        if not src:
            break
        for j in range(len(src)):
            p, q = src[j], src[(j + 1) % len(src)]
            if inside(q, a, b):
                if not inside(p, a, b):
                    out.append(cross_point(p, q, a, b))
                out.append(q)
            elif inside(p, a, b):
                out.append(cross_point(p, q, a, b))
    return out


# --------------------------------------------------------------------------
# Target records

@dataclass
class Ref:
    """What matching needs to know about one target, old or new."""
    ra: float
    dec: float
    corners: list | None
    field_arcmin: float          # shorter side of the footprint
    names: frozenset
    hours: float
    target_id: int | None = None
    index: int | None = None     # position in the new target list


def _norm_name(s: str) -> str:
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def target_hours(t: dict) -> float:
    total = 0.0
    for d in (t.get("filters") or {}).values():
        if isinstance(d, dict):
            try:
                total += float(d.get("total_hours") or 0.0)
            except (TypeError, ValueError):
                pass
    return total


def _footprint(t: dict):
    fp = t.get("footprint_arcmin") or t.get("fov_arcmin")
    if isinstance(fp, (list, tuple)) and len(fp) == 2:
        try:
            w, h = float(fp[0]), float(fp[1])
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError):
            pass
    return None


def ref_from_target(t: dict, index: int | None = None) -> Ref | None:
    try:
        ra = float(t["center_ra_deg"])
        dec = float(t["center_dec_deg"])
    except (KeyError, TypeError, ValueError):
        return None
    fp = _footprint(t)
    corners = t.get("corners_icrs") or t.get("corners")
    if not (isinstance(corners, list) and len(corners) >= 3):
        corners = _box_corners(ra, dec, fp[0], fp[1], t.get("rotation_deg")) if fp else None
    tid = t.get("target_id")
    try:
        tid = int(tid) if tid is not None else None
    except (TypeError, ValueError):
        tid = None
    hours = t.get("hours")
    if hours is None:
        hours = target_hours(t)
    return Ref(
        ra=ra, dec=dec, corners=corners,
        field_arcmin=min(fp) if fp else DEFAULT_FIELD_ARCMIN,
        names=frozenset(n for n in (_norm_name(o) for o in (t.get("objects") or [])) if n),
        hours=float(hours or 0.0), target_id=tid, index=index,
    )


def _overlap(a: Ref, b: Ref) -> tuple[float, float]:
    """(intersection / smaller area, intersection / union) of two footprints."""
    if not a.corners or not b.corners:
        return 0.0, 0.0
    pa = [_project(a.ra, a.dec, c[0], c[1]) for c in a.corners]
    pb = [_project(a.ra, a.dec, c[0], c[1]) for c in b.corners]
    if any(p is None for p in pa + pb):
        return 0.0, 0.0
    pa, pb = _ccw(pa), _ccw(pb)
    area_a, area_b = abs(_polygon_area(pa)), abs(_polygon_area(pb))
    if area_a <= 0 or area_b <= 0:
        return 0.0, 0.0
    inter = _clip(pa, pb)
    ai = abs(_polygon_area(inter)) if len(inter) >= 3 else 0.0
    union = area_a + area_b - ai
    return ai / min(area_a, area_b), (ai / union if union > 0 else 0.0)


def match_score(a: Ref, b: Ref):
    """A sortable score if ``a`` and ``b`` look like the same target, else None."""
    sep_arcmin = separation_deg(a.ra, a.dec, b.ra, b.dec) * 60.0
    # Cheap rejection: too far apart for the footprints to touch.
    reach = (max(a.field_arcmin, DEFAULT_FIELD_ARCMIN) + max(b.field_arcmin, DEFAULT_FIELD_ARCMIN)) * 1.5
    if a.corners and b.corners:
        reach = max(reach, 2 * 60.0 * max(
            max(separation_deg(a.ra, a.dec, c[0], c[1]) for c in a.corners),
            max(separation_deg(b.ra, b.dec, c[0], c[1]) for c in b.corners)))
    if sep_arcmin > reach:
        return None
    centre_ok = sep_arcmin <= 0.5 * min(a.field_arcmin, b.field_arcmin)
    over_small, iou = _overlap(a, b)
    if not (centre_ok or over_small >= OVERLAP_MATCH):
        return None
    shared = len(a.names & b.names)
    if a.names and b.names and not shared:
        if not (centre_ok and iou >= UNNAMED_RENAME_IOU):
            return None
    return (1 if shared else 0, round(iou, 6), -sep_arcmin)


def read_previous_targets(manifest_path: Path | None) -> list[dict] | None:
    """The target list of the manifest a scan is about to replace, if any."""
    data = _read_json(manifest_path)
    if isinstance(data, dict) and isinstance(data.get("targets"), list):
        return [t for t in data["targets"] if isinstance(t, dict)]
    return None


def describe_report(r: dict) -> str:
    parts = [f"{r.get('kept', 0)} kept their number", f"{r.get('new', 0)} new",
             f"{r.get('retired', 0)} retired", f"{r.get('merged', 0)} merged"]
    if r.get("revived"):
        parts.append(f"{r['revived']} came back")
    if r.get("split"):
        parts.append(f"{r['split']} split")
    src = r.get("previous_source")
    tail = {"ledger": " (matched against the ledger, no previous manifest)",
            "none": " (first scan, numbered from scratch)"}.get(src, "")
    return ", ".join(parts) + tail


# --------------------------------------------------------------------------
# Ledger

def empty_ledger() -> dict:
    return {"version": LEDGER_VERSION, "next_id": 1, "retired": [],
            "merges": {}, "targets": {}}


def load_ledger(path: Path | None) -> dict | None:
    data = _read_json(path)
    if not isinstance(data, dict):
        return None
    led = empty_ledger()
    try:
        led["next_id"] = max(1, int(data.get("next_id") or 1))
    except (TypeError, ValueError):
        pass
    led["retired"] = sorted({int(x) for x in data.get("retired") or []
                             if str(x).lstrip("-").isdigit()})
    led["merges"] = {str(k): int(v) for k, v in (data.get("merges") or {}).items()
                     if str(v).lstrip("-").isdigit()}
    led["targets"] = {str(k): v for k, v in (data.get("targets") or {}).items()
                      if isinstance(v, dict)}
    return led


def _ledger_entry(t: dict, status: str, when: str) -> dict:
    return {
        "status": status,
        "center_ra_deg": t.get("center_ra_deg"),
        "center_dec_deg": t.get("center_dec_deg"),
        "objects": list(t.get("objects") or []),
        "footprint_arcmin": t.get("footprint_arcmin") or t.get("fov_arcmin"),
        "rotation_deg": t.get("rotation_deg"),
        "corners_icrs": t.get("corners_icrs") or t.get("corners"),
        "hours": round(t["hours"] if t.get("hours") is not None else target_hours(t), 3),
        "last_seen": when,
    }


# --------------------------------------------------------------------------
# Assignment

def _components(n_prev: int, n_new: int, edges: dict):
    """Connected components of the bipartite match graph."""
    parent = list(range(n_prev + n_new))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (pi, ni) in edges:
        ra, rb = find(pi), find(n_prev + ni)
        if ra != rb:
            parent[ra] = rb
    groups: dict[int, tuple[list, list]] = {}
    for (pi, ni) in edges:
        g = groups.setdefault(find(pi), ([], []))
        if pi not in g[0]:
            g[0].append(pi)
        if ni not in g[1]:
            g[1].append(ni)
    return list(groups.values())


def assign_target_ids(new_targets: list[dict],
                      previous_targets: list[dict] | None,
                      ledger: dict | None,
                      now: str | None = None) -> tuple[dict, dict]:
    """Set ``target_id`` on every new target, keeping ids stable.

    ``previous_targets`` is the target list of the manifest being replaced
    (None or empty if there is none). ``ledger`` is the loaded ledger or None.
    Mutates ``new_targets`` in place. Returns ``(new_ledger, report)``; the
    report carries the counts for the scan summary and ``merges`` as
    ``{retired id: surviving id}`` for ``carry_over_merges``.
    """
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    led = ledger if ledger is not None else empty_ledger()
    led_targets = led.get("targets") or {}

    # Previous targets: the old manifest first, then any active ledger entry
    # the old manifest does not carry (it was replaced or trimmed by hand).
    prev: list[Ref] = []
    seen_ids: set[int] = set()
    for t in previous_targets or []:
        r = ref_from_target(t)
        if r is None or r.target_id is None or r.target_id in seen_ids:
            continue
        seen_ids.add(r.target_id)
        prev.append(r)
    for k, e in sorted(led_targets.items(), key=lambda kv: int(kv[0])):
        if e.get("status") != "active" or int(k) in seen_ids:
            continue
        r = ref_from_target({**e, "target_id": int(k)})
        if r is not None:
            seen_ids.add(r.target_id)
            prev.append(r)
    prev.sort(key=lambda r: r.target_id)

    news = [ref_from_target(t, i) for i, t in enumerate(new_targets)]

    edges: dict[tuple[int, int], tuple] = {}
    for pi, p in enumerate(prev):
        for ni, n in enumerate(news):
            if n is None:
                continue
            s = match_score(p, n)
            if s is not None:
                edges[(pi, ni)] = s

    assigned: dict[int, int] = {}       # new index -> id
    used_prev: set[int] = set()
    merges: dict[int, int] = {}         # retired id -> surviving id
    split_count = 0

    def best(cands, key):
        return max(cands, key=key)

    for prev_idx, new_idx in _components(len(prev), len(news), edges):
        prev_idx.sort(key=lambda i: prev[i].target_id)
        new_idx.sort()
        if len(prev_idx) == 1:
            pi = prev_idx[0]
            ni = best(new_idx, lambda n: (news[n].hours, edges[(pi, n)], -n))
            assigned[ni] = prev[pi].target_id
            used_prev.add(pi)
            split_count += len(new_idx) > 1
        elif len(new_idx) == 1:
            ni = new_idx[0]
            pi = best(prev_idx, lambda p: (prev[p].hours, edges[(p, ni)], -prev[p].target_id))
            assigned[ni] = prev[pi].target_id
            used_prev.add(pi)
            for p in prev_idx:
                if p != pi:
                    merges[prev[p].target_id] = prev[pi].target_id
                    used_prev.add(p)
        else:
            pairs = sorted(((edges[(p, n)], p, n) for p in prev_idx for n in new_idx
                            if (p, n) in edges),
                           key=lambda x: (x[0], -prev[x[1]].target_id, -x[2]), reverse=True)
            for _, p, n in pairs:
                if p in used_prev or n in assigned:
                    continue
                assigned[n] = prev[p].target_id
                used_prev.add(p)
            for p in prev_idx:
                if p in used_prev:
                    continue
                cands = [n for n in new_idx if (p, n) in edges and n in assigned]
                if cands:
                    n = best(cands, lambda n: edges[(p, n)])
                    merges[prev[p].target_id] = assigned[n]
                    used_prev.add(p)

    retired_now = [prev[i].target_id for i in range(len(prev)) if i not in used_prev]

    # A previously retired id comes back only for the same patch of sky.
    active_ids = {tid for tid in assigned.values()}
    revive_pool = []
    for k, e in led_targets.items():
        tid = int(k)
        if e.get("status") == "retired" and tid not in active_ids and tid not in seen_ids:
            r = ref_from_target({**e, "target_id": tid})
            if r is not None:
                revive_pool.append(r)
    revived = []
    if revive_pool:
        pairs = []
        for r in revive_pool:
            for ni, n in enumerate(news):
                if n is None or ni in assigned:
                    continue
                s = match_score(r, n)
                if s is not None:
                    pairs.append((s, r.target_id, ni))
        pairs.sort(key=lambda x: (x[0], -x[1], -x[2]), reverse=True)
        taken = set()
        for _, tid, ni in pairs:
            if tid in taken or ni in assigned:
                continue
            assigned[ni] = tid
            taken.add(tid)
            revived.append(tid)

    # Everything left gets a fresh id above the highest ever issued.
    all_known = ([r.target_id for r in prev] + [int(k) for k in led_targets]
                 + [int(k) for k in (led.get("merges") or {})]
                 + list(led.get("retired") or []))
    next_id = max([int(led.get("next_id") or 1)] + [i + 1 for i in all_known])
    new_count = 0
    for i, t in enumerate(new_targets):
        if i in assigned:
            t["target_id"] = assigned[i]
        else:
            t["target_id"] = next_id
            next_id += 1
            new_count += 1

    # Build the new ledger.
    out = {
        "version": LEDGER_VERSION,
        "next_id": next_id,
        "retired": [],
        "merges": {str(k): int(v) for k, v in (led.get("merges") or {}).items()},
        "targets": {k: dict(v) for k, v in led_targets.items()},
    }
    for r in prev:
        # Make sure every previous id is on file, so matching can fall back
        # to the ledger if the manifest goes missing later.
        key = str(r.target_id)
        if key not in out["targets"]:
            src = next((t for t in previous_targets or []
                        if str(t.get("target_id")) == key), None)
            if src is not None:
                out["targets"][key] = _ledger_entry(src, "active", now)
    for tid in retired_now:
        if str(tid) in out["targets"]:
            out["targets"][str(tid)]["status"] = "retired"
    for old, new in merges.items():
        out["merges"][str(old)] = new
        if str(old) in out["targets"]:
            out["targets"][str(old)]["status"] = "merged"
    for t in new_targets:
        out["targets"][str(t["target_id"])] = _ledger_entry(t, "active", now)
    # Chains: 3 merged into 5, later 5 into 8, so 3 now resolves to 8.
    for k in list(out["merges"]):
        seen = {int(k)}
        v = out["merges"][k]
        while str(v) in out["merges"] and v not in seen:
            seen.add(v)
            v = out["merges"][str(v)]
        out["merges"][k] = v
    out["retired"] = sorted(int(k) for k, e in out["targets"].items()
                            if e.get("status") in ("retired", "merged"))

    kept = sum(1 for i in assigned if assigned[i] in {r.target_id for r in prev})
    report = {
        "kept": kept,
        "new": new_count,
        "retired": len(retired_now),
        "merged": len(merges),
        "revived": len(revived),
        "split": split_count,
        "merges": {str(k): v for k, v in sorted(merges.items())},
        "retired_ids": sorted(retired_now),
        "revived_ids": sorted(revived),
        "previous_source": ("manifest" if previous_targets else
                            "ledger" if prev else "none"),
    }
    return out, report


# --------------------------------------------------------------------------
# Carrying per-target user state across merges

def _combine_override(old: dict, new: dict | None) -> dict:
    """Finished marks: the merged target is finished if either part was."""
    if not isinstance(new, dict):
        return dict(old)
    out = dict(new)
    if "finished" in old or "finished" in new:
        out["finished"] = bool(old.get("finished")) or bool(new.get("finished"))
    stamps = [s for s in (old.get("updated_at"), new.get("updated_at")) if s]
    if stamps:
        out["updated_at"] = max(stamps)
    return out


@dataclass
class TargetStore:
    """A JSON file of per-target entries that must follow merges.

    ``path`` is called at carry-over time so environment overrides apply.
    ``key`` names the dict inside the file that is keyed by target id
    (``None`` when the file itself is that dict). ``combine(old, new)``
    returns the entry to keep on the surviving id, where ``new`` is the
    surviving id's own entry or None.
    """
    name: str
    path: Callable[[], Path]
    key: str | None
    combine: Callable[[dict, dict | None], dict]


def _overrides_path() -> Path:
    repo = Path(__file__).resolve().parent.parent
    return Path(os.environ.get("TARGET_OVERRIDES_PATH")
                or (repo / "data" / "target_overrides.json"))


def _combine_hidden(old: dict, new: dict | None) -> dict:
    """Hidden marks: the merged target is hidden if either part was."""
    return dict(new) if isinstance(new, dict) else dict(old)


def _hidden_path() -> Path:
    repo = Path(__file__).resolve().parent.parent
    return Path(os.environ.get("HIDDEN_PATH")
                or (repo / "data" / "hidden.json"))


# Register any new per-target file here (hidden marks, for one) so a merge
# carries its entries across. Each needs a combine rule for two entries.
TARGET_STORES: list[TargetStore] = [
    TargetStore("finished marks", _overrides_path, "overrides", _combine_override),
    TargetStore("hidden marks", _hidden_path, "targets", _combine_hidden),
]


def carry_over_merges(merges: dict, stores: list[TargetStore] | None = None,
                      log=print) -> dict:
    """Move entries keyed by a retired id onto its surviving id, atomically.

    Returns ``{store name: number of entries moved}``.
    """
    moved = {}
    if not merges:
        return moved
    for store in stores if stores is not None else TARGET_STORES:
        path = store.path()
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        entries = data.get(store.key) if store.key else data
        if not isinstance(entries, dict):
            continue
        entries = dict(entries)
        n = 0
        for old, new in merges.items():
            old_k, new_k = str(old), str(new)
            if old_k not in entries:
                continue
            entries[new_k] = store.combine(entries.pop(old_k), entries.get(new_k))
            n += 1
        if n:
            if store.key:
                data = {**data, store.key: entries}
            else:
                data = entries
            atomic_write_json(path, data, indent=2)
            log(f"  Moved {n} {store.name} entr{'y' if n == 1 else 'ies'} onto merged targets")
        moved[store.name] = n
    return moved
