#!/usr/bin/env python
"""Build the coverage manifest the planner reads from your FITS/XISF archive.

Scans one or more image roots for FITS + XISF files, reads WCS + gear headers
(TELESCOP, INSTRUME, FOCALLEN, XPIXSZ, APTDIA, GAIN, OFFSET, XBINNING, etc.),
clusters files into targets by spatial position, and emits the JSON manifest
the Coverage Planner reads from ``data/manifest.json``.

Environment variables (all optional):
  FITS_ROOTS     Semicolon-separated list of image roots to scan.
                 Default: the paths listed in ``NAS_ROOTS`` below — edit for
                 your setup or set this env var instead.
  FULL_MASTERS   Extra root of stacked masters (optional).
                 Default: ``<repo>/state/full_masters`` if it exists.
  MANIFEST_PATH  Where to write the manifest. Default: ``<repo>/data/manifest.json``
                 (i.e. exactly where the Coverage Planner expects it).
  PIPELINE_DB    Optional sqlite DB with a ``frames`` table for per-sub hours
                 (a calibration tool's job_queue.db). Missing is fine — hours
                 then come solely from master-file headers.

Run:
  python scripts/build_archive_manifest.py

Then start the planner; the manifest will be picked up automatically.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
import astropy.units as u

import warnings
warnings.filterwarnings("ignore", category=Warning)

REPO_ROOT = Path(__file__).resolve().parent.parent

def _env_paths(var: str) -> list[Path] | None:
    raw = os.environ.get(var)
    if not raw:
        return None
    return [Path(p.strip()) for p in raw.split(";") if p.strip()]

# NAS roots (Windows SMB mappings) — edit this list for your setup, or override
# with FITS_ROOTS="D:/Astro/Images;E:/Archive" before running.
NAS_ROOTS = _env_paths("FITS_ROOTS") or [
    Path("Z:/Astro/Images"),
]
NAS_PREFIX = os.environ.get("NAS_PREFIX", "/mnt/remotes/NAS/")
LOCAL_NAS_PREFIX = "Z:/"

# Optional extra root of stacked full-master files (skipped if it doesn't exist).
FULL_MASTERS = Path(os.environ.get("FULL_MASTERS") or (REPO_ROOT / "state" / "full_masters"))

# Optional pipeline DB for per-sub hours aggregation. Missing is fine; hours
# then come solely from master-file EXPTIME × NCOMBINE.
DB_PATH = Path(os.environ.get("PIPELINE_DB") or (REPO_ROOT / "state" / "job_queue.db"))

# Manifest output — default to ``data/manifest.json`` (the path the planner
# reads by default). Override with MANIFEST_PATH if you want it elsewhere.
MANIFEST_PATH = Path(os.environ.get("MANIFEST_PATH") or (REPO_ROOT / "data" / "manifest.json"))
REPORT_DIR = MANIFEST_PATH.parent
SUMMARY_PATH = REPORT_DIR / "archive_manifest_summary.md"

EXTENSIONS = (".fit", ".fits", ".fts", ".xisf")

# Mount-name patterns that show up in the TELESCOP header instead of the scope.
# These are NOT telescopes — they're mounts. Drop them so the coverage UI doesn't
# create bogus "RainbowAstro" / "iOptron" chips.
MOUNT_TELESCOP_PATTERNS = (
    "rainbowastro", "rainbow astro",
    "ioptron", "gem28", "gem45", "cem26", "cem40", "cem60", "hem27", "hem44",
    "skywatcher eq", "eq6", "eq8",
    "rst/", "rst ", "mc700", "mach2",
)

# Canonicalize slightly-different telescope names to one form so the UI doesn't
# split the same rig into multiple chips. Keys are the exact lowercased TELESCOP
# value; values are the display name.
TELESCOPE_ALIAS = {
    "sw maknewt 190": "190MN",
    "skywatcher maknewt 190": "190MN",
    "skywatcher mn190": "190MN",
    "sky-watcher mn190": "190MN",
    "190mak": "190MN",
    "skywatcher 190makn": "190MN",
    "redcat51": "RedCat 51",
    "william optics redcat 51": "RedCat 51",
    "ap 110gtx": "110GTX",
    "astro-physics 110gtx": "110GTX",
}

# Canonical display spellings. Any TELESCOP value that matches one of these once
# case and spacing are ignored is rewritten to this exact spelling, so a rig that
# reads "HyperStar" in one session and "Hyperstar" in another collapses to one
# chip. Register a scope here once and every casing variant folds to it, without
# needing a per-variant TELESCOPE_ALIAS entry.
TELESCOPE_CANONICAL = (
    "110GTX", "190MN", "RedCat 51", "HyperStar",
)
_TELESCOPE_CANONICAL_BY_FOLD = {
    re.sub(r"\s+", " ", c).strip().casefold(): c for c in TELESCOPE_CANONICAL
}


def sanitize_telescope(raw):
    """Return canonical scope name or None if the TELESCOP value is actually a mount."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    lr = s.lower()
    if any(p in lr for p in MOUNT_TELESCOP_PATTERNS):
        return None
    s = TELESCOPE_ALIAS.get(lr, s)
    fold = re.sub(r"\s+", " ", s).strip().casefold()
    return _TELESCOPE_CANONICAL_BY_FOLD.get(fold, s)


FILTER_CANON = {
    "H": "Ha", "HA": "Ha", "Ha": "Ha", "Halpha": "Ha", "H-alpha": "Ha",
    "HALPHA": "Ha", "H_ALPHA": "Ha",
    "O": "OIII", "O3": "OIII", "OIII": "OIII", "O-III": "OIII", "O_III": "OIII",
    "S": "SII", "S2": "SII", "SII": "SII", "S-II": "SII", "S_II": "SII",
    "L": "L", "LUM": "L", "LUMINANCE": "L", "LIGHT": "L", "CLEAR": "L",
    "R": "R", "RED": "R",
    "G": "G", "GREEN": "G",
    "B": "B", "BLUE": "B",
    "V": "V",
    "IDAS": "IDAS", "IR": "IR", "UV": "UV",
    # No filter wheel, or an empty slot. Kept as its own bucket rather than
    # guessed into L: on a mono camera it is a wide luminance, on a colour
    # camera it is RGB at once (issue #63).
    "NOFILTER": "NoFilter", "NO FILTER": "NoFilter", "NO_FILTER": "NoFilter",
    "NONE": "NoFilter", "EMPTY": "NoFilter", "OPEN": "NoFilter",
}

# Dual and multi band filters used on colour cameras. The canonical spelling
# is the maker's. Matched case-insensitively in headers and filenames so the
# hyphen in "L-eXtreme" is never split into a bare L.
OSC_BAND_FILTERS = {
    "l-extreme": "L-eXtreme", "l-enhance": "L-eNhance", "l-ultimate": "L-Ultimate",
    "l-pro": "L-Pro", "l-quad": "L-Quad", "l-quef": "L-QEF",
    "nbz": "NBZ", "nbz uhs": "NBZ UHS", "alp-t": "ALP-T", "duo-band": "Duo-Band",
    "duo-narrowband": "Duo-Narrowband", "quad-band": "Quad-Band",
    "hao3": "HaO3", "ha/o3": "HaO3", "svbony sv220": "SV220", "sv220": "SV220",
}


_FILTER_BRANDS = {"ANTLIA", "ASTRONOMIK", "ASTRODON", "BAADER", "CHROMA", "OPTOLONG",
                  "ZWO", "SVBONY", "IDAS", "ALTAIR", "PLAYERONE", "ASKAR"}


