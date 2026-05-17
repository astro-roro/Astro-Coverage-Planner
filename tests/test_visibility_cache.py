"""Tests for /api/visibility* caching behaviour.

The visibility compute is the most expensive operation in the app
(~1-3s per call for a typical manifest). It's cached in a module-level
dict keyed by (site, coord, year, manifest_mtime where applicable).
Cache misses on round-trippable params would silently make the planner
2-3× slower — these tests pin down the cache hit/miss contract.

We patch compute_year_visibility with a Mock so we can count calls
without paying astropy's cost.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _fresh_state():
    """Clear caches AND the manifest path so each test gets a clean slate."""
    td = Path(tempfile.mkdtemp())
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module._manifest_cache = None
    app_module._manifest_cache_mtime = None
    app_module._visibility_cache.clear()
    return td


def _write_manifest(targets=None):
    if targets is None:
        targets = [
            {"target_id": 1, "center_ra_deg": 100.0, "center_dec_deg": -30.0},
            {"target_id": 2, "center_ra_deg": 150.0, "center_dec_deg": -20.0},
        ]
    app_module.MANIFEST_PATH.write_text(
        json.dumps({"targets": targets}), encoding="utf-8"
    )


def _fake_compute(_targets, **_kwargs):
    """Stand-in for compute_year_visibility — returns one 'great' bin
    per month per target id."""
    out: dict = {}
    for t in _targets:
        tid = int(t["target_id"])
        out[tid] = [
            {"month": m, "label": "great",
             "peak_alt_deg": 75.0, "hours_above_min": 5.0}
            for m in range(1, 13)
        ]
    return out


class TestVisibilityPointCache(unittest.TestCase):
    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_second_identical_call_hits_cache(self):
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            r1 = self.client.get("/api/visibility/point?ra=100&dec=-30&year=2026")
            r2 = self.client.get("/api/visibility/point?ra=100&dec=-30&year=2026")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        # Single compute call — second request served from cache.
        self.assertEqual(m.call_count, 1,
            f"expected 1 compute call, got {m.call_count}")

    def test_different_year_misses_cache(self):
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            self.client.get("/api/visibility/point?ra=100&dec=-30&year=2026")
            self.client.get("/api/visibility/point?ra=100&dec=-30&year=2027")
        self.assertEqual(m.call_count, 2)

    def test_different_coords_miss_cache(self):
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            self.client.get("/api/visibility/point?ra=100&dec=-30&year=2026")
            self.client.get("/api/visibility/point?ra=101&dec=-30&year=2026")
            self.client.get("/api/visibility/point?ra=100&dec=-31&year=2026")
        self.assertEqual(m.call_count, 3)

    def test_subdegree_coord_changes_round_to_same_key(self):
        # Cache key rounds to 4dp — so ra=100.00001 and ra=100.00002
        # both round to 100.0000 and should hit the same entry.
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            self.client.get("/api/visibility/point?ra=100.00001&dec=-30&year=2026")
            self.client.get("/api/visibility/point?ra=100.00002&dec=-30&year=2026")
        self.assertEqual(m.call_count, 1,
            "5th-decimal coord differences should round to same cache key")

    def test_different_site_misses_cache(self):
        # Same coord, same year, but different lat → different cache key.
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            self.client.get(
                "/api/visibility/point?ra=100&dec=-30&year=2026&lat=20&lon=-155")
            self.client.get(
                "/api/visibility/point?ra=100&dec=-30&year=2026&lat=-31&lon=149")
        self.assertEqual(m.call_count, 2)


class TestVisibilityPanelsCache(unittest.TestCase):
    """The panels endpoint computes only the misses — verify that mixed
    hit/miss panel lists don't recompute already-cached panels."""

    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_panels_warmup_seeds_point_cache(self):
        # A panels-endpoint call caches each panel individually under
        # the SAME key shape as /point. So a follow-up /point with one
        # of those panels' coords should hit cache.
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            r1 = self.client.post("/api/visibility/panels",
                json={"panels": [
                    {"ra_deg": 100.0, "dec_deg": -30.0},
                    {"ra_deg": 150.0, "dec_deg": -20.0},
                ], "year": 2026})
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(m.call_count, 1)  # one batched compute
            # Now point a /point call at one of those panels.
            r2 = self.client.get(
                "/api/visibility/point?ra=100&dec=-30&year=2026")
            self.assertEqual(r2.status_code, 200)
        # Should NOT have re-computed — the panels call seeded the cache.
        self.assertEqual(m.call_count, 1,
            "/point should hit cache seeded by /panels")

    def test_partial_panel_hit_only_computes_misses(self):
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            # Seed cache with two panels.
            self.client.post("/api/visibility/panels",
                json={"panels": [
                    {"ra_deg": 100.0, "dec_deg": -30.0},
                    {"ra_deg": 150.0, "dec_deg": -20.0},
                ], "year": 2026})
            calls_after_seed = m.call_count
            # Request three panels: two cached, one new.
            r = self.client.post("/api/visibility/panels",
                json={"panels": [
                    {"ra_deg": 100.0, "dec_deg": -30.0},   # cached
                    {"ra_deg": 150.0, "dec_deg": -20.0},   # cached
                    {"ra_deg": 200.0, "dec_deg": -10.0},   # new
                ], "year": 2026})
            self.assertEqual(r.status_code, 200)
        # One extra compute call for the single miss.
        self.assertEqual(m.call_count, calls_after_seed + 1)
        # Compute was called with exactly 1 target (the miss only).
        last_call_targets = m.call_args_list[-1][0][0]
        self.assertEqual(len(last_call_targets), 1)
        self.assertAlmostEqual(last_call_targets[0]["center_ra_deg"], 200.0)

    def test_all_panels_cached_skips_compute_entirely(self):
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            # First call seeds.
            self.client.post("/api/visibility/panels",
                json={"panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                      "year": 2026})
            calls_after_seed = m.call_count
            # Identical second call should NOT compute.
            r = self.client.post("/api/visibility/panels",
                json={"panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                      "year": 2026})
            self.assertEqual(r.status_code, 200)
        self.assertEqual(m.call_count, calls_after_seed,
            "all-hit panel list should not re-trigger compute")


class TestVisibilityManifestCache(unittest.TestCase):
    """/api/visibility (no /point or /panels suffix) keys the cache on
    manifest_mtime — so editing the manifest invalidates the cache."""

    def setUp(self):
        _fresh_state()
        _write_manifest()
        self.client = app.test_client()

    def test_repeat_call_uses_cache(self):
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            self.client.get("/api/visibility?year=2026")
            self.client.get("/api/visibility?year=2026")
        self.assertEqual(m.call_count, 1)

    def test_manifest_edit_invalidates_cache(self):
        import os
        import time
        with mock.patch.object(app_module, "compute_year_visibility",
                                side_effect=_fake_compute) as m:
            self.client.get("/api/visibility?year=2026")
            self.assertEqual(m.call_count, 1)
            # Mutate the manifest + bump mtime so load_manifest reloads.
            time.sleep(0.05)
            _write_manifest(targets=[
                {"target_id": 1, "center_ra_deg": 100.0, "center_dec_deg": -30.0},
                {"target_id": 2, "center_ra_deg": 150.0, "center_dec_deg": -20.0},
                {"target_id": 3, "center_ra_deg": 200.0, "center_dec_deg": -10.0},
            ])
            new_mtime = app_module.MANIFEST_PATH.stat().st_mtime + 1
            os.utime(app_module.MANIFEST_PATH, (new_mtime, new_mtime))
            self.client.get("/api/visibility?year=2026")
        self.assertEqual(m.call_count, 2,
            "manifest mutation should invalidate /api/visibility cache")


class TestVisibilityCacheValidation(unittest.TestCase):
    """The validation paths must come BEFORE the cache lookup, otherwise
    invalid coords would poison the cache with bad keys."""

    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_bad_ra_returns_400_no_cache_entry(self):
        before = len(app_module._visibility_cache)
        r = self.client.get("/api/visibility/point?ra=999&dec=0")
        self.assertEqual(r.status_code, 400)
        after = len(app_module._visibility_cache)
        self.assertEqual(before, after, "400 path must not write cache")

    def test_missing_ra_returns_400_no_cache_entry(self):
        before = len(app_module._visibility_cache)
        r = self.client.get("/api/visibility/point?dec=0")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(before, len(app_module._visibility_cache))

    def test_panels_oversize_list_returns_400_no_cache(self):
        before = len(app_module._visibility_cache)
        panels = [{"ra_deg": i * 0.1, "dec_deg": 0.0} for i in range(401)]
        r = self.client.post("/api/visibility/panels",
            json={"panels": panels, "year": 2026})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(before, len(app_module._visibility_cache))


if __name__ == "__main__":
    unittest.main()
