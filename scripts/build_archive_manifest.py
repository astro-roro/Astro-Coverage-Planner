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
                 (Astro-Auto-Calibration's job_queue.db). Missing is fine — hours
                 then come solely from master-file headers.

Run:
  python scripts/build_archive_manifest.py

Then start the planner; the manifest will be picked up automatically.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
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
    Path("Z:/Astro With RoRo/Images"),
]
NAS_PREFIX = "/mnt/remotes/SINGULARITY.ERKO_Singularity/"
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
# split the same rig into multiple chips.
TELESCOPE_ALIAS = {
    "sw maknewt 190": "190MN",
    "skywatcher maknewt 190": "190MN",
    "skywatcher mn190": "190MN",
    "sky-watcher mn190": "190MN",
    "redcat51": "RedCat 51",
    "william optics redcat 51": "RedCat 51",
    "ap 110gtx": "110GTX",
    "astro-physics 110gtx": "110GTX",
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
    return TELESCOPE_ALIAS.get(lr, s)


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
}


def canon_filter(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return FILTER_CANON.get(s, str(raw).strip())


def filter_from_path(p: Path) -> str | None:
    """Cascading heuristics: header missed/absent? try filename & parent dirs."""
    name = p.name
    # Filename patterns: *_Ha*, *_SII*, H.xisf, S_integration.xisf, etc.
    stem = p.stem
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

    # Parent folder name: /H/, /SII/, /OIII/, /L/, /R/, /G/, /B/, /V/
    for part in p.parts[-5:]:
        u = part.upper()
        if u in ("H", "HA", "HALPHA"):
            return "Ha"
        if u in ("S", "SII", "S2"):
            return "SII"
        if u in ("O", "OIII", "O3"):
            return "OIII"
        if u in ("L", "LUM", "LIGHT"):
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

    # ADPP full_masters (plate-solved, per-object deep stacks)
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

    # ADPP calibrated output dirs (per-job calibrated lights)
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
        "pix_arcsec": None,
        "pix_arcsec_focal": None,
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
            if out["pix_arcsec"] is None and out["pix_arcsec_focal"]:
                out["pix_arcsec"] = out["pix_arcsec_focal"]
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
        "pix_arcsec": None,
        "pix_arcsec_focal": None,
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
                out["ra_deg"] = float(crval1)
                out["dec_deg"] = float(crval2)
                out["has_wcs"] = True
                # Pixel scale from CD matrix / CDELT
                cd1_1 = fk("CD1_1"); cd1_2 = fk("CD1_2"); cd2_1 = fk("CD2_1"); cd2_2 = fk("CD2_2")
                cdelt1 = fk("CDELT1"); cdelt2 = fk("CDELT2")
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
        try:
            focal = float(fk("FOCALLEN") or 0) or None
            xpsz = float(fk("XPIXSZ") or 0) or None
            if focal and xpsz and focal > 0 and xpsz > 0:
                out["focallen"] = focal
                out["xpixsz"] = xpsz
                out["pix_arcsec_focal"] = 206.265 * xpsz / focal
        except Exception:
            pass
        if out["pix_arcsec"] is None and out["pix_arcsec_focal"]:
            out["pix_arcsec"] = out["pix_arcsec_focal"]
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


PIPELINE_STAGE_FOLDERS = {"calibrated", "registered", "master", "og", "starless", "stars"}
DERIVATIVE_STAGES = {"og", "starless", "stars"}
WBPP_SIGNATURE_STAGES = {"og", "starless", "stars", "master"}  # markers of WBPP-style session
STAGE_PRIORITY = {"master": 0, "calibrated": 1, "root": 2, "registered": 3}


def session_root_and_stage(bucket_path: str, valid_session_roots: set | None = None) -> tuple[str, str]:
    """Walk up the bucket path looking for a pipeline-stage folder name.

    Returns (session_root, stage). `stage` is one of PIPELINE_STAGE_FOLDERS or 'root'.

    `valid_session_roots` gates dedup: only bucket paths whose pipeline-stage
    ancestor sits directly under a known WBPP-style session root are treated
    as stage folders. This prevents `Images/calibrated/{job_hash}` (ADPP job
    storage, where each hash is a distinct session) from being deduplicated.
    """
    from pathlib import PurePath
    parts = list(PurePath(bucket_path).parts)
    for i in range(len(parts) - 1, -1, -1):
        name = parts[i].lower()
        if name in PIPELINE_STAGE_FOLDERS:
            session_root = str(PurePath(*parts[:i])) if i > 0 else parts[0]
            if valid_session_roots is None or session_root in valid_session_roots:
                return session_root, name
            # Not a real WBPP session; ignore this stage-named folder and keep walking up
    return bucket_path, "root"


def detect_wbpp_session_roots(bucket_paths: list[str]) -> set[str]:
    """Identify directories that are real WBPP-style session roots.

    A directory qualifies if its children include at least two pipeline-stage
    folders AND at least one derivative-stage or 'master' marker folder —
    that combination is unique to WBPP's per-target output layout. This
    excludes ADPP's `calibrated/{job_hash}` storage where `calibrated/` has
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
                children_by_parent[parent_path].add(name)
                break
    return {
        root for root, stages in children_by_parent.items()
        if len(stages) >= 2 and (stages & WBPP_SIGNATURE_STAGES)
    }


def compute_fov_from_meta(meta: dict) -> tuple[list | None, float | None, str]:
    """Return (fov_arcmin, pix_arcsec, method).

    Cross-checks CD-matrix pixel scale (Method A) against FOCALLEN+XPIXSZ
    (Method B). If they disagree by more than 1.5x, trust Method B — the
    plate solve likely stored a scaled/drizzled matrix.
    """
    naxis1, naxis2 = meta.get("naxis1"), meta.get("naxis2")
    if not (naxis1 and naxis2):
        return None, None, "no_naxis"
    pix_a = meta.get("pix_arcsec")
    pix_b = meta.get("pix_arcsec_focal")
    if pix_a and pix_b:
        ratio = max(pix_a, pix_b) / min(pix_a, pix_b)
        if ratio > 1.5:
            pix = pix_b
            method = "focal_length_override"
        else:
            pix = pix_a
            method = "CD_matrix"
    elif pix_a:
        pix, method = pix_a, "CD_matrix"
    elif pix_b:
        pix, method = pix_b, "focal_length"
    else:
        return None, None, "no_pix_scale"
    return [naxis1 * pix / 60.0, naxis2 * pix / 60.0], pix, method


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
    # Windows UNC may fail — try Y: prefix
    db_to_open = db_path
    if str(db_path).startswith("\\\\"):
        alt = Path("Y:/D Drive Backup/Work/GitHub/Astro-Auto-Calibration/state/job_queue.db")
        if alt.exists():
            db_to_open = alt
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
    print(f"[{time.time()-t0:6.1f}s] Step 2: Pre-filtering calibration + picking header-read set")
    classified = []
    to_read = []                 # Files we will actually open
    to_read_sample_path = {}     # parent_folder → representative path (for sampling one sub per folder)
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
        # For imaging-candidate fits/xisf, pick ONE representative per parent folder to
        # header-read (saves ~10× I/O). Subs in the same folder usually share WCS/filter/exptime.
        # We still record every file, but only sample-read.
        parent = str(p.parent)
        if parent not in to_read_sample_path:
            to_read_sample_path[parent] = str(p)
            to_read.append(entry)
        classified.append(entry)

    print(f"[{time.time()-t0:6.1f}s] Pre-filtered {pre_cal_count} calibration files by name; "
          f"header-reading {len(to_read)} folder-sample files (covering {len(classified) - pre_cal_count} imaging candidates)")

    # Step 3: header-read ALL imaging-candidate folder samples in parallel
    WORKERS = 32

    def _read_one(f):
        p = Path(f["path"])
        if p.suffix.lower() == ".xisf":
            m = read_xisf_meta(p)
        else:
            m = read_fits_meta(p)
        return f["path"], m

    sample_meta = {}  # path → meta
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_read_one, f): f for f in to_read}
        done = 0
        for fut in as_completed(futures):
            try:
                path, meta = fut.result()
                sample_meta[path] = meta
                # Copy meta into the classified entry for the sample path too
                for k, v in meta.items():
                    if k != "path":
                        futures[fut][k] = v
            except Exception as e:
                futures[fut]["error"] = f"{type(e).__name__}: {e}"
            done += 1
            if done % 200 == 0:
                print(f"[{time.time()-t0:6.1f}s]   headers read {done}/{len(to_read)}")
    print(f"[{time.time()-t0:6.1f}s] Headers complete ({len(sample_meta)} samples read)")

    # Step 3a (WCS recovery pass): for folders whose first sample has no WCS, try up to
    # 4 more files per folder. Many sessions have the first frame saved before a plate
    # solve ran, but later frames in the same folder DO have CRVAL1/2 written.
    folder_candidates = defaultdict(list)
    for entry in classified:
        if entry["role"] == "calibration":
            continue
        parent = str(Path(entry["path"]).parent)
        folder_candidates[parent].append(entry["path"])

    needs_retry = []
    for parent, paths in folder_candidates.items():
        first_sample = to_read_sample_path.get(parent)
        meta = sample_meta.get(first_sample) if first_sample else None
        if meta is None or not meta.get("ok"):
            continue
        if meta.get("has_wcs"):
            continue
        # Try up to 4 more files (evenly-spaced picks to increase chance of hitting a solved one)
        remaining = [p for p in paths if p != first_sample]
        if len(remaining) <= 1:
            continue
        # Pick 4 evenly-spaced candidates from the remaining
        step = max(1, len(remaining) // 4)
        picks = remaining[::step][:4]
        for pick in picks:
            needs_retry.append((parent, pick))

    if needs_retry:
        print(f"[{time.time()-t0:6.1f}s] Step 3a: WCS-recovery pass on {len({p for p,_ in needs_retry})} folders ({len(needs_retry)} additional header reads)")
        def _try_one(pp):
            parent, path = pp
            p = Path(path)
            if p.suffix.lower() == ".xisf":
                m = read_xisf_meta(p)
            else:
                m = read_fits_meta(p)
            return parent, path, m
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(_try_one, pp): pp for pp in needs_retry}
            recovered = 0
            for fut in as_completed(futures):
                try:
                    parent, path, meta = fut.result()
                except Exception:
                    continue
                if meta.get("ok") and meta.get("has_wcs"):
                    # Promote this file as the folder's sample
                    if not sample_meta.get(to_read_sample_path.get(parent, ""), {}).get("has_wcs"):
                        sample_meta[path] = meta
                        to_read_sample_path[parent] = path
                        recovered += 1
            print(f"[{time.time()-t0:6.1f}s] WCS-recovery pass: gained WCS for {recovered} folders")

    # Step 3b: classify each file using header (from its folder's sample) + path fallback
    print(f"[{time.time()-t0:6.1f}s] Step 3b: Post-read classification (IMAGETYP-first)")
    roles_count = defaultdict(int)
    for entry in classified:
        if entry["role"] == "calibration":
            roles_count["calibration"] += 1
            continue
        p = Path(entry["path"])
        # Use this file's own meta if it was the sample; otherwise fall back to its folder's sample
        parent = str(p.parent)
        meta = sample_meta.get(entry["path"]) or sample_meta.get(to_read_sample_path.get(parent, ""))
        role = classify_by_header(meta or {}, p, entry["size_bytes"])
        entry["role"] = role
        entry["_meta_source"] = "self" if sample_meta.get(entry["path"]) else "folder_sample"
        roles_count[role] += 1
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

    folder_subs = []
    for parent, paths in session_buckets.items():
        sample_path = to_read_sample_path.get(parent)
        meta = sample_meta.get(sample_path or "") or {}
        if not meta.get("ok"):
            continue
        filt = meta.get("filter") or filter_from_path(Path(sample_path or parent))
        if filt is None:
            continue
        exp = meta.get("exptime") or 0
        if exp <= 0:
            continue
        count = len(paths)
        basenames = frozenset(Path(p).name for p in paths)
        folder_subs.append({
            "bucket": parent,
            "filter": canon_filter(filt),
            "exptime": exp,
            "n_subs": count,
            "total_hours": exp * count / 3600.0,
            "ra_deg": meta.get("ra_deg"),
            "dec_deg": meta.get("dec_deg"),
            "pix_arcsec": meta.get("pix_arcsec"),
            "naxis1": meta.get("naxis1"),
            "naxis2": meta.get("naxis2"),
            "date_obs": meta.get("date_obs"),
            "object": meta.get("object"),
            "telescope": meta.get("telescope"),
            "sample_path": sample_path,
            "has_wcs": bool(meta.get("has_wcs")),
            "_basenames": basenames,
        })
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
    # treat ADPP's calibrated/{job_hash} folders (where each hash is a separate
    # session) as one shared session.
    all_bucket_paths = [fs["bucket"] for fs in folder_subs] + [str(Path(m["path"]).parent) for m in masters]
    wbpp_session_roots = detect_wbpp_session_roots(all_bucket_paths)
    print(f"[{time.time()-t0:6.1f}s] Detected {len(wbpp_session_roots)} WBPP-style session roots")

    session_dedup_log = []
    session_master_seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for fs in folder_subs:
        sr, stage = session_root_and_stage(fs["bucket"], wbpp_session_roots)
        fs["_session_root"] = sr
        fs["_stage"] = stage
        if stage == "master":
            session_master_seen[(sr, fs["filter"])].append(fs["bucket"])

    # Also: if a proper master file (from `masters` list) has its parent
    # within a detected WBPP session, suppress that session's folder_subs.
    for m in masters:
        mp = Path(m["path"])
        mparent = str(mp.parent)
        sr, stage = session_root_and_stage(mparent, wbpp_session_roots)
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
        key = (fs["filter"], round(fs["exptime"], 1), fs.get("_basenames"))
        if not key[2]:
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
    # Add folder_subs as pseudo-members for clustering (tagged with role=folder_sub)
    wcs_folder_subs = [{
        "role": "folder_sub",
        "path": fs["sample_path"],
        "ra_deg": fs["ra_deg"],
        "dec_deg": fs["dec_deg"],
        "naxis1": fs["naxis1"], "naxis2": fs["naxis2"],
        "pix_arcsec": fs["pix_arcsec"],
        "date_obs": fs["date_obs"],
        "object": fs["object"],
        "telescope": fs["telescope"],
        "filter": fs["filter"],
        "exptime": fs["exptime"],
        "ncombine": fs["n_subs"],
        "has_wcs": True,
        "_folder_sub": fs,
    } for fs in folder_subs if fs.get("has_wcs")]
    cluster_members = wcs_masters + wcs_folder_subs
    clusters = cluster_by_coords(cluster_members, radius_arcmin=30.0)
    print(f"[{time.time()-t0:6.1f}s] {len(clusters)} WCS-clustered targets "
          f"(from {len(wcs_masters)} masters + {len(wcs_folder_subs)} sub-folder blocks)")

    # Build target records
    targets = []
    for i, idxs in enumerate(clusters):
        members = [cluster_members[k] for k in idxs]
        ras = np.array([m["ra_deg"] for m in members])
        decs = np.array([m["dec_deg"] for m in members])
        # RA wrap
        if ras.max() - ras.min() > 180:
            ras = np.where(ras < 180, ras + 360, ras)
        ra_c = float(np.median(ras)) % 360.0
        dec_c = float(np.median(decs))
        sc = SkyCoord(ra_c * u.deg, dec_c * u.deg)
        gal = sc.galactic

        # Derive filters & hours from cluster members.
        # Masters contribute NCOMBINE×EXPTIME when available, else EXPTIME.
        # Folder-sub blocks contribute n_subs×exptime (their exptime/ncombine already encode this).
        filters_data = defaultdict(lambda: {
            "total_hours": 0.0, "files": 0, "paths": [],
            "sub_folders": 0, "n_subs": 0, "folder_sub_buckets": [],
        })
        for m in members:
            f = m.get("filter") or "Unknown"
            is_folder_sub = m.get("role") == "folder_sub"
            if not is_folder_sub:
                filters_data[f]["paths"].append(m["path"])
                filters_data[f]["files"] += 1
            else:
                filters_data[f]["sub_folders"] += 1
                filters_data[f]["n_subs"] += m.get("ncombine") or 0
                fs = m.get("_folder_sub") or {}
                filters_data[f]["folder_sub_buckets"].append({
                    "bucket": fs.get("bucket"),
                    "n_subs": fs.get("n_subs"),
                    "exptime": fs.get("exptime"),
                    "hours": round((fs.get("exptime") or 0) * (fs.get("n_subs") or 0) / 3600.0, 2),
                    "stage": fs.get("_stage"),
                    "session_root": fs.get("_session_root"),
                    "sample_path": fs.get("sample_path"),
                    "telescope": fs.get("telescope"),
                })
            if m.get("exptime") and m.get("ncombine"):
                filters_data[f]["total_hours"] += m["exptime"] * m["ncombine"] / 3600.0
            elif m.get("exptime"):
                filters_data[f]["total_hours"] += m["exptime"] / 3600.0

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
