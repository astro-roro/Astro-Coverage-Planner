"""Build and publish the public "live" document for a static web page.

Spec: docs/specs/shooting-page.md. Only plans that are public (explicitly,
or by default for a "pretty" plan) and have recent activity are included. The document is scanned for path-shaped strings before it
leaves this module, using the same tripwire as the friend-manifest sanitiser.

This module has no Flask import so the CLI can use it without starting
the app. app.py imports it lazily for the two live-page endpoints.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent
_SCRIPTS = str(REPO_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from build_archive_manifest import canon_filter, sanitize_camera, sanitize_telescope  # noqa: E402
import filter_rows as fr  # noqa: E402
from sanitise_manifest import _aperture_class, validate_no_paths  # noqa: E402

DOC_VERSION = 1

# How long a project stays on the live page, counted from its last imaged
# night (or from the day it was marked finished). Change a window here and
# nowhere else. Boundaries are inclusive.
LIVE_STATUS_WINDOWS = {
    "imaging_now_nights": 3,   # last imaged within this many nights: imaging_now
    "recent_days": 14,         # last imaged within this many days: recent
    "finished_days": 30,       # finished within this many days: finished
}

_ASTROBIN_MAX_LEN = 300

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


# A site tag such as " (Dark Skies)" or " (Any Skies)" is a note for the user
# about where a project can be shot from. It means nothing to a reader.
_SITE_TAG = re.compile(r"\s*\([^()]*skies\)\s*$", re.IGNORECASE)


def strip_site_tag(name: str) -> str:
    """Drop one trailing parenthetical ending in "Skies". Published text only."""
    return _SITE_TAG.sub("", name).strip()


def _check_free_text(field: str, value: str) -> str:
    if _FREE_TEXT_LEAK.search(value):
        raise RuntimeError(f"path-shaped text in {field}: {value!r}")
    return value


class PublishConfigError(RuntimeError):
    """ACP_PUBLISH_DEST is unset or empty."""


# --- Status and visibility --------------------------------------------------

def effective_visibility(plan: dict) -> str:
    """"public" or "private". An explicit visibility always wins. With none
    set, a "pretty" plan is public and every other class is private."""
    vis = plan.get("visibility")
    if vis in ("public", "private"):
        return vis
    return "public" if plan.get("target_class") == "pretty" else "private"


def parse_finished_at(value) -> date | None:
    """A plan's finished_at as a date, or None when unset or unreadable."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def valid_astrobin_url(value) -> bool:
    """https on astrobin.com or a subdomain, no credentials, no whitespace."""
    if not isinstance(value, str) or not value or len(value) > _ASTROBIN_MAX_LEN:
        return False
    if any(c.isspace() for c in value):
        return False
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        parts.port  # raises ValueError on a bad port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    return host == "astrobin.com" or host.endswith(".astrobin.com")


def project_status(plan: dict, last: str | None, today: date) -> str | None:
    """The live-page status for a public plan, or None to leave it out.
    Checked in order: vote, finished, imaging_now, recent."""
    if plan.get("public_section") == "vote":
        return "vote"
    w = LIVE_STATUS_WINDOWS
    fin = parse_finished_at(plan.get("finished_at"))
    if fin is not None:
        # A finished project is judged by its finish date alone. Once the
        # window passes it drops out, even if frames were added since.
        return "finished" if (today - fin).days <= w["finished_days"] else None
    if last:
        nights = (today - date.fromisoformat(last)).days
        if nights <= w["imaging_now_nights"]:
            return "imaging_now"
        if nights <= w["recent_days"]:
            return "recent"
    return None


def _totals(filters: dict[str, dict]) -> tuple[float, float]:
    """Overall (done, target) hours, each filter's done capped at its target."""
    done = sum(min(f["done_hours"], f["target_hours"]) for f in filters.values())
    target = sum(f["target_hours"] for f in filters.values())
    return round(done, 1), round(target, 1)


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

_GEAR_NOISE = re.compile(r"[^a-z0-9]+")


def _gear_key(raw) -> str:
    return _GEAR_NOISE.sub("", str(raw or "").casefold())


def _rig_matches(row: dict, plan_scope: str, plan_cam: str) -> bool:
    """Whether an archive rig row was shot with the plan's rig. Compared by
    name (the manifest records the TELESCOP and INSTRUME names, not a focal
    length). A side the row or the plan does not name never excludes it."""
    row_scope, row_cam = _gear_key(row.get("telescope")), _gear_key(row.get("camera"))
    if plan_scope and row_scope and plan_scope != row_scope:
        return False
    if plan_cam and row_cam and plan_cam != row_cam:
        return False
    return True


