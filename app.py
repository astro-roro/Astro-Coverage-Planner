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
- GET /api/catalog-registry      declarative list of catalogues to surface in the rail
- GET /api/tile-sources          metadata for every registered PrioritisedTilesSource
- GET /api/tiles/<source_id>     tile list for one source (filtered server-side optionally)
- GET /api/saved-searches, POST  CRUD for saved Inventory filter bundles
- DELETE /api/saved-searches/<id>
- GET /api/sources               list of registered coverage sources
- GET /api/moc/<source_id>       FITS MOC blob for a survey source (lazy-fetched, cached)
- GET /api/observability         altaz for all targets at (lat, lon, time)
- GET /api/visibility            12-month per-target visibility bins for a site
- GET /api/visibility/point      same bins for an arbitrary (ra, dec) point
- POST /api/visibility/panels    aggregated bins for a list of mosaic panels
- GET /api/sites, POST           CRUD for saved observing sites
- GET /api/export/priority       CSV of gap-mode candidates
- GET /api/gaps                  multi-source gap-finder (JSON)
- GET /api/gaps/moc.fits         gap MOC as raw FITS bytes

Env:
- MANIFEST_PATH   path to archive manifest JSON  (default: ./data/manifest.json)
- CATALOGS_PATH   path to catalogs JSON          (default: ./data/catalogs.json)
- ACP_SURVEYS_PATH  path to survey registry JSON (default: ./data/surveys.json)
- HOST            bind host                      (default: 127.0.0.1)
- PORT            bind port                      (default: 5555)
- ACP_EXTENSIONS_DIR  directory of optional extension modules (default: unset)
- ACP_FRIEND_MANIFESTS  semicolon-separated paths to sanitised friend manifests (default: unset)
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

# Reconfigure stdout/stderr to UTF-8 so non-ASCII characters in log/print
# output don't crash on Windows where the default console codec (cp1252)
# can't encode characters like em-dashes or arrows.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass

try:
    from mocpy import MOC  # noqa: F401
    _MOCPY_AVAILABLE = True
except ImportError:
    _MOCPY_AVAILABLE = False
    logging.warning(
        "mocpy is not installed — MOC overlays disabled. "
        "Install with `pip install mocpy>=0.13` to enable /api/moc/<id>."
    )

from gaps import candidates_in_moc, compute_gap_moc

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = Path(os.environ.get("MANIFEST_PATH", REPO_ROOT / "data" / "manifest.json"))
CATALOGS_PATH = Path(os.environ.get("CATALOGS_PATH", REPO_ROOT / "data" / "catalogs.json"))
GEAR_PATH = Path(os.environ.get("GEAR_PATH", REPO_ROOT / "data" / "gear.json"))
PLANS_PATH = Path(os.environ.get("PLANS_PATH", REPO_ROOT / "data" / "plans.json"))
SITES_PATH = Path(os.environ.get("SITES_PATH", REPO_ROOT / "data" / "sites.json"))
SAVED_SEARCHES_PATH = Path(os.environ.get(
    "SAVED_SEARCHES_PATH", REPO_ROOT / "data" / "saved_searches.json"))
TARGET_OVERRIDES_PATH = Path(os.environ.get(
    "TARGET_OVERRIDES_PATH", REPO_ROOT / "data" / "target_overrides.json"))
TS_DB_PATH = os.environ.get(
    "TS_DB_PATH",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "NINA" / "SchedulerPlugin" / "schedulerdb.sqlite"),
)
ZIP_OUTPUT_DIR = Path(os.environ.get("ZIP_OUTPUT_DIR", REPO_ROOT / "data" / "exports"))
SURVEYS_PATH = Path(os.environ.get("ACP_SURVEYS_PATH", REPO_ROOT / "data" / "surveys.json"))
CATALOG_REGISTRY_PATH = Path(os.environ.get(
    "ACP_CATALOG_REGISTRY", REPO_ROOT / "data" / "catalog_registry.json"))
MOC_CACHE_DIR = Path(os.environ.get("ACP_MOC_CACHE_DIR", REPO_ROOT / "data" / "moc_cache"))

# Hostname allowlist for MOC fetches. Both entries point at the same CDS
# infrastructure: alasky.u-strasbg.fr is the legacy hostname kept alive for
# backward compat. New entries should be reviewed in PR — this is the trust
# boundary for the only ACP feature that fetches over the network at runtime.
_MOC_ALLOWED_HOSTS = frozenset({"alasky.cds.unistra.fr", "alasky.u-strasbg.fr"})
_MOC_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_MOC_FETCH_TIMEOUT_S = 30
_MOC_CACHE_TTL_S = 30 * 24 * 3600  # 30 days

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # dev: browsers revalidate every request
app.jinja_env.auto_reload = True

