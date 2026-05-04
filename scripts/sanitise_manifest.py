#!/usr/bin/env python
"""Strip a coverage manifest down to just the shareable fields.

Removes local file paths, telescope serials/model strings, exact dates, and
anything else that fingerprints the user's machine or imaging history. What's
left is the polygon footprint, aperture-class telescope info, and per-filter
total hours — enough for a friend to overlay your coverage on theirs without
seeing your filesystem.

CLI:
    python scripts/sanitise_manifest.py <input.json> <output.json> [--label "Dave"]

Module API:
    sanitise_dict(manifest, label="") -> dict
    validate_no_paths(obj) -> None  (raises RuntimeError on leak)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any


# --- Path-shaped string detection (final safety net before write) -----------

_LEAK_PATTERNS = (
    re.compile(r"^[A-Za-z]:[\\/]"),                 # C:\ or C:/
    re.compile(r"^/(home|Users|var|tmp|mnt)/"),      # POSIX system dirs
    re.compile(r"\\\\"),                              # UNC \\server\share
    re.compile(r"\.(fits?|xisf|raw|cr2|nef|arw|dng)$", re.IGNORECASE),
)

# Per-target whitelist — anything outside this set is dropped.
_TARGET_KEEP = {
    "target_id", "objects",
    "center_ra_deg", "center_dec_deg", "center_l_deg", "center_b_deg",
    "fov_arcmin", "pix_arcsec",
    "corners_icrs", "corners_galactic",
    "telescopes", "filters",
}


def validate_no_paths(obj: Any, _path: tuple = ()) -> None:
    """Recursively scan for path-shaped strings. Raises RuntimeError on leak."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            validate_no_paths(v, _path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            validate_no_paths(v, _path + (f"[{i}]",))
    elif isinstance(obj, str):
        for pat in _LEAK_PATTERNS:
            if pat.search(obj):
                where = ".".join(_path) or "<root>"
                raise RuntimeError(
                    f"path-shaped value leaked at {where!s}: {obj!r}"
                )


# --- Telescope name → aperture class ----------------------------------------

# Order matters: try mm before inches so "AP110" beats a stray quote elsewhere.
_MM_RE = re.compile(r"(\d{2,4})\s*mm", re.IGNORECASE)
_BARE_MM_RE = re.compile(r"(?<!\d)(\d{2,4})(?!\d)")  # bare number, e.g. "AP110", "190 MakNewt"
_INCH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\"|-?inch|in\b)", re.IGNORECASE)

_REFRACTOR_HINTS = ("refractor", "apo", "redcat", "esprit", "tak ", "fsq",
                    "ap ", "ap1", "askar", "william optics", "tsa", "fcd",
                    "gtx", "petzval", "doublet", "triplet")
_REFLECTOR_HINTS = ("newton", "newt", "rasa", "rc ", "ritchey", "edge",
                    "edgehd", "sct", "schmidt", "cassegrain", "dob")


def _aperture_class(name: str) -> str:
    """Heuristically reduce a telescope name to a generic aperture-class string."""
    if not name:
        return "telescope"
    s = str(name).strip()
    lower = s.lower()

    aperture_mm: int | None = None
    if m := _MM_RE.search(s):
        aperture_mm = int(m.group(1))
    elif m := _INCH_RE.search(s):
        aperture_mm = int(round(float(m.group(1)) * 25.4))
    elif m := _BARE_MM_RE.search(s):
        # Bare digits — only trust if in a plausible amateur-aperture range.
        n = int(m.group(1))
        if 40 <= n <= 1000:
            aperture_mm = n

    if aperture_mm is None:
        return "telescope"

    if any(h in lower for h in _REFRACTOR_HINTS):
        kind = "refractor"
    elif any(h in lower for h in _REFLECTOR_HINTS):
        kind = "reflector" if "newt" in lower or "newton" in lower or "rasa" in lower or "dob" in lower else "telescope"
    else:
        kind = "telescope"
    return f"{aperture_mm}mm {kind}"


# --- Object name sanity -----------------------------------------------------

_SUSPICIOUS_OBJ_RE = re.compile(r"[:\\/]|\.(fits?|xisf)$", re.IGNORECASE)