def _filter_hours(cfg: dict, rig: tuple[str, str] | None) -> float:
    """total_hours less the headline hours of rig rows that are not the
    plan's rig. Hours with no rig row (database floor, an older manifest)
    stay in, as before."""
    total = float(cfg.get("total_hours") or 0)
    if rig is None:
        return total
    other = sum(
        float(row.get("headline_hours") or 0)
        for row in (cfg.get("rigs") or {}).values()
        if isinstance(row, dict) and not _rig_matches(row, *rig)
    )
    return max(0.0, total - other)


def _plan_rig(telescope: dict | None, camera: dict | None) -> tuple[str, str] | None:
    """The plan's telescope and camera as comparable keys, normalised the same
    way the scanner normalises header names. None when the plan names neither."""
    scope = _gear_key(sanitize_telescope((telescope or {}).get("name")))
    cam = _gear_key(sanitize_camera((camera or {}).get("name")))
    return (scope, cam) if (scope or cam) else None


def _hours_by_filter(targets: list[dict], rig: tuple[str, str] | None = None) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in targets:
        for fname, cfg in (t.get("filters") or {}).items():
            key = canon_filter(fname) or fname
            out[key] = out.get(key, 0.0) + _filter_hours(cfg or {}, rig)
    return out


def _last_imaged(targets: list[dict]) -> str | None:
    dates = []
    for t in targets:
        dr = t.get("date_range") or []
        if len(dr) == 2 and dr[1]:
            dates.append(str(dr[1])[:10])
    return max(dates) if dates else None


def _plan_context(plan: dict, manifest_targets: list[dict], gear: dict):
    """Everything both the baseline and the published entry need: geometry,
    the manifest targets that belong to the plan, and the logged hours."""
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
    return tg, ra, dec, telescope, mosaic, ext, matched, _plan_rig(telescope, camera)


def _done_and_goals(plan: dict, matched: list[dict],
                    rig: tuple[str, str] | None = None) -> dict[str, dict]:
    """Per canonical filter: the plan's goal and the hours done so far."""
    logged = _hours_by_filter(matched, rig)
    out: dict[str, dict] = {}
    for fname, goal in fr.hours_by_filter(plan).items():
        key = canon_filter(fname) or fname
        # credited_hours comes from the Target Scheduler extension when it is
        # installed and is usually fresher than the last archive scan. A
        # filter with two rows (R and R Stars) shows their sum. For a mosaic
        # each panel counts up to its own goal only, as the planner shows it.
        done = max(logged.get(key, 0.0), goal["credited_hours"])
        out[key] = {"target_hours": goal["target_hours"], "done_hours": done}
    return out


def current_done_hours(plan: dict, manifest: dict | None, gear: dict) -> dict[str, float]:
    """Hours done per canonical filter, as the live page would show them for
    a current project. Used to set a vote plan's baseline."""
    targets = list((manifest or {}).get("targets") or [])
    *_, matched, rig = _plan_context(plan, targets, gear)
    return {k: round(v["done_hours"], 3) for k, v in _done_and_goals(plan, matched, rig).items()}


def _vote_filters(plan: dict, done: dict[str, dict]) -> dict[str, dict]:
    """A vote project shows only the filters in vote_goals. The goal is the
    vote goal, and done is what was added since the plan entered the vote."""
    baseline = {
        (canon_filter(k) or k): float(v)
        for k, v in (plan.get("vote_baseline") or {}).items()
    }
    goals: dict[str, float] = {}
    for fname, hours in (plan.get("vote_goals") or {}).items():
        key = canon_filter(fname) or fname
        goals[key] = goals.get(key, 0.0) + float(hours)
    out: dict[str, dict] = {}
    for key, goal in goals.items():
        now = done.get(key, {}).get("done_hours", 0.0)
        out[key] = {
            "target_hours": round(goal, 1),
            "done_hours": round(max(0.0, now - baseline.get(key, 0.0)), 1),
        }
    return out


