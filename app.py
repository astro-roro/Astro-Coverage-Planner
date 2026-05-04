#!/usr/bin/env python
"""Astro Coverage Planner — Flask server.

Serves an Aladin Lite viewer over a sky-coverage manifest describing which
targets you've imaged, in what filters, for how long. See README.md for the
manifest schema.

Endpoints:
- GET /                          frontend
- GET /api/manifest              slim manifest (no large path lists)
- GET /api/target/<id>           full per-target detail
- GET /api/catalogs              optional overlay catalogs
- GET /api/sources               list of registered coverage sources
- GET /api/observability         altaz for all targets at (lat, lon, time)
- GET /api/export/priority       CSV of gap-mode candidates

Env:
- MANIFEST_PATH   path to archive manifest JSON  (default: ./data/manifest.json)
- CATALOGS_PATH   path to catalogs JSON          (default: ./data/catalogs.json)
- HOST            bind host                      (default: 127.0.0.1)
- PORT            bind port                      (default: 5555)
- ACP_EXTENSIONS_DIR  directory of optional extension modules (default: unset)
- ACP_FRIEND_MANIFESTS  semicolon-separated paths to sanitised friend manifests (default: unset)
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import sqlite3
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from flask import Flask, jsonify, render_template, request

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = Path(os.environ.get("MANIFEST_PATH", REPO_ROOT / "data" / "manifest.json"))
CATALOGS_PATH = Path(os.environ.get("CATALOGS_PATH", REPO_ROOT / "data" / "catalogs.json"))
GEAR_PATH = Path(os.environ.get("GEAR_PATH", REPO_ROOT / "data" / "gear.json"))
PLANS_PATH = Path(os.environ.get("PLANS_PATH", REPO_ROOT / "data" / "plans.json"))
TARGET_OVERRIDES_PATH = Path(os.environ.get(
    "TARGET_OVERRIDES_PATH", REPO_ROOT / "data" / "target_overrides.json"))
TS_DB_PATH = os.environ.get(
    "TS_DB_PATH",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "NINA" / "SchedulerPlugin" / "schedulerdb.sqlite"),
)
ZIP_OUTPUT_DIR = Path(os.environ.get("ZIP_OUTPUT_DIR", REPO_ROOT / "data" / "exports"))

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # dev: browsers revalidate every request
app.jinja_env.auto_reload = True

if not CATALOGS_PATH.exists():
    print(
        f"[acp] WARN: catalogs file not found at {CATALOGS_PATH} — "
        "right-rail catalog overlays (Green SNR / SMGPS / EMU / WISE HII) will be empty. "
        "Run scripts/fetch_catalogs.py to populate (network I/O, ~30s)."
    )

_manifest_cache: dict | None = None
_manifest_cache_mtime: float | None = None
_catalogs_cache: dict | None = None
_catalogs_cache_mtime: float | None = None
_gear_cache: dict | None = None
_gear_cache_mtime: float | None = None
_plans_cache: dict | None = None
_plans_cache_mtime: float | None = None
_target_overrides_cache: dict | None = None
_target_overrides_cache_mtime: float | None = None


def load_manifest() -> dict | None:
    global _manifest_cache, _manifest_cache_mtime
    if not MANIFEST_PATH.exists():
        return None
    mtime = MANIFEST_PATH.stat().st_mtime
    if _manifest_cache is None or _manifest_cache_mtime != mtime:
        _manifest_cache = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        _manifest_cache_mtime = mtime
    return _manifest_cache


def load_catalogs() -> dict:
    global _catalogs_cache, _catalogs_cache_mtime
    if not CATALOGS_PATH.exists():
        _catalogs_cache = {}
        _catalogs_cache_mtime = None
        return _catalogs_cache
    mtime = CATALOGS_PATH.stat().st_mtime
    if _catalogs_cache is None or _catalogs_cache_mtime != mtime:
        _catalogs_cache = json.loads(CATALOGS_PATH.read_text(encoding="utf-8"))
        _catalogs_cache_mtime = mtime
    return _catalogs_cache


def load_gear() -> dict:
    global _gear_cache, _gear_cache_mtime
    if not GEAR_PATH.exists():
        return {"version": 1, "presets": []}
    mtime = GEAR_PATH.stat().st_mtime
    if _gear_cache is None or _gear_cache_mtime != mtime:
        _gear_cache = json.loads(GEAR_PATH.read_text(encoding="utf-8"))
        _gear_cache_mtime = mtime
    return _gear_cache


def load_plans() -> dict:
    global _plans_cache, _plans_cache_mtime
    if not PLANS_PATH.exists():
        return {"version": 1, "plans": []}
    mtime = PLANS_PATH.stat().st_mtime
    if _plans_cache is None or _plans_cache_mtime != mtime:
        _plans_cache = json.loads(PLANS_PATH.read_text(encoding="utf-8"))
        _plans_cache_mtime = mtime
    return _plans_cache


def save_plans(data: dict) -> None:
    global _plans_cache, _plans_cache_mtime
    PLANS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLANS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _plans_cache = data
    _plans_cache_mtime = PLANS_PATH.stat().st_mtime


def load_target_overrides() -> dict:
    global _target_overrides_cache, _target_overrides_cache_mtime
    if not TARGET_OVERRIDES_PATH.exists():
        return {"version": 1, "overrides": {}}
    mtime = TARGET_OVERRIDES_PATH.stat().st_mtime
    if _target_overrides_cache is None or _target_overrides_cache_mtime != mtime:
        _target_overrides_cache = json.loads(TARGET_OVERRIDES_PATH.read_text(encoding="utf-8"))
        _target_overrides_cache_mtime = mtime
    return _target_overrides_cache


def save_target_overrides(data: dict) -> None:
    global _target_overrides_cache, _target_overrides_cache_mtime
    TARGET_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    TARGET_OVERRIDES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _target_overrides_cache = data
    _target_overrides_cache_mtime = TARGET_OVERRIDES_PATH.stat().st_mtime


class JsonManifestSource:
    """Coverage source backed by a JSON manifest file on local disk.

    Drives both the user's own archive (path = MANIFEST_PATH) and any
    sanitised friend manifests configured via ACP_FRIEND_MANIFESTS. The
    manifest shape is identical for both — the only difference is the
    surfaced metadata (label, kind, attribution).
    """

    def __init__(self, *, source_id: str, label: str, color: str,
                 attribution: str, enabled_default: bool, path: Path | str,
                 kind: str = "manifest") -> None:
        self._source_id = source_id
        self._label = label
        self._color = color
        self._attribution = attribution
        self._enabled_default = enabled_default
        self._path = Path(path)
        self._kind = kind
        self._cache: dict | None = None
        self._cache_mtime: float | None = None

    def id(self) -> str:
        return self._source_id

    def metadata(self) -> dict:
        return {
            "label": self._label,
            "color": self._color,
            "kind": self._kind,
            "attribution": self._attribution,
            "enabled_default": self._enabled_default,
        }

    def _load(self) -> dict | None:
        if not self._path.exists():
            return None
        mtime = self._path.stat().st_mtime
        if self._cache is None or self._cache_mtime != mtime:
            self._cache = json.loads(self._path.read_text(encoding="utf-8"))
            self._cache_mtime = mtime
        return self._cache

    def coverage(self):
        manifest = self._load()
        if not manifest:
            return
        for t in manifest.get("targets", []) or []:
            corners = t.get("corners_icrs") or []
            vertices = [(float(ra), float(dec)) for ra, dec in corners]
            filters = {
                fname: {
                    "hours": float(d.get("total_hours", 0.0)),
                    "files": int(d.get("files", 0)),
                }
                for fname, d in (t.get("filters") or {}).items()
            }
            yield {
                "kind": "polygon",
                "vertices": vertices,
                "filters": filters,
                "name": ", ".join(t.get("objects") or []),
                "metadata": {
                    "target_id": t.get("target_id"),
                    "telescopes": list(t.get("telescopes") or []),
                },
            }


def ManifestCoverageSource() -> JsonManifestSource:
    """Built-in source backed by the user's own archive manifest."""
    return JsonManifestSource(
        source_id="manifest",
        label="Your archive",
        color="",
        attribution="Local archive scan",
        enabled_default=True,
        path=MANIFEST_PATH,
        kind="manifest",
    )


