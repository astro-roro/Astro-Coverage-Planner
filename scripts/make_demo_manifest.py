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
    targets = [
        target(1, "Eta Carinae",   161.26, -59.68, 287.60, -0.63,
               {"Ha": 3.2, "OIII": 1.4, "SII": 0.3}, "RedCat 51", "ASI2600MM Pro"),
        target(2, "Tarantula Nebula", 84.67, -69.10, 279.46, -31.67,
               {"Ha": 5.1, "OIII": 2.2, "SII": 1.8}, "RedCat 51", "ASI2600MM Pro"),
        target(3, "Rho Ophiuchi",    246.82, -24.39, 353.15, 16.95,
               {"L": 2.0, "R": 1.5, "G": 1.5, "B": 1.5}, "HyperStar", "ASI2600MC Pro"),
        target(4, "Helix Nebula",    337.41, -20.84, 36.16, -57.12,
               {"Ha": 4.2, "OIII": 3.1, "SII": 2.0}, "RedCat 51", "ASI2600MM Pro"),
        target(5, "Running Chicken", 172.00, -62.68, 294.63, 0.04,
               {"Ha": 2.6, "OIII": 0.9, "SII": 0.1}, "RedCat 51", "ASI2600MM Pro"),
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