def _project_entry(plan: dict, manifest_targets: list[dict], gear: dict, today: date) -> dict | None:
    """The published entry for one plan, or None when its status says to leave
    it out. The caller has already checked visibility."""
    tg, ra, dec, telescope, mosaic, ext, matched, rig = _plan_context(plan, manifest_targets, gear)
    done = _done_and_goals(plan, matched, rig)
    section = "vote" if plan.get("public_section") == "vote" else "current"
    if section == "vote":
        filters = _vote_filters(plan, done)
    else:
        filters = {
            k: {"target_hours": round(v["target_hours"], 1), "done_hours": round(v["done_hours"], 1)}
            for k, v in done.items()
        }

    last = _last_imaged(matched)
    status = project_status(plan, last, today)
    if status is None:
        return None
    nights = (today - date.fromisoformat(last)).days if last else None
    done_total, target_total = _totals(filters)

    entry = {
        "project_name": _check_free_text("project_name", strip_site_tag(str(plan.get("project_name") or ""))),
        "target_name": _check_free_text("target_name", strip_site_tag(str(tg.get("name") or ""))),
        "blurb": _check_free_text("public_blurb", str(plan.get("public_blurb") or "")),
        "status": status,
        # is_current and section stay for one release so the old page renders.
        "is_current": bool(plan.get("is_current")),
        "section": section,
        "center_ra_deg": round(ra, 2),
        "center_dec_deg": round(dec, 2),
        "fov_arcmin": [round(ext[0], 1), round(ext[1], 1)],
        "telescope": _aperture_class((telescope or {}).get("name") or ""),
        "filters": filters,
        "done_hours": done_total,
        "target_hours": target_total,
        "last_imaged": last,
        "last_imaged_nights_ago": nights,
    }
    if status == "finished":
        entry["finished_at"] = parse_finished_at(plan.get("finished_at")).isoformat()
        url = plan.get("astrobin_url")
        if url and valid_astrobin_url(url):
            entry["astrobin_url"] = url
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
    entries = (
        _project_entry(p, targets, gear, today)
        for p in plans
        if effective_visibility(p) == "public"
    )
    projects = [e for e in entries if e is not None]

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


class PushResult:
    """Mirror of the subset of subprocess.CompletedProcess the callers use."""

    def __init__(self, returncode: int, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr


def parse_dest(dest: str) -> tuple[str | None, str, int, str]:
    """Split user@host:/remote/path (optional :port before the path is not
    supported; use the ssh config for that). Returns (user, host, port, path)."""
    if ":" not in dest or "/" not in dest.split(":", 1)[1]:
        raise PublishConfigError(f"ACP_PUBLISH_DEST must look like user@host:/remote/path, got {dest!r}")
    account, remote_path = dest.split(":", 1)
    user, host = (account.split("@", 1) + [None])[:2] if "@" in account else (None, account)
    if "@" in account:
        user, host = account.split("@", 1)
    return user, host, 22, remote_path


def push_document(path: Path, dest: str, ssh_key: str | None = None) -> PushResult:
    """Upload one file to dest over SFTP. The push is always initiated from
    this machine. Uses paramiko rather than an ssh binary so it works inside
    a container running as a uid with no passwd entry.

    Host keys live in known_hosts next to the document. The first contact
    records the server's key; a changed key is refused after that.
    """
    import paramiko  # imported here so the builder stays importable without it

    user, host, port, remote_path = parse_dest(dest)
    known = Path(path).parent / "known_hosts"
    class _FirstContactOnly(paramiko.MissingHostKeyPolicy):
        """Trust on first use: record the key only while known_hosts is empty.
        Once any host is recorded, an unknown or changed key is refused."""

        def missing_host_key(self, client_, hostname, key):
            if len(client_.get_host_keys()) > 0:
                raise paramiko.SSHException(
                    f"host key for {hostname} is not in {known}; refusing to connect"
                )
            client_.get_host_keys().add(hostname, key.get_name(), key)

    client = paramiko.SSHClient()
    known.parent.mkdir(parents=True, exist_ok=True)
    if not known.exists():
        known.touch()
    client.load_host_keys(str(known))
    client.set_missing_host_key_policy(_FirstContactOnly())
    try:
        client.connect(
            host, port=port, username=user, key_filename=ssh_key,
            allow_agent=ssh_key is None, look_for_keys=ssh_key is None, timeout=20,
        )
        client.save_host_keys(str(known))
        sftp = client.open_sftp()
        try:
            tmp_remote = remote_path + ".tmp"
            sftp.put(str(path), tmp_remote)
            sftp.posix_rename(tmp_remote, remote_path)
        finally:
            sftp.close()
    except Exception as e:  # paramiko raises a wide family; report, do not crash the app
        return PushResult(1, f"{type(e).__name__}: {e}")
    finally:
        client.close()
    return PushResult(0, "")