def _clean_objects(objs: Any) -> list[str]:
    if not isinstance(objs, list):
        return []
    out: list[str] = []
    for o in objs:
        s = str(o) if o is not None else ""
        if not s or _SUSPICIOUS_OBJ_RE.search(s):
            out.append("unknown")
        else:
            out.append(s)
    return out


# --- Per-target rebuild -----------------------------------------------------

def _stable_target_id(orig_id: Any, ra: Any, dec: Any) -> str:
    """Hash the original id + position so the new id is stable but unguessable."""
    payload = f"{orig_id!r}|{ra!r}|{dec!r}".encode("utf-8")
    return "f_" + hashlib.sha256(payload).hexdigest()[:12]


def _clean_filter_entry(entry: Any) -> dict:
    """Keep only total_hours (rounded 0.1h) and files count."""
    if not isinstance(entry, dict):
        return {"total_hours": 0.0, "files": 0}
    hours = entry.get("total_hours", 0.0) or 0.0
    files = entry.get("files", 0) or 0
    try:
        hours = round(float(hours), 1)
    except (TypeError, ValueError):
        hours = 0.0
    try:
        files = int(files)
    except (TypeError, ValueError):
        files = 0
    return {"total_hours": hours, "files": files}


def _clean_target(t: dict) -> dict:
    """Rebuild a single target from the whitelist."""
    out: dict[str, Any] = {}
    out["target_id"] = _stable_target_id(
        t.get("target_id"), t.get("center_ra_deg"), t.get("center_dec_deg")
    )
    out["objects"] = _clean_objects(t.get("objects"))

    for k in ("center_ra_deg", "center_dec_deg", "center_l_deg", "center_b_deg",
              "pix_arcsec"):
        if k in t:
            out[k] = t[k]
    if "fov_arcmin" in t:
        out["fov_arcmin"] = t["fov_arcmin"]
    for k in ("corners_icrs", "corners_galactic"):
        if k in t:
            out[k] = t[k]

    scopes = t.get("telescopes") or []
    if isinstance(scopes, list):
        out["telescopes"] = [_aperture_class(s) for s in scopes]
    else:
        out["telescopes"] = [_aperture_class(str(scopes))]

    filters_in = t.get("filters") or {}
    out["filters"] = {
        str(k): _clean_filter_entry(v) for k, v in filters_in.items()
    } if isinstance(filters_in, dict) else {}

    # Sanity: drop any keys that snuck in but aren't in the keep-set.
    return {k: v for k, v in out.items() if k in _TARGET_KEEP}


# --- Top-level rebuild ------------------------------------------------------

def _truncate_to_month(scan_date: Any) -> str:
    """ISO datetime → first-of-month YYYY-MM-01."""
    if not isinstance(scan_date, str) or len(scan_date) < 7:
        return ""
    return scan_date[:7] + "-01"


def sanitise_dict(manifest: dict, label: str = "") -> dict:
    """Return a sanitised copy of the manifest. Pure function — no I/O."""
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a dict")
    targets_in = manifest.get("targets") or []
    targets_out = [_clean_target(t) for t in targets_in if isinstance(t, dict)]
    out = {
        "sanitised": True,
        "friend_label": str(label or ""),
        "scan_date": _truncate_to_month(manifest.get("scan_date")),
        "total_targets": manifest.get("total_targets", len(targets_out)),
        "total_integration_hours": manifest.get("total_integration_hours", 0.0),
        "targets": targets_out,
    }
    return out


# --- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="Path to source manifest.json")
    ap.add_argument("output", help="Path to write the sanitised manifest")
    ap.add_argument("--label", default="", help="Optional friend label embedded in output")
    args = ap.parse_args(argv)

    with open(args.input, "r", encoding="utf-8") as fh:
        src = json.load(fh)

    out = sanitise_dict(src, label=args.label)
    try:
        validate_no_paths(out)
    except RuntimeError as e:
        print(f"ERROR: sanitiser leak check failed — {e}", file=sys.stderr)
        return 1

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"wrote {args.output} ({len(out['targets'])} targets, sanitised)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
