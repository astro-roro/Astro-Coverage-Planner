"""One-shot timing benchmark for compute_gap_moc on a 100-target manifest.

Not part of the regression suite — invoked manually during Phase 4a verification.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import app as app_module  # noqa: E402
from gaps import compute_gap_moc  # noqa: E402


def _grid_target(i: int) -> dict:
    """Place targets on a 10x10 grid covering RA 0..100, Dec 0..40 with 1deg squares."""
    row, col = divmod(i, 10)
    ra0 = 5.0 + col * 10.0
    dec0 = 5.0 + row * 4.0
    half = 0.5
    # Half the targets get Ha, all get OIII; some overlap on Ha.
    filters = {"OIII": {"total_hours": 5.0, "files": 10}}
    if i % 2 == 0:
        filters["Ha"] = {"total_hours": 5.0, "files": 10}
    return {
        "target_id": i,
        "objects": [f"T{i}"],
        "corners_icrs": [
            [ra0 - half, dec0 - half],
            [ra0 + half, dec0 - half],
            [ra0 + half, dec0 + half],
            [ra0 - half, dec0 + half],
        ],
        "telescopes": [],
        "filters": filters,
    }


def main() -> None:
    targets = [_grid_target(i) for i in range(100)]
    p = Path(tempfile.mkdtemp()) / "bench_manifest.json"
    p.write_text(json.dumps({"targets": targets}), encoding="utf-8")

    src = app_module.JsonManifestSource(
        source_id="bench", label="bench", color="", attribution="",
        enabled_default=True, path=p, kind="manifest",
    )

    # Cold call (builds + caches per-filter MOC unions internally).
    t0 = time.perf_counter()
    res = compute_gap_moc([src], have_filter="Ha", missing_filter="OIII")
    t_cold = time.perf_counter() - t0

    # Warm call (hits the per-source moc_cache).
    t0 = time.perf_counter()
    res2 = compute_gap_moc([src], have_filter="Ha", missing_filter="OIII")
    t_warm = time.perf_counter() - t0

    print(f"100-target manifest, max_depth=10:")
    print(f"  cold compute_gap_moc: {t_cold*1000:.1f} ms")
    print(f"  warm compute_gap_moc: {t_warm*1000:.1f} ms")
    print(f"  gap sky_fraction: {res.gap_sky_fraction:.3e} (warm: {res2.gap_sky_fraction:.3e})")
    print(f"  threshold: 2000 ms — {'OK' if t_cold < 2.0 else 'SLOW'}")


if __name__ == "__main__":
    main()