def FriendManifestSource(*, source_id: str, label: str, color: str,
                         path: Path | str) -> JsonManifestSource:
    """Sanitised manifest shared by another imager."""
    return JsonManifestSource(
        source_id=source_id,
        label=label,
        color=color,
        attribution=f"Shared by {label}",
        enabled_default=True,
        path=path,
        kind="friend",
    )


# Caps applied during friend-manifest validation. Loose enough that any
# legitimate hobby archive will fit, tight enough that a malformed (or
# malicious) file can't blow up parse cost or memory.
_FRIEND_MAX_TARGETS = 10000
_FRIEND_MAX_VERTICES = 64
_FRIEND_MAX_POLYGONS = 64


def _validate_friend_manifest(data: object, source_label: str) -> None:
    """Reject any sanitised manifest that fails the safety checks.

    Raises ValueError on the first failure. The caller logs and skips —
    a bad friend manifest must not block the app from starting.
    """
    if not isinstance(data, dict):
        raise ValueError("top-level value is not an object")
    # Tripwire: refuse anything not explicitly produced by the sanitiser.
    # If a user accidentally points ACP_FRIEND_MANIFESTS at their own raw
    # manifest, their filesystem paths would surface in the public UI.
    if not data.get("sanitised"):
        raise ValueError("missing 'sanitised: true' flag — refusing to load "
                         "(probably an unsanitised manifest)")
    targets = data.get("targets")
    if not isinstance(targets, list):
        raise ValueError("'targets' is not a list")
    if len(targets) > _FRIEND_MAX_TARGETS:
        raise ValueError(
            f"target count {len(targets)} exceeds cap {_FRIEND_MAX_TARGETS}"
        )
    for i, t in enumerate(targets):
        if not isinstance(t, dict):
            raise ValueError(f"targets[{i}] is not an object")
        corners = t.get("corners_icrs") or []
        if not isinstance(corners, list):
            raise ValueError(f"targets[{i}].corners_icrs is not a list")
        # corners_icrs in the sanitiser is a single polygon (list of [ra,dec]
        # pairs). Treat the whole list as one polygon for vertex counting,
        # but defend against a future shape with multiple polygons too.
        if corners and isinstance(corners[0], (list, tuple)) and corners[0] \
                and isinstance(corners[0][0], (list, tuple)):
            polygons = corners
        else:
            polygons = [corners]
        if len(polygons) > _FRIEND_MAX_POLYGONS:
            raise ValueError(
                f"targets[{i}] has {len(polygons)} polygons "
                f"(cap {_FRIEND_MAX_POLYGONS})"
            )
        for j, poly in enumerate(polygons):
            if not isinstance(poly, list):
                raise ValueError(
                    f"targets[{i}].corners_icrs[{j}] is not a list"
                )
            if len(poly) > _FRIEND_MAX_VERTICES:
                raise ValueError(
                    f"targets[{i}] polygon {j} has {len(poly)} vertices "
                    f"(cap {_FRIEND_MAX_VERTICES})"
                )
    # Final safety net — scan the entire object for path-shaped strings.
    # Imported lazily so app startup doesn't pay the cost when the env var
    # is unset.
    _scripts_dir = str(REPO_ROOT / "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from sanitise_manifest import validate_no_paths  # noqa: WPS433
    validate_no_paths(data)


# Coverage-source registry. Extensions can append their own sources to this
# list inside their `register(app)` body; the built-in manifest source ships
# pre-registered so a stock checkout always exposes one entry on /api/sources.
app.coverage_sources = [ManifestCoverageSource()]


# Friend manifests — semicolon-separated paths in ACP_FRIEND_MANIFESTS. Each
# is validated (sanitised flag + caps + path-leak scan) before joining the
# registry; failures log a warning and skip without crashing the app.
_friend_paths = os.environ.get("ACP_FRIEND_MANIFESTS", "").strip()
if _friend_paths:
    for raw_path in _friend_paths.split(";"):
        raw_path = raw_path.strip()
        if not raw_path:
            continue
        p = Path(raw_path).expanduser()
        if not p.is_file():
            logging.warning(
                "ACP_FRIEND_MANIFESTS: path not found, skipping: %s", p
            )
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _validate_friend_manifest(data, source_label=p.stem)
        except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            logging.warning(
                "ACP_FRIEND_MANIFESTS: skipping %s — %s", p, exc
            )
            continue
        label = (data.get("friend_label") or p.stem) or "Friend"
        # Path-traversal-safe: stem already strips dirs; sanitise to alnum/_/-
        # so the id is URL-safe even for adversarial filenames.
        raw_id = f"friend_{p.stem.lower()}"
        source_id = "".join(
            c if c.isalnum() or c in "_-" else "_" for c in raw_id
        )
        app.coverage_sources.append(FriendManifestSource(
            source_id=source_id, label=label, color="", path=p,
        ))
        logging.info("Loaded friend manifest: %s (%s)", label, p)


# Optional extensions — load any user-supplied modules from the directory
# named by ACP_EXTENSIONS_DIR. No-op when the env var is unset or the loader
# is absent, so a stock checkout runs unchanged. Loaded after the built-in
# source registers so extensions can read or extend `app.coverage_sources`.
try:
    from extensions import load_extensions
    load_extensions(app)
except ImportError:
    pass


def _fov_arcmin(telescope: dict | None, camera: dict | None) -> list[float]:
    """Derive FOV from a telescope + camera pair. arcsec/px = 206.265 × pixel_um / fl_mm."""
    if not telescope or not camera:
        return [0.0, 0.0]
    try:
        fl = float(telescope["focal_length_mm"])
        px_um = float(camera["pixel_size_um"])
        w_px, h_px = camera["sensor_px"]
    except (KeyError, ValueError, TypeError):
        return [0.0, 0.0]
    if fl <= 0:
        return [0.0, 0.0]
    arcsec_per_px = 206.265 * px_um / fl
    return [round(w_px * arcsec_per_px / 60.0, 2), round(h_px * arcsec_per_px / 60.0, 2)]


def save_gear(data: dict) -> None:
    global _gear_cache, _gear_cache_mtime
    GEAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEAR_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _gear_cache = data
    _gear_cache_mtime = GEAR_PATH.stat().st_mtime


# Normalize a telescope/camera name for fuzzy matching. Mirrors _normTelName
# in static/app.js so client and server agree on what counts as "the same rig".
_NORM_STRIP = re.compile(r"\b(apo|pro|mk[\s-]*[ivx]+|edge[\s-]*hd|hd|f\/?\d+(\.\d+)?|mm|inch|in|\"|zwo|qhy|celestron|svbony|sky[-\s]*watcher|williams?[\s-]*optics?|askar|takahashi)\b")
_NORM_WS = re.compile(r"[^a-z0-9]+")

def _norm_gear_name(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = _NORM_STRIP.sub(" ", s)
    s = _NORM_WS.sub(" ", s)
    return s.strip()


def _slug_id(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip()).strip("-").lower()
    return s or "item"


def _looks_truncated(name: str) -> bool:
    if not name:
        return True
    return name.count("(") != name.count(")") or name.count("[") != name.count("]")


def _extract_gear_from_manifest() -> dict:
    """Walk the manifest and collect telescope + camera metadata observed in the
    data. Pulls whatever the manifest exposes today (telescope names, filters per
    telescope, pix_arcsec, fov_arcmin) and also looks for richer FITS-derived
    fields (camera, focal_length_mm, pixel_size_um, aperture_mm) if the upstream
    manifest builder has started surfacing them — safe if absent."""
    manifest = load_manifest()
    if not manifest:
        return {"telescopes": [], "cameras": []}
    tels: dict[str, dict] = {}
    cams: dict[str, dict] = {}
    for target in manifest.get("targets", []) or []:
        for name in (target.get("telescopes") or []):
            if name and not _looks_truncated(name):
                tels.setdefault(name, {"filters": set(), "pix_arcsecs": [], "fovs": [],
                                       "focal_mm": [], "aperture_mm": [], "cameras_seen": set()})
        for m in (target.get("per_master_fov") or []):
            name = m.get("telescope")
            if not name or _looks_truncated(name):
                continue
            t = tels.setdefault(name, {"filters": set(), "pix_arcsecs": [], "fovs": [],
                                       "focal_mm": [], "aperture_mm": [], "cameras_seen": set()})
            if m.get("filter"): t["filters"].add(m["filter"])
            if m.get("pix_arcsec"):
                try: t["pix_arcsecs"].append(float(m["pix_arcsec"]))
                except (TypeError, ValueError): pass
            fov = m.get("fov_arcmin")
            if isinstance(fov, (list, tuple)) and len(fov) == 2:
                try: t["fovs"].append([float(fov[0]), float(fov[1])])
                except (TypeError, ValueError): pass
            for k, bucket in (("focal_length_mm", "focal_mm"), ("aperture_mm", "aperture_mm")):
                v = m.get(k)
                if v is not None:
                    try: t[bucket].append(float(v))
                    except (TypeError, ValueError): pass
            cam_name = m.get("camera") or m.get("instrument")
            if cam_name and not _looks_truncated(cam_name):
                t["cameras_seen"].add(cam_name)
                c = cams.setdefault(cam_name, {"filters": set(), "pixel_um": [], "sensor_px": []})
                if m.get("filter"): c["filters"].add(m["filter"])
                if m.get("pixel_size_um"):
                    try: c["pixel_um"].append(float(m["pixel_size_um"]))
                    except (TypeError, ValueError): pass
                sensor = m.get("sensor_px")
                if isinstance(sensor, (list, tuple)) and len(sensor) == 2:
                    try: c["sensor_px"].append([int(sensor[0]), int(sensor[1])])
                    except (TypeError, ValueError): pass

    def _median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None

    telescopes = []
    for name, info in tels.items():
        telescopes.append({
            "name": name,
            "filters": sorted(info["filters"]),
            "focal_length_mm": _median(info["focal_mm"]) or 0,
            "aperture_mm": _median(info["aperture_mm"]) or 0,
            "observed_pix_arcsec": round(_median(info["pix_arcsecs"]), 3) if info["pix_arcsecs"] else None,
            "observed_fov_arcmin": _median([f[0] for f in info["fovs"]]) if info["fovs"] else None,
            "cameras_seen": sorted(info["cameras_seen"]),
        })
    cameras = []
    for name, info in cams.items():
        cameras.append({
            "name": name,
            "filters": sorted(info["filters"]),
            "pixel_size_um": _median(info["pixel_um"]) or 0,
            "sensor_px": info["sensor_px"][-1] if info["sensor_px"] else None,
        })
    return {"telescopes": telescopes, "cameras": cameras}


def _seed_gear_from_manifest() -> dict:
    """Idempotent: merge unseen manifest telescopes + cameras into gear.json.
    Uses normalized-name fuzzy matching so existing gear isn't duplicated."""
    extracted = _extract_gear_from_manifest()
    gear = load_gear()
    gear.setdefault("version", 2)
    telescopes = gear.setdefault("telescopes", [])
    cameras = gear.setdefault("cameras", [])

    existing_tel_norms = {_norm_gear_name(t.get("name", "")) for t in telescopes} - {""}
    existing_tel_ids = {t.get("id") for t in telescopes}
    existing_cam_norms = {_norm_gear_name(c.get("name", "")) for c in cameras} - {""}
    existing_cam_ids = {c.get("id") for c in cameras}

    # Default filter config for a camera seeded from observed filter names.
    def _default_filter_cfg(f):
        is_narrow = f in ("Ha", "OIII", "SII", "NII", "OI")
        return {
            "ts_template_id": None, "ts_template_name": None,
            "default_sub_s": 300 if is_narrow else 120,
            "gain": -1, "offset": -1, "bin": 1,
        }

    added_tels = []
    for info in extracted["telescopes"]:
        if _norm_gear_name(info["name"]) in existing_tel_norms:
            continue
        tel_id = _slug_id(info["name"])
        base = tel_id; n = 2
        while tel_id in existing_tel_ids:
            tel_id = f"{base}-{n}"; n += 1
        new_tel = {
            "id": tel_id,
            "name": info["name"],
            "focal_length_mm": info["focal_length_mm"] or 0,
            "aperture_mm": info["aperture_mm"] or 0,
        }
        if info["observed_pix_arcsec"] is not None:
            new_tel["observed_pix_arcsec"] = info["observed_pix_arcsec"]
        if info["observed_fov_arcmin"] is not None:
            new_tel["observed_fov_arcmin"] = round(info["observed_fov_arcmin"], 2)
        telescopes.append(new_tel)
        added_tels.append(info["name"])
        existing_tel_norms.add(_norm_gear_name(info["name"]))
        existing_tel_ids.add(tel_id)

    added_cams = []
    for info in extracted["cameras"]:
        if _norm_gear_name(info["name"]) in existing_cam_norms:
            continue
        cam_id = _slug_id(info["name"])
        base = cam_id; n = 2
        while cam_id in existing_cam_ids:
            cam_id = f"{base}-{n}"; n += 1
        cameras.append({
            "id": cam_id,
            "name": info["name"],
            "pixel_size_um": info["pixel_size_um"] or 0,
            "sensor_px": info["sensor_px"] or [0, 0],
            "filters": {f: _default_filter_cfg(f) for f in info["filters"]},
        })
        added_cams.append(info["name"])
        existing_cam_norms.add(_norm_gear_name(info["name"]))
        existing_cam_ids.add(cam_id)

    if added_tels or added_cams:
        save_gear(gear)
    return {
        "ok": True,
        "added_telescopes": added_tels,
        "added_cameras": added_cams,
        "scanned_telescopes": [t["name"] for t in extracted["telescopes"]],
        "scanned_cameras": [c["name"] for c in extracted["cameras"]],
    }


@app.route("/api/gear/seed", methods=["POST"])
def api_gear_seed():
    return jsonify(_seed_gear_from_manifest())


@app.route("/")
def index():
    m = load_manifest()
    if m is None:
        return (
            f"Manifest not found at {MANIFEST_PATH}. "
            "See README.md for how to build one, or set MANIFEST_PATH.",
            500,
        )
    return render_template(
        "index.html",
        total_targets=m.get("total_targets", 0),
        total_hours=m.get("total_integration_hours", 0),
        scan_date=(m.get("scan_date") or "")[:10],
    )


@app.route("/api/manifest")
def api_manifest():
    m = load_manifest()
    if m is None:
        return jsonify({"error": "manifest not found"}), 404
    slim_targets = []
    for t in m["targets"]:
        ft = {f: {k: v for k, v in d.items() if k != "paths"} for f, d in t["filters"].items()}
        slim_targets.append({**t, "filters": ft})
    return jsonify({
        "scan_date": m.get("scan_date"),
        "total_targets": m.get("total_targets"),
        "total_integration_hours": m.get("total_integration_hours"),
        "targets": slim_targets,
    })


@app.route("/api/target/<int:target_id>")
def api_target(target_id: int):
    m = load_manifest()
    if m is None:
        return jsonify({"error": "manifest not found"}), 404
    for t in m["targets"]:
        if t["target_id"] == target_id:
            return jsonify(t)
    return jsonify({"error": "not found"}), 404


@app.route("/api/catalogs")
def api_catalogs():
    return jsonify(load_catalogs())


@app.route("/api/sources")
def api_sources():
    """List of registered coverage sources for the frontend Sources rail."""
    out = []
    for src in app.coverage_sources:
        meta = src.metadata()
        out.append({
            "id": src.id(),
            "label": meta["label"],
            "color": meta["color"],
            "kind": meta["kind"],
            "attribution": meta["attribution"],
            "enabled_default": meta["enabled_default"],
        })
    return jsonify(out)


def _clamped_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        v = float(request.args.get(name, default))
    except (TypeError, ValueError):
        v = default
    if v != v or v < lo or v > hi:
        v = default
    return v


@app.route("/api/observability")
def api_observability():
    try:
        from astropy.coordinates import AltAz, EarthLocation, SkyCoord
        from astropy.time import Time
        import astropy.units as u
    except Exception as e:
        return jsonify({"error": f"astropy not available: {e}"}), 500

    lat = _clamped_float("lat", -33.87, -90.0, 90.0)
    lon = _clamped_float("lon", 151.21, -180.0, 180.0)
    height = _clamped_float("height", 20.0, -430.0, 9000.0)
    iso = request.args.get("time") or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    try:
        t = Time(iso)
    except Exception:
        return jsonify({"error": "invalid time"}), 400

    m = load_manifest()
    if m is None:
        return jsonify({"error": "manifest not found"}), 404
    if not m["targets"]:
        return jsonify({"lat": lat, "lon": lon, "time": iso, "targets": []})

    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=height * u.m)
    ras = [tg["center_ra_deg"] for tg in m["targets"]]
    decs = [tg["center_dec_deg"] for tg in m["targets"]]
    sc = SkyCoord(ras * u.deg, decs * u.deg)
    altaz = sc.transform_to(AltAz(obstime=t, location=loc))
    out = [
        {"target_id": tg["target_id"], "alt_deg": round(float(alt), 2), "az_deg": round(float(az), 2)}
        for tg, alt, az in zip(m["targets"], altaz.alt.deg, altaz.az.deg)
    ]
    return jsonify({"lat": lat, "lon": lon, "time": iso, "targets": out})


@app.route("/api/export/priority")
def api_export_priority():
    """CSV of overlay candidates where Ha >= 1h but SII < 0.5h (validation-gap bucket)."""
    cats = load_catalogs()
    m = load_manifest()
    if m is None or not cats:
        return ("manifest or catalogs missing", 404)

    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
    except Exception:
        return ("astropy missing", 500)

    target_ras = [t["center_ra_deg"] for t in m["targets"]]
    target_decs = [t["center_dec_deg"] for t in m["targets"]]
    target_coords = (
        SkyCoord(target_ras * u.deg, target_decs * u.deg) if target_ras else None
    )

    out = StringIO()
    w = csv.writer(out)
    w.writerow([
        "catalog", "name", "ra_deg", "dec_deg", "l_deg", "b_deg",
        "overlap_target_id", "overlap_target_objects",
        "ha_hours", "sii_hours", "oiii_hours",
    ])

    for cat_name, entries in cats.items():
        if cat_name not in ("green_snrs", "smgps_candidates", "emu_candidates"):
            continue
        valid = [e for e in entries if e.get("ra_deg") is not None and e.get("dec_deg") is not None]
        if not valid:
            continue
        if target_coords is not None:
            cras = [e["ra_deg"] for e in valid]
            cdecs = [e["dec_deg"] for e in valid]
            cand = SkyCoord(cras * u.deg, cdecs * u.deg)
            # match_to_catalog_sky returns nearest target for each candidate
            idx, sep, _ = cand.match_to_catalog_sky(target_coords)
            sep_arcmin = sep.arcminute
        else:
            idx = [None] * len(valid)
            sep_arcmin = [None] * len(valid)

        for e, best_idx, best_sep in zip(valid, idx, sep_arcmin):
            match_tid, match_objs = "", ""
            ha = sii = oiii = 0.0
            if target_coords is not None and best_sep is not None and best_sep <= 45:
                t = m["targets"][int(best_idx)]
                match_tid = t["target_id"]
                match_objs = "|".join(t.get("objects", []))
                ha = t["filters"].get("Ha", {}).get("total_hours", 0.0)
                sii = t["filters"].get("SII", {}).get("total_hours", 0.0)
                oiii = t["filters"].get("OIII", {}).get("total_hours", 0.0)
            if ha >= 1.0 and sii < 0.5:
                w.writerow([
                    cat_name, e.get("name", ""), e["ra_deg"], e["dec_deg"],
                    e.get("l_deg", ""), e.get("b_deg", ""),
                    match_tid, match_objs, ha, sii, oiii,
                ])

    return out.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=priority_sii_targets.csv",
    }


