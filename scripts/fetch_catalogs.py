#!/usr/bin/env python
"""Fetch SNR / HII catalogs for the coverage webapp (Phase 3 overlays).

Writes webapp/.cache/catalogs.json with the structure expected by the
frontend:

    {
      "green_snrs":       [ {name, ra_deg, dec_deg, l_deg, b_deg, type?}, ... ],
      "anderson_hii":     [ {name, ra_deg, dec_deg, l_deg, b_deg, type}, ... ],
      "smgps_candidates": [ ... ],
    }

Sources:
- Green 2019 SNR catalogue via Vizier ``VII/284`` (294 Galactic SNRs).
- Anderson 2014 WISE HII catalogue via Vizier ``J/ApJS/212/1`` (8,399 entries).
- SMGPS candidates: best-effort Vizier lookup — if not yet published on Vizier
  we leave the bucket empty and log a note.
- Messier objects (110): hardcoded J2000 — small, stable, avoids network dep.
- Sharpless 2 HII via Vizier ``VII/20`` (313 entries, galactic coords).
- Strasbourg-ESO PNe via Vizier ``V/84`` table 0 (1,143 entries; covers Abell
  PNe as a subset — entries like "A 66" appear in the Name column).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

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


def fetch_messier() -> list[dict]:
    """110 Messier objects with J2000 ICRS coords. Hardcoded — beats a network
    call for a list this small that hasn't changed in 240 years."""
    # (M number, RA hours, RA min, RA sec, Dec sign, Dec deg, Dec min, type)
    rows = [
        (1, 5, 34, 31.94, "+", 22, 0.85, "SNR"), (2, 21, 33, 27.02, "-", 0, 49.39, "GC"),
        (3, 13, 42, 11.62, "+", 28, 22.62, "GC"), (4, 16, 23, 35.22, "-", 26, 31.55, "GC"),
        (5, 15, 18, 33.22, "+", 2, 4.85, "GC"), (6, 17, 40, 20.0, "-", 32, 15.2, "OC"),
        (7, 17, 53, 51.0, "-", 34, 47.6, "OC"), (8, 18, 3, 41.0, "-", 24, 22.8, "Neb"),
        (9, 17, 19, 11.78, "-", 18, 30.99, "GC"), (10, 16, 57, 8.92, "-", 4, 5.97, "GC"),
        (11, 18, 51, 5.0, "-", 6, 16.2, "OC"), (12, 16, 47, 14.18, "-", 1, 56.87, "GC"),
        (13, 16, 41, 41.24, "+", 36, 27.6, "GC"), (14, 17, 37, 36.15, "-", 3, 14.75, "GC"),
        (15, 21, 29, 58.33, "+", 12, 10.43, "GC"), (16, 18, 18, 48.0, "-", 13, 49.0, "OC"),
        (17, 18, 20, 47.0, "-", 16, 10.3, "Neb"), (18, 18, 19, 58.0, "-", 17, 6.0, "OC"),
        (19, 17, 2, 37.69, "-", 26, 16.05, "GC"), (20, 18, 2, 23.0, "-", 23, 1.8, "Neb"),
        (21, 18, 4, 13.0, "-", 22, 29.5, "OC"), (22, 18, 36, 23.94, "-", 23, 54.28, "GC"),
        (23, 17, 56, 51.0, "-", 19, 1.1, "OC"), (24, 18, 16, 30.0, "-", 18, 50.0, "MW"),
        (25, 18, 31, 47.0, "-", 19, 6.7, "OC"), (26, 18, 45, 18.0, "-", 9, 23.0, "OC"),
        (27, 19, 59, 36.34, "+", 22, 43.27, "PN"), (28, 18, 24, 32.89, "-", 24, 52.19, "GC"),
        (29, 20, 23, 56.0, "+", 38, 31.4, "OC"), (30, 21, 40, 22.12, "-", 23, 10.8, "GC"),
        (31, 0, 42, 44.3, "+", 41, 16.15, "Gal"), (32, 0, 42, 41.83, "+", 40, 51.92, "Gal"),
        (33, 1, 33, 50.89, "+", 30, 39.62, "Gal"), (34, 2, 42, 5.0, "+", 42, 45.7, "OC"),
        (35, 6, 9, 0.0, "+", 24, 21.0, "OC"), (36, 5, 36, 18.0, "+", 34, 8.4, "OC"),
        (37, 5, 52, 18.0, "+", 32, 33.2, "OC"), (38, 5, 28, 42.0, "+", 35, 51.3, "OC"),
        (39, 21, 31, 48.0, "+", 48, 26.0, "OC"), (40, 12, 22, 12.5, "+", 58, 4.97, "Dbl"),
        (41, 6, 46, 0.0, "-", 20, 45.4, "OC"), (42, 5, 35, 17.3, "-", 5, 23.45, "Neb"),
        (43, 5, 35, 31.0, "-", 5, 16.17, "Neb"), (44, 8, 40, 24.0, "+", 19, 40.0, "OC"),
        (45, 3, 47, 0.0, "+", 24, 7.0, "OC"), (46, 7, 41, 46.8, "-", 14, 48.6, "OC"),
        (47, 7, 36, 35.0, "-", 14, 28.7, "OC"), (48, 8, 13, 43.0, "-", 5, 45.0, "OC"),
        (49, 12, 29, 46.7, "+", 8, 0.02, "Gal"), (50, 7, 3, 12.0, "-", 8, 20.0, "OC"),
        (51, 13, 29, 52.7, "+", 47, 11.72, "Gal"), (52, 23, 24, 48.0, "+", 61, 35.6, "OC"),
        (53, 13, 12, 55.25, "+", 18, 10.18, "GC"), (54, 18, 55, 3.33, "-", 30, 28.78, "GC"),
        (55, 19, 39, 59.71, "-", 30, 57.86, "GC"), (56, 19, 16, 35.57, "+", 30, 11.06, "GC"),
        (57, 18, 53, 35.08, "+", 33, 1.75, "PN"), (58, 12, 37, 43.5, "+", 11, 49.05, "Gal"),
        (59, 12, 42, 2.3, "+", 11, 38.79, "Gal"), (60, 12, 43, 39.6, "+", 11, 33.16, "Gal"),
        (61, 12, 21, 54.9, "+", 4, 28.42, "Gal"), (62, 17, 1, 12.6, "-", 30, 6.7, "GC"),
        (63, 13, 15, 49.3, "+", 42, 1.78, "Gal"), (64, 12, 56, 43.7, "+", 21, 40.95, "Gal"),
        (65, 11, 18, 55.9, "+", 13, 5.55, "Gal"), (66, 11, 20, 15.0, "+", 12, 59.49, "Gal"),
        (67, 8, 51, 18.0, "+", 11, 48.0, "OC"), (68, 12, 39, 28.01, "-", 26, 44.63, "GC"),
        (69, 18, 31, 23.23, "-", 32, 20.88, "GC"), (70, 18, 43, 12.76, "-", 32, 17.53, "GC"),
        (71, 19, 53, 46.49, "+", 18, 46.78, "GC"), (72, 20, 53, 27.7, "-", 12, 32.23, "GC"),
        (73, 20, 58, 56.0, "-", 12, 38.0, "Ast"), (74, 1, 36, 41.75, "+", 15, 47.02, "Gal"),
        (75, 20, 6, 4.85, "-", 21, 55.28, "GC"), (76, 1, 42, 19.94, "+", 51, 34.52, "PN"),
        (77, 2, 42, 40.71, "-", 0, 0.8, "Gal"), (78, 5, 46, 46.7, "+", 0, 0.07, "Neb"),
        (79, 5, 24, 10.59, "-", 24, 31.45, "GC"), (80, 16, 17, 2.41, "-", 22, 58.55, "GC"),
        (81, 9, 55, 33.2, "+", 69, 3.92, "Gal"), (82, 9, 55, 52.2, "+", 69, 40.78, "Gal"),
        (83, 13, 37, 0.92, "-", 29, 51.93, "Gal"), (84, 12, 25, 3.7, "+", 12, 53.21, "Gal"),
        (85, 12, 25, 24.1, "+", 18, 11.45, "Gal"), (86, 12, 26, 11.7, "+", 12, 56.75, "Gal"),
        (87, 12, 30, 49.42, "+", 12, 23.46, "Gal"), (88, 12, 31, 59.2, "+", 14, 25.23, "Gal"),
        (89, 12, 35, 39.8, "+", 12, 33.39, "Gal"), (90, 12, 36, 49.8, "+", 13, 9.79, "Gal"),
        (91, 12, 35, 26.4, "+", 14, 29.81, "Gal"), (92, 17, 17, 7.39, "+", 43, 8.18, "GC"),
        (93, 7, 44, 30.0, "-", 23, 51.4, "OC"), (94, 12, 50, 53.06, "+", 41, 7.22, "Gal"),
        (95, 10, 43, 57.7, "+", 11, 42.23, "Gal"), (96, 10, 46, 45.7, "+", 11, 49.21, "Gal"),
        (97, 11, 14, 47.7, "+", 55, 1.05, "PN"), (98, 12, 13, 48.3, "+", 14, 54.02, "Gal"),
        (99, 12, 18, 49.6, "+", 14, 24.99, "Gal"), (100, 12, 22, 54.9, "+", 15, 49.34, "Gal"),
        (101, 14, 3, 12.6, "+", 54, 20.95, "Gal"), (102, 15, 6, 29.5, "+", 55, 45.8, "Gal"),
        (103, 1, 33, 23.0, "+", 60, 39.0, "OC"), (104, 12, 39, 59.4, "-", 11, 37.37, "Gal"),
        (105, 10, 47, 49.6, "+", 12, 34.91, "Gal"), (106, 12, 18, 57.5, "+", 47, 18.24, "Gal"),
        (107, 16, 32, 31.86, "-", 13, 3.22, "GC"), (108, 11, 11, 31.0, "+", 55, 40.45, "Gal"),
        (109, 11, 57, 36.0, "+", 53, 22.47, "Gal"), (110, 0, 40, 22.05, "+", 41, 41.12, "Gal"),
    ]
    out = []
    for m, rh, rm, rs, ds, dd, dm, kind in rows:
        ra = (rh + rm / 60 + rs / 3600) * 15.0
        dec = dd + dm / 60
        if ds == "-":
            dec = -dec
        out.append(with_gal({"name": f"M{m}", "ra_deg": ra, "dec_deg": dec, "type": kind}))
    return out