if not CATALOGS_PATH.exists():
    print(
        f"[acp] WARN: catalogs file not found at {CATALOGS_PATH} -- "
        "right-rail catalog overlays (Green SNR / SMGPS / WISE HII / Messier / etc.) will be empty. "
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
_sites_cache: dict | None = None
_sites_cache_mtime: float | None = None
_saved_searches_cache: dict | None = None
_saved_searches_cache_mtime: float | None = None
# Per-(site, manifest, year) cache of computed visibility bins. Visibility is
# essentially constant year-to-year (sun barycentric position is fixed
# relative to RA/Dec) so a one-shot compute per site is fine, and the entry
# is invalidated implicitly when the manifest mtime ticks over.
_visibility_cache: dict[tuple, dict] = {}
_catalog_registry_cache: dict | None = None
_catalog_registry_cache_mtime: float | None = None

DEFAULT_SITES = {
    "version": 1,
    "sites": [
        {"id": "mauna_kea",  "name": "Mauna Kea, Hawaii",   "lat":  19.82, "lon": -155.47, "elev_m": 4205, "min_alt_deg": 30},
        {"id": "paranal",    "name": "Cerro Paranal, Chile", "lat": -24.63, "lon":  -70.40, "elev_m": 2635, "min_alt_deg": 30},
    ],
}


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


def load_sites() -> dict:
    global _sites_cache, _sites_cache_mtime
    if not SITES_PATH.exists():
        return json.loads(json.dumps(DEFAULT_SITES))
    mtime = SITES_PATH.stat().st_mtime
    if _sites_cache is None or _sites_cache_mtime != mtime:
        _sites_cache = json.loads(SITES_PATH.read_text(encoding="utf-8"))
        _sites_cache_mtime = mtime
    return _sites_cache


def save_sites(data: dict) -> None:
    global _sites_cache, _sites_cache_mtime
    SITES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SITES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _sites_cache = data
    _sites_cache_mtime = SITES_PATH.stat().st_mtime


def load_saved_searches() -> dict:
    global _saved_searches_cache, _saved_searches_cache_mtime
    if not SAVED_SEARCHES_PATH.exists():
        return {"version": 1, "searches": []}
    mtime = SAVED_SEARCHES_PATH.stat().st_mtime
    if _saved_searches_cache is None or _saved_searches_cache_mtime != mtime:
        _saved_searches_cache = json.loads(
            SAVED_SEARCHES_PATH.read_text(encoding="utf-8"))
        _saved_searches_cache_mtime = mtime
    return _saved_searches_cache


def save_saved_searches(data: dict) -> None:
    global _saved_searches_cache, _saved_searches_cache_mtime
    SAVED_SEARCHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAVED_SEARCHES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _saved_searches_cache = data
    _saved_searches_cache_mtime = SAVED_SEARCHES_PATH.stat().st_mtime


def load_catalog_registry() -> dict:
    """Load the declarative catalogue registry.

    Returns the file's contents (cached on mtime) or an empty registry if
    the file is absent. Out-of-tree code injecting catalogues at runtime
    (see `app.extra_catalogues`) is merged on top by api_catalog_registry.
    """
    global _catalog_registry_cache, _catalog_registry_cache_mtime
    if not CATALOG_REGISTRY_PATH.exists():
        return {"version": 1, "catalogues": []}
    mtime = CATALOG_REGISTRY_PATH.stat().st_mtime
    if _catalog_registry_cache is None or _catalog_registry_cache_mtime != mtime:
        _catalog_registry_cache = json.loads(
            CATALOG_REGISTRY_PATH.read_text(encoding="utf-8"))
        _catalog_registry_cache_mtime = mtime
    return _catalog_registry_cache


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
        # Phase 4: per-filter MOC cache keyed by (filter_name, manifest mtime).
        # Tuple-keyed so an mtime change auto-invalidates without bookkeeping.
        self._moc_cache: dict[tuple[str, float], object] = {}

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

    def coverage_moc(self, filter_name: str):
        """Union of every target polygon that has >0 hours at `filter_name`.

        Cached per (filter, manifest-mtime). Returns None if mocpy is missing,
        the manifest is absent, or no target carries this filter.
        """
        if not _MOCPY_AVAILABLE:
            return None
        manifest = self._load()
        if not manifest:
            return None
        cache_key = (filter_name, self._cache_mtime or 0.0)
        if cache_key in self._moc_cache:
            return self._moc_cache[cache_key]

        from astropy.coordinates import SkyCoord  # local: keep app boot cheap
        import astropy.units as u

        per_target: list = []
        for t in manifest.get("targets", []) or []:
            f = (t.get("filters") or {}).get(filter_name)
            if not f or float(f.get("total_hours", 0.0)) <= 0.0:
                continue
            corners = t.get("corners_icrs") or []
            if len(corners) < 3:
                continue
            ras = [float(ra) for ra, _ in corners]
            decs = [float(dec) for _, dec in corners]
            sc = SkyCoord(ras, decs, unit=u.deg, frame="icrs")
            per_target.append(MOC.from_polygon_skycoord(sc, max_depth=10))

        if not per_target:
            self._moc_cache[cache_key] = None
            return None
        # union(another, *rest): single arg is fine for a 2-element list,
        # variadic for >2. Slice into head + tail to satisfy both.
        merged = per_target[0] if len(per_target) == 1 \
            else per_target[0].union(*per_target[1:])
        self._moc_cache[cache_key] = merged
        return merged


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


def _validate_moc_url(moc_url: str) -> None:
    """HTTPS-only, hostname-allowlisted. Raises ValueError on rejection.

    Run at config-load time (registration) and again before every fetch — the
    on-fetch check defends against an attacker who can swap in a registered
    source's URL between boot and first hit.
    """
    if not isinstance(moc_url, str) or not moc_url:
        raise ValueError("moc_url is empty")
    parsed = urllib.parse.urlparse(moc_url)
    if parsed.scheme != "https":
        raise ValueError(f"moc_url must use https:// (got {parsed.scheme!r})")
    if parsed.hostname not in _MOC_ALLOWED_HOSTS:
        raise ValueError(
            f"moc_url host {parsed.hostname!r} not in allowlist "
            f"({sorted(_MOC_ALLOWED_HOSTS)})"
        )


class _MocFetchError(Exception):
    """Upstream fetch / validation failed in a way the route surfaces as 502."""


def _fetch_moc_bytes(moc_url: str) -> bytes:
    """Download a MOC over HTTPS with a streamed size cap.

    Refuses Content-Length headers above the cap up front, and aborts mid-read
    if a server with no/wrong Content-Length tries to feed us more than the
    cap. The bytes returned are the raw FITS payload — the caller is
    responsible for handing them to MOC.from_fits for validation.
    """
    _validate_moc_url(moc_url)
    req = urllib.request.Request(moc_url, headers={"User-Agent": "ACP-MOC/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_MOC_FETCH_TIMEOUT_S) as resp:
            cl = resp.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) > _MOC_MAX_BYTES:
                        raise _MocFetchError(
                            f"Content-Length {cl} exceeds cap {_MOC_MAX_BYTES}"
                        )
                except ValueError:
                    pass  # malformed header → fall through to streaming check
            buf = BytesIO()
            # 64 KiB chunks: small enough to abort early on a runaway body,
            # large enough that a 200 KB MOC takes ~3 reads.
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if buf.tell() + len(chunk) > _MOC_MAX_BYTES:
                    raise _MocFetchError(
                        f"response body exceeded cap {_MOC_MAX_BYTES}"
                    )
                buf.write(chunk)
            return buf.getvalue()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise _MocFetchError(f"fetch failed: {exc}") from exc


class MocCoverageSource:
    """Coverage source backed by a CDS-hosted FITS MOC blob.

    Lazy fetch on first hit to /api/moc/<id>; cached on disk under
    data/moc_cache/<id>.fits with a 30-day TTL sidecar. The Phase 3 frontend
    consumes the FITS bytes directly via Aladin Lite and never needs the
    coverage() iterator — see comment in coverage() for Phase 4 plans.
    """

    def __init__(self, *, source_id: str, label: str, color: str,
                 attribution: str, enabled_default: bool, moc_url: str,
                 filter_name: str | None = None,
                 cache_dir: Path | None = None) -> None:
        # Re-validate on construct so a malformed URL slipped past the loader
        # still fails loudly, not at first /api/moc hit.
        _validate_moc_url(moc_url)
        self._source_id = source_id
        self._label = label
        self._color = color
        self._attribution = attribution
        self._enabled_default = enabled_default
        self._moc_url = moc_url
        self._filter_name = filter_name
        self._cache_dir = Path(cache_dir) if cache_dir else MOC_CACHE_DIR
        self._lock = threading.Lock()
        self._parsed_moc = None  # lazy parse of the cached FITS for coverage_moc

    def id(self) -> str:
        return self._source_id

    def metadata(self) -> dict:
        return {
            "label": self._label,
            "color": self._color,
            "kind": "moc",
            "attribution": self._attribution,
            "enabled_default": self._enabled_default,
        }

    def coverage(self):
        # Phase 4's gap-finder will need real region yielding from MOC
        # union/intersection. For Phase 3 we leave this empty — the frontend
        # consumes /api/moc/<id> directly, not coverage().
        return iter(())

    def coverage_moc(self, filter_name: str):
        """Return the cached MOC if it exists on disk and `filter_name` matches.

        Lazy: never triggers a network fetch. Caller (gap-finder) decides
        whether to pre-warm the cache via ensure_cached() in advance.
        """
        if not _MOCPY_AVAILABLE:
            return None
        if filter_name != self._filter_name:
            return None
        if self._parsed_moc is not None:
            return self._parsed_moc
        fits_path, _ = self._cache_paths()
        if not fits_path.exists():
            return None
        self._parsed_moc = MOC.from_fits(str(fits_path))
        return self._parsed_moc

    @property
    def moc_url(self) -> str:
        return self._moc_url

    def _cache_paths(self) -> tuple[Path, Path]:
        return (
            self._cache_dir / f"{self._source_id}.fits",
            self._cache_dir / f"{self._source_id}.meta.json",
        )

    def _cache_fresh(self, fits_path: Path, meta_path: Path) -> bool:
        if not fits_path.exists() or not meta_path.exists():
            return False
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        # URL drift in surveys.json must invalidate the cached blob.
        if meta.get("url") != self._moc_url:
            return False
        try:
            fetched_at = datetime.fromisoformat(meta["fetched_at"])
        except (KeyError, ValueError):
            return False
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
        return age < _MOC_CACHE_TTL_S

    def ensure_cached(self) -> Path:
        """Return the path to a fresh local FITS MOC, fetching if needed.

        Per-source lock prevents two concurrent first-hit requests from racing
        the same network fetch. Raises _MocFetchError on upstream / validation
        failure; the route translates that to 502.
        """
        if not _MOCPY_AVAILABLE:
            # Caller (the route) checks this first; re-checking here keeps the
            # invariant local — no half-fetched bytes hit disk without mocpy.
            raise _MocFetchError("mocpy not available")
        fits_path, meta_path = self._cache_paths()
        with self._lock:
            if self._cache_fresh(fits_path, meta_path):
                return fits_path
            data = _fetch_moc_bytes(self._moc_url)
            try:
                MOC.from_fits(BytesIO(data))
            except Exception as exc:
                # Don't write malformed bytes to disk — next hit refetches.
                raise _MocFetchError(f"invalid MOC FITS: {exc}") from exc
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            fits_path.write_bytes(data)
            meta_path.write_text(json.dumps({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_sha256": hashlib.sha256(data).hexdigest(),
                "url": self._moc_url,
            }, indent=2), encoding="utf-8")
            return fits_path


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
# list inside their `register(app)` body; the built-in manifest source is
# only pre-registered when MANIFEST_PATH actually exists on disk so a user
# running ACP off pure extension-supplied sources (e.g. a private
# survey-tile extension with no FITS archive of their own) doesn't see an
# empty "Your archive" entry in the Sources rail.
app.coverage_sources = []
if MANIFEST_PATH.exists():
    app.coverage_sources.append(ManifestCoverageSource())
# Out-of-tree extensions can append catalogue registry entries here at
# `register(app)` time. Same shape as `data/catalog_registry.json` entries.
# Defaults to an empty list — extensions add to it, never replace it.
app.extra_catalogues: list[dict] = []
# PrioritisedTilesSource registry. Empty by default; extensions append via
# `app.tile_sources.append(...)` in their register() callback. Each source
# implements the Protocol declared in sources.py.
app.tile_sources: list = []
# CategorisedCatalogSource registry. Same registration pattern as above.
app.catalog_sources: list = []


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
        # so the id is URL-safe even for adversarial filenames. Skip the
        # `friend_` prefix if the filename already starts with one (case- and
        # whitespace-insensitive) to avoid `friend_friend_dave` ids.
        stem_lower = p.stem.lower()
        stem_canonical = stem_lower.replace(" ", "_")
        raw_id = stem_lower if stem_canonical.startswith("friend_") else f"friend_{stem_lower}"
        source_id = "".join(
            c if c.isalnum() or c in "_-" else "_" for c in raw_id
        )
        app.coverage_sources.append(FriendManifestSource(
            source_id=source_id, label=label, color="", path=p,
        ))
        logging.info("Loaded friend manifest: %s (%s)", label, p)


# Survey MOC sources — declarative registry committed at data/surveys.json (or
# ACP_SURVEYS_PATH). The file is intentionally tracked: PRs welcome to add
# more surveys. Per-entry validation failures log a warning and skip; bad
# entries must not block app boot.
def _load_surveys_registry() -> list[dict]:
    if not SURVEYS_PATH.exists():
        logging.info("Surveys registry not found at %s — no MOC sources", SURVEYS_PATH)
        return []
    try:
        data = json.loads(SURVEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Surveys registry %s unreadable: %s", SURVEYS_PATH, exc)
        return []
    if not isinstance(data, list):
        logging.warning("Surveys registry %s: top-level value must be a list", SURVEYS_PATH)
        return []
    return data


for _entry in _load_surveys_registry():
    _eid = (_entry or {}).get("id", "<unknown>")
    try:
        if not isinstance(_entry, dict):
            raise ValueError("entry is not an object")
        for _required in ("id", "label", "moc_url", "attribution"):
            if not _entry.get(_required):
                raise ValueError(f"missing required field {_required!r}")
        app.coverage_sources.append(MocCoverageSource(
            source_id=_entry["id"],
            label=_entry["label"],
            color=_entry.get("color", ""),
            attribution=_entry["attribution"],
            enabled_default=bool(_entry.get("enabled_default", False)),
            moc_url=_entry["moc_url"],
            filter_name=_entry.get("filter"),
        ))
        logging.info("Loaded MOC source: %s (%s)", _entry["label"], _entry["id"])
    except (ValueError, TypeError) as exc:
        logging.warning("Skipping MOC source %s: %s", _eid, exc)


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
    # Render the page even when no manifest exists yet — a fresh-install
    # user lands here, the frontend fetches /api/manifest, sees an empty
    # targets list, and shows the onboarding banner directing them at the
    # archive setup guide. Erroring out here would just give them a
    # white-screen 500 with no path forward.
    m = load_manifest() or {}
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
        # Empty manifest rather than 404 — the frontend's onboarding
        # banner triggers on targets.length === 0, so a clean empty
        # response is exactly what we want here.
        return jsonify({
            "scan_date": None,
            "total_targets": 0,
            "total_integration_hours": 0,
            "targets": [],
        })
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
    """Marker data for every catalogue surfaced in the rail.

    Two contributors merged at request time:
      - file-loaded entries from `data/catalogs.json` (populated by
        scripts/fetch_catalogs.py); keyed by the registry's `data_key`,
      - `app.catalog_sources` (extension-registered Protocols); keyed by
        the source's `id()`. Each object is a dict with at least
        `name`, `ra_deg`, `dec_deg`, plus optional `category` + extras.
    """
    out = dict(load_catalogs() or {})
    for src in (getattr(app, "catalog_sources", []) or []):
        try:
            src_id = src.id()
        except Exception as exc:
            logging.warning("catalog source missing id(): %s", exc)
            continue
        try:
            objs = list(src.objects() or [])
        except Exception as exc:
            logging.warning("catalog source %r objects() raised: %s", src_id, exc)
            continue
        # Out-of-tree sources win on key collisions — same precedence as the
        # registry merge — so an extension can deliberately override a
        # file-loaded catalogue with a richer per-class build.
        out[src_id] = objs
    return jsonify(out)


def _entry_from_catalog_source(src) -> dict | None:
    """Auto-generate a registry entry for a CategorisedCatalogSource."""
    try:
        sid = src.id()
        meta = src.metadata() or {}
    except Exception as exc:
        logging.warning("catalog source missing id/metadata: %s", exc)
        return None
    if not isinstance(sid, str) or not sid:
        return None
    cats: list = []
    if hasattr(src, "categories"):
        try:
            cats = list(src.categories() or [])
        except Exception as exc:
            logging.warning("catalog source %r categories() raised: %s", sid, exc)
    return {
        "id": sid,
        "data_key": sid,
        "label": meta.get("label") or sid,
        "color": meta.get("color") or "#888",
        "marker": meta.get("marker") or "circle",
        "size": int(meta.get("size") or 10),
        "attribution": meta.get("attribution") or "",
        "enabled_default": bool(meta.get("enabled_default")),
        "categories": cats,
    }


# --- Tile sources (Plan 4) -----------------------------------------------
# A "tile source" is anything implementing sources.PrioritisedTilesSource —
# a curated, ranked list of sky cells with per-band coverage. In-tree code
# does not ship one; out-of-tree extensions register via
# `app.tile_sources.append(...)`. The Inventory rail surfaces whichever
# are present at request time.

def _tile_completion(tile: dict) -> float:
    """Fraction of declared bands marked covered. 0.0 if no per-band data."""
    pb = tile.get("per_band") or {}
    if not pb:
        return 0.0
    n = len(pb)
    n_covered = sum(1 for v in pb.values()
                    if isinstance(v, dict) and v.get("covered"))
    return (n_covered / n) if n else 0.0


def _summarise_tile_source(src) -> dict:
    """Probe a tile source for the metadata the Inventory rail needs.

    Walks the tiles once to derive `n_tiles`, `max_priority_level`, and
    union sets for `categories` + `bands`. Sources with very large tile
    lists may want to override these via dedicated methods later, but the
    walk is fast (a few thousand tiles ≈ 10 ms).
    """
    try:
        sid = src.id()
        meta = src.metadata() or {}
    except Exception as exc:
        logging.warning("tile source missing id/metadata: %s", exc)
        return {}
    n = 0
    max_pri = 0
    cats: set[str] = set()
    bands: set[str] = set()
    try:
        for tile in src.tiles() or []:
            n += 1
            if isinstance(tile.get("priority_level"), int) and tile["priority_level"] > max_pri:
                max_pri = tile["priority_level"]
            for k in (tile.get("category_counts") or {}).keys():
                if isinstance(k, str):
                    cats.add(k)
            for k in (tile.get("per_band") or {}).keys():
                if isinstance(k, str):
                    bands.add(k)
    except Exception as exc:
        logging.warning("tile source %r tiles() raised during summary: %s", sid, exc)
    return {
        "id": sid,
        "label": meta.get("label") or sid,
        "color": meta.get("color") or "",
        "attribution": meta.get("attribution") or "",
        "enabled_default": bool(meta.get("enabled_default")),
        "n_tiles": n,
        "max_priority_level": max_pri,
        "categories": sorted(cats),
        "bands": sorted(bands),
        "facets": _coerce_facets(meta.get("facets")),
        "color_facet": meta.get("color_facet") or "",
    }


def _coerce_facets(raw) -> list[dict]:
    """Pass-through validator for extension-declared facets.

    Drops malformed entries silently so a typo in one facet doesn't break
    the whole rail. Each kept facet has id+label+field+values, where
    each value has value+label+color.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        fid = f.get("id"); lbl = f.get("label"); field = f.get("field")
        vals = f.get("values")
        if not (isinstance(fid, str) and isinstance(lbl, str)
                and isinstance(field, str) and isinstance(vals, list)):
            continue
        kept_vals = []
        for v in vals:
            if not isinstance(v, dict):
                continue
            if "value" not in v or not isinstance(v.get("label"), str):
                continue
            kept_vals.append({
                "value": v["value"],
                "label": v["label"],
                "color": v.get("color") or "",
            })
        if kept_vals:
            out.append({"id": fid, "label": lbl, "field": field, "values": kept_vals})
    return out


@app.route("/api/tile-sources")
def api_tile_sources():
    """List metadata for every registered PrioritisedTilesSource.

    Frontend renders the Inventory rail only when this returns at least
    one source. Each entry includes summary stats (n_tiles, max priority,
    available categories + bands) so the rail can build filter chips
    without a second request.
    """
    out = []
    for src in (getattr(app, "tile_sources", []) or []):
        summary = _summarise_tile_source(src)
        if summary:
            out.append(summary)
    return jsonify({"sources": out})


@app.route("/api/tiles/<source_id>")
def api_tiles(source_id: str):
    """Return all tiles for one source.

    No server-side filtering for v1 — payloads are small enough (~100KB
    for a few thousand tiles) that the frontend can filter live as the
    user toggles chips. Add `?priority=1,2&missing=Ha,SII` later if a
    payload grows past comfort.
    """
    src = next(
        (s for s in (getattr(app, "tile_sources", []) or [])
         if hasattr(s, "id") and s.id() == source_id),
        None,
    )
    if src is None:
        return jsonify({"error": f"unknown tile source {source_id!r}"}), 404
    tiles_out: list[dict] = []
    try:
        for tile in src.tiles() or []:
            if not isinstance(tile, dict):
                continue
            tiles_out.append(tile)
    except Exception as exc:
        logging.warning("tile source %r tiles() raised: %s", source_id, exc)
        return jsonify({"error": f"source raised: {exc}"}), 500
    return jsonify({"id": source_id, "tiles": tiles_out})


# --- Saved Inventory searches (Plan 6) -----------------------------------
# Each entry: {id, name, source_id, filters: {priorities, missing,
# categories, hidePlanned}, created_at}. Per-source so the user can have
# different named searches against different tile sources.

def _validate_saved_search(s: dict) -> str | None:
    if not isinstance(s, dict):
        return "search must be an object"
    if not isinstance(s.get("name"), str) or not s["name"].strip():
        return "name must be a non-empty string"
    if not isinstance(s.get("source_id"), str) or not s["source_id"].strip():
        return "source_id must be a non-empty string"
    f = s.get("filters") or {}
    if not isinstance(f, dict):
        return "filters must be an object"
    for k in ("priorities", "missing", "categories"):
        if k in f and not isinstance(f[k], list):
            return f"filters.{k} must be a list"
    if "hidePlanned" in f and not isinstance(f["hidePlanned"], bool):
        return "filters.hidePlanned must be a boolean"
    return None


@app.route("/api/saved-searches", methods=["GET", "POST"])
def api_saved_searches():
    if request.method == "GET":
        return jsonify(load_saved_searches())
    payload = request.get_json(silent=True) or {}
    err = _validate_saved_search(payload)
    if err:
        return jsonify({"error": err}), 400
    data = load_saved_searches()
    searches = list(data.get("searches", []))
    sid = payload.get("id") or str(uuid.uuid4())
    f = payload.get("filters") or {}
    entry = {
        "id": sid,
        "name": payload["name"].strip(),
        "source_id": payload["source_id"].strip(),
        "filters": {
            "priorities": list(f.get("priorities") or []),
            "missing":    list(f.get("missing") or []),
            "categories": list(f.get("categories") or []),
            "hidePlanned": bool(f.get("hidePlanned")),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # upsert by id
    searches = [s for s in searches if s.get("id") != sid]
    searches.append(entry)
    save_saved_searches({"version": 1, "searches": searches})
    return jsonify(entry), 201


@app.route("/api/saved-searches/<search_id>", methods=["DELETE"])
def api_saved_search_delete(search_id: str):
    data = load_saved_searches()
    searches = [s for s in data.get("searches", []) if s.get("id") != search_id]
    if len(searches) == len(data.get("searches", [])):
        return jsonify({"error": "not found"}), 404
    save_saved_searches({"version": 1, "searches": searches})
    return ("", 204)


@app.route("/api/catalog-registry")
def api_catalog_registry():
    """Merged catalogue registry: extensions + file-loaded defaults.

    Three contributors, in precedence order:
      1. `app.catalog_sources` Protocols (extension-registered),
      2. `app.extra_catalogues` dicts (extension-registered raw entries),
      3. `data/catalog_registry.json` file entries.

    Frontend uses this to render the Catalogues rail dynamically, so
    adding a new catalogue is an extension append or a JSON edit rather
    than a code change.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def _add(entry: dict | None) -> None:
        if not isinstance(entry, dict):
            return
        cid = entry.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            return
        seen.add(cid)
        out.append(entry)

    for src in (getattr(app, "catalog_sources", []) or []):
        _add(_entry_from_catalog_source(src))
    for entry in (app.extra_catalogues or []):
        _add(entry)
    for entry in load_catalog_registry().get("catalogues", []):
        _add(entry)
    return jsonify({"version": 1, "catalogues": out})


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


@app.route("/api/moc/<source_id>")
def api_moc(source_id: str):
    """Serve the cached FITS MOC blob for a registered MOC source.

    Lazy-fetches on first hit. Returns 404 for unknown ids or non-MOC sources,
    503 when mocpy is missing, 502 when the upstream fetch or validation
    fails. Successful responses are application/octet-stream — Aladin Lite's
    FITS MOC loader takes the bytes directly.
    """
    src = next(
        (s for s in app.coverage_sources
         if s.id() == source_id and isinstance(s, MocCoverageSource)),
        None,
    )
    if src is None:
        return jsonify({"error": "MOC source not found"}), 404
    if not _MOCPY_AVAILABLE:
        return jsonify({
            "error": "mocpy not installed; MOC overlays disabled",
        }), 503
    try:
        fits_path = src.ensure_cached()
    except _MocFetchError as exc:
        logging.warning("MOC fetch failed for %s: %s", source_id, exc)
        return jsonify({"error": f"upstream fetch failed: {exc}"}), 502
    return Response(
        fits_path.read_bytes(),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{source_id}.fits"'},
    )


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

    lat = _clamped_float("lat", 19.82, -90.0, 90.0)
    lon = _clamped_float("lon", -155.47, -180.0, 180.0)
    height = _clamped_float("height", 4205.0, -430.0, 9000.0)
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


# --- Visibility (Plan A.3) ----------------------------------------------
# Bin a (peak_alt, hours_above_min) pair into one of five labels. Highest-
# quality gates run first; "fair" is the catch-all for "≥1h above min but
# doesn't meet good/great". See planner-design memory for the locked rules.
_VIS_LABELS = ("not_visible", "partial", "fair", "good", "great")


def _bin_visibility(peak_alt_deg: float, hours_above_min: float, min_alt_deg: float) -> str:
    if peak_alt_deg < min_alt_deg:
        return "not_visible"
    if hours_above_min < 1.0:
        return "partial"
    if peak_alt_deg >= 60.0 and hours_above_min >= 3.0:
        return "great"
    if peak_alt_deg >= 45.0 and hours_above_min >= 2.0:
        return "good"
    return "fair"


def compute_year_visibility(
    targets: list[dict],
    *, lat: float, lon: float, elev_m: float, min_alt_deg: float,
    year: int, sample_step_min: int = 15,
) -> dict[int, list[dict]]:
    """Return {target_id: [12 bins]} of visibility per month for the given site.

    For each month we sample altitudes across a 24h window centred on the
    15th at noon UTC, mask to astronomical-darkness times (sun alt < -18°),
    and reduce per-target to peak alt + hours-above-min. The (peak, hours,
    min) tuple feeds _bin_visibility for the label.

    Vectorised: one AltAz transform per month for the (N×T) (target, time)
    grid, then per-target reductions in numpy. ~1-3s for N≈70 targets.
    """
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_sun
    from astropy.time import Time
    import astropy.units as u
    import numpy as np
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    out: dict[int, list[dict]] = {int(t["target_id"]): [] for t in targets}
    if not targets:
        return out

    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=elev_m * u.m)
    ras = np.array([float(t["center_ra_deg"]) for t in targets])
    decs = np.array([float(t["center_dec_deg"]) for t in targets])
    ids = [int(t["target_id"]) for t in targets]
    n_targets = len(ids)
    samples_per_day = (24 * 60) // sample_step_min  # 96 at 15-min steps

    for month in range(1, 13):
        anchor = _dt(year, month, 15, 12, 0, 0, tzinfo=_tz.utc).replace(tzinfo=None)
        times_dt = [anchor + _td(minutes=sample_step_min * i)
                    for i in range(samples_per_day + 1)]
        t_grid = Time(times_dt)
        n_t = len(t_grid)

        # Astronomical darkness mask via sun altitude.
        sun_altaz = get_sun(t_grid).transform_to(
            AltAz(obstime=t_grid, location=loc))
        is_dark = sun_altaz.alt.deg < -18.0  # shape (T,)

        if not bool(is_dark.any()):
            for tid in ids:
                out[tid].append({
                    "month": month, "label": "not_visible",
                    "peak_alt_deg": None, "hours_above_min": 0.0,
                })
            continue

        # One big AltAz transformation: target i at time j.
        ras_full = np.repeat(ras, n_t)
        decs_full = np.repeat(decs, n_t)
        times_full = Time(np.tile(t_grid.jd, n_targets), format="jd")
        sc = SkyCoord(ras_full * u.deg, decs_full * u.deg)
        alt_grid = sc.transform_to(
            AltAz(obstime=times_full, location=loc)).alt.deg
        alt_grid = alt_grid.reshape(n_targets, n_t)

        for i, tid in enumerate(ids):
            alts_dark = alt_grid[i, is_dark]
            if alts_dark.size == 0:
                out[tid].append({
                    "month": month, "label": "not_visible",
                    "peak_alt_deg": None, "hours_above_min": 0.0,
                })
                continue
            peak = float(np.max(alts_dark))
            hours = float(np.sum(alts_dark >= min_alt_deg)
                          * sample_step_min / 60.0)
            label = _bin_visibility(peak, hours, min_alt_deg)
            out[tid].append({
                "month": month, "label": label,
                "peak_alt_deg": round(peak, 2),
                "hours_above_min": round(hours, 2),
            })

    return out


def _resolve_site_from_request() -> tuple[dict, tuple[Response, int] | None]:
    """Resolve the site for a /api/visibility call.

    Either `site_id=<id>` (looks up sites.json) or explicit lat/lon/elev_m/
    min_alt_deg query params. Defaults match the existing /api/observability
    fallback so behaviour stays predictable.
    """
    sid = request.args.get("site_id")
    if sid:
        sdata = next(
            (s for s in load_sites().get("sites", []) if s.get("id") == sid),
            None,
        )
        if not sdata:
            return {}, (jsonify({"error": f"unknown site_id {sid!r}"}), 404)
        return {
            "id": sid,
            "lat": float(sdata["lat"]),
            "lon": float(sdata["lon"]),
            "elev_m": float(sdata.get("elev_m") or 0.0),
            "min_alt_deg": float(sdata.get("min_alt_deg") or 30.0),
        }, None
    return {
        "id": None,
        "lat": _clamped_float("lat", 19.82, -90.0, 90.0),
        "lon": _clamped_float("lon", -155.47, -180.0, 180.0),
        "elev_m": _clamped_float("elev_m", 0.0, -430.0, 9000.0),
        "min_alt_deg": _clamped_float("min_alt_deg", 30.0, 0.0, 90.0),
    }, None


@app.route("/api/visibility/point")
def api_visibility_point():
    """Visibility bins for one arbitrary (ra_deg, dec_deg) point.

    Same compute as /api/visibility but the "target list" is a single
    synthesised point, so callers (e.g. the Inventory tile-detail panel)
    can show year-curves for cells that aren't in the manifest.
    """
    try:
        import astropy  # noqa: F401  - presence check only
    except Exception as e:
        return jsonify({"error": f"astropy not available: {e}"}), 500
    site, err = _resolve_site_from_request()
    if err is not None:
        return err
    try:
        ra = float(request.args.get("ra"))
        dec = float(request.args.get("dec"))
    except (TypeError, ValueError):
        return jsonify({"error": "ra and dec required (decimal degrees)"}), 400
    if not (-360.0 <= ra <= 360.0) or not (-90.0 <= dec <= 90.0):
        return jsonify({"error": "ra/dec out of range"}), 400
    try:
        year = int(request.args.get("year") or datetime.now(timezone.utc).year)
    except ValueError:
        return jsonify({"error": "year must be an integer"}), 400
    if not (1900 <= year <= 2200):
        return jsonify({"error": "year out of range"}), 400

    # Cache by (site, ra, dec, year). Manifest mtime is irrelevant here since
    # the point is supplied directly, not looked up in the manifest.
    cache_key = (
        round(site["lat"], 4), round(site["lon"], 4),
        round(site["min_alt_deg"], 2),
        round(ra, 4), round(dec, 4), year,
    )
    bins = _visibility_cache.get(cache_key)
    if bins is None:
        bins = compute_year_visibility(
            [{"target_id": 0, "center_ra_deg": ra, "center_dec_deg": dec}],
            lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
            min_alt_deg=site["min_alt_deg"], year=year,
        )
        _visibility_cache[cache_key] = bins
    return jsonify({
        "site": site,
        "year": year,
        "labels": list(_VIS_LABELS),
        "ra_deg": ra,
        "dec_deg": dec,
        "months": bins.get(0, []),
    })


@app.route("/api/visibility/panels", methods=["POST"])
def api_visibility_panels():
    """Aggregated visibility for a list of mosaic panel (ra, dec) centres.

    Used by the planner to summarise a mosaic's per-month "fraction of
    panels visible". POST body::

        { "panels": [{"ra_deg": ..., "dec_deg": ...}, ...],
          "year":     optional int,
          "site_id":  optional str  (else lat/lon/min_alt_deg from query) }

    Returns per-panel month-bins identical to /api/visibility/point, plus
    a ``months`` aggregate of {panels_visible, total_panels} per month.
    Single-panel calls degenerate to the same bins as /api/visibility/point.
    """
    try:
        import astropy  # noqa: F401  - presence check only
    except Exception as e:
        return jsonify({"error": f"astropy not available: {e}"}), 500
    site, err = _resolve_site_from_request()
    if err is not None:
        return err
    body = request.get_json(silent=True) or {}
    panels_in = body.get("panels") if isinstance(body, dict) else None
    if not isinstance(panels_in, list) or not panels_in:
        return jsonify({"error": "panels: non-empty list required"}), 400
    if len(panels_in) > 400:
        return jsonify({"error": "panels: max 400 per request"}), 400
    try:
        year = int(body.get("year") or datetime.now(timezone.utc).year)
    except (TypeError, ValueError):
        return jsonify({"error": "year must be an integer"}), 400
    if not (1900 <= year <= 2200):
        return jsonify({"error": "year out of range"}), 400

    parsed: list[tuple[float, float]] = []
    for i, p in enumerate(panels_in):
        if not isinstance(p, dict):
            return jsonify({"error": f"panel {i}: must be object"}), 400
        try:
            ra = float(p["ra_deg"])
            dec = float(p["dec_deg"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": f"panel {i}: ra_deg/dec_deg required"}), 400
        if not (-360.0 <= ra <= 360.0) or not (-90.0 <= dec <= 90.0):
            return jsonify({"error": f"panel {i}: ra/dec out of range"}), 400
        parsed.append((round(ra, 4), round(dec, 4)))

    site_key = (
        round(site["lat"], 4), round(site["lon"], 4),
        round(site["min_alt_deg"], 2),
    )
    per_panel_bins: list[list[dict] | None] = [None] * len(parsed)
    misses: list[tuple[int, float, float]] = []
    for idx, (ra, dec) in enumerate(parsed):
        cached = _visibility_cache.get(site_key + (ra, dec, year))
        if cached is None:
            misses.append((idx, ra, dec))
        else:
            per_panel_bins[idx] = cached.get(0, [])

    if misses:
        targets = [{"target_id": i, "center_ra_deg": ra, "center_dec_deg": dec}
                   for i, (_, ra, dec) in enumerate(misses)]
        bins_dict = compute_year_visibility(
            targets,
            lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
            min_alt_deg=site["min_alt_deg"], year=year,
        )
        for i, (idx, ra, dec) in enumerate(misses):
            single_bins = bins_dict.get(i, [])
            _visibility_cache[site_key + (ra, dec, year)] = {0: single_bins}
            per_panel_bins[idx] = single_bins

    total = len(parsed)
    months: list[dict] = []
    for m in range(1, 13):
        visible = 0
        for bins in per_panel_bins:
            b = next((x for x in (bins or []) if x.get("month") == m), None)
            if b and b.get("label") != "not_visible":
                visible += 1
        months.append({"month": m, "panels_visible": visible, "total_panels": total})

    return jsonify({
        "site": site,
        "year": year,
        "labels": list(_VIS_LABELS),
        "panel_count": total,
        "months": months,
        "per_panel": [
            {"ra_deg": parsed[i][0], "dec_deg": parsed[i][1],
             "months": per_panel_bins[i] or []}
            for i in range(total)
        ],
    })


@app.route("/api/visibility")
def api_visibility():
    try:
        import astropy  # noqa: F401  - presence check only
    except Exception as e:
        return jsonify({"error": f"astropy not available: {e}"}), 500

    site, err = _resolve_site_from_request()
    if err is not None:
        return err

    m = load_manifest()
    if m is None:
        return jsonify({"error": "manifest not found"}), 404

    try:
        year = int(request.args.get("year") or datetime.now(timezone.utc).year)
    except ValueError:
        return jsonify({"error": "year must be an integer"}), 400
    if not (1900 <= year <= 2200):
        return jsonify({"error": "year out of range"}), 400

    # Cache only the bins (the expensive part). The site dict comes from
    # the live request so site_id-based and lat/lon-based calls with the
    # same coords don't pollute each other's metadata.
    cache_key = (
        round(site["lat"], 4), round(site["lon"], 4),
        round(site["min_alt_deg"], 2),
        _manifest_cache_mtime or 0.0, year,
    )
    bins = _visibility_cache.get(cache_key)
    if bins is None:
        targets = m.get("targets") or []
        bins = compute_year_visibility(
            targets,
            lat=site["lat"], lon=site["lon"], elev_m=site["elev_m"],
            min_alt_deg=site["min_alt_deg"], year=year,
        )
        _visibility_cache[cache_key] = bins
    return jsonify({
        "site": site,
        "year": year,
        "labels": list(_VIS_LABELS),
        "targets": bins,
    })


def _validate_site(s: dict) -> str | None:
    if not isinstance(s, dict):
        return "site must be an object"
    sid = s.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return "id must be a non-empty string"
    if not isinstance(s.get("name"), str) or not s["name"].strip():
        return f"site {sid!r}: name must be a non-empty string"
    try:
        lat = float(s.get("lat"))
        lon = float(s.get("lon"))
    except (TypeError, ValueError):
        return f"site {sid!r}: lat and lon must be numbers"
    if not (-90.0 <= lat <= 90.0):
        return f"site {sid!r}: lat out of range"
    if not (-180.0 <= lon <= 180.0):
        return f"site {sid!r}: lon out of range"
    if "elev_m" in s and s["elev_m"] is not None:
        try:
            elev = float(s["elev_m"])
        except (TypeError, ValueError):
            return f"site {sid!r}: elev_m must be a number"
        if not (-430.0 <= elev <= 9000.0):
            return f"site {sid!r}: elev_m out of range"
    if "min_alt_deg" in s and s["min_alt_deg"] is not None:
        try:
            ma = float(s["min_alt_deg"])
        except (TypeError, ValueError):
            return f"site {sid!r}: min_alt_deg must be a number"
        if not (0.0 <= ma <= 90.0):
            return f"site {sid!r}: min_alt_deg out of range"
    return None


@app.route("/api/sites", methods=["GET", "POST"])
def api_sites():
    if request.method == "GET":
        return jsonify(load_sites())
    payload = request.get_json(silent=True) or {}
    sites = payload.get("sites")
    if not isinstance(sites, list) or not sites:
        return jsonify({"error": "sites array required and must be non-empty"}), 400
    seen_ids: set[str] = set()
    cleaned: list[dict] = []
    for s in sites:
        err = _validate_site(s)
        if err:
            return jsonify({"error": err}), 400
        sid = s["id"].strip()
        if sid in seen_ids:
            return jsonify({"error": f"duplicate site id {sid!r}"}), 400
        seen_ids.add(sid)
        out = {
            "id": sid,
            "name": s["name"].strip(),
            "lat": float(s["lat"]),
            "lon": float(s["lon"]),
        }
        if s.get("elev_m") is not None:
            out["elev_m"] = float(s["elev_m"])
        if s.get("min_alt_deg") is not None:
            out["min_alt_deg"] = float(s["min_alt_deg"])
        cleaned.append(out)
    save_sites({"version": 1, "sites": cleaned})
    return jsonify({"ok": True, "sites": cleaned})


def _gaps_query_params() -> tuple[dict, tuple[Response, int] | None]:
    """Parse + validate /api/gaps query params. Returns (params, error_response).

    On success error_response is None. On bad input it's a (response, status)
    tuple ready to return from the route. Defaults match the documented
    "Ha covered, SII not yet" recipe.
    """
    have = request.args.get("have", "Ha")
    missing = request.args.get("missing", "SII")
    if not isinstance(have, str) or not have or not isinstance(missing, str) or not missing:
        return {}, (jsonify({"error": "have/missing must be non-empty strings"}), 400)
    sources_raw = request.args.get("sources")
    if sources_raw is not None:
        wanted = [s.strip() for s in sources_raw.split(",") if s.strip()]
    else:
        wanted = None  # None = all enabled-by-default sources
    try:
        min_have = float(request.args.get("min_have_hours", 1.0))
        max_missing = float(request.args.get("max_missing_hours", 0.5))
    except (TypeError, ValueError):
        return {}, (jsonify({"error": "min_have_hours/max_missing_hours must be numeric"}), 400)
    return {
        "have": have, "missing": missing,
        "source_ids": wanted,
        "min_have_hours": min_have,
        "max_missing_hours": max_missing,
    }, None


def _source_passes_threshold(src, filter_name: str, min_hours: float) -> bool:
    """True if any region from src.coverage() has >= min_hours of filter_name.

    Returns True for sources without per-region hours (MOC sources): the
    threshold is meaningless there, so they always qualify if their declared
    filter matches — that match is enforced separately by compute_gap_moc.
    """
    coverage = getattr(src, "coverage", None)
    if coverage is None:
        return True
    saw_any_region = False
    for region in coverage():
        saw_any_region = True
        f = (region.get("filters") or {}).get(filter_name)
        if f and float(f.get("hours", 0.0)) >= min_hours:
            return True
    # No regions at all → MOC source (or empty manifest). Don't filter it out
    # here; compute_gap_moc will skip it if it has no coverage at this filter.
    return not saw_any_region


def _select_gap_sources(have: str, missing: str, source_ids: list[str] | None,
                       min_have: float, max_missing: float) -> tuple[list, list[str], list[tuple[str, str]]]:
    """Filter app.coverage_sources by id + per-side hour thresholds.

    Returns (sources_to_pass_in, restricted_ids, threshold_skipped). Sources
    that satisfy *either* side's threshold are passed through; compute_gap_moc
    sorts out which side they actually contribute to.
    """
    candidates = list(app.coverage_sources)
    if source_ids is not None:
        wanted = set(source_ids)
        candidates = [s for s in candidates if s.id() in wanted]
    else:
        # Default = enabled-by-default sources only.
        candidates = [s for s in candidates if s.metadata().get("enabled_default")]

    selected: list = []
    selected_ids: list[str] = []
    skipped: list[tuple[str, str]] = []
    for src in candidates:
        ok_have = _source_passes_threshold(src, have, min_have)
        ok_missing = _source_passes_threshold(src, missing, max_missing)
        if ok_have or ok_missing:
            selected.append(src)
            selected_ids.append(src.id())
        else:
            skipped.append((src.id(),
                            f"below {min_have}h threshold for {have} and "
                            f"below {max_missing}h threshold for {missing}"))
    return selected, selected_ids, skipped


@app.route("/api/gaps")
def api_gaps():
    """Multi-source gap finder — where `have` is covered but `missing` isn't.

    Returns 503 when mocpy isn't installed (MOC algebra is the whole point of
    this route; CSV consumers fall back via /api/export/priority instead).
    """
    if not _MOCPY_AVAILABLE:
        return jsonify({"error": "mocpy not installed"}), 503
    params, err = _gaps_query_params()
    if err is not None:
        return err

    sources, selected_ids, threshold_skipped = _select_gap_sources(
        params["have"], params["missing"], params["source_ids"],
        params["min_have_hours"], params["max_missing_hours"],
    )
    result = compute_gap_moc(
        sources,
        have_filter=params["have"],
        missing_filter=params["missing"],
        source_ids=selected_ids,
    )
    skipped_payload = [{"source_id": sid, "reason": reason}
                       for sid, reason in (threshold_skipped + result.skipped)]

    if result.gap_moc is None:
        return jsonify({
            "have_filter": params["have"],
            "missing_filter": params["missing"],
            "have_sources": result.have_sources,
            "missing_sources": result.missing_sources,
            "gap_sky_fraction": 0.0,
            "candidates": [],
            "skipped": skipped_payload,
        })

    cands = candidates_in_moc(result.gap_moc, load_catalogs())
    # moc_url echoes the same query string so a frontend can fetch the FITS
    # without re-parsing — keep the param order stable for cache-friendliness.
    qs = urllib.parse.urlencode({
        "have": params["have"],
        "missing": params["missing"],
        "sources": ",".join(selected_ids),
        "min_have_hours": params["min_have_hours"],
        "max_missing_hours": params["max_missing_hours"],
    })
    return jsonify({
        "have_filter": params["have"],
        "missing_filter": params["missing"],
        "have_sources": result.have_sources,
        "missing_sources": result.missing_sources,
        "gap_sky_fraction": result.gap_sky_fraction,
        "candidates": [
            {"catalog": c.catalog, "name": c.name,
             "ra_deg": c.ra_deg, "dec_deg": c.dec_deg}
            for c in cands
        ],
        "skipped": skipped_payload,
        "moc_url": f"/api/gaps/moc.fits?{qs}",
    })


@app.route("/api/gaps/moc.fits")
def api_gaps_moc_fits():
    """Serve the gap MOC as raw FITS bytes."""
    if not _MOCPY_AVAILABLE:
        return jsonify({"error": "mocpy not installed"}), 503
    params, err = _gaps_query_params()
    if err is not None:
        return err

    sources, selected_ids, _ = _select_gap_sources(
        params["have"], params["missing"], params["source_ids"],
        params["min_have_hours"], params["max_missing_hours"],
    )
    result = compute_gap_moc(
        sources,
        have_filter=params["have"],
        missing_filter=params["missing"],
        source_ids=selected_ids,
    )
    if result.gap_moc is None:
        return jsonify({"error": "no gap region for these parameters"}), 404

    buf = BytesIO()
    result.gap_moc.serialize(format="fits").writeto(buf)
    return Response(
        buf.getvalue(),
        mimetype="application/octet-stream",
        headers={"Content-Disposition": 'inline; filename="gap.fits"'},
    )


# Catalogs that contributed to the legacy /api/export/priority CSV. Kept here
# because the gap-finder pulls candidates from the entire load_catalogs() dict
# but the CSV consumer expects only these three.
_PRIORITY_CSV_CATALOGS = ("green_snrs", "smgps_candidates")
_PRIORITY_CSV_HEADER = [
    "catalog", "name", "ra_deg", "dec_deg", "l_deg", "b_deg",
    "overlap_target_id", "overlap_target_objects",
    "ha_hours", "sii_hours", "oiii_hours",
]


def _priority_csv_response(rows: list[list]) -> tuple[str, int, dict]:
    out = StringIO()
    w = csv.writer(out)
    w.writerow(_PRIORITY_CSV_HEADER)
    for row in rows:
        w.writerow(row)
    return out.getvalue(), 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=priority_sii_targets.csv",
    }


@app.route("/api/export/priority")
def api_export_priority():
    """CSV of overlay candidates where Ha >= 1h but SII < 0.5h (validation-gap bucket).

    With mocpy installed: thin wrapper over compute_gap_moc against the user's
    own manifest. CSV-shaped per-target overlap fields (galactic l/b, target id,
    filter hours) are re-derived here — they're export-shape, not gap-math, so
    they don't belong inside gaps.py.

    Without mocpy: falls back to the original inline implementation so the CSV
    keeps working on stripped-down installs.
    """
    cats = load_catalogs()
    m = load_manifest()
    if m is None or not cats:
        return ("manifest or catalogs missing", 404)

    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
    except Exception:
        return ("astropy missing", 500)

    if _MOCPY_AVAILABLE:
        manifest_src = next(
            (s for s in app.coverage_sources if s.id() == "manifest"), None,
        )
        if manifest_src is None:
            return ("manifest source not registered", 404)
        result = compute_gap_moc(
            [manifest_src],
            have_filter="Ha", missing_filter="SII",
            source_ids=["manifest"],
        )
        if result.gap_moc is None:
            return _priority_csv_response([])

        # Restrict to the legacy CSV's catalog subset before MOC-filtering.
        scoped = {k: v for k, v in cats.items() if k in _PRIORITY_CSV_CATALOGS}
        candidates = candidates_in_moc(result.gap_moc, scoped)

        target_ras = [t["center_ra_deg"] for t in m["targets"]]
        target_decs = [t["center_dec_deg"] for t in m["targets"]]
        target_coords = (
            SkyCoord(target_ras * u.deg, target_decs * u.deg) if target_ras else None
        )
        cat_lookup = {(name, e.get("name", "")): e
                      for name, entries in scoped.items() for e in entries}

        rows: list[list] = []
        for c in candidates:
            entry = cat_lookup.get((c.catalog, c.name), {})
            match_tid, match_objs = "", ""
            ha = sii = oiii = 0.0
            if target_coords is not None:
                cand_sc = SkyCoord(c.ra_deg * u.deg, c.dec_deg * u.deg)
                idx, sep, _ = cand_sc.match_to_catalog_sky(target_coords)
                if float(sep.arcminute) <= 45:
                    t = m["targets"][int(idx)]
                    match_tid = t["target_id"]
                    match_objs = "|".join(t.get("objects", []))
                    ha = t["filters"].get("Ha", {}).get("total_hours", 0.0)
                    sii = t["filters"].get("SII", {}).get("total_hours", 0.0)
                    oiii = t["filters"].get("OIII", {}).get("total_hours", 0.0)
            # Re-apply the 1h/0.5h gate: the gap MOC is union-of-Ha minus
            # union-of-SII at the cell level, but the legacy CSV gates on the
            # nearest-target's hours. Targets near a candidate but with low Ha
            # would otherwise sneak in.
            if ha < 1.0 or sii >= 0.5:
                continue
            rows.append([
                c.catalog, c.name, c.ra_deg, c.dec_deg,
                entry.get("l_deg", ""), entry.get("b_deg", ""),
                match_tid, match_objs, ha, sii, oiii,
            ])
        return _priority_csv_response(rows)

    # --- mocpy-missing fallback: pre-Phase-4 inline implementation ----------
    target_ras = [t["center_ra_deg"] for t in m["targets"]]
    target_decs = [t["center_dec_deg"] for t in m["targets"]]
    target_coords = (
        SkyCoord(target_ras * u.deg, target_decs * u.deg) if target_ras else None
    )

    out = StringIO()
    w = csv.writer(out)
    w.writerow(_PRIORITY_CSV_HEADER)

    for cat_name, entries in cats.items():
        if cat_name not in _PRIORITY_CSV_CATALOGS:
            continue
        valid = [e for e in entries if e.get("ra_deg") is not None and e.get("dec_deg") is not None]
        if not valid:
            continue
        if target_coords is not None:
            cras = [e["ra_deg"] for e in valid]
            cdecs = [e["dec_deg"] for e in valid]
            cand = SkyCoord(cras * u.deg, cdecs * u.deg)
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


# Filter names flow into innerHTML in renderPlanEditor and a few other
# places. Frontend escapes them, but defence-in-depth: reject anything
# outside the legitimate astrophoto filter-name shape on the way in,
# so a poisoned gear.json can never enter the system in the first place.
# Real filter names are short alphanumeric tokens (Ha, SII, OIII, L, R,
# G, B, V, IDAS, U, NII, Hb, etc.) — < and " have no place here.
_FILTER_NAME_RE = re.compile(r"^[A-Za-z0-9_+\-]{1,32}$")


def _validate_gear_payload(payload: dict) -> str | None:
    cameras = payload.get("cameras") or []
    if not isinstance(cameras, list):
        return "cameras must be a list"
    for i, c in enumerate(cameras):
        if not isinstance(c, dict):
            return f"cameras[{i}] must be an object"
        filters = c.get("filters") or {}
        if not isinstance(filters, dict):
            return f"cameras[{i}].filters must be an object"
        for fname in filters.keys():
            if not isinstance(fname, str) or not _FILTER_NAME_RE.match(fname):
                return (f"cameras[{i}].filters has invalid filter name "
                        f"{fname!r} — must match {_FILTER_NAME_RE.pattern}")
    return None


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
    err = _validate_gear_payload(payload)
    if err:
        return jsonify({"error": err}), 400
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

# Target Scheduler enums use [JsonConverter(typeof(StringEnumConverter))]
# in NINA.Plugin.TargetScheduler.Database.Schema, so JSON values are
# enum NAMES, not ordinal ints. Must match exactly: "Draft" / "Active" /
# "Inactive" / "Closed" and "Low" / "Normal" / "High".
_TS_PRIORITY_NAME = {0: "Low", 1: "Normal", 2: "High"}


def _ts_database_version() -> str:
    """Read PRAGMA user_version from the user's TS sqlite DB.

    TS's ImportProfile compares this against the export's `DatabaseVersion`
    and refuses imports newer than its own. Reading the live value at sync
    time guarantees a clean import without the "are you sure?" prompt.
    """
    path = Path(os.path.expandvars(TS_DB_PATH))
    if not path.exists():
        return "0"
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        return str(int(ver))
    except sqlite3.Error:
        return "0"


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
    records. Returns (payload, warnings).

    JSON shape matches Newtonsoft.Json [JsonProperty(MemberSerialization.OptIn)]
    on the TS schema classes (PascalCase property names; StringEnumConverter for
    State/Priority enums; Epoch as ordinal int 0=J2000). ExposurePlans reference
    templates by Id (synthesised here); the importer rewrites IDs through its
    exposureTemplateIdMap so our IDs only need to be stable WITHIN this payload.
    """
    telescopes_by_id = {t["id"]: t for t in gear_data.get("telescopes", [])}
    cameras_by_id = {c["id"]: c for c in gear_data.get("cameras", [])}

    # Group plans into TS Projects. When the user sets `project_name` we
    # honour that as the grouping key. When it's blank, each plan becomes
    # its own Project named after the plan's target — TS docs explicitly
    # call out that "many projects will have only a single target", so
    # giving every untagged plan its own Project is idiomatic and avoids
    # the previous "Unassigned" catch-all dump.
    projects_by_name: dict[str, list] = {}
    for pl in plans_list:
        explicit = (pl.get("project_name") or "").strip()
        if explicit:
            pname = explicit
        else:
            tg = pl.get("target") or {}
            pname = (tg.get("name") or "").strip() or pl.get("id") or "Untitled"
        projects_by_name.setdefault(pname, []).append(pl)

    ts_projects: list[dict] = []
    ts_templates: list[dict] = []
    template_seen: dict[tuple[str, str], dict] = {}
    # Per-entity ID counters. TS's importer keys its remapping dictionaries
    # (projectsIdMap / targetsIdMap / exposurePlansIdMap / exposureTemplateIdMap)
    # by the exported Id. If two entries of the same kind share an Id (e.g.
    # two ExposurePlans both default to 0), Dictionary.Add throws "An item
    # with the same key has already been added. Key: 0". So every entity
    # we emit needs a unique Id within its kind, even though the importer
    # immediately rewrites them.
    project_id_seq, target_id_seq = [0], [0]
    exposure_plan_id_seq, template_id_seq = [0], [0]
    warnings: list[dict] = []

    def _next_id(seq: list[int]) -> int:
        seq[0] += 1
        return seq[0]
    def _next_template_id() -> int: return _next_id(template_id_seq)
    def _next_project_id() -> int: return _next_id(project_id_seq)
    def _next_target_id() -> int: return _next_id(target_id_seq)
    def _next_exposure_plan_id() -> int: return _next_id(exposure_plan_id_seq)

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
        # Reserve the project id up front so each Target can carry it as
        # ProjectId — TS's importer remaps target.ProjectId via
        # projectsIdMap.GetValueOrDefault(); leaving it at 0 produces an
        # orphan target that TS surfaces under an "Unassigned" node.
        proj_id = _next_project_id()
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

            # Resolve exposure plans for this plan. Each entry tracks the
            # (sub_s, desired, acquired, template_id) so all panels of the
            # mosaic share the same set.
            exp_plan_specs: list[dict] = []
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
                        # Synthetic ID — TS's importer rewrites these through
                        # exposureTemplateIdMap. Only needs to be unique within
                        # this payload so ExposurePlan.ExposureTemplateId can
                        # reference the right template before remapping.
                        "Id": _next_template_id(),
                        "Guid": str(uuid.uuid4()),
                        "Name": tpl_name,
                        "FilterName": fname,
                        "DefaultExposure": float(filt_cfg.get("default_sub_s") or sub_s),
                        "Gain": int(filt_cfg.get("gain", -1)),
                        "Offset": int(filt_cfg.get("offset", -1)),
                        "bin": int(filt_cfg.get("bin", 1)),       # lowercase per TS schema
                        "ReadoutMode": 0,
                        "TwilightLevel": 1,                        # Astronomical (TS default)
                        "MinutesOffset": 0,
                        "MoonAvoidanceEnabled": False,
                        "MoonAvoidanceSeparation": 0.0,
                        "MoonAvoidanceWidth": 0,
                        "MoonRelaxScale": 0.0,
                        "MoonRelaxMaxAltitude": 0.0,
                        "MoonRelaxMinAltitude": 0.0,
                        "MoonDownEnabled": False,
                        "DitherEvery": 0,
                        "MaximumHumidity": 100.0,
                    }
                    template_seen[key] = tpl
                    ts_templates.append(tpl)
                tpl = template_seen[key]
                exp_plan_specs.append({
                    "sub_s": sub_s,
                    "desired": desired,
                    "acquired": acquired,
                    "template_id": tpl["Id"],
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
                if multi:
                    panel_idx = panel["row"] * cols + panel["col"] + 1
                    pname_suffix = f" Panel {panel_idx} (R{panel['row']+1}C{panel['col']+1})"
                else:
                    pname_suffix = ""
                tgt_id = _next_target_id()
                exp_plans = [{
                    "Id": _next_exposure_plan_id(),
                    "Guid": str(uuid.uuid4()),
                    "TargetId": tgt_id,    # EF can auto-link via nesting too,
                                            # but setting explicitly is safer.
                    "Exposure": float(spec["sub_s"]),
                    "Desired": int(spec["desired"]),
                    "Acquired": int(spec["acquired"]),
                    "Accepted": int(spec["acquired"]),
                    "IsEnabled": True,
                    "ExposureTemplateId": int(spec["template_id"]),
                } for spec in exp_plan_specs]
                ts_targets.append({
                    "Id": tgt_id,
                    "ProjectId": proj_id,    # link to parent project
                    "Guid": str(uuid.uuid4()),
                    "Name": f"{base_name}{pname_suffix}",
                    "Enabled": True,
                    # Target Scheduler stores RA in HOURS, Dec in degrees.
                    "RA": panel["ra_deg"] / 15.0,
                    "Dec": panel["dec_deg"],
                    "Epoch": 0,                         # 0 = J2000 (NINA enum)
                    "Rotation": rot_deg,
                    "ROI": 100.0,
                    "ExposurePlans": exp_plans,
                    "OverrideExposureOrders": [],
                })

        ts_projects.append({
            "Id": proj_id,
            "Guid": str(uuid.uuid4()),
            "Name": pname,
            "Description": "",
            "State": "Active",
            "Priority": _TS_PRIORITY_NAME.get(PRIORITY_RANK.get(pri_name, 1), "Normal"),
            "CreateDate": datetime.now(timezone.utc).isoformat(),
            "ActiveDate": None,
            "InactiveDate": None,
            "IsMosaic": False,
            "FlatsHandling": 0,
            "MinimumTime": 0,
            "MinimumAltitude": float(min_alt),
            "MaximumAltitude": 90.0,
            "UseCustomHorizon": False,
            "HorizonOffset": 0.0,
            "MeridianWindow": int(meridian),
            "FilterSwitchFrequency": 0,
            "DitherEvery": 0,
            "EnableGrader": False,
            "Targets": ts_targets,
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

    # Match TS's ExportMetadata schema (PascalCase). DatabaseVersion read
    # from the user's TS sqlite means the import goes through silently
    # instead of triggering the "newer/older than current" prompt.
    metadata = {
        "ExportDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "TargetSchedulerVersion": "5.0.0",
        "DatabaseVersion": _ts_database_version(),
        "ExportedProfileName": "Astro Coverage Planner",
        "ExportedProfileId": str(uuid.uuid4()),
    }

    # profilePreference.json intentionally omitted — TS skips the import
    # step when the file is absent, which preserves the user's existing
    # profile preferences and avoids EF validation on a default shell.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))
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
        "zip_filename": zip_path.name,
        "download_url": f"/api/sync/download/{zip_path.name}",
        "warnings": warnings,
        "conflicts": warnings,  # alias — the UI inspects this to offer renames
    })


# Filename whitelist matches the timestamped name produced by /api/sync.
# send_from_directory already blocks path traversal; this is defense in depth
# so a stray request can't probe arbitrary names in ZIP_OUTPUT_DIR.
_SYNC_ZIP_NAME_RE = re.compile(r"^acp-sync-\d{8}T\d{6}Z\.zip$")


@app.route("/api/sync/download/<path:filename>")
def api_sync_download(filename: str):
    if not _SYNC_ZIP_NAME_RE.match(filename):
        return jsonify({"error": "invalid filename"}), 400
    return send_from_directory(
        ZIP_OUTPUT_DIR,
        filename,
        as_attachment=True,
        mimetype="application/zip",
    )


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5555))
    print(f"Astro Coverage Planner -> http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
