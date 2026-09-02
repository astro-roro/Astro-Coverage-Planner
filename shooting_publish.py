"""Build and publish the public "live" document for a static web page.

Spec: docs/specs/shooting-page.md. Only plans with visibility == "public"
are included. The document is scanned for path-shaped strings before it
leaves this module, using the same tripwire as the friend-manifest sanitiser.

This module has no Flask import so the CLI can use it without starting
the app. app.py imports it lazily for the two live-page endpoints.
"""
from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from build_archive_manifest import canon_filter  # noqa: E402
from sanitise_manifest import _aperture_class, validate_no_paths  # noqa: E402

DOC_VERSION = 1

# The friend sanitiser's tripwire only matches a path at the start or a
# file extension at the end of a string. Free text written by a person can
# carry a path anywhere, so the three text fields get an unanchored scan.
_FREE_TEXT_LEAK = re.compile(
    r"(?:[A-Za-z]:[\\/])"                       # C:\ or C:/
    r"|(?:/(?:home|Users|var|tmp|mnt|media|Volumes)/)"  # POSIX system dirs, anywhere
    r"|(?:\\\\)"                                  # UNC \\server\share
    r"|(?:\.(?:fits?|xisf|raw|cr2|nef|arw|dng)\b)",  # image file extensions, anywhere
    re.IGNORECASE,
)


def _check_free_text(field: str, value: str) -> str:
    if _FREE_TEXT_LEAK.search(value):
        raise RuntimeError(f"path-shaped text in {field}: {value!r}")
    return value


class PublishConfigError(RuntimeError):
    """ACP_PUBLISH_DEST is unset or empty."""


# --- Geometry ---------------------------------------------------------------

def _fov_arcmin(telescope: dict | None, camera: dict | None) -> tuple[float, float]:
    """Same formula as app._fov_arcmin, duplicated so this module stays Flask-free."""
    if not telescope or not camera:
        return (0.0, 0.0)
    try:
        fl = float(telescope["focal_length_mm"])
        px_um = float(camera["pixel_size_um"])
        w_px, h_px = camera["sensor_px"]
    except (KeyError, ValueError, TypeError):
        return (0.0, 0.0)
    if fl <= 0:
        return (0.0, 0.0)
    aspp = 206.265 * px_um / fl
    return (w_px * aspp / 60.0, h_px * aspp / 60.0)


def _mosaic_extent_arcmin(fov_w: float, fov_h: float, mosaic: dict | None) -> tuple[float, float]:
    """Whole-mosaic footprint: panel stride is fov * (1 - overlap)."""
    m = mosaic or {}
    rows = max(1, int(m.get("rows") or 1))
    cols = max(1, int(m.get("cols") or 1))
    ov = max(0.0, min(0.99, float(m.get("overlap_pct") or 0) / 100.0))
    return (fov_w * (1 + (cols - 1) * (1 - ov)), fov_h * (1 + (rows - 1) * (1 - ov)))