def fetch_sharpless() -> list[dict]:
    """Sharpless 2 HII regions via VizieR VII/20. Has GLon/GLat (galactic frame
    is equinox-independent) so we convert straight to ICRS — bypasses B1900."""
    from astroquery.vizier import Vizier
    v = Vizier(columns=["*"], row_limit=-1, timeout=120)
    print("  fetching Sharpless 2 VII/20 ...", flush=True)
    res = v.get_catalogs("VII/20")
    if not res:
        return []
    t = res[0]
    out = []
    for row in t:
        try:
            name = f"Sh2-{int(row['Sh2'])}"
            l = float(row["GLon"])
            b = float(row["GLat"])
            sc = SkyCoord(l=l * u.deg, b=b * u.deg, frame="galactic").icrs
            entry = {
                "name": name,
                "ra_deg": float(sc.ra.deg),
                "dec_deg": float(sc.dec.deg),
                "type": "HII",
            }
            if "Diam" in t.colnames:
                try: entry["diameter_arcmin"] = float(row["Diam"])
                except Exception: pass
            out.append(with_gal(entry))
        except Exception:
            continue
    return out


def fetch_eso_pne() -> list[dict]:
    """Strasbourg-ESO PNe main catalogue (V/84 table 0). Includes Abell PNe as
    a subset — entries like "A 66" appear in the Name column. _RA.icrs and
    _DE.icrs come back as sexagesimal STRINGS (e.g. "18 13 18.03",
    "-32 19 43.0"), not floats — VizieR auto-precesses from the B1950
    originals but emits HMS/DMS, not decimal degrees."""
    from astroquery.vizier import Vizier
    from astropy.coordinates import Angle
    import astropy.units as u
    v = Vizier(columns=["*"], row_limit=-1, timeout=120)
    print("  fetching Strasbourg-ESO PNe V/84 ...", flush=True)
    res = v.get_catalogs("V/84")
    if not res:
        return []
    t = res[0]
    if "_RA.icrs" not in t.colnames or "_DE.icrs" not in t.colnames:
        print("  V/84: missing _RA.icrs / _DE.icrs columns; skipping", flush=True)
        return []
    out = []
    parse_failures = 0
    for row in t:
        try:
            name = str(row["Name"]).strip() or f"PNG {row['PNG']}"
            ra = Angle(str(row["_RA.icrs"]).strip(), unit=u.hour).degree
            dec = Angle(str(row["_DE.icrs"]).strip(), unit=u.deg).degree
            entry = {"name": name, "ra_deg": ra, "dec_deg": dec, "type": "PN"}
            if "PNG" in t.colnames:
                entry["png"] = str(row["PNG"]).strip()
            out.append(with_gal(entry))
        except Exception:
            parse_failures += 1
            continue
    if parse_failures:
        print(f"  V/84: {parse_failures} rows failed to parse (out of {len(t)})", flush=True)
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

    # EMU Ball et al. SNR candidates were tried here historically but the
    # catalogue isn't mirrored in VizieR under any paper-ID we could find
    # (six candidates probed, none matched). If a future publication lands
    # on VizieR, restore the loop pattern used for SMGPS above and re-add
    # the registry entry in data/catalog_registry.json.

    # Each in its own try so one VizieR hiccup doesn't take out the others.
    try:
        cats["messier"] = fetch_messier()
        print(f"  messier: {len(cats['messier'])}", flush=True)
    except Exception as e:
        print(f"  messier FAILED: {e}")
        cats["messier"] = []

    try:
        cats["sharpless"] = fetch_sharpless()
        print(f"  sharpless: {len(cats['sharpless'])}", flush=True)
    except Exception as e:
        print(f"  sharpless FAILED: {e}")
        cats["sharpless"] = []

    try:
        cats["eso_pne"] = fetch_eso_pne()
        print(f"  eso_pne: {len(cats['eso_pne'])}", flush=True)
    except Exception as e:
        print(f"  eso_pne FAILED: {e}")
        cats["eso_pne"] = []

    CACHE_PATH.write_text(json.dumps(cats, indent=1), encoding="utf-8")
    total = sum(len(v) for v in cats.values())
    print(f"\nWrote {CACHE_PATH} — {total} entries total in {time.time()-t0:.1f}s")
    for k, v in cats.items():
        print(f"  {k:24s} {len(v):6d}")


if __name__ == "__main__":
    main()
