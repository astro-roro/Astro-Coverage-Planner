#!/usr/bin/env python
"""Fetch SNR / HII catalogs for the coverage webapp (Phase 3 overlays).

Writes webapp/.cache/catalogs.json with the structure expected by the
frontend:

    {
      "green_snrs":       [ {name, ra_deg, dec_deg, l_deg, b_deg, type?}, ... ],
      "anderson_hii":     [ {name, ra_deg, dec_deg, l_deg, b_deg, type}, ... ],
      "smgps_candidates": [ ... ],
      "emu_candidates":   [ ... ],
    }

Sources:
- Green 2019 SNR catalogue via Vizier ``VII/284`` (294 Galactic SNRs).
- Anderson 2014 WISE HII catalogue via Vizier ``J/ApJS/212/1`` (8,399 entries).
- SMGPS / EMU candidates: best-effort Vizier lookup — if not yet published on
  Vizier, we leave the bucket empty and log a note. Phase 3 can drop a manual
  CSV into webapp/static/data/ later.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
import astropy.units as u

import os

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = Path(os.environ.get("CATALOGS_PATH", REPO_ROOT / "data" / "catalogs.json"))
CACHE_DIR = CACHE_PATH.parent


def with_gal(entry: dict) -> dict:
    if entry.get("ra_deg") is None or entry.get("dec_deg") is None:
        return entry
    sc = SkyCoord(entry["ra_deg"] * u.deg, entry["dec_deg"] * u.deg).galactic
    entry["l_deg"] = float(sc.l.deg)
    entry["b_deg"] = float(sc.b.deg)
    return entry


def fetch_green_2019() -> list[dict]:
    from astroquery.vizier import Vizier
    v = Vizier(columns=["*"], row_limit=-1, timeout=60)
    print("  fetching Green 2019 VII/284 ...", flush=True)
    res = v.get_catalogs("VII/284")
    if not res:
        return []
    t = res[0]
    out = []
    for row in t:
        try:
            # RA/Dec are sexagesimal strings in VII/284
            ra_str = str(row["RAJ2000"]).strip()
            dec_str = str(row["DEJ2000"]).strip()
            sc = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg))
            name = str(row["SNR"]).strip()
            if not name.startswith("G"):
                name = "G" + name
            entry = {
                "name": name,
                "ra_deg": float(sc.ra.deg),
                "dec_deg": float(sc.dec.deg),
                "type": str(row["type"]).strip() if "type" in t.colnames else "",
            }
            # Try to pull size (major diameter) for rendering circle markers
            for sz in ("MajDiam", "Dmaj", "Size"):
                if sz in t.colnames:
                    try:
                        entry["diameter_arcmin"] = float(row[sz])
                    except Exception:
                        pass
                    break
            out.append(with_gal(entry))
        except Exception:
            continue
    return out


def fetch_anderson_hii() -> list[dict]:
    from astroquery.vizier import Vizier
    v = Vizier(columns=["*"], row_limit=-1, timeout=120)
    print("  fetching Anderson 2014 J/ApJS/212/1 ...", flush=True)
    res = v.get_catalogs("J/ApJS/212/1")
    if not res:
        return []
    t = res[0]
    out = []
    for row in t:
        try:
            name = str(row["WISE"]).strip() if "WISE" in t.colnames else (
                str(row["Name"]).strip() if "Name" in t.colnames else ""
            )
            # Use GLON/GLAT and convert to ICRS if _RA.icrs not present
            if "_RA.icrs" in t.colnames and "_DE.icrs" in t.colnames:
                ra = float(row["_RA.icrs"]); dec = float(row["_DE.icrs"])
            else:
                l = float(row["GLON"]); b = float(row["GLAT"])
                sc = SkyCoord(l=l * u.deg, b=b * u.deg, frame="galactic").icrs
                ra, dec = float(sc.ra.deg), float(sc.dec.deg)
            cls = str(row["Cl"]).strip() if "Cl" in t.colnames else ""
            entry = {"name": name, "ra_deg": ra, "dec_deg": dec, "type": cls}
            if "Rad" in t.colnames:
                try: entry["radius_arcmin"] = float(row["Rad"])
                except Exception: pass
            out.append(with_gal(entry))
        except Exception:
            continue
    return out


def fetch_optional(cat_id: str, name_key: str, timeout: int = 90) -> list[dict]:
    """Best-effort fetch for catalogs that may or may not be on Vizier."""
    from astroquery.vizier import Vizier
    v = Vizier(columns=["*"], row_limit=-1, timeout=timeout)
    try:
        res = v.get_catalogs(cat_id)
    except Exception as e:
        print(f"  {cat_id} unavailable: {e}", flush=True)
        return []
    if not res:
        return []
    t = res[0]
    print(f"  {cat_id}: {len(t)} rows, cols={list(t.colnames)[:8]}", flush=True)
    out = []
    cols = {c.lower(): c for c in t.colnames}
    name_col = cols.get(name_key.lower()) or list(t.colnames)[0]
    ra_col = cols.get("raj2000") or cols.get("ra_icrs") or cols.get("_ra.icrs") or cols.get("ra")
    dec_col = cols.get("dej2000") or cols.get("de_icrs") or cols.get("_de.icrs") or cols.get("dec") or cols.get("de")
    l_col = cols.get("glon")
    b_col = cols.get("glat")
    for row in t:
        try:
            nm = str(row[name_col]).strip()
            if ra_col and dec_col:
                ra_v = row[ra_col]; dec_v = row[dec_col]
                if isinstance(ra_v, (bytes, str)):
                    sc = SkyCoord(str(ra_v), str(dec_v), unit=(u.hourangle, u.deg))
                    ra, dec = float(sc.ra.deg), float(sc.dec.deg)
                else:
                    ra, dec = float(ra_v), float(dec_v)
            elif l_col and b_col:
                sc = SkyCoord(l=float(row[l_col]) * u.deg, b=float(row[b_col]) * u.deg, frame="galactic").icrs
                ra, dec = float(sc.ra.deg), float(sc.dec.deg)
            else:
                continue
            entry = with_gal({"name": nm, "ra_deg": ra, "dec_deg": dec})
            out.append(entry)
        except Exception:
            continue
    return out


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching catalogs for Phase 3 overlays...", flush=True)
    t0 = time.time()

    cats = {}

    try:
        cats["green_snrs"] = fetch_green_2019()
        print(f"  green_snrs: {len(cats['green_snrs'])}", flush=True)
    except Exception as e:
        print(f"  green_snrs FAILED: {e}")
        cats["green_snrs"] = []

    try:
        cats["anderson_hii"] = fetch_anderson_hii()
        print(f"  anderson_hii: {len(cats['anderson_hii'])}", flush=True)
    except Exception as e:
        print(f"  anderson_hii FAILED: {e}")
        cats["anderson_hii"] = []

    # SMGPS Anderson et al. 2024 — published in A&A / MNRAS. Try a few plausible IDs;
    # if none work, we emit an empty list and the user can drop a manual CSV later.
    smgps = []
    for cid in ("J/MNRAS/530/4928", "J/A+A/680/A92", "J/A+A/681/A123", "J/A+A/688/A110"):
        try:
            found = fetch_optional(cid, "Name")
            if found:
                smgps = found
                print(f"  smgps_candidates: matched {cid} ({len(found)})", flush=True)
                break
        except Exception:
            pass
    cats["smgps_candidates"] = smgps

    # EMU Ball et al. 2025 — the paper has a MNRAS ID. Try plausible IDs.
    emu = []
    for cid in ("J/MNRAS/518/1273", "J/MNRAS/535/4250", "J/PASA/42/e005", "J/MNRAS/500/2493"):
        try:
            found = fetch_optional(cid, "Name")
            if found:
                emu = found
                print(f"  emu_candidates: matched {cid} ({len(found)})", flush=True)
                break
        except Exception:
            pass
    cats["emu_candidates"] = emu

    CACHE_PATH.write_text(json.dumps(cats, indent=1), encoding="utf-8")
    total = sum(len(v) for v in cats.values())
    print(f"\nWrote {CACHE_PATH} — {total} entries total in {time.time()-t0:.1f}s")
    for k, v in cats.items():
        print(f"  {k:24s} {len(v):6d}")


if __name__ == "__main__":
    main()
