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
- GET /api/observability         altaz for all targets at (lat, lon, time)
- GET /api/export/priority       CSV of gap-mode candidates

Env:
- MANIFEST_PATH   path to archive manifest JSON  (default: ./data/manifest.json)
- CATALOGS_PATH   path to catalogs JSON          (default: ./data/catalogs.json)
- HOST            bind host                      (default: 127.0.0.1)
- PORT            bind port                      (default: 5555)
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from flask import Flask, jsonify, render_template, request

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = Path(os.environ.get("MANIFEST_PATH", REPO_ROOT / "data" / "manifest.json"))
CATALOGS_PATH = Path(os.environ.get("CATALOGS_PATH", REPO_ROOT / "data" / "catalogs.json"))

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

_manifest_cache: dict | None = None
_manifest_cache_mtime: float | None = None
_catalogs_cache: dict | None = None
_catalogs_cache_mtime: float | None = None


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


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5555))
    print(f"Astro Coverage Planner → http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
