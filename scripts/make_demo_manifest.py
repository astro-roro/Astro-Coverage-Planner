#!/usr/bin/env python
"""Generate a tiny demo manifest so the webapp has something to render out of
the box. Writes data/manifest.json with a handful of well-known targets.

For real use, replace data/manifest.json with a manifest describing YOUR
archive — see README.md for the schema.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("MANIFEST_PATH", REPO_ROOT / "data" / "manifest.json"))


def fov_corners(ra: float, dec: float, w_arcmin: float, h_arcmin: float) -> list[list[float]]:
    dra = (w_arcmin / 60.0) / 2.0
    ddec = (h_arcmin / 60.0) / 2.0
    return [
        [ra - dra, dec - ddec],
        [ra - dra, dec + ddec],
        [ra + dra, dec + ddec],
        [ra + dra, dec - ddec],
    ]


def target(tid: int, name: str, ra: float, dec: float, l_deg: float, b_deg: float,
           filters: dict, telescope: str, camera: str, fov_arcmin=(120.0, 90.0)) -> dict:
    return {
        "target_id": tid,
        "objects": [name],
        "center_ra_deg": ra,
        "center_dec_deg": dec,
        "center_l_deg": l_deg,
        "center_b_deg": b_deg,
        "fov_arcmin": list(fov_arcmin),
        "pix_arcsec": 1.5,
        "corners_icrs": fov_corners(ra, dec, *fov_arcmin),
        "corners_galactic": fov_corners(l_deg, b_deg, *fov_arcmin),
        "telescopes": [telescope],
        "cameras": [camera],
        "date_range": ["2025-01-01", "2025-12-31"],
        "filters": {f: {"total_hours": h, "files": max(1, int(h * 4))} for f, h in filters.items()},
        "master_files": [f"/demo/masters/{name.replace(' ', '_')}_master.fit"],
    }


def main() -> None:
    # Five well-known targets spanning both hemispheres, with three
    # rig combos so the legend shows visual variety. All telescope and
    # camera labels are deliberately generic (no brand names) — this is
    # a "look how the viewer works" demo, not "look at the author's gear".
    targets = [
        target(1, "Orion Nebula (M 42)",       83.82,  -5.39, 209.01, -19.38,
               {"Ha": 3.4, "OIII": 1.8, "SII": 0.6}, "Wide-field refractor", "Mono CMOS"),
        target(2, "Andromeda Galaxy (M 31)",   10.68,  41.27, 121.17, -21.57,
               {"L": 4.1, "R": 2.0, "G": 2.0, "B": 2.0}, "Wide-field refractor", "Colour CMOS",
               fov_arcmin=(180.0, 135.0)),
        target(3, "Eta Carinae Nebula",       161.26, -59.68, 287.60,  -0.63,
               {"Ha": 5.6, "OIII": 2.4, "SII": 1.9}, "Wide-field refractor", "Mono CMOS"),
        target(4, "Pleiades (M 45)",           56.85,  24.12, 166.57, -23.51,
               {"L": 2.2, "R": 1.4, "G": 1.4, "B": 1.4}, "Wide-field refractor", "Colour CMOS"),
        target(5, "Lagoon Nebula (M 8)",      270.92, -24.39,   6.02,  -1.18,
               {"Ha": 4.8, "OIII": 0.4, "L": 1.5}, "8-inch SCT", "Mono CMOS",
               fov_arcmin=(50.0, 38.0)),
    ]
    total_hours = round(sum(
        d["total_hours"] for t in targets for d in t["filters"].values()
    ), 1)
    manifest = {
        "scan_date": datetime.now(timezone.utc).isoformat(),
        "total_targets": len(targets),
        "total_integration_hours": total_hours,
        "targets": targets,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {OUT} ({len(targets)} targets, {total_hours}h)")


if __name__ == "__main__":
    main()