@app.route("/api/gear", methods=["GET", "POST"])
def api_gear():
    if request.method == "GET":
        g = load_gear()
        return jsonify({
            "version": g.get("version", 2),
            "telescopes": g.get("telescopes", []),
            "cameras": g.get("cameras", []),
        })
    payload = request.get_json(silent=True) or {}
    if "telescopes" not in payload or "cameras" not in payload:
        return jsonify({"error": "telescopes and cameras arrays required"}), 400
    save_gear({
        "version": 2,
        "telescopes": payload.get("telescopes", []),
        "cameras": payload.get("cameras", []),
    })
    return jsonify({"ok": True})


@app.route("/api/plans", methods=["GET", "POST"])
def api_plans():
    data = load_plans()
    if request.method == "GET":
        return jsonify(data)
    payload = request.get_json(silent=True) or {}
    if "id" not in payload or not payload["id"]:
        return jsonify({"error": "id required"}), 400
    now = datetime.now(timezone.utc).isoformat()
    payload.setdefault("guid", str(uuid.uuid4()))
    payload.setdefault("created_at", now)
    payload.setdefault("last_synced_at", None)
    payload["updated_at"] = now
    plans = [p for p in data.get("plans", []) if p.get("id") != payload["id"]]
    plans.append(payload)
    save_plans({"version": data.get("version", 1), "plans": plans})
    return jsonify(payload), 201