def _sep_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    a = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def _diag_arcmin(fov) -> float:
    try:
        w, h = float(fov[0]), float(fov[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    return math.hypot(w, h)


def _matched_targets(plan_ra: float, plan_dec: float, plan_extent: tuple[float, float],
                     targets: list[dict]) -> list[dict]:
    """Manifest targets whose centre sits within half the larger diagonal of
    the plan footprint or the target footprint. Plans have no target ids,
    so sky position is the only link between a plan and logged data."""
    plan_diag = _diag_arcmin(plan_extent)
    out = []
    for t in targets:
        try:
            ra, dec = float(t["center_ra_deg"]), float(t["center_dec_deg"])
        except (KeyError, TypeError, ValueError):
            continue
        limit = max(plan_diag, _diag_arcmin(t.get("fov_arcmin") or (0, 0))) / 2.0 / 60.0
        if limit > 0 and _sep_deg(plan_ra, plan_dec, ra, dec) <= limit:
            out.append(t)
    return out


# --- Aggregation ------------------------------------------------------------

def _hours_by_filter(targets: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in targets:
        for fname, cfg in (t.get("filters") or {}).items():
            key = canon_filter(fname) or fname
            out[key] = out.get(key, 0.0) + float((cfg or {}).get("total_hours") or 0)
    return out


def _last_imaged(targets: list[dict]) -> str | None:
    dates = []
    for t in targets:
        dr = t.get("date_range") or []
        if len(dr) == 2 and dr[1]:
            dates.append(str(dr[1])[:10])
    return max(dates) if dates else None


def _project_entry(plan: dict, manifest_targets: list[dict], gear: dict, today: date) -> dict:
    tg = plan.get("target") or {}
    ra = float(tg.get("center_ra_deg") or 0) % 360.0
    dec = float(tg.get("center_dec_deg") or 0)
    tels = {t.get("id"): t for t in gear.get("telescopes", [])}
    cams = {c.get("id"): c for c in gear.get("cameras", [])}
    telescope = tels.get(plan.get("telescope_id") or "")
    camera = cams.get(plan.get("camera_id") or "")
    fov_w, fov_h = _fov_arcmin(telescope, camera)
    mosaic = tg.get("mosaic") or {}
    ext = _mosaic_extent_arcmin(fov_w, fov_h, mosaic)
    matched = _matched_targets(ra, dec, ext, manifest_targets)
    logged = _hours_by_filter(matched)

    filters: dict[str, dict] = {}
    for fname, goal in (plan.get("filter_goals") or {}).items():
        key = canon_filter(fname) or fname
        goal = goal or {}
        # actual_hours comes from the Target Scheduler extension when it is
        # installed and is usually fresher than the last archive scan.
        done = max(logged.get(key, 0.0), float(goal.get("actual_hours") or 0))
        filters[key] = {
            "target_hours": round(float(goal.get("target_hours") or 0), 1),
            "done_hours": round(done, 1),
        }

    last = _last_imaged(matched)
    nights = (today - date.fromisoformat(last)).days if last else None

    entry = {
        "project_name": _check_free_text("project_name", str(plan.get("project_name") or "")),
        "target_name": _check_free_text("target_name", str(tg.get("name") or "")),
        "blurb": _check_free_text("public_blurb", str(plan.get("public_blurb") or "")),
        "is_current": bool(plan.get("is_current")),
        "center_ra_deg": round(ra, 2),
        "center_dec_deg": round(dec, 2),
        "fov_arcmin": [round(ext[0], 1), round(ext[1], 1)],
        "telescope": _aperture_class((telescope or {}).get("name") or ""),
        "filters": filters,
        "last_imaged": last,
        "last_imaged_nights_ago": nights,
    }
    rows, cols = int(mosaic.get("rows") or 1), int(mosaic.get("cols") or 1)
    if rows > 1 or cols > 1:
        entry["mosaic"] = {"rows": rows, "cols": cols}
    return entry


def build_shooting_document(plans: list[dict], manifest: dict | None, gear: dict,
                            now: datetime | None = None) -> dict:
    """Pure builder. Raises RuntimeError if a path-shaped string slips in."""
    now = now or datetime.now(timezone.utc).astimezone()
    today = now.date()
    targets = list((manifest or {}).get("targets") or [])
    scan = str((manifest or {}).get("scan_date") or "")[:10] or None
    projects = [
        _project_entry(p, targets, gear, today)
        for p in plans
        if p.get("visibility") == "public"
    ]

    def _key(p: dict):
        last = p["last_imaged"]
        return (last is None, -date.fromisoformat(last).toordinal() if last else 0, p["project_name"].lower())

    projects.sort(key=_key)
    doc = {
        "version": DOC_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "data_as_of": scan,
        "projects": projects,
    }
    validate_no_paths(doc)
    return doc


# --- Write and push ---------------------------------------------------------

def resolve_dest() -> str:
    dest = (os.environ.get("ACP_PUBLISH_DEST") or "").strip()
    if not dest:
        raise PublishConfigError(
            "ACP_PUBLISH_DEST is not set. Example: "
            "user@your-web-host:/var/www/site/live/shooting.json"
        )
    return dest


def write_document(doc: dict, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "shooting.json"
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, target)
    return target


def push_document(path: Path, dest: str, ssh_key: str | None = None) -> subprocess.CompletedProcess:
    """rsync one file to dest. The push is always initiated from this machine."""
    ssh_cmd = "ssh -o BatchMode=yes"
    if ssh_key:
        ssh_cmd += f" -i {shlex.quote(ssh_key)}"
    cmd = ["rsync", "-az", "-e", ssh_cmd, str(path), dest]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)