def canon_filter(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    hit = FILTER_CANON.get(s)
    if hit is not None:
        return hit
    band = OSC_BAND_FILTERS.get(s.lower())
    if band is not None:
        return band
    # "Antlia Ha", "Astronomik OIII" and the like: drop the maker's name and
    # try again on what is left.
    words = [w for w in re.split(r"[\s_]+", s) if w]
    if len(words) > 1 and words[0] in _FILTER_BRANDS:
        return canon_filter(" ".join(words[1:]))
    return str(raw).strip()


def _osc_band_in(text: str) -> str | None:
    low = text.lower()
    for key, name in OSC_BAND_FILTERS.items():
        if key in low:
            return name
    return None


# Which coverage bands a filter credits. Anything not listed credits a band
# named after itself, so unknown filters (IR, sodium, ...) still show up.
# Broadband light pollution filters behave like no filter at all: L on mono,
# RGB on a colour camera.
_BROADBAND_LIKE_NOFILTER = {"NoFilter", "L-Pro", "L-Quad", "L-QEF", "CLS", "UHC"}
_MULTI_BAND = {
    "L-eXtreme": ["Ha", "OIII"], "L-eNhance": ["Ha", "OIII"], "L-Ultimate": ["Ha", "OIII"],
    "NBZ": ["Ha", "OIII"], "NBZ UHS": ["Ha", "OIII"], "ALP-T": ["Ha", "OIII"],
    "Duo-Band": ["Ha", "OIII"], "Duo-Narrowband": ["Ha", "OIII"], "HaO3": ["Ha", "OIII"],
    "SV220": ["Ha", "OIII"],
    "Quad-Band": ["Ha", "OIII", "SII"],
}


def bands_for(filt: str | None, colour: bool) -> list[str]:
    """Coverage bands a frame credits, given its canonical filter and sensor type."""
    if not filt:
        return ["Unknown"]
    if filt in _BROADBAND_LIKE_NOFILTER:
        return ["R", "G", "B"] if colour else ["L"]
    if filt in _MULTI_BAND:
        return list(_MULTI_BAND[filt])
    return [filt]


def filter_label(filt: str | None, colour: bool) -> str:
    """Display name for the real filter behind a band credit."""
    if not filt:
        return "Unknown"
    if filt == "NoFilter" and colour:
        return "OSC"
    return filt


def build_filters_data(members: list[dict]) -> dict:
    """Per-band hours for one target from its cluster members.

    Masters contribute NCOMBINE x EXPTIME when available, else EXPTIME. Folder
    sub blocks contribute n_subs x exptime (their exptime and ncombine already
    encode this). Each member credits every band its filter maps to, and each
    band records which real filters fed it under ``sources``.
    """
    filters_data = defaultdict(lambda: {
        "total_hours": 0.0, "files": 0, "paths": [],
        "sub_folders": 0, "n_subs": 0, "folder_sub_buckets": [],
        "sources": defaultdict(float),
    })
    for m in members:
        colour = bool(m.get("colour"))
        label = filter_label(m.get("filter"), colour)
        is_folder_sub = m.get("role") == "folder_sub"
        if m.get("exptime") and m.get("ncombine"):
            hours = m["exptime"] * m["ncombine"] / 3600.0
        elif m.get("exptime"):
            hours = m["exptime"] / 3600.0
        else:
            hours = 0.0
        for band in bands_for(m.get("filter"), colour):
            d = filters_data[band]
            if not is_folder_sub:
                d["paths"].append(m["path"])
                d["files"] += 1
            else:
                d["sub_folders"] += 1
                d["n_subs"] += m.get("ncombine") or 0
                fs = m.get("_folder_sub") or {}
                d["folder_sub_buckets"].append({
                    "bucket": fs.get("bucket"),
                    "n_subs": fs.get("n_subs"),
                    "exptime": fs.get("exptime"),
                    "hours": round((fs.get("exptime") or 0) * (fs.get("n_subs") or 0) / 3600.0, 2),
                    "stage": fs.get("_stage"),
                    "session_root": fs.get("_session_root"),
                    "sample_path": fs.get("sample_path"),
                    "telescope": fs.get("telescope"),
                })
            d["total_hours"] += hours
            d["sources"][label] += hours
    for d in filters_data.values():
        d["sources"] = {k: round(v, 2) for k, v in d["sources"].items()}
    return dict(filters_data)


def filter_from_path(p: Path) -> str | None:
    """Cascading heuristics: header missed/absent? try filename & parent dirs."""
    # Dual band names carry a hyphen ("L-eXtreme"); catch them whole before the
    # separator split below can reduce them to a bare "L".
    stem = p.stem
    band = _osc_band_in(stem)
    if band:
        return band
    # Filename patterns: *_Ha*, *_SII*, H.xisf, S_integration.xisf, etc.
    # Exact suffix patterns (e.g., target_Ha.fit)
    for sep in ("_", ".", "-", " "):
        parts = stem.split(sep)
        for piece in parts:
            u = piece.upper()
            if u in ("HA", "HALPHA", "H"):
                return "Ha"
            if u in ("SII", "S2", "S"):
                return "SII"
            if u in ("OIII", "O3", "O"):
                return "OIII"
            if u == "L" or u == "LUM":
                return "L"
            if u == "R" or u == "RED":
                return "R"
            if u == "G" or u == "GREEN":
                return "G"
            if u == "B" or u == "BLUE":
                return "B"
            if u == "V":
                return "V"

    # Parent folder name: /H/, /SII/, /OIII/, /L/, /R/, /G/, /B/, /V/.
    # Innermost folder first, so TARGET/DATE/LIGHT/Ha/ reads as Ha. A folder
    # called LIGHT is NINA's image-type folder, not a luminance filter, so it
    # is deliberately not in the L set (issue #63).
    for part in reversed(p.parts[-5:-1]):
        band = _osc_band_in(part)
        if band:
            return band
        u = part.upper()
        if u in ("H", "HA", "HALPHA"):
            return "Ha"
        if u in ("S", "SII", "S2"):
            return "SII"
        if u in ("O", "OIII", "O3"):
            return "OIII"
        if u in ("L", "LUM"):
            return "L"
        if u == "R":
            return "R"
        if u == "G":
            return "G"
        if u == "B":
            return "B"
        if u == "V":
            return "V"
    return None


_CATALOG_PREFIXES = [
    "NGC", "IC", "Sh2", "Abell", "vdB", "LDN", "LBN", "RCW",
    "PGC", "UGC", "Ced", "Mel", "Cr", "Tr", "Stock",
]
_CATALOG_CANON = {p.lower(): p for p in _CATALOG_PREFIXES}
_CATALOG_PREFIX_RE = re.compile(
    r'((?:' + '|'.join(_CATALOG_PREFIXES) + r')[-_]?\d+)',
    re.IGNORECASE,
)
_MESSIER_RE = re.compile(r'(?:^|_)(M\d{1,3})(?:_|$)', re.IGNORECASE)


def object_from_filename(stem: str) -> str | None:
    """Extract a catalog designation from a filename stem when OBJECT is missing.

    Handles ASIAIR-style filenames like ``Light_NGC6960_180.0s_...`` and
    similar patterns from other capture software.
    """
    m = _CATALOG_PREFIX_RE.search(stem)
    if m:
        raw = m.group(1)
        def _canon(g):
            prefix = _CATALOG_CANON[g.group(1).lower()]
            sep = '-' if prefix == 'Sh2' else ' '
            return prefix + sep
        return re.sub(r'(?i)(' + '|'.join(_CATALOG_PREFIXES) + r')[-_\s]*',
                      _canon, raw, count=1).strip()
    m = _MESSIER_RE.search(stem)
    if m:
        return m.group(1).upper()
    return None


CALIBRATION_NAME_PATTERNS = (
    "flat_", "dark_", "bias_", "_flat.", "_dark.", "_bias.",
    "masterflat", "masterdark", "masterbias", "master_flat", "master_dark", "master_bias",
    "flatfield", "flat-field", "flatdark",
)


def prefilter_calibration(p: Path) -> bool:
    """Return True if file is obviously calibration by filename/path — skip header read.

    The point is to avoid header-reading the tens of thousands of flat/dark frames.
    Anything not matching here will still get a header read and be classified by
    IMAGETYP later; this is just a fast pre-filter.
    """
    name = p.name.lower()
    if any(tok in name for tok in CALIBRATION_NAME_PATTERNS):
        return True
    path_str = str(p).lower().replace("\\", "/")
    # Explicit calibration folders
    if any(seg in path_str for seg in (
        "/flats/", "/darks/", "/bias/", "/biases/",
        "/flat/", "/dark/", "/.calibration/", "/master darks/", "/master flats/",
    )):
        return True
    return False


def classify_by_header(meta: dict, p: Path, size: int) -> str:
    """Classify a FITS file AFTER its header has been read.

    Priority:
      1. IMAGETYP (authoritative when present): LIGHT/OBJECT → sub, FLAT/DARK/BIAS → calibration.
      2. File-size heuristic for masters (plate-solved integrations are usually >200 MB).
      3. Path-based fallback (folder names, state/full_masters, etc.).
    """
    name = p.name.lower()
    path_str = str(p).lower().replace("\\", "/")
    imagetyp = (meta.get("imagetyp") or "").strip().upper() if meta else ""

    # the calibration pipeline full_masters (plate-solved, per-object deep stacks)
    if "state/full_masters" in path_str or "\\state\\full_masters" in str(p).lower():
        if name.startswith("full_master_"):
            return "master"
        if "/align_work/" in path_str or "\\align_work\\" in str(p).lower():
            return "processed"
        # Everything else under full_masters/ is intermediate
        return "processed"

    # Explicit master naming
    if name.startswith("full_master_") or name.startswith("nightly_master_") or name.startswith("master_"):
        return "master"
    # WBPP writes masterLight_BIN-1_..., which the master_ prefix above misses;
    # masterFlat/masterDark/masterBias go to calibration by IMAGETYP or the
    # prefilter, never here.
    if name.startswith("masterlight"):
        return "master"
    if "integration" in name or "_stack" in name or name.endswith("_stk.fit") or name.endswith("_stk.fits"):
        return "master"
    if "starless" in name or "denoise" in name:
        return "processed"

    # Header IMAGETYP is authoritative when present
    if imagetyp:
        if imagetyp in ("LIGHT", "LIGHTFRAME", "LIGHT FRAME", "OBJECT", "SCIENCE"):
            # Could still be a master (large or ncombine>1). Defer size check below.
            pass
        elif imagetyp in ("FLAT", "FLATFIELD", "FLAT FIELD", "DOMEFLAT", "SKYFLAT"):
            return "calibration"
        elif imagetyp in ("DARK", "DARKFRAME", "DARK FRAME"):
            return "calibration"
        elif imagetyp in ("BIAS", "ZERO"):
            return "calibration"

    # Master heuristic: large files are probably integrations.
    if size > 200 * 1024 * 1024 and p.suffix.lower() in (".fit", ".fits", ".xisf"):
        # Also require some evidence of being an image (NAXIS1/2 present).
        if (meta or {}).get("naxis1") and (meta or {}).get("naxis2"):
            return "master"

    # Post-processing paths
    if "/working/" in path_str:
        return "processed"
    if "/final images/" in path_str or "/final_images/" in path_str:
        return "processed"

    # the calibration pipeline calibrated output dirs (per-job calibrated lights)
    if "/calibrated/" in path_str and "calibrated_pretty" not in path_str:
        return "calibrated_sub"
    if "/calibrated_pretty/" in path_str:
        return "calibrated_sub"

    # Everything else with a readable header + EXPTIME > 0 + OBJECT is probably a sub.
    if (meta or {}).get("exptime") and (meta.get("object") or imagetyp in ("LIGHT", "OBJECT", "SCIENCE")):
        return "sub"

    # Folder fallback for ambiguous cases
    if "/raw_scanned/" in path_str or "/raw/" in path_str:
        return "sub"
    if "/lights/" in path_str or "/light/" in path_str:
        return "sub"
    if "/pre fy24/" in path_str:
        # Pre FY24 mostly contains lights/calibration; lights we now catch by IMAGETYP above
        return "sub" if (meta or {}).get("exptime") else "unknown"

    return "unknown"


def read_fits_meta(path: Path) -> dict:
    """Read FITS header, extract WCS center, pixel scale, exposure, filter, date, imagetyp."""
    out = {
        "path": str(path),
        "ok": False,
        "filter": None,
        "exptime": None,
        "ncombine": None,
        "naxis1": None,
        "naxis2": None,
        # Solved-grid dimensions: when a plate solve ran on a downsampled frame
        # (IMAGEW/IMAGEH < NAXIS) the WCS pixel scale is per downsampled pixel,
        # so FOV must multiply the *solved* grid, not native NAXIS, by it.
        # None means the solve grid equals NAXIS (no downsample).
        "wcs_naxis1": None,
        "wcs_naxis2": None,
        "pix_arcsec": None,
        "pix_arcsec_focal": None,
        # Canonical FOV pairing resolved at parse time (see _resolve_fov_pairing):
        # the single (grid, scale, method) that compute_fov_from_meta consumes, so
        # no downstream consumer can mispair a per-native-pixel scale with the
        # downsampled solve grid.
        "fov_grid1": None,
        "fov_grid2": None,
        "fov_pix_arcsec": None,
        "fov_method": None,
        "focallen": None,
        "xpixsz": None,
        "ypixsz": None,
        "aperture_mm": None,
        "camera": None,
        "gain": None,
        "offset": None,
        "xbinning": None,
        "ra_deg": None,
        "dec_deg": None,
        "has_wcs": False,
        "date_obs": None,
        "object": None,
        "telescope": None,
        "imagetyp": None,
        "colour": False,
        "error": None,
    }
    try:
        with fits.open(path, memmap=True, ignore_missing_end=True) as hdul:
            h = None
            for hdu in hdul:
                if getattr(hdu, "header", None) is None:
                    continue
                if hdu.header.get("NAXIS", 0) >= 2:
                    h = hdu.header
                    break
            if h is None:
                h = hdul[0].header
            out["filter"] = canon_filter(h.get("FILTER") or h.get("FILTRE"))
            out["exptime"] = float(h.get("EXPTIME") or h.get("EXPOSURE") or 0) or None
            for k in ("NCOMBINE", "STACKCNT", "NSTACKED", "NSUBS", "NIMAGES"):
                v = h.get(k)
                if v is not None:
                    try:
                        out["ncombine"] = int(v)
                        break
                    except Exception:
                        pass
            out["naxis1"] = int(h.get("NAXIS1") or 0) or None
            out["naxis2"] = int(h.get("NAXIS2") or 0) or None
            out["date_obs"] = (h.get("DATE-OBS") or "")[:19] or None
            out["object"] = str(h.get("OBJECT") or "").strip() or None
            if out["object"] is None:
                out["object"] = object_from_filename(Path(path).stem)
            out["telescope"] = sanitize_telescope(h.get("TELESCOP") or h.get("INSTRUME"))
            # Record INSTRUME independently as the camera identity — the telescope
            # fallback above is a legacy workaround for files missing TELESCOP.
            cam_raw = str(h.get("INSTRUME") or "").strip()
            out["camera"] = cam_raw or None
            out["imagetyp"] = str(h.get("IMAGETYP") or h.get("OBSTYPE") or "").strip() or None
            # A Bayer matrix keyword means a colour sensor. NINA, SGP and
            # ASIAIR all write BAYERPAT for OSC cameras; a debayered RGB stack
            # has NAXIS3 == 3 instead.
            out["colour"] = bool(str(h.get("BAYERPAT") or h.get("COLORTYP") or "").strip()) \
                or int(h.get("NAXIS3") or 0) == 3
            # Sensor/exposure settings useful for TS template seeding.
            for src_key, dst_key, caster in (
                ("GAIN", "gain", float), ("OFFSET", "offset", float),
                ("XBINNING", "xbinning", int),
            ):
                v = h.get(src_key)
                if v is not None:
                    try: out[dst_key] = caster(v)
                    except (TypeError, ValueError): pass
            try:
                apt = float(h.get("APTDIA") or 0) or None
                if apt and apt > 0:
                    out["aperture_mm"] = apt
            except (TypeError, ValueError):
                pass
            try:
                ypsz = float(h.get("YPIXSZ") or h.get("PIXSIZE2") or 0) or None
                if ypsz and ypsz > 0:
                    out["ypixsz"] = ypsz
            except (TypeError, ValueError):
                pass

            # WCS
            if "CRVAL1" in h and "CRVAL2" in h and out["naxis1"] and out["naxis2"]:
                try:
                    w = WCS(h)
                    # IMAGEW/IMAGEH: if the plate-solve ran on a downsampled
                    # frame (common with ASIAIR / astrometry.net), the WCS
                    # pixel grid is smaller than NAXIS.  Use the solved
                    # dimensions so the center pixel lands correctly.
                    wcs_w = h.get("IMAGEW")
                    wcs_h = h.get("IMAGEH")
                    if wcs_w and wcs_h:
                        wcs_w, wcs_h = int(float(wcs_w)), int(float(wcs_h))
                        if wcs_w < out["naxis1"] or wcs_h < out["naxis2"]:
                            cx, cy = wcs_w / 2.0, wcs_h / 2.0
                            # Record the downsampled solve grid so FOV multiplies
                            # it (not native NAXIS) by the per-solved-pixel scale.
                            out["wcs_naxis1"], out["wcs_naxis2"] = wcs_w, wcs_h
                        else:
                            cx, cy = out["naxis1"] / 2.0, out["naxis2"] / 2.0
                    else:
                        cx, cy = out["naxis1"] / 2.0, out["naxis2"] / 2.0
                    sky = w.pixel_to_world(cx, cy)
                    out["ra_deg"] = float(sky.ra.deg)
                    out["dec_deg"] = float(sky.dec.deg)
                    try:
                        pix = w.proj_plane_pixel_scales()
                        out["pix_arcsec"] = float(np.mean([p.to(u.arcsec).value for p in pix]))
                    except Exception:
                        cdelt = h.get("CDELT1")
                        if cdelt:
                            out["pix_arcsec"] = abs(float(cdelt)) * 3600.0
                    out["has_wcs"] = True
                except Exception:
                    pass
            # OBJCTRA fallback
            if not out["has_wcs"]:
                ora = h.get("OBJCTRA"); odec = h.get("OBJCTDEC")
                if ora and odec:
                    try:
                        sc = SkyCoord(str(ora), str(odec), unit=(u.hourangle, u.deg))
                        out["ra_deg"] = float(sc.ra.deg)
                        out["dec_deg"] = float(sc.dec.deg)
                    except Exception:
                        pass
            # Always record FOCALLEN + XPIXSZ so FOV can be derived from
            # physics (Method B) even when CD/CDELT is present but bogus.
            try:
                focal = float(h.get("FOCALLEN") or 0) or None
                xpsz = float(h.get("XPIXSZ") or h.get("PIXSIZE1") or 0) or None
                if focal and xpsz and focal > 0 and xpsz > 0:
                    out["focallen"] = focal
                    out["xpixsz"] = xpsz
                    out["pix_arcsec_focal"] = 206.265 * xpsz / focal
            except Exception:
                pass
            # Resolve the (grid, scale) pairing once, here, into canonical fields.
            # Do NOT backfill pix_arcsec from pix_arcsec_focal: pix_arcsec is the
            # per-solved-pixel WCS scale and would then get paired with the
            # downsampled solve grid, undersizing FOV by the downsample ratio. The
            # resolver pairs the focal (per-native) scale with the native grid.
            _resolve_fov_pairing(out)
            out["ok"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def read_xisf_meta(path: Path) -> dict:
    """Read XISF header metadata (WCS + FITSKeywords) without pixel data."""
    out = {
        "path": str(path),
        "ok": False,
        "filter": None,
        "exptime": None,
        "ncombine": None,
        "naxis1": None,
        "naxis2": None,
        "wcs_naxis1": None,
        "wcs_naxis2": None,
        "pix_arcsec": None,
        "pix_arcsec_focal": None,
        # Canonical FOV pairing resolved at parse time (see _resolve_fov_pairing).
        "fov_grid1": None,
        "fov_grid2": None,
        "fov_pix_arcsec": None,
        "fov_method": None,
        "focallen": None,
        "xpixsz": None,
        "ypixsz": None,
        "aperture_mm": None,
        "camera": None,
        "gain": None,
        "offset": None,
        "xbinning": None,
        "ra_deg": None,
        "dec_deg": None,
        "has_wcs": False,
        "date_obs": None,
        "object": None,
        "telescope": None,
        "imagetyp": None,
        "colour": False,
        "error": None,
    }
    try:
        from xisf import XISF
        x = XISF(str(path))
        images = x.get_images_metadata()
        if not images:
            out["error"] = "no images"
            return out
        img = images[0]
        geom = img.get("geometry")
        if geom and len(geom) >= 2:
            out["naxis1"] = int(geom[0])
            out["naxis2"] = int(geom[1])
        # FITSKeywords is dict of key -> list of {value, comment}
        fits_kw = img.get("FITSKeywords") or {}
        def fk(k, default=None):
            v = fits_kw.get(k)
            if v and isinstance(v, list) and len(v):
                return v[0].get("value")
            return default
        # Colour sensor: Bayer keyword carried through, or a 3-channel RGB stack.
        out["colour"] = bool(str(fk("BAYERPAT") or fk("COLORTYP") or "").strip()) \
            or (bool(geom) and len(geom) >= 3 and int(geom[2]) == 3) \
            or str(img.get("colorSpace") or "").upper() == "RGB"
        out["filter"] = canon_filter(fk("FILTER") or fk("FILTRE"))
        try:
            exp = fk("EXPTIME") or fk("EXPOSURE")
            out["exptime"] = float(exp) if exp is not None else None
        except Exception:
            pass
        for k in ("NCOMBINE", "STACKCNT", "NSTACKED", "NSUBS", "NIMAGES"):
            v = fk(k)
            if v is not None:
                try:
                    out["ncombine"] = int(v); break
                except Exception:
                    pass
        out["date_obs"] = str(fk("DATE-OBS") or "")[:19] or None
        out["object"] = str(fk("OBJECT") or "").strip() or None
        if out["object"] is None:
            out["object"] = object_from_filename(Path(path).stem)
        out["telescope"] = sanitize_telescope(fk("TELESCOP") or fk("INSTRUME"))
        cam_raw = str(fk("INSTRUME") or "").strip()
        out["camera"] = cam_raw or None
        out["imagetyp"] = str(fk("IMAGETYP") or fk("OBSTYPE") or "").strip() or None
        for src_key, dst_key, caster in (
            ("GAIN", "gain", float), ("OFFSET", "offset", float),
            ("XBINNING", "xbinning", int),
        ):
            v = fk(src_key)
            if v is not None:
                try: out[dst_key] = caster(v)
                except (TypeError, ValueError): pass
        try:
            apt = float(fk("APTDIA") or 0) or None
            if apt and apt > 0:
                out["aperture_mm"] = apt
        except (TypeError, ValueError):
            pass
        try:
            ypsz = float(fk("YPIXSZ") or 0) or None
            if ypsz and ypsz > 0:
                out["ypixsz"] = ypsz
        except (TypeError, ValueError):
            pass

        crval1 = fk("CRVAL1"); crval2 = fk("CRVAL2")
        if crval1 is not None and crval2 is not None and out["naxis1"]:
            try:
                # IMAGEW/IMAGEH downsample correction, ported from the FITS path.
                # A downsampled solve stores a CD scale per solved pixel, so
                # record the solved grid for FOV.
                iw = fk("IMAGEW"); ih = fk("IMAGEH")
                if iw and ih and out["naxis2"]:
                    try:
                        iw, ih = int(float(iw)), int(float(ih))
                        if iw < out["naxis1"] or ih < out["naxis2"]:
                            out["wcs_naxis1"], out["wcs_naxis2"] = iw, ih
                    except (TypeError, ValueError):
                        pass
                # CRVAL is the sky position AT CRPIX, not necessarily the
                # image centre — a plate solver is free to put its reference
                # pixel anywhere (astrometry.net in particular often doesn't
                # centre it). Build the actual WCS and evaluate it at the
                # image centre pixel, exactly like the FITS path, instead of
                # assuming CRPIX == centre and using CRVAL directly: that
                # assumption silently mis-points every target whose solve
                # didn't happen to centre CRPIX, by up to half the FOV.
                crpix1 = fk("CRPIX1"); crpix2 = fk("CRPIX2")
                cd1_1 = fk("CD1_1"); cd1_2 = fk("CD1_2"); cd2_1 = fk("CD2_1"); cd2_2 = fk("CD2_2")
                cdelt1 = fk("CDELT1"); cdelt2 = fk("CDELT2")
                if crpix1 is not None and crpix2 is not None and (
                        cd1_1 is not None or cdelt1 is not None):
                    w = WCS(naxis=2)
                    w.wcs.crval = [float(crval1), float(crval2)]
                    w.wcs.crpix = [float(crpix1), float(crpix2)]
                    ctype1 = fk("CTYPE1") or "RA---TAN"
                    ctype2 = fk("CTYPE2") or "DEC--TAN"
                    w.wcs.ctype = [str(ctype1), str(ctype2)]
                    if cd1_1 is not None:
                        w.wcs.cd = [[float(cd1_1), float(cd1_2 or 0)],
                                    [float(cd2_1 or 0), float(cd2_2 or cd1_1)]]
                    else:
                        w.wcs.cdelt = [float(cdelt1), float(cdelt2 or cdelt1)]
                    if out["wcs_naxis1"] and out["wcs_naxis2"]:
                        cx, cy = out["wcs_naxis1"] / 2.0, out["wcs_naxis2"] / 2.0
                    else:
                        cx, cy = out["naxis1"] / 2.0, out["naxis2"] / 2.0
                    sky = w.pixel_to_world(cx, cy)
                    out["ra_deg"] = float(sky.ra.deg)
                    out["dec_deg"] = float(sky.dec.deg)
                else:
                    # No CRPIX/CD available to build a real WCS — fall back
                    # to CRVAL as the best available approximation.
                    out["ra_deg"] = float(crval1)
                    out["dec_deg"] = float(crval2)
                out["has_wcs"] = True
                # Pixel scale from CD matrix / CDELT
                if cd1_1 is not None:
                    try:
                        cd = np.array([[float(cd1_1), float(cd1_2 or 0)],
                                       [float(cd2_1 or 0), float(cd2_2 or cd1_1)]])
                        scales = np.sqrt(np.sum(cd ** 2, axis=0))
                        out["pix_arcsec"] = float(np.mean(scales)) * 3600.0
                    except Exception:
                        pass
                if out["pix_arcsec"] is None and cdelt1:
                    try:
                        out["pix_arcsec"] = abs(float(cdelt1)) * 3600.0
                    except Exception:
                        pass
            except Exception:
                pass
        # OBJCTRA/OBJCTDEC fallback (ported from the FITS path) so pointing-only
        # WBPP XISF that never got a plate solve still cluster by their fallback
        # coords instead of vanishing.
        if not out["has_wcs"]:
            ora = fk("OBJCTRA"); odec = fk("OBJCTDEC")
            if ora and odec:
                try:
                    sc = SkyCoord(str(ora), str(odec), unit=(u.hourangle, u.deg))
                    out["ra_deg"] = float(sc.ra.deg)
                    out["dec_deg"] = float(sc.dec.deg)
                except Exception:
                    pass
        try:
            focal = float(fk("FOCALLEN") or 0) or None
            xpsz = float(fk("XPIXSZ") or 0) or None
            if focal and xpsz and focal > 0 and xpsz > 0:
                out["focallen"] = focal
                out["xpixsz"] = xpsz
                out["pix_arcsec_focal"] = 206.265 * xpsz / focal
        except Exception:
            pass
        # Same canonical pairing as the FITS path (no per-native scale ever gets
        # paired with the downsampled solve grid). See _resolve_fov_pairing.
        _resolve_fov_pairing(out)
        out["ok"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def glob_archive(roots, extensions, log=print):
    """Glob all matching files under roots; return list of Path with stat."""
    files = []
    for root in roots:
        if not root.exists():
            log(f"  skip missing root: {root}")
            continue
        log(f"  scanning {root}...")
        t0 = time.time()
        for ext in extensions:
            for p in root.rglob(f"*{ext}"):
                try:
                    sz = p.stat().st_size
                except Exception:
                    sz = 0
                files.append((p, sz))
        log(f"  scan {root}: {len(files)} files so far ({time.time()-t0:.1f}s)")
    return files


def translate_nas_path(p: str) -> str:
    return p.replace(NAS_PREFIX, LOCAL_NAS_PREFIX)


# Canonical pipeline-stage folder names. Some WBPP configurations (and users
# who reorganise by hand) write to differently-named folders for the same
# stage; STAGE_FOLDER_ALIASES maps those variants to a canonical stage so the
# session-dedup keys on the stage, not its spelling. Resolved by
# canon_stage_name(); PIPELINE_STAGE_FOLDERS is the union of canonical names
# and every alias so membership checks still recognise the folder.
STAGE_FOLDER_ALIASES = {
    "cal": "calibrated",
    "reg": "registered",
    "aligned": "registered",
    "masters": "master",
    "integration": "master",
    "original": "og",
    "originals": "og",
    "original_fits": "og",
}
_CANON_STAGE_FOLDERS = {"calibrated", "registered", "master", "og", "starless", "stars"}
PIPELINE_STAGE_FOLDERS = _CANON_STAGE_FOLDERS | set(STAGE_FOLDER_ALIASES)
DERIVATIVE_STAGES = {"og", "starless", "stars"}
WBPP_SIGNATURE_STAGES = {"og", "starless", "stars", "master"}  # markers of WBPP-style session
STAGE_PRIORITY = {"master": 0, "calibrated": 1, "root": 2, "registered": 3}

# WBPP appends stage suffixes to frame stems as it processes them, always in
# the fixed pipeline order:
#   _c     calibrated            _cc    cosmetic-corrected
#   _d     debayered             _r     registered/aligned
# A frame may pass through any subset of these stages, but the suffixes it
# carries always appear in that order. A one-shot-colour (OSC) frame that was
# calibrated, cosmetic-corrected, debayered and registered ends up as
# ``light_001_c_cc_d_r``; a mono frame skipping the debayer step is
# ``light_001_c_cc_r``; and so on. An archive that has been manually flattened
# keeps the raw stem (``light_001.fits``) alongside one or more of these
# processed siblings (``light_001_c_cc_d_r.xisf``).
#
# WBPP_STAGE_SUFFIXES is the closed set of suffix chains we strip when pairing a
# FITS frame with its XISF derivative. We *generate* it as every non-empty
# ordered subset of WBPP_STAGE_SEQUENCE (order preserved) rather than
# hand-enumerating — the old hand-written list silently dropped the _cc+_d
# chains (``_cc_d``, ``_c_cc_d``, ``_cc_d_r``, ``_c_cc_d_r``) that OSC/debayered
# data take, which is the precise miss issue #24 set out to fix. The generated
# tuple is ordered longest-chain-first so a caller iterating it strips the most
# specific match before any prefix of it. Chains are anchored at the end of the
# stem and never fuzzy-matched, so a target whose real name happens to end in
# "_c" is left untouched unless the whole chain matches. Kept as a module-level
# tuple so tests can assert membership.
WBPP_STAGE_SEQUENCE = ("c", "cc", "d", "r")


def _generate_stage_suffixes(stages: tuple[str, ...]) -> tuple[str, ...]:
    """All non-empty ordered subsets of ``stages`` as ``_a_b`` suffix chains.

    Order within the original sequence is preserved (so ``_c`` always precedes
    ``_cc`` precedes ``_d`` precedes ``_r``), and the result is sorted
    longest-chain-first so a caller iterating it strips the most specific match
    first. For ``("c","cc","d","r")`` this yields, longest-first:
    ``_c_cc_d_r``, ``_c_cc_d``, ``_c_cc_r``, ``_c_d_r``, ``_cc_d_r``,
    ``_c_cc``, ``_c_d``, ``_c_r``, ``_cc_d``, ``_cc_r``, ``_d_r``,
    ``_c``, ``_cc``, ``_d``, ``_r`` (15 chains for 4 stages).
    """
    from itertools import combinations
    chains: list[str] = []
    for r in range(1, len(stages) + 1):
        for combo in combinations(stages, r):
            chains.append("_" + "_".join(combo))
    # Sort longest-first; tie-break alphabetically for a deterministic order.
    chains.sort(key=lambda s: (-len(s), s))
    return tuple(chains)


WBPP_STAGE_SUFFIXES = _generate_stage_suffixes(WBPP_STAGE_SEQUENCE)


def canon_stage_name(name: str) -> str:
    """Map a folder name (lowercased) to its canonical pipeline stage."""
    return STAGE_FOLDER_ALIASES.get(name, name)


def _strip_stage_suffix(stem: str) -> str:
    """Return ``stem`` with its single longest trailing WBPP stage-suffix removed.

    Anchored exact-suffix match against WBPP_STAGE_SUFFIXES (the tuple is already
    ordered longest-first so ``_c_cc_d_r`` wins over ``_d_r`` wins over ``_r``).
    Single match only — the chains in the allowlist already enumerate the legal
    combinations, so we never strip more than one. If nothing matches, the stem
    is returned unchanged. This is the longest-strip; for the full set of
    candidate base stems (longest strip first, then progressively shorter) use
    ``_candidate_stripped_stems``.
    """
    low = stem.lower()
    for suf in WBPP_STAGE_SUFFIXES:
        if low.endswith(suf) and len(stem) > len(suf):
            return stem[: -len(suf)]
    return stem


def _candidate_stripped_stems(stem: str) -> list[str]:
    """Every base stem an XISF ``stem`` could collapse onto, longest-strip first.

    Returns the candidate keys produced by stripping each *trailing-anchored*
    WBPP stage-suffix chain that matches, ordered so the longest chain (shortest
    resulting base stem) comes first, then progressively shorter chains, and
    finally the full unstripped stem itself. For ``light_001_c_cc_d_r`` this is::

        ["light_001", "light_001_c", "light_001_c_cc", "light_001_c_cc_d",
         "light_001_c_cc_d_r"]

    Directional collapse uses this list to absorb an XISF into the FITS frame
    whose stem matches the *longest* strip, falling back to shorter strips (an
    intermediate pipeline stage) only when no FITS matches a longer one. The
    full stem is always the last candidate so an XISF with no FITS match keys on
    itself and stays a separate frame. WBPP_STAGE_SUFFIXES is already ordered
    longest-first, so iterating it preserves the longest-strip-first preference.
    """
    low = stem.lower()
    keys: list[str] = []
    for suf in WBPP_STAGE_SUFFIXES:
        if low.endswith(suf) and len(stem) > len(suf):
            stripped = stem[: -len(suf)]
            if stripped not in keys:
                keys.append(stripped)
    keys.append(stem)
    return keys


_FITS_FAMILY = {".fit", ".fits", ".fts"}


def collapse_fits_xisf_pairs(paths: list[str]) -> tuple[list[str], int]:
    """Collapse in-folder FITS+XISF pairs of the same frame to one frame.

    Pure helper for the manifest bucket loop. The collapse is **directional**:
    only an XISF may be absorbed into a FITS frame, never the reverse and never
    FITS-into-FITS. This keeps two genuinely distinct raw frames that happen to
    differ only by a WBPP suffix token (``sub.fits`` + ``sub_r.fits``) as two
    frames, fixing the under-count/frame-loss direction of issue #24's
    medium-severity hole.

    Algorithm (given the file paths in one folder bucket):

      1. Every FITS-family file (.fit/.fits/.fts) keys on its **full** stem and
         is a frame of its own. FITS files never merge with each other.
      2. Each XISF computes its candidate base stems via
         ``_candidate_stripped_stems`` — longest WBPP-suffix strip first, then
         progressively shorter strips, then its own full stem last. It is
         absorbed into the FITS frame matching the **longest** strip; if no FITS
         matches that, the next-shorter strip (an intermediate pipeline stage)
         is tried, and so on. An XISF that matches no FITS keys on its own full
         stem and stays a separate frame (current behaviour preserved).
      3. When an XISF is absorbed, the surviving representative is the XISF (its
         header carries the calibrated sample-meta the rest of the loop wants).

    Deterministic longest-strip-first matching means ``x.fits`` + ``x_c.fits`` +
    ``x_c.xisf`` stay 2 frames: ``x_c.xisf`` strips longest to ``x`` (a FITS) and
    is absorbed there, leaving ``x`` and ``x_c`` as distinct frames. An OSC
    flattened pair ``x.fits`` + ``x_c_cc_d.xisf`` collapses to 1.

    Intentional limitation: collapse is only triggered by a FITS anchor. A
    folder with NO FITS member — e.g. ``a.xisf`` + ``a_c.xisf`` — is left intact
    as two frames even though one is plainly derived from the other, because
    without the raw FITS we cannot tell a stage-derivative apart from two
    distinct frames whose names collide on a suffix token, and merging XISF-into-
    XISF would re-open the under-count hole this directional rule closes.

    Returns ``(collapsed_paths, n_physical)`` where ``n_physical`` is the number
    of distinct physical frames (== len(collapsed_paths)). ``collapsed_paths``
    preserves first-seen order of the surviving representatives.
    """
    # Pass 1: index FITS-family stems (full stem, lowercased) -> first path.
    # Each distinct FITS path is its own frame; map every FITS stem to the frame
    # key it owns so XISF absorption can find it. (If two FITS share a stem,
    # e.g. x.fit + x.fits, they remain separate frames keyed by path identity.)
    fits_stem_to_paths: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        pp = Path(p)
        if pp.suffix.lower() in _FITS_FAMILY:
            fits_stem_to_paths[pp.stem.lower()].append(p)

    # Frames keyed by an identity token, preserving first-seen order. A FITS
    # frame's key is ("fits", path); an XISF that stays separate keys on
    # ("xisf", full_stem); an absorbed XISF joins its FITS anchor's key.
    frame_members: dict[object, list[str]] = defaultdict(list)
    order: list[object] = []

    def _touch(key):
        if key not in frame_members:
            order.append(key)
        return frame_members[key]

    for p in paths:
        pp = Path(p)
        ext = pp.suffix.lower()
        if ext in _FITS_FAMILY:
            # Each FITS path is its own frame — never merges with anything.
            _touch(("fits", p)).append(p)
            continue
        if ext != ".xisf":
            # Unexpected extension — treat as a standalone frame, unchanged.
            _touch(("other", p)).append(p)
            continue
        # XISF: try to absorb into a FITS frame, longest strip first.
        anchor = None
        for cand in _candidate_stripped_stems(pp.stem):
            fits_paths = fits_stem_to_paths.get(cand.lower())
            if fits_paths:
                anchor = ("fits", fits_paths[0])  # first FITS with that stem
                break
        if anchor is not None:
            _touch(anchor).append(p)
        else:
            # No FITS match — stays a separate frame keyed on its own stem.
            _touch(("xisf", pp.stem.lower())).append(p)

    collapsed: list[str] = []
    for key in order:
        members = frame_members[key]
        # Prefer the XISF representative when a FITS frame absorbed one (its
        # header carries the calibrated sample-meta the rest of the loop wants).
        xisf_members = [m for m in members if Path(m).suffix.lower() == ".xisf"]
        collapsed.append(xisf_members[0] if xisf_members else members[0])
    return collapsed, len(collapsed)


_ORIGINALS_FOLDER_RE = re.compile(r"^originals?(?:[_\-].*)?$")


def session_root_and_stage(
    bucket_path: str,
    valid_session_roots: set | None = None,
    originals_session_roots: dict | None = None,
) -> tuple[str, str]:
    """Walk up the bucket path looking for a pipeline-stage folder name.

    Returns (session_root, stage). `stage` is one of PIPELINE_STAGE_FOLDERS or 'root'.

    `valid_session_roots` gates dedup: only bucket paths whose pipeline-stage
    ancestor sits directly under a known WBPP-style session root are treated
    as stage folders. This prevents `Images/calibrated/{job_hash}` (the calibration pipeline job
    storage, where each hash is a distinct session) from being deduplicated.

    `originals_session_roots` maps an ``original*/`` folder bucket path to the
    session root it shares with a ``master/`` sibling (see
    `detect_originals_master_siblings`). When the bucket is such a folder it
    resolves to that session root with the derivative ``og`` stage, so the
    master-present suppression fires. Checked first because these folders carry
    names (e.g. ``original_lights/``) that are not in PIPELINE_STAGE_FOLDERS.
    """
    from pathlib import PurePath
    if originals_session_roots and bucket_path in originals_session_roots:
        return originals_session_roots[bucket_path], "og"
    parts = list(PurePath(bucket_path).parts)
    for i in range(len(parts) - 1, -1, -1):
        name = parts[i].lower()
        if name in PIPELINE_STAGE_FOLDERS:
            session_root = str(PurePath(*parts[:i])) if i > 0 else parts[0]
            if valid_session_roots is None or session_root in valid_session_roots:
                return session_root, canon_stage_name(name)
            # Not a real WBPP session; ignore this stage-named folder and keep walking up
    return bucket_path, "root"


def detect_originals_master_siblings(bucket_paths: list[str]) -> dict[str, str]:
    """Map ``original*/`` folders to the session root of a ``master/`` sibling.

    An archive where the raw lights live in an ``original_fits/`` (or
    ``original_lights/`` etc.) folder that sits beside a ``master/`` folder is a
    flattened/reorganised WBPP session: the master integration already accounts
    for those frames, so the originals must resolve to the master's session root
    for the master-present suppression to drop them.

    Gated on master-present: a standalone raw archive (an ``originals/`` folder
    with no master sibling) returns no mapping and is left to count normally.
    Returns ``{originals_bucket_path: session_root}``.
    """
    from pathlib import PurePath
    master_parents: set[str] = set()
    originals_by_parent: dict[str, list[str]] = defaultdict(list)
    for bp in bucket_paths:
        parts = list(PurePath(bp).parts)
        if not parts:
            continue
        name = parts[-1].lower()
        parent = str(PurePath(*parts[:-1])) if len(parts) > 1 else parts[0]
        if canon_stage_name(name) == "master":
            master_parents.add(parent)
        elif _ORIGINALS_FOLDER_RE.match(name):
            originals_by_parent[parent].append(bp)
    out: dict[str, str] = {}
    for parent, buckets in originals_by_parent.items():
        if parent in master_parents:
            for b in buckets:
                out[b] = parent
    return out


def detect_wbpp_session_roots(bucket_paths: list[str]) -> set[str]:
    """Identify directories that are real WBPP-style session roots.

    A directory qualifies if its children include at least two pipeline-stage
    folders AND at least one derivative-stage or 'master' marker folder —
    that combination is unique to WBPP's per-target output layout. This
    excludes the calibration pipeline's `calibrated/{job_hash}` storage where `calibrated/` has
    no `og/`/`starless/`/`stars/` sibling.
    """
    from pathlib import PurePath
    children_by_parent: dict[str, set[str]] = defaultdict(set)
    for bp in bucket_paths:
        parts = list(PurePath(bp).parts)
        for i in range(len(parts) - 1, -1, -1):
            name = parts[i].lower()
            if name in PIPELINE_STAGE_FOLDERS:
                parent_path = str(PurePath(*parts[:i])) if i > 0 else parts[0]
                children_by_parent[parent_path].add(canon_stage_name(name))
                break
    roots = {
        root for root, stages in children_by_parent.items()
        if len(stages) >= 2 and (stages & WBPP_SIGNATURE_STAGES)
    }
    # A parent holding a master/ and an original*/ sibling is a flattened WBPP
    # session even if original*/ isn't a recognised stage-folder name (issue #24).
    roots |= set(detect_originals_master_siblings(bucket_paths).values())
    return roots


def _resolve_fov_pairing(meta: dict) -> tuple[tuple, float | None, str]:
    """Resolve the two competing (grid, scale) pairs into one canonical set.

    A solved frame can carry two independent pixel-scale/grid pairs:

      * the WCS/CD-matrix scale (``pix_arcsec``), which is per *solved* pixel and
        so pairs with the solved grid — ``wcs_naxis*`` when the plate solve ran
        downsampled, else native NAXIS; and
      * the focal-length scale (``pix_arcsec_focal`` = 206.265 x XPIXSZ / FOCALLEN),
        which is per *native* pixel and always pairs with native NAXIS.

    Only this function knows the pairing rule. It cross-checks the two scales
    (Method A vs Method B): if they disagree by more than 1.5x, the plate solve
    likely stored a scaled/drizzled matrix, so the focal-length physics wins.
    Returns ``((grid_w, grid_h), pix_arcsec, method)``. It stores the result on
    ``meta`` (fov_grid1/fov_grid2/fov_pix_arcsec/fov_method) so the resolution
    happens once, at parse time, and no consumer downstream can mispair the
    scale with the wrong grid (the backfill-into-downsampled-grid bug).
    """
    naxis1, naxis2 = meta.get("naxis1"), meta.get("naxis2")
    native_grid = (naxis1, naxis2)
    if meta.get("wcs_naxis1") and meta.get("wcs_naxis2"):
        cd_grid = (meta["wcs_naxis1"], meta["wcs_naxis2"])
    else:
        cd_grid = native_grid
    pix_cd = meta.get("pix_arcsec")           # per solved pixel (WCS/CD)
    pix_focal = meta.get("pix_arcsec_focal")  # per native pixel (focal length)
    if pix_cd and pix_focal:
        ratio = max(pix_cd, pix_focal) / min(pix_cd, pix_focal)
        if ratio > 1.5:
            grid, pix, method = native_grid, pix_focal, "focal_length_override"
        else:
            grid, pix, method = cd_grid, pix_cd, "CD_matrix"
    elif pix_cd:
        grid, pix, method = cd_grid, pix_cd, "CD_matrix"
    elif pix_focal:
        grid, pix, method = native_grid, pix_focal, "focal_length"
    else:
        grid, pix, method = native_grid, None, "no_pix_scale"
    meta["fov_grid1"], meta["fov_grid2"] = grid
    meta["fov_pix_arcsec"] = pix
    meta["fov_method"] = method
    return grid, pix, method


def compute_fov_from_meta(meta: dict) -> tuple[list | None, float | None, str]:
    """Return (fov_arcmin, pix_arcsec, method).

    Consumes only the canonical FOV fields (fov_grid*/fov_pix_arcsec/fov_method)
    that ``_resolve_fov_pairing`` settled at parse time. Bare meta dicts that were
    never run through the parser (tests, cluster pseudo-members) are resolved on
    the fly from their raw fields, using the same single pairing rule.
    """
    if meta.get("fov_method"):
        gw, gh = meta.get("fov_grid1"), meta.get("fov_grid2")
        pix, method = meta.get("fov_pix_arcsec"), meta["fov_method"]
    else:
        (gw, gh), pix, method = _resolve_fov_pairing(meta)
    if not (gw and gh):
        return None, None, "no_naxis"
    if not pix:
        return None, None, "no_pix_scale"
    return [gw * pix / 60.0, gh * pix / 60.0], pix, method


def cluster_by_coords(items, radius_arcmin=30.0):
    """Greedy spatial clustering. Items must have (ra_deg, dec_deg). Returns list of lists of indices."""
    with_coords = [(i, it) for i, it in enumerate(items)
                   if it.get("ra_deg") is not None and it.get("dec_deg") is not None]
    if not with_coords:
        return []
    idx_map = [i for i, _ in with_coords]
    ras = np.array([it.get("ra_deg") for _, it in with_coords])
    decs = np.array([it.get("dec_deg") for _, it in with_coords])
    coords = SkyCoord(ras * u.deg, decs * u.deg)

    clusters = []
    assigned = np.zeros(len(with_coords), dtype=bool)
    for i in range(len(with_coords)):
        if assigned[i]:
            continue
        seeds = [i]
        assigned[i] = True
        sep = coords[i].separation(coords).arcminute
        for j in range(i + 1, len(with_coords)):
            if not assigned[j] and sep[j] <= radius_arcmin:
                seeds.append(j)
                assigned[j] = True
        clusters.append([idx_map[k] for k in seeds])
    return clusters


def detect_sii_ha_correlation(cluster_files, log=print) -> list:
    """For masters in a cluster, compare Ha master vs SII master pixel values.

    Returns list of flags for suspected copy-bug files.
    """
    from astropy.io import fits as _fits
    flagged = []
    # Bucket master paths by filter
    by_filter = defaultdict(list)
    for f in cluster_files:
        if f["role"] == "master" and f.get("filter") in ("Ha", "SII"):
            by_filter[f["filter"]].append(f["path"])
    if "Ha" not in by_filter or "SII" not in by_filter:
        return flagged
    # Only compare masters that share a target dir (to avoid cross-target nonsense)
    for ha_path in by_filter["Ha"]:
        ha_dir = str(Path(ha_path).parent)
        for sii_path in by_filter["SII"]:
            sii_dir = str(Path(sii_path).parent)
            if ha_dir != sii_dir:
                continue
            try:
                with _fits.open(ha_path, memmap=True) as h_hdu:
                    ha = h_hdu[0].data
                with _fits.open(sii_path, memmap=True) as s_hdu:
                    sii = s_hdu[0].data
                if ha is None or sii is None:
                    continue
                if ha.shape != sii.shape:
                    continue
                # Downsample heavily for speed
                ha_ds = ha[::32, ::32].ravel()
                sii_ds = sii[::32, ::32].ravel()
                if len(ha_ds) < 100:
                    continue
                # Pearson correlation
                corr = float(np.corrcoef(ha_ds.astype("float64"), sii_ds.astype("float64"))[0, 1])
                if corr > 0.95:
                    flagged.append({
                        "ha_path": ha_path,
                        "sii_path": sii_path,
                        "correlation": corr,
                        "note": "SII >95% correlated with Ha — likely migrate_custom_ha copy bug",
                    })
            except Exception as e:
                log(f"  correlation check failed for {ha_path} vs {sii_path}: {e}")
    return flagged


def aggregate_db_subs(db_path: Path, log=print) -> dict:
    """Read DB frames table; return mapping (object_name, canonical_filter) → {hours, n, dates}."""
    import sqlite3 as _sqlite3
    out = defaultdict(lambda: {"hours": 0.0, "n_subs": 0, "dates": set()})
    if not db_path.exists():
        log(f"  DB missing: {db_path}")
        return out
    # Windows UNC may fail — allow an env-configured alternate path (PIPELINE_DB_ALT)
    db_to_open = db_path
    if str(db_path).startswith("\\\\"):
        _alt = os.environ.get("PIPELINE_DB_ALT", "")
        if _alt and Path(_alt).exists():
            db_to_open = Path(_alt)
    try:
        c = _sqlite3.connect(str(db_to_open))
        c.row_factory = _sqlite3.Row
        for r in c.execute(
            "SELECT object_name, filter_name, exptime, captured_at, path FROM frames "
            "WHERE object_name IS NOT NULL AND exptime IS NOT NULL"
        ):
            obj = r["object_name"]
            f = canon_filter(r["filter_name"])
            if f is None:
                continue
            key = (obj, f)
            out[key]["hours"] += (r["exptime"] or 0) / 3600.0
            out[key]["n_subs"] += 1
            d = (r["captured_at"] or "")[:10]
            if d:
                out[key]["dates"].add(d)
        log(f"  DB aggregated: {len(out)} (object, filter) buckets")
    except Exception as e:
        log(f"  DB aggregation failed: {e}")
    return dict(out)


def circular_mean_deg(degs) -> float:
    """Circular mean of a sequence of angles in degrees, wrapped to [0, 360).

    Uses the unit-vector mean (atan2 of the mean sin/cos) so a cluster that
    straddles the RA=0/360 seam gets a correct centre. A plain median-after-
    unwrap is fragile near the seam and a bare ``median % 360`` returns the
    antipode for seam-straddling inputs (e.g. [359.9, 0.1] -> 180). Empty input
    returns 0.0.
    """
    arr = np.asarray(list(degs), dtype="float64")
    if arr.size == 0:
        return 0.0
    r = np.radians(arr)
    ang = np.arctan2(np.mean(np.sin(r)), np.mean(np.cos(r)))
    return float(np.degrees(ang) % 360.0)


def circular_median_deg(degs) -> float:
    """Circular median of a sequence of angles in degrees, wrapped to [0, 360).

    Unwraps every angle onto the branch nearest the circular mean (so a cluster
    straddling the RA=0/360 seam stays contiguous), takes the plain median of the
    unwrapped values, then re-normalises. The median keeps the outlier resistance
    a plain circular mean loses: one mis-solved frame at the greedy cluster's
    ~30-60 arcmin edge can drag a small cluster's mean by ~10-20 arcmin, but the
    median stays with the majority. Empty input returns 0.0.
    """
    arr = np.asarray(list(degs), dtype="float64")
    if arr.size == 0:
        return 0.0
    centre = circular_mean_deg(arr)
    # Map each angle into [centre - 180, centre + 180) so seam-straddling values
    # unwrap to a single contiguous run around the circular mean.
    unwrapped = centre + ((arr - centre + 180.0) % 360.0 - 180.0)
    return float(np.median(unwrapped) % 360.0)


def content_dedup_key(fs: dict):
    """Signature identifying a folder-sub block for content (backup-copy) dedup.

    Keyed on filter, exptime, the file-name set, AND the pointing (RA/Dec rounded
    to ~0.1 deg) plus the observation date. Filename-set alone is too weak: two
    genuinely different targets shot with generic auto-numbered filenames at the
    same filter/exposure share a name set and used to collide, silently dropping
    one target's hours. A true duplicate (the same files reached twice) still
    shares coords and date, so it collapses as before. Returns None when there is
    no name set (nothing to dedup on).
    """
    names = fs.get("_basenames")
    if not names:
        return None
    ra = fs.get("ra_deg")
    dec = fs.get("dec_deg")
    ra_key = round(ra, 1) if ra is not None else None
    dec_key = round(dec, 1) if dec is not None else None
    date_key = (fs.get("date_obs") or "")[:10] or None
    return (fs.get("filter"), round(float(fs.get("exptime") or 0), 1),
            names, ra_key, dec_key, date_key)


def build_folder_sub_blocks(parent: str, paths: list[str], meta_by_path: dict) -> list[dict]:
    """Split one folder's light files into per-(filter, exptime) sub blocks.

    Every file is classified by its OWN header meta (``meta_by_path[path]``), so a
    folder holding e.g. 2x Ha@300s + 2x OIII@600s + 1x SII@600s yields three
    blocks instead of one block that inherits a single sampled file's fields. A
    mis-named calibration frame that slipped through the name pre-filter is
    classified by its own header upstream, so it never lands here wearing a
    neighbour's filter.

    FITS+XISF pipeline-stage pairs are collapsed to one physical frame first
    (see ``collapse_fits_xisf_pairs``), then survivors are grouped. Within a
    group the representative (for pointing/pixel-scale/date fields) prefers a
    member with a real WCS, then any member with fallback coords, else the first.
    Blocks with fallback-only coords keep ``has_wcs=False`` but still carry
    coords so pointing-only subs cluster instead of vanishing.
    """
    collapsed, _ = collapse_fits_xisf_pairs(paths)
    groups: dict[tuple, list] = defaultdict(list)
    for p in collapsed:
        meta = meta_by_path.get(p) or {}
        # Exclude reads that raised partway: read_fits_meta sets filter/exptime
        # early, so a later raise leaves ok=False with real-looking partial
        # fields. Counting those would leak corrupt reads into blocks/hours (the
        # old pipeline excluded them with `if not meta.get("ok"): continue`).
        if not meta.get("ok"):
            continue
        filt = meta.get("filter") or filter_from_path(Path(p))
        filt = canon_filter(filt) if filt else None
        exp = meta.get("exptime") or 0
        if filt is None or exp <= 0:
            continue
        groups[(filt, round(float(exp), 1))].append((p, meta))

    blocks: list[dict] = []
    for (filt, _exp_r), members in groups.items():
        rep_path, rep_meta = members[0]
        for pth, meta in members:
            if meta.get("has_wcs"):
                rep_path, rep_meta = pth, meta
                break
        else:
            for pth, meta in members:
                if meta.get("ra_deg") is not None:
                    rep_path, rep_meta = pth, meta
                    break
        count = len(members)
        # Sum each file's own exposure (all ~equal within the group) so hours
        # are exact rather than count x one sampled exptime.
        total_hours = sum((m.get("exptime") or 0) for _, m in members) / 3600.0
        blocks.append({
            "bucket": parent,
            "filter": filt,
            "exptime": rep_meta.get("exptime"),
            "n_subs": count,
            "total_hours": total_hours,
            "ra_deg": rep_meta.get("ra_deg"),
            "dec_deg": rep_meta.get("dec_deg"),
            "pix_arcsec": rep_meta.get("pix_arcsec"),
            "pix_arcsec_focal": rep_meta.get("pix_arcsec_focal"),
            "naxis1": rep_meta.get("naxis1"),
            "naxis2": rep_meta.get("naxis2"),
            "wcs_naxis1": rep_meta.get("wcs_naxis1"),
            "wcs_naxis2": rep_meta.get("wcs_naxis2"),
            "date_obs": rep_meta.get("date_obs"),
            "object": rep_meta.get("object"),
            "telescope": rep_meta.get("telescope"),
            "camera": rep_meta.get("camera"),
            "colour": bool(rep_meta.get("colour")),
            "sample_path": rep_path,
            "has_wcs": bool(rep_meta.get("has_wcs")),
            "_basenames": frozenset(Path(p).name for p, _ in members),
        })
    return blocks


def main():
    t0 = time.time()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[{time.time()-t0:6.1f}s] Phase 1: Deep archive scan")
    print(f"[{time.time()-t0:6.1f}s] Step 1: Globbing file tree")
    scan_roots = list(NAS_ROOTS)
    if FULL_MASTERS.exists():
        scan_roots.append(FULL_MASTERS)
    missing = [r for r in scan_roots if not r.exists()]
    if missing:
        print(f"[{time.time()-t0:6.1f}s] WARNING: roots missing and will be skipped: {missing}")
    scan_roots = [r for r in scan_roots if r.exists()]
    if not scan_roots:
        print(f"[{time.time()-t0:6.1f}s] ERROR: no valid roots to scan. Set FITS_ROOTS "
              f"(e.g. FITS_ROOTS='D:/Astro/Images;E:/Archive') or edit NAS_ROOTS at the "
              f"top of {Path(__file__).name}.")
        sys.exit(1)
    files = glob_archive(scan_roots, EXTENSIONS,
                         log=lambda m: print(f"[{time.time()-t0:6.1f}s]{m}"))
    print(f"[{time.time()-t0:6.1f}s] Found {len(files)} files total")

    # Step 2: Pre-filter by filename/folder for obvious calibration (skip header read)
    print(f"[{time.time()-t0:6.1f}s] Step 2: Pre-filtering calibration + selecting header-read set")
    classified = []
    to_read = []                 # Files we will header-read
    pre_cal_count = 0
    for p, sz in files:
        entry = {
            "path": str(p),
            "ext": p.suffix.lower(),
            "size_bytes": sz,
            "role": None,
        }
        if prefilter_calibration(p):
            entry["role"] = "calibration"
            pre_cal_count += 1
            classified.append(entry)
            continue
        # Read EVERY imaging-candidate on its own header. A single per-folder
        # sample let one file's filter/exposure/IMAGETYP be inherited by every
        # sibling, so a mixed-filter folder (or a mis-named calibration frame
        # sitting among lights) was classified from a neighbour, non-
        # deterministically. Header-only reads (fits.open memmap, no pixel data)
        # stay cheap and run in parallel below.
        to_read.append(entry)
        classified.append(entry)

    print(f"[{time.time()-t0:6.1f}s] Pre-filtered {pre_cal_count} calibration files by name; "
          f"header-reading {len(to_read)} imaging-candidate files (own header each)")

    # Step 3: header-read every imaging candidate in parallel
    WORKERS = 32

    def _read_one(f):
        p = Path(f["path"])
        if p.suffix.lower() == ".xisf":
            m = read_xisf_meta(p)
        else:
            m = read_fits_meta(p)
        return f["path"], m

    sample_meta = {}  # path → meta (one entry per imaging-candidate file)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_read_one, f): f for f in to_read}
        done = 0
        for fut in as_completed(futures):
            try:
                path, meta = fut.result()
                sample_meta[path] = meta
                # Copy meta into the classified entry too
                for k, v in meta.items():
                    if k != "path":
                        futures[fut][k] = v
            except Exception as e:
                futures[fut]["error"] = f"{type(e).__name__}: {e}"
            done += 1
            if done % 500 == 0:
                print(f"[{time.time()-t0:6.1f}s]   headers read {done}/{len(to_read)}")
    print(f"[{time.time()-t0:6.1f}s] Headers complete ({len(sample_meta)} files read)")

    # (Every imaging candidate is now header-read, so the old folder-sample
    # WCS-recovery pass is gone: a folder's WCS is captured wherever a solved
    # frame exists, and each block picks a WCS-bearing member as its representative.)

    # Step 3b: classify each file by its OWN header + path fallback.
    print(f"[{time.time()-t0:6.1f}s] Step 3b: Post-read classification (IMAGETYP-first)")
    roles_count = defaultdict(int)
    n_unreadable = 0
    for entry in classified:
        if entry["role"] == "calibration":
            roles_count["calibration"] += 1
            continue
        p = Path(entry["path"])
        meta = sample_meta.get(entry["path"])
        # A read that raised partway leaves ok=False with real-looking partial
        # fields (filter/exptime are set before the raise). Exclude it here so it
        # never reaches classify/bucket/count — otherwise a corrupt read leaks
        # into counted blocks. (The old pipeline dropped not-ok metas outright.)
        if meta is not None and not meta.get("ok"):
            entry["role"] = "unreadable"
            roles_count["unreadable"] += 1
            n_unreadable += 1
            continue
        role = classify_by_header(meta or {}, p, entry["size_bytes"])
        entry["role"] = role
        roles_count[role] += 1
    if n_unreadable:
        print(f"[{time.time()-t0:6.1f}s]   excluded {n_unreadable} unreadable file(s) "
              f"(header read raised partway; ok=False)")
    for role, n in sorted(roles_count.items(), key=lambda x: -x[1]):
        print(f"  {role:20s} {n:>7d}")

    # Step 4: Identify masters (self-plate-solved) and build folder-sub blocks for light frames
    masters = []
    for entry in classified:
        if entry["role"] == "master":
            meta = sample_meta.get(entry["path"])
            if meta is None:
                # Master not header-read (shouldn't happen, but defensive): read now
                continue
            m = {**entry, **meta}
            m["path"] = entry["path"]
            masters.append(m)

    # Bucket all light/sub/calibrated_sub files by parent folder — each bucket = a sub-session.
    light_classes = {"sub", "calibrated_sub"}
    session_buckets: dict[str, list[str]] = defaultdict(list)
    for entry in classified:
        if entry["role"] in light_classes:
            parent = str(Path(entry["path"]).parent)
            session_buckets[parent].append(entry["path"])

    # Split each folder into per-(filter, exptime) blocks classified by every
    # file's own header. A folder mixing e.g. Ha@300s and OIII@600s now yields
    # one block per real filter/exposure instead of one block wearing whichever
    # file happened to be sampled first. FITS+XISF pipeline-stage pairs are still
    # collapsed to one physical frame inside the helper (issue #24).
    folder_subs = []
    for parent, paths in session_buckets.items():
        folder_subs.extend(build_folder_sub_blocks(parent, paths, sample_meta))
    n_fs_wcs_raw = sum(1 for f in folder_subs if f["has_wcs"])
    total_sub_hours_raw = sum(f["total_hours"] for f in folder_subs)
    print(f"[{time.time()-t0:6.1f}s] Built {len(folder_subs)} raw folder-sub blocks "
          f"({n_fs_wcs_raw} with WCS) representing {total_sub_hours_raw:.1f}h before dedup")

    # Bug 1 fix: dedupe folder-sub blocks that represent the same frames at
    # different pipeline stages. calibrated/ + registered/ + og/ + starless/ +
    # stars/ under one session root are all derived from the same ~16 subs.
    # Priority: master > calibrated > root > registered. Drop og/starless/stars.
    # Also: if a master/ folder is present in the same session, it already
    # represents the integration — skip all folder_sub blocks for that session
    # (the master file contributes hours via the masters list).
    #
    # First, find the real WBPP-style session roots so we don't accidentally
    # treat the calibration pipeline's calibrated/{job_hash} folders (where each hash is a separate
    # session) as one shared session.
    all_bucket_paths = [fs["bucket"] for fs in folder_subs] + [str(Path(m["path"]).parent) for m in masters]
    wbpp_session_roots = detect_wbpp_session_roots(all_bucket_paths)
    print(f"[{time.time()-t0:6.1f}s] Detected {len(wbpp_session_roots)} WBPP-style session roots")

    # original*/ folders that sit beside a master/ folder belong to that
    # master's session (issue #24) — resolve them so master-present suppression
    # fires. Standalone raw archives (no master sibling) get no mapping.
    originals_session_roots = detect_originals_master_siblings(all_bucket_paths)
    if originals_session_roots:
        print(f"[{time.time()-t0:6.1f}s] Mapped {len(originals_session_roots)} originals folder(s) "
              f"to a master sibling's session root")

    session_dedup_log = []
    session_master_seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fs in folder_subs:
        sr, stage = session_root_and_stage(fs["bucket"], wbpp_session_roots, originals_session_roots)
        fs["_session_root"] = sr
        fs["_stage"] = stage
        if stage == "master":
            session_master_seen[(sr, fs["filter"])].append(fs["bucket"])

    # Also: if a proper master file (from `masters` list) has its parent
    # within a detected WBPP session, suppress that session's folder_subs.
    for m in masters:
        mp = Path(m["path"])
        mparent = str(mp.parent)
        sr, stage = session_root_and_stage(mparent, wbpp_session_roots, originals_session_roots)
        filt = m.get("filter")
        if filt and stage in PIPELINE_STAGE_FOLDERS:
            session_master_seen[(sr, filt)].append(m["path"])

    session_groups: dict[tuple[str, str], list] = defaultdict(list)
    for fs in folder_subs:
        session_groups[(fs["_session_root"], fs["filter"])].append(fs)

    deduped_folder_subs = []
    for (sr, filt), group in session_groups.items():
        has_external_master = bool(session_master_seen.get((sr, filt)))
        # Drop derivative stages unconditionally
        kept = []
        for fs in group:
            if fs["_stage"] in DERIVATIVE_STAGES:
                session_dedup_log.append({
                    "session_root": sr, "filter": filt, "bucket": fs["bucket"],
                    "stage": fs["_stage"], "n_subs": fs["n_subs"],
                    "hours": round(fs["total_hours"], 3),
                    "action": "dropped (derivative product)",
                })
            else:
                kept.append(fs)
        if not kept:
            continue
        if has_external_master:
            # A master integrated file exists for this session/filter. The
            # master contributes the hours; skip all folder_sub entries.
            for fs in kept:
                session_dedup_log.append({
                    "session_root": sr, "filter": filt, "bucket": fs["bucket"],
                    "stage": fs["_stage"], "n_subs": fs["n_subs"],
                    "hours": round(fs["total_hours"], 3),
                    "action": "dropped (master present in session)",
                })
            continue
        kept.sort(key=lambda fs: STAGE_PRIORITY.get(fs["_stage"], 99))
        primary = kept[0]
        deduped_folder_subs.append(primary)
        for fs in kept[1:]:
            session_dedup_log.append({
                "session_root": sr, "filter": filt, "bucket": fs["bucket"],
                "stage": fs["_stage"], "n_subs": fs["n_subs"],
                "hours": round(fs["total_hours"], 3),
                "action": f"dropped (keeping {primary['_stage']} for session)",
            })

    dropped_hours = total_sub_hours_raw - sum(fs["total_hours"] for fs in deduped_folder_subs)
    folder_subs = deduped_folder_subs
    n_fs_wcs = sum(1 for f in folder_subs if f["has_wcs"])
    total_sub_hours = sum(f["total_hours"] for f in folder_subs)
    print(f"[{time.time()-t0:6.1f}s] After session dedup: {len(folder_subs)} blocks "
          f"({n_fs_wcs} with WCS) representing {total_sub_hours:.1f}h "
          f"(dropped {dropped_hours:.1f}h across {len(session_dedup_log)} pipeline-stage duplicates)")

    # Bug 1b fix: content-signature dedup. Some folders are exact backups of
    # others (e.g. the ASIAIR / ASIAIR Mini pair, where the user copied the
    # whole tree from one NAS share to another). Buckets sharing the same
    # filter, exptime, and filename-set are duplicate subs — keep one, drop
    # the rest. Prefer shorter paths as canonical.
    content_dedup_log = []
    content_groups: dict[tuple, list] = defaultdict(list)
    for fs in folder_subs:
        # Key includes pointing (RA/Dec ~0.1 deg) and date, not just filter +
        # exptime + filename-set: two different targets shot with generic auto-
        # numbered filenames at the same filter/exposure share a name set and
        # used to collide (one target's hours vanished). True backup copies still
        # share coords and date, so they still collapse.
        key = content_dedup_key(fs)
        if key is None:
            continue
        content_groups[key].append(fs)
    content_kept: list = []
    keep_ids = set()
    for key, group in content_groups.items():
        if len(group) <= 1:
            keep_ids.add(id(group[0]))
            continue
        group.sort(key=lambda fs: (len(fs["bucket"]), fs["bucket"]))
        primary = group[0]
        keep_ids.add(id(primary))
        for dup in group[1:]:
            content_dedup_log.append({
                "filter": dup["filter"],
                "exptime": dup["exptime"],
                "n_subs": dup["n_subs"],
                "hours": round(dup["total_hours"], 3),
                "bucket": dup["bucket"],
                "action": f"dropped (content-identical to {primary['bucket']})",
            })
    folder_subs = [fs for fs in folder_subs if id(fs) in keep_ids]
    content_dropped_hours = total_sub_hours - sum(fs["total_hours"] for fs in folder_subs)
    total_sub_hours = sum(fs["total_hours"] for fs in folder_subs)
    n_fs_wcs = sum(1 for f in folder_subs if f["has_wcs"])
    print(f"[{time.time()-t0:6.1f}s] After content dedup: {len(folder_subs)} blocks "
          f"({n_fs_wcs} with WCS) representing {total_sub_hours:.1f}h "
          f"(dropped {content_dropped_hours:.1f}h across {len(content_dedup_log)} content-identical duplicates)")

    # Filter: masters with WCS are trustable centers; without, try to keep
    masters_by_id = {f["path"]: f for f in masters}
    n_wcs = sum(1 for f in masters if f.get("has_wcs"))
    print(f"[{time.time()-t0:6.1f}s] {n_wcs}/{len(masters)} masters have WCS")

    # Step 4: DB sub aggregation
    print(f"[{time.time()-t0:6.1f}s] Step 4: DB sub aggregation")
    db_aggs = aggregate_db_subs(DB_PATH, log=lambda m: print(f"[{time.time()-t0:6.1f}s]{m}"))

    # Step 5: Cluster masters + folder-sub-samples by WCS center.
    print(f"[{time.time()-t0:6.1f}s] Step 5: Clustering targets (masters + folder-sub samples)")
    wcs_masters = [m for m in masters if m.get("has_wcs")]
    # Add folder_subs as pseudo-members for clustering (tagged with role=folder_sub).
    # Include ANY block with coords, not just plate-solved ones: pointing-only
    # subs (OBJCTRA/OBJCTDEC fallback, no plate solve) populate ra/dec but
    # has_wcs=False, and used to be dropped here so their hours silently vanished.
    # They cluster by their fallback coords; the block's real has_wcs is carried
    # through so the footprint stays honestly marked (fov_flag 'estimated' when a
    # cluster has no solved master).
    wcs_folder_subs = [{
        "role": "folder_sub",
        "path": fs["sample_path"],
        "ra_deg": fs["ra_deg"],
        "dec_deg": fs["dec_deg"],
        "naxis1": fs["naxis1"], "naxis2": fs["naxis2"],
        "wcs_naxis1": fs.get("wcs_naxis1"), "wcs_naxis2": fs.get("wcs_naxis2"),
        "pix_arcsec": fs["pix_arcsec"],
        "pix_arcsec_focal": fs.get("pix_arcsec_focal"),
        "date_obs": fs["date_obs"],
        "object": fs["object"],
        "telescope": fs["telescope"],
        "camera": fs.get("camera"),
        "colour": bool(fs.get("colour")),
        "filter": fs["filter"],
        "exptime": fs["exptime"],
        "ncombine": fs["n_subs"],
        "has_wcs": bool(fs.get("has_wcs")),
        "_folder_sub": fs,
    } for fs in folder_subs
      if fs.get("ra_deg") is not None and fs.get("dec_deg") is not None]
    cluster_members = wcs_masters + wcs_folder_subs
    clusters = cluster_by_coords(cluster_members, radius_arcmin=30.0)
    n_pointing_only = sum(1 for m in wcs_folder_subs if not m["has_wcs"])
    print(f"[{time.time()-t0:6.1f}s] {len(clusters)} clustered targets "
          f"(from {len(wcs_masters)} masters + {len(wcs_folder_subs)} sub-folder blocks, "
          f"{n_pointing_only} pointing-only)")

    # Build target records
    targets = []
    for i, idxs in enumerate(clusters):
        members = [cluster_members[k] for k in idxs]
        ras = np.array([m["ra_deg"] for m in members])
        decs = np.array([m["dec_deg"] for m in members])
        # RA centre via circular MEDIAN: unwrap about the circular mean (so a
        # cluster straddling the RA=0/360 seam stays contiguous, unlike a plain
        # median % 360 which returns the antipode) then take the median, which
        # resists one mis-solved frame at the cluster edge dragging the centre.
        # Dec has no wrap so its median is fine.
        ra_c = circular_median_deg(ras)
        dec_c = float(np.median(decs))
        sc = SkyCoord(ra_c * u.deg, dec_c * u.deg)
        gal = sc.galactic

        # Derive per-band hours from cluster members (see build_filters_data).
        filters_data = build_filters_data(members)

        # Attach DB sub hours by matching object names that fall near the cluster
        # (We don't re-read all sub headers; we trust DB object_name → spatial match
        # via the closest cluster heuristically.)
        objects = sorted({m.get("object") or "" for m in members if m.get("object")})

        # Bug 2 fix: compute FOV only from this target's master files.
        # Never inherit from a sibling folder_sub (which may belong to a
        # different instrument that happened to spatially cluster here).
        master_members = [m for m in members if m.get("role") != "folder_sub"]
        per_master_fov = []
        for mm in master_members:
            fov_mm, pix_mm, method = compute_fov_from_meta(mm)
            if fov_mm:
                entry = {
                    "path": mm["path"],
                    "fov_arcmin": [round(fov_mm[0], 2), round(fov_mm[1], 2)],
                    "pix_arcsec": round(pix_mm, 3),
                    "method": method,
                    "filter": mm.get("filter"),
                    "telescope": mm.get("telescope"),
                }
                # Surface extra FITS-derived fields used by the Coverage-Planner
                # to auto-seed gear and do physics-based telescope/camera matching.
                # All optional — omitted when the source header didn't carry them.
                for k_src, k_out in (
                    ("camera", "camera"),
                    ("focallen", "focal_length_mm"),
                    ("xpixsz", "pixel_size_um"),
                    ("aperture_mm", "aperture_mm"),
                    ("gain", "gain"),
                    ("offset", "offset"),
                    ("xbinning", "bin"),
                ):
                    v = mm.get(k_src)
                    if v is not None:
                        entry[k_out] = v
                if mm.get("naxis1") and mm.get("naxis2"):
                    entry["sensor_px"] = [int(mm["naxis1"]), int(mm["naxis2"])]
                per_master_fov.append(entry)
        fov_arcmin = None
        pix_arcsec = None
        fov_flag = "from_master"
        if per_master_fov:
            # Envelope = largest FOV among the target's masters.
            envelope = max(per_master_fov, key=lambda x: x["fov_arcmin"][0] * x["fov_arcmin"][1])
            fov_arcmin = envelope["fov_arcmin"]
            pix_arcsec = envelope["pix_arcsec"]
        else:
            # No master for this cluster. Compute from the cluster's member
            # meta (typically a folder_sub sample from THIS cluster, not a
            # foreign instrument since clustering is done by spatial proximity).
            # Flag as estimated so downstream reports can surface the caveat.
            fov_flag = "estimated"
            first = members[0]
            fov_candidate, pix_candidate, method = compute_fov_from_meta(first)
            if fov_candidate:
                fov_arcmin = [round(fov_candidate[0], 2), round(fov_candidate[1], 2)]
                pix_arcsec = round(pix_candidate, 3)
        # FOV corners (ICRS + Galactic)
        corners_icrs = []
        corners_gal = []
        if fov_arcmin:
            w_arcmin, h_arcmin = fov_arcmin
            for dx, dy in [(-1, -1), (-1, 1), (1, 1), (1, -1)]:
                # Offset in arcmin
                c_ra = ra_c + (dx * w_arcmin / 2.0) / 60.0 / np.cos(np.radians(dec_c))
                c_dec = dec_c + (dy * h_arcmin / 2.0) / 60.0
                corners_icrs.append([c_ra, c_dec])
                sc2 = SkyCoord(c_ra * u.deg, c_dec * u.deg).galactic
                corners_gal.append([float(sc2.l.deg), float(sc2.b.deg)])

        # Date range
        dates = sorted({m.get("date_obs")[:10] for m in members if m.get("date_obs")})
        date_range = [dates[0], dates[-1]] if dates else None

        targets.append({
            "target_id": i + 1,
            "objects": objects,
            "center_ra_deg": ra_c,
            "center_dec_deg": dec_c,
            "center_l_deg": float(gal.l.deg),
            "center_b_deg": float(gal.b.deg),
            "fov_arcmin": fov_arcmin,
            "fov_flag": fov_flag,
            "per_master_fov": per_master_fov,
            "pix_arcsec": pix_arcsec,
            "corners_icrs": corners_icrs,
            "corners_galactic": corners_gal,
            "filters": {
                f: {
                    "total_hours": round(d["total_hours"], 2),
                    "files": d["files"],
                    "paths": d["paths"][:10],  # cap for JSON size
                    "sub_folders": d.get("sub_folders", 0),
                    "n_subs": d.get("n_subs", 0),
                    "folder_sub_buckets": d.get("folder_sub_buckets", [])[:10],
                    "sources": d.get("sources", {}),
                }
                for f, d in filters_data.items()
            },
            "master_files": [m["path"] for m in members if m.get("role") != "folder_sub"],
            "telescopes": sorted({m.get("telescope") for m in members if m.get("telescope")}),
            "cameras": sorted({m.get("camera") for m in members if m.get("camera")}),
            "date_range": date_range,
            "n_masters": sum(1 for m in members if m.get("role") != "folder_sub"),
            "n_sub_folders": sum(1 for m in members if m.get("role") == "folder_sub"),
        })

    # Enrich targets with DB sub hours by fuzzy object-name matching
    # For each target, find DB (object, filter) rows whose object name looks similar to any of target.objects
    print(f"[{time.time()-t0:6.1f}s] Step 5b: Attaching DB sub hours")
    obj_to_target = {}  # normalized_obj_name -> target_idx
    for ti, t in enumerate(targets):
        for obj in t["objects"]:
            obj_to_target[obj.lower().replace("_", " ").strip()] = ti
    db_unmatched = 0
    for (obj, filt), agg in db_aggs.items():
        key = obj.lower().replace("_", " ").strip()
        ti = obj_to_target.get(key)
        if ti is None:
            db_unmatched += 1
            continue
        t = targets[ti]
        f_entry = t["filters"].setdefault(filt, {"total_hours": 0.0, "files": 0, "paths": []})
        f_entry["db_sub_hours"] = f_entry.get("db_sub_hours", 0.0) + agg["hours"]
        f_entry["db_sub_count"] = f_entry.get("db_sub_count", 0) + agg["n_subs"]
        # Use db hours as authoritative total if no master-derived hours
        if f_entry["total_hours"] == 0.0:
            f_entry["total_hours"] = round(agg["hours"], 2)
    print(f"[{time.time()-t0:6.1f}s] {db_unmatched} DB (object,filter) rows unmatched to target clusters")

    # Step 6: Integrity checks
    print(f"[{time.time()-t0:6.1f}s] Step 6: Integrity checks (sii vs ha correlation)")
    flagged = []
    for t in targets:
        # Gather master file records for this target
        cluster_files = []
        for p in t["master_files"]:
            m = masters_by_id.get(p)
            if m:
                cluster_files.append(m)
        # Only check if target has both Ha and SII masters
        if any(f == "Ha" for f in t["filters"].keys()) and any(f == "SII" for f in t["filters"].keys()):
            f = detect_sii_ha_correlation(cluster_files)
            for item in f:
                item["target_id"] = t["target_id"]
                item["objects"] = t["objects"]
                flagged.append(item)
    print(f"[{time.time()-t0:6.1f}s] {len(flagged)} sii-ha correlation warnings")

    # Ambiguous filter flags
    ambig = []
    for m in masters:
        if not m.get("filter"):
            # Try path-based fallback
            path_filt = filter_from_path(Path(m["path"]))
            if path_filt:
                m["filter"] = path_filt
            else:
                ambig.append(m["path"])
    no_wcs = [m["path"] for m in masters if not m.get("has_wcs")]

    # Summarise totals
    total_hours = 0.0
    for t in targets:
        for f, d in t["filters"].items():
            total_hours += d["total_hours"]

    # Step 7: Write manifest
    print(f"[{time.time()-t0:6.1f}s] Step 7: Writing manifest")
    manifest = {
        "scan_date": datetime.now().isoformat(),
        "scan_duration_sec": round(time.time() - t0, 1),
        "scan_roots": [str(r) for r in scan_roots],
        "total_files_scanned": len(files),
        "file_role_counts": dict(roles_count),
        "total_targets": len(targets),
        "total_masters_with_wcs": n_wcs,
        "total_integration_hours": round(total_hours, 1),
        "targets": targets,
        "integrity_flags": {
            "sii_ha_correlation_suspects": flagged,
            "masters_missing_wcs": no_wcs,
            "masters_ambiguous_filter": ambig,
            "session_dedup_drops": session_dedup_log,
            "session_dedup_hours_dropped": round(dropped_hours, 2),
            "session_dedup_buckets_dropped": len(session_dedup_log),
            "content_dedup_drops": content_dedup_log,
            "content_dedup_hours_dropped": round(content_dropped_hours, 2),
            "content_dedup_buckets_dropped": len(content_dedup_log),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[{time.time()-t0:6.1f}s] Wrote {MANIFEST_PATH}")

    # Optional sanitised copy for sharing — same data, but stripped of paths,
    # serials, and exact dates. See scripts/sanitise_manifest.py.
    if _CLI_ARGS and _CLI_ARGS.sanitise:
        from sanitise_manifest import sanitise_dict, validate_no_paths
        out_path = Path(_CLI_ARGS.sanitise)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sanitised = sanitise_dict(manifest, label=_CLI_ARGS.label or "")
        validate_no_paths(sanitised)
        out_path.write_text(
            json.dumps(sanitised, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"[{time.time()-t0:6.1f}s] Wrote sanitised copy to {out_path}")

    # Step 8: Summary markdown
    write_summary(manifest)
    print(f"[{time.time()-t0:6.1f}s] Wrote {SUMMARY_PATH}")

    print(f"\n[{time.time()-t0:6.1f}s] DONE.")
    print(f"  Total files:       {len(files)}")
    print(f"  Masters:           {len(masters)}")
    print(f"  Targets (clusters): {len(targets)}")
    print(f"  Total hours (gross, all filters): {total_hours:.1f}")
    print(f"  sii==ha suspects:  {len(flagged)}")
    print(f"  Masters missing WCS: {len(no_wcs)}")


def write_summary(m: dict):
    lines = []
    lines.append("# Archive Manifest Summary")
    lines.append("")
    lines.append(f"**Scan date**: {m['scan_date'][:19]}  |  **Scan duration**: {m['scan_duration_sec']}s")
    lines.append("")
    lines.append(f"- **Total files scanned**: {m['total_files_scanned']:,}")
    lines.append(f"- **Unique targets** (spatial clusters, 30′): {m['total_targets']}")
    lines.append(f"- **Masters with WCS**: {m['total_masters_with_wcs']}")
    lines.append(f"- **Total integration (gross, all filters)**: {m['total_integration_hours']} h")
    lines.append("")
    lines.append("## File role counts")
    lines.append("")
    lines.append("| Role | Count |")
    lines.append("|---|---:|")
    for role, n in sorted(m["file_role_counts"].items(), key=lambda x: -x[1]):
        lines.append(f"| {role} | {n:,} |")
    lines.append("")

    # Coverage by filter
    lines.append("## Coverage by filter (across all targets)")
    lines.append("")
    per_filter = defaultdict(lambda: {"targets": 0, "hours": 0.0, "masters": 0})
    for t in m["targets"]:
        for f, d in t["filters"].items():
            per_filter[f]["targets"] += 1
            per_filter[f]["hours"] += d["total_hours"]
            per_filter[f]["masters"] += d["files"]
    lines.append("| Filter | #targets | #masters | hours |")
    lines.append("|---|---:|---:|---:|")
    for f in ("Ha", "SII", "OIII", "L", "R", "G", "B", "V", "IDAS", "IR", "Unknown"):
        if f in per_filter:
            d = per_filter[f]
            lines.append(f"| {f} | {d['targets']} | {d['masters']} | {d['hours']:.1f} |")
    for f in sorted(set(per_filter) - {"Ha", "SII", "OIII", "L", "R", "G", "B", "V", "IDAS", "IR", "Unknown"}):
        d = per_filter[f]
        lines.append(f"| {f} | {d['targets']} | {d['masters']} | {d['hours']:.1f} |")
    lines.append("")

    # Top 15 targets by total hours
    lines.append("## Top 15 targets by integration")
    lines.append("")
    lines.append("| Target (objects) | l | b | filters | hours |")
    lines.append("|---|---:|---:|---|---:|")
    tgts = sorted(m["targets"], key=lambda t: -sum(d["total_hours"] for d in t["filters"].values()))[:15]
    for t in tgts:
        objs = ", ".join(t["objects"][:2]) + (f" (+{len(t['objects'])-2})" if len(t["objects"]) > 2 else "")
        flist = ", ".join(f"{f}={d['total_hours']:.1f}h" for f, d in sorted(t["filters"].items(), key=lambda x: -x[1]["total_hours"]) if d["total_hours"] > 0)
        total = sum(d["total_hours"] for d in t["filters"].values())
        lines.append(f"| {objs or '(unknown)'} | {t['center_l_deg']:.2f} | {t['center_b_deg']:+.2f} | {flist} | {total:.1f} |")
    lines.append("")

    # Integrity flags
    flags = m.get("integrity_flags", {})
    lines.append("## Integrity flags")
    lines.append("")
    sus = flags.get("sii_ha_correlation_suspects", [])
    lines.append(f"- **SII==Ha copy-bug suspects** (correlation > 0.95): {len(sus)}")
    for s in sus[:10]:
        lines.append(f"  - r={s['correlation']:.3f} — `{Path(s['ha_path']).name}` vs `{Path(s['sii_path']).name}` (target {s['target_id']}: {', '.join(s.get('objects', []))})")
    no_wcs = flags.get("masters_missing_wcs", [])
    lines.append(f"- **Masters missing WCS**: {len(no_wcs)}")
    amb = flags.get("masters_ambiguous_filter", [])
    lines.append(f"- **Masters with ambiguous filter**: {len(amb)}")
    lines.append("")
    lines.append(f"_Full detail in `archive_manifest.json` (integrity_flags section)._")

    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")


_CLI_ARGS = None


def _parse_cli(argv: list[str] | None = None):
    import argparse
    ap = argparse.ArgumentParser(description="Build the coverage manifest.")
    ap.add_argument("--sanitise", metavar="OUT",
                    help="After writing the manifest, also write a sanitised "
                         "(shareable) copy to this path.")
    ap.add_argument("--label", default="",
                    help="Friend-label embedded in the sanitised manifest.")
    return ap.parse_args(argv)


if __name__ == "__main__":
    _CLI_ARGS = _parse_cli()
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