@app.route("/api/plans/<plan_id>", methods=["GET", "PUT", "DELETE"])
def api_plan(plan_id: str):
    data = load_plans()
    plans = data.get("plans", [])
    idx = next((i for i, p in enumerate(plans) if p.get("id") == plan_id), None)
    if idx is None:
        return jsonify({"error": "not found"}), 404
    if request.method == "GET":
        return jsonify(plans[idx])
    if request.method == "DELETE":
        plans.pop(idx)
        save_plans({"version": data.get("version", 1), "plans": plans})
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    existing = plans[idx]
    payload["id"] = plan_id
    payload["guid"] = existing.get("guid") or str(uuid.uuid4())
    payload["created_at"] = existing.get("created_at") or datetime.now(timezone.utc).isoformat()
    payload.setdefault("last_synced_at", existing.get("last_synced_at"))
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    plans[idx] = payload
    save_plans({"version": data.get("version", 1), "plans": plans})
    return jsonify(payload)


@app.route("/api/target-overrides", methods=["GET", "POST"])
def api_target_overrides():
    """Per-target state the user sets manually — primarily the finished flag for
    targets that have no plan but should still be treated as done. Keyed by
    target_id (string) to survive JSON round-trips cleanly."""
    data = load_target_overrides()
    if request.method == "GET":
        return jsonify({
            "version": data.get("version", 1),
            "overrides": data.get("overrides", {}),
        })
    payload = request.get_json(silent=True) or {}
    tid = payload.get("target_id")
    if tid is None or (isinstance(tid, str) and not tid.strip()):
        return jsonify({"error": "target_id required"}), 400
    key = str(tid)
    overrides = dict(data.get("overrides", {}))
    # A null/missing "finished" deletes the override so the target falls back to
    # plan-derived status — this is how the frontend clears a manual mark.
    if "finished" not in payload or payload["finished"] is None:
        overrides.pop(key, None)
    else:
        overrides[key] = {
            "finished": bool(payload["finished"]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    save_target_overrides({"version": data.get("version", 1), "overrides": overrides})
    return jsonify({"ok": True, "overrides": overrides})


@app.route("/api/ts-templates")
def api_ts_templates():
    """Read-only dump of user's Target Scheduler ExposureTemplates. Falls back gracefully if DB unreachable."""
    path = Path(os.path.expandvars(TS_DB_PATH))
    if not path.exists():
        return jsonify({
            "available": False,
            "path": str(path),
            "error": "TS database not found at configured path",
            "templates": [],
        })
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT Id, profileId, name, filtername, defaultexposure, gain, offset, bin "
            "FROM exposuretemplate ORDER BY filtername, name"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return jsonify({
            "available": False,
            "path": str(path),
            "error": f"sqlite: {e}",
            "templates": [],
        })
    templates = [{
        "id": r["Id"],
        "profile_id": r["profileId"],
        "name": r["name"],
        "filter": r["filtername"],
        "default_exposure_s": r["defaultexposure"],
        "gain": r["gain"],
        "offset": r["offset"],
        "bin": r["bin"],
    } for r in rows]
    return jsonify({"available": True, "path": str(path), "templates": templates})


PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2}


def _mosaic_panel_centers(ra_c: float, dec_c: float, fov_w_arcmin: float, fov_h_arcmin: float,
                          rot_deg: float, rows: int, cols: int, overlap_pct: float) -> list[dict]:
    """Return per-panel centers [{row, col, ra_deg, dec_deg}] for an rows×cols mosaic
    anchored on (ra_c, dec_c). Panel stride = fov × (1 − overlap). Row 0 is north."""
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    overlap = max(0.0, min(0.99, float(overlap_pct) / 100.0))
    # stride in degrees (camera-frame)
    stride_w = (fov_w_arcmin / 60.0) * (1.0 - overlap)
    stride_h = (fov_h_arcmin / 60.0) * (1.0 - overlap)
    R = math.radians(rot_deg or 0)
    cosR = math.cos(R)
    sinR = math.sin(R)
    cosD = max(1e-6, math.cos(math.radians(dec_c)))
    panels = []
    for i in range(rows):
        for j in range(cols):
            # Camera-frame offset: row 0 at top (+camy = north when rot=0), col 0 at left
            cx = (j - (cols - 1) / 2.0) * stride_w
            cy = ((rows - 1) / 2.0 - i) * stride_h
            # Rotate into sky (east, north) — camera-Y sits at PA east-of-north
            de =  cx * cosR + cy * sinR
            dn = -cx * sinR + cy * cosR
            panels.append({
                "row": i, "col": j,
                "ra_deg": ra_c + de / cosD,
                "dec_deg": dec_c + dn,
            })
    return panels


def _build_ts_export(plans_list: list, gear_data: dict) -> tuple[dict, list]:
    """Group plans by project_name, apply strictest-wins for constraints, expand
    mosaics into per-panel targets, and emit TS-shape project + exposureTemplate
    records. Returns (payload, warnings)."""
    telescopes_by_id = {t["id"]: t for t in gear_data.get("telescopes", [])}
    cameras_by_id = {c["id"]: c for c in gear_data.get("cameras", [])}

    projects_by_name: dict[str, list] = {}
    for pl in plans_list:
        pname = (pl.get("project_name") or "").strip() or "Unassigned"
        projects_by_name.setdefault(pname, []).append(pl)

    ts_projects: list[dict] = []
    ts_templates: list[dict] = []
    template_seen: dict[tuple[str, str], dict] = {}
    warnings: list[dict] = []

    def _warn(kind: str, pname: str, resolved, group: list, suggested_suffix: str, msg: str) -> None:
        warnings.append({
            "project_name": pname, "kind": kind, "resolved": resolved,
            "plan_ids": [p["id"] for p in group],
            "plan_name": group[0].get("target", {}).get("name") or group[0]["id"],
            "plan_id": group[0]["id"],
            "suggested_name": f"{pname} ({suggested_suffix})",
            "message": msg,
        })

    for pname, group in projects_by_name.items():
        min_alt = max(float(p.get("min_altitude_deg") or 0) for p in group)
        merid_vals = [float(p.get("meridian_window_min") or 0) for p in group]
        nonzero = [v for v in merid_vals if v > 0]
        meridian = min(nonzero) if nonzero else 0.0
        pri_name = max(group, key=lambda p: PRIORITY_RANK.get(p.get("priority", "normal"), 1)).get("priority", "normal")

        if len(set(p.get("min_altitude_deg") or 0 for p in group)) > 1:
            _warn("min_altitude", pname, min_alt, group, "strict",
                  f"Min altitude differed across plans; using strictest ({min_alt}°).")
        if len(set(p.get("meridian_window_min") or 0 for p in group)) > 1:
            _warn("meridian_window", pname, meridian, group, "narrow",
                  f"Meridian window differed; using {meridian} min.")
        if len(set(p.get("priority", "normal") for p in group)) > 1:
            _warn("priority", pname, pri_name, group, pri_name,
                  f"Priority differed; using '{pri_name}'.")

        ts_targets: list[dict] = []
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

            # Build exposure plans once per plan (shared across mosaic panels)
            exp_plans: list[dict] = []
            for fname, goal in (pl.get("filter_goals") or {}).items():
                th = float(goal.get("target_hours") or 0)
                if th <= 0:
                    continue
                sub_s = int(goal.get("sub_exposure_s") or 300)
                desired = max(1, int(math.ceil(th * 3600.0 / max(1, sub_s))))
                acquired = int(round(float(goal.get("actual_hours") or 0) * 3600.0 / max(1, sub_s)))
                # Dedupe ExposureTemplate by (camera_id, filter) — gain/offset/etc live on the camera.
                key = (pl.get("camera_id") or "", fname)
                if key not in template_seen:
                    filt_cfg = (camera or {}).get("filters", {}).get(fname, {}) if camera else {}
                    tpl_name = filt_cfg.get("ts_template_name") or (
                        f"{fname} ({camera['name']})" if camera else fname
                    )
                    tpl = {
                        "name": tpl_name,
                        "filtername": fname,
                        "defaultexposure": filt_cfg.get("default_sub_s") or sub_s,
                        "gain": filt_cfg.get("gain", -1),
                        "offset": filt_cfg.get("offset", -1),
                        "bin": filt_cfg.get("bin", 1),
                    }
                    template_seen[key] = tpl
                    ts_templates.append(tpl)
                tpl = template_seen[key]
                exp_plans.append({
                    "exposure": sub_s,
                    "desired": desired,
                    "acquired": acquired,
                    "accepted": acquired,
                    "exposureTemplateName": tpl["name"],
                    "filtername": fname,
                })

            base_name = tg.get("name") or pl.get("id")
            if rows > 1 or cols > 1:
                if fov_w <= 0 or fov_h <= 0:
                    warnings.append({
                        "project_name": pname, "kind": "mosaic_no_fov", "resolved": "skipped",
                        "plan_ids": [pl["id"]], "plan_id": pl["id"],
                        "plan_name": base_name, "suggested_name": base_name,
                        "message": f"Mosaic '{base_name}' has no valid FOV (pick a telescope + camera). Emitted as single target.",
                    })
                    rows = cols = 1

            panels = _mosaic_panel_centers(ra_deg, dec_deg, fov_w, fov_h, rot_deg, rows, cols, overlap)
            multi = len(panels) > 1
            for panel in panels:
                pname_suffix = f" r{panel['row'] + 1}c{panel['col'] + 1}" if multi else ""
                ts_targets.append({
                    "name": f"{base_name}{pname_suffix}",
                    "active": True,
                    # Target Scheduler stores RA in HOURS, Dec in degrees.
                    "ra": panel["ra_deg"] / 15.0,
                    "dec": panel["dec_deg"],
                    "rotation": rot_deg,
                    "roi": 100,
                    "exposurePlans": exp_plans,
                })

        ts_projects.append({
            "name": pname,
            "description": "",
            "state": 1,  # Active
            "priority": PRIORITY_RANK.get(pri_name, 1),
            "minimumAltitude": min_alt,
            "meridianWindow": meridian,
            "filterSwitchFrequency": 0,
            "ditherEvery": 0,
            "enableGrader": False,
            "targets": ts_targets,
        })

    return {"projects": ts_projects, "exposureTemplates": ts_templates}, warnings


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """Build a Target Scheduler Import Profile zip from current plans.

    Writes metadata.json, profilePreference.json, exposureTemplates.json, and
    projects.json into a zip under ZIP_OUTPUT_DIR. Returns the zip path and any
    strictest-wins warnings so the UI can offer an inline rename and re-sync.
    """
    data = load_plans()
    pls = data.get("plans", [])
    if not pls:
        return jsonify({"error": "no plans to sync"}), 400

    payload, warnings = _build_ts_export(pls, load_gear())

    ZIP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = ZIP_OUTPUT_DIR / f"acp-sync-{stamp}.zip"

    metadata = {
        "version": 5,
        "sourceProfileName": "Astro Coverage Planner",
        "sourceDate": datetime.now(timezone.utc).isoformat(),
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))
        zf.writestr("profilePreference.json", json.dumps({}, indent=2))
        zf.writestr("exposureTemplates.json", json.dumps(payload["exposureTemplates"], indent=2))
        zf.writestr("projects.json", json.dumps(payload["projects"], indent=2))

    now = datetime.now(timezone.utc).isoformat()
    for pl in pls:
        pl["last_synced_at"] = now
    save_plans({"version": data.get("version", 1), "plans": pls})

    return jsonify({
        "ok": True,
        "plan_count": len(pls),
        "project_count": len(payload["projects"]),
        "template_count": len(payload["exposureTemplates"]),
        "zip_path": str(zip_path),
        "warnings": warnings,
        "conflicts": warnings,  # alias — the UI inspects this to offer renames
    })


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5555))
    print(f"Astro Coverage Planner → http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
