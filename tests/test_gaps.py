"""Tests for the Phase 4a gap-finder (gaps.compute_gap_moc + candidates_in_moc).

Bare-imperative style matching tests/test_moc_source.py. All offline — synthetic
manifests in tempdirs, synthetic MOCs built in-process.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import app as app_module  # noqa: E402
import gaps as gaps_module  # noqa: E402
from gaps import GapResult, candidates_in_moc, compute_gap_moc  # noqa: E402

from mocpy import MOC  # noqa: E402
import numpy as np  # noqa: E402
from astropy.coordinates import SkyCoord  # noqa: E402
import astropy.units as u  # noqa: E402


def _write_manifest(targets: list[dict]) -> Path:
    """Write a minimal manifest JSON to a fresh tempfile and return its path."""
    p = Path(tempfile.mkdtemp()) / "manifest.json"
    p.write_text(json.dumps({"targets": targets}), encoding="utf-8")
    return p


def _square_target(target_id: int, ra0: float, dec0: float, side: float, filters: dict) -> dict:
    """Build a manifest target with a square ICRS polygon centered at (ra0,dec0)."""
    half = side / 2.0
    return {
        "target_id": target_id,
        "objects": [f"T{target_id}"],
        "corners_icrs": [
            [ra0 - half, dec0 - half],
            [ra0 + half, dec0 - half],
            [ra0 + half, dec0 + half],
            [ra0 - half, dec0 + half],
        ],
        "telescopes": [],
        "filters": filters,
    }


# === 1. Synthetic 2-source overlap: Ha (A) covers a square, SII (B) covers ===
# === a partially-overlapping square. gap = A's square minus the overlap.   ===
ha_path = _write_manifest([
    _square_target(1, ra0=10.0, dec0=10.0, side=2.0,
                   filters={"Ha": {"total_hours": 5.0, "files": 10}}),
])
sii_path = _write_manifest([
    _square_target(2, ra0=11.0, dec0=10.0, side=2.0,
                   filters={"SII": {"total_hours": 3.0, "files": 6}}),
])
src_a = app_module.JsonManifestSource(
    source_id="A", label="A", color="", attribution="", enabled_default=True,
    path=ha_path, kind="manifest",
)
src_b = app_module.JsonManifestSource(
    source_id="B", label="B", color="", attribution="", enabled_default=True,
    path=sii_path, kind="manifest",
)
res = compute_gap_moc([src_a, src_b], have_filter="Ha", missing_filter="SII")
print(f"gap (A Ha minus B SII): sky_fraction={res.gap_sky_fraction:.3e} "
      f"have={res.have_sources} missing={res.missing_sources}")
assert isinstance(res, GapResult)
assert res.gap_moc is not None
assert res.have_sources == ["A"]
assert res.missing_sources == ["B"]
# A's 2x2 deg square is ~4 sq deg ≈ 9.7e-5 of full sky. The SII square overlaps
# the eastern half (~2 sq deg), so the gap should be roughly 2 sq deg, but at
# max_depth=10 the cell discretisation makes the bound loose. Sanity-window:
assert 0.0 < res.gap_sky_fraction < 1.5e-4, res.gap_sky_fraction
# And the gap is strictly smaller than the original Ha union:
ha_union_sf = src_a.coverage_moc("Ha").sky_fraction
assert res.gap_sky_fraction < ha_union_sf, (res.gap_sky_fraction, ha_union_sf)
print("synthetic 2-source overlap OK")


# === 2. Empty have side — both sources only have OIII; ask for Ha ============
oiii1_path = _write_manifest([
    _square_target(3, ra0=20.0, dec0=10.0, side=1.0,
                   filters={"OIII": {"total_hours": 5.0, "files": 10}}),
])
oiii2_path = _write_manifest([
    _square_target(4, ra0=21.0, dec0=10.0, side=1.0,
                   filters={"OIII": {"total_hours": 5.0, "files": 10}}),
])
src_c = app_module.JsonManifestSource(
    source_id="C", label="C", color="", attribution="", enabled_default=True,
    path=oiii1_path, kind="manifest",
)
src_d = app_module.JsonManifestSource(
    source_id="D", label="D", color="", attribution="", enabled_default=True,
    path=oiii2_path, kind="manifest",
)
res2 = compute_gap_moc([src_c, src_d], have_filter="Ha", missing_filter="SII")
print(f"empty-have: gap_moc={res2.gap_moc} skipped={res2.skipped}")
assert res2.gap_moc is None
assert res2.have_sources == []
# Both sources should appear in skipped — neither has Ha or SII.
skipped_ids = {sid for sid, _ in res2.skipped}
assert skipped_ids == {"C", "D"}, skipped_ids
print("empty-have-side OK")


# === 3. MOC source contributes to the have side ==============================
synth_moc = MOC.from_polygon_skycoord(
    SkyCoord([30.0, 31.0, 31.0, 30.0], [5.0, 5.0, 6.0, 6.0],
             unit="deg", frame="icrs"),
    max_depth=10,
)
moc_cache_dir = Path(tempfile.mkdtemp())
moc_fits_path = moc_cache_dir / "synth_moc_src.fits"
synth_moc.save(str(moc_fits_path), format="fits", overwrite=True)
src_moc = app_module.MocCoverageSource(
    source_id="synth_moc_src", label="Synth", color="", attribution="x",
    enabled_default=False,
    moc_url="https://alasky.cds.unistra.fr/test/synth.fits",
    filter_name="Ha",
    cache_dir=moc_cache_dir,
)
# coverage_moc is lazy — file is on disk; no network here.
contributed = src_moc.coverage_moc("Ha")
assert contributed is not None
assert contributed.sky_fraction > 0
# Wrong filter returns None:
assert src_moc.coverage_moc("OIII") is None

# Gap with the MOC source on the have side and a manifest source on the missing
# side that doesn't overlap → entire MOC region is the gap.
no_overlap_path = _write_manifest([
    _square_target(5, ra0=200.0, dec0=-20.0, side=1.0,
                   filters={"SII": {"total_hours": 1.0, "files": 1}}),
])
src_far = app_module.JsonManifestSource(
    source_id="far", label="Far", color="", attribution="", enabled_default=True,
    path=no_overlap_path, kind="manifest",
)
res3 = compute_gap_moc([src_moc, src_far], have_filter="Ha", missing_filter="SII")
print(f"moc-source contribution: have={res3.have_sources} sf={res3.gap_sky_fraction:.3e}")
assert "synth_moc_src" in res3.have_sources
assert res3.gap_moc is not None
# No overlap → gap area equals the synth MOC area (within rounding).
assert abs(res3.gap_sky_fraction - synth_moc.sky_fraction) < 1e-9
print("MOC source contribution OK")


# === 4. Source-id filter — two sources with Ha, restrict to one =============
ha2_path = _write_manifest([
    _square_target(6, ra0=50.0, dec0=10.0, side=2.0,
                   filters={"Ha": {"total_hours": 5.0, "files": 10}}),
])
src_a2 = app_module.JsonManifestSource(
    source_id="A2", label="A2", color="", attribution="", enabled_default=True,
    path=ha_path, kind="manifest",
)
src_b2 = app_module.JsonManifestSource(
    source_id="B2", label="B2", color="", attribution="", enabled_default=True,
    path=ha2_path, kind="manifest",
)
res4 = compute_gap_moc(
    [src_a2, src_b2],
    have_filter="Ha", missing_filter="SII",
    source_ids=["A2"],
)
print(f"source-id filter: have={res4.have_sources}")
assert res4.have_sources == ["A2"]
# B2's Ha union must NOT be contributing — confirm via area: only A2's square.
only_a2_sf = src_a2.coverage_moc("Ha").sky_fraction
assert abs(res4.gap_sky_fraction - only_a2_sf) < 1e-9
print("source-id filter OK")


# === 5. mocpy-missing fallback ===============================================
_orig_avail = gaps_module._MOCPY_AVAILABLE
gaps_module._MOCPY_AVAILABLE = False
try:
    res5 = compute_gap_moc([src_a, src_b], have_filter="Ha", missing_filter="SII")
    print(f"mocpy-missing: gap_moc={res5.gap_moc} skipped={res5.skipped}")
    assert isinstance(res5, GapResult)
    assert res5.gap_moc is None
    assert res5.gap_sky_fraction == 0.0
    assert res5.skipped, "expected at least one skipped entry"
finally:
    gaps_module._MOCPY_AVAILABLE = _orig_avail
print("mocpy-missing fallback OK")


# === 6. candidates_in_moc — three entries: in, out, just-outside ============
small_moc = MOC.from_polygon_skycoord(
    SkyCoord([0.0, 10.0, 10.0, 0.0], [0.0, 0.0, 10.0, 10.0],
             unit="deg", frame="icrs"),
    max_depth=10,
)
catalogs = {
    "test_cat": [
        {"name": "inside", "ra_deg": 5.0, "dec_deg": 5.0},
        {"name": "outside_far", "ra_deg": 100.0, "dec_deg": -50.0},
        {"name": "just_outside", "ra_deg": 11.0, "dec_deg": 5.0},
    ],
}
hits = candidates_in_moc(small_moc, catalogs)
print(f"candidates: {[(h.catalog, h.name) for h in hits]}")
assert len(hits) == 1, hits
assert hits[0].name == "inside"
assert hits[0].catalog == "test_cat"
assert hits[0].ra_deg == 5.0 and hits[0].dec_deg == 5.0
print("candidates_in_moc OK")


print("ALL OK")
