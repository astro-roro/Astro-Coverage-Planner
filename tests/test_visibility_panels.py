"""Tests for POST /api/visibility/panels.

70 lines of mosaic-visibility logic that was 100% untested before
2026-05-17. The panels endpoint is what the planner uses to compute
"how many panels of this mosaic are visible in month X" — a regression
here would silently mis-render the season-bar on every mosaic plan.
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


def _fresh_sites_with_test_site():
    """Redirect SITES_PATH to a tempfile with a known southern-hemi
    site so panels at dec=-30 sit comfortably inside the visible band.
    """
    td = Path(tempfile.mkdtemp())
    app_module.SITES_PATH = td / "sites.json"
    app_module._sites_cache = None
    app_module._sites_cache_mtime = None
    (app_module.SITES_PATH).write_text(json.dumps({
        "version": 1,
        "sites": [{
            "id": "test_south",
            "name": "Test Southern Site",
            "lat": -33.87, "lon": 151.21,
            "elev_m": 50, "min_alt_deg": 30,
        }],
    }), encoding="utf-8")


def _clear_visibility_cache():
    """Wipe the per-(site,target,year) visibility cache between tests
    so cache hits don't mask compute-path bugs."""
    app_module._visibility_cache.clear()


class TestVisibilityPanelsHappyPath(unittest.TestCase):
    def setUp(self):
        _fresh_sites_with_test_site()
        _clear_visibility_cache()
        self.client = app.test_client()

    def test_2x2_mosaic_with_site_id(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={
                "panels": [
                    {"ra_deg": 100.0, "dec_deg": -30.0},
                    {"ra_deg": 100.0, "dec_deg": -30.5},
                    {"ra_deg": 100.5, "dec_deg": -30.0},
                    {"ra_deg": 100.5, "dec_deg": -30.5},
                ],
                "year": 2026,
            },
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["panel_count"], 4)
        self.assertEqual(body["year"], 2026)
        self.assertEqual(len(body["months"]), 12)
        for m in body["months"]:
            self.assertEqual(m["total_panels"], 4)
            self.assertGreaterEqual(m["panels_visible"], 0)
            self.assertLessEqual(m["panels_visible"], 4)
            self.assertIn("month", m)
            self.assertGreaterEqual(m["month"], 1)
            self.assertLessEqual(m["month"], 12)
        self.assertIn("labels", body)
        self.assertEqual(body["site"]["id"], "test_south")

    def test_single_panel_degenerates_to_point_visibility(self):
        # A 1-panel call should behave the same as /api/visibility/point
        # for the same coordinate — every month either visible or not.
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={
                "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                "year": 2026,
            },
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["panel_count"], 1)
        for m in body["months"]:
            self.assertEqual(m["total_panels"], 1)
            self.assertIn(m["panels_visible"], (0, 1))

    def test_year_defaults_to_current_year_when_omitted(self):
        from datetime import datetime, timezone
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": [{"ra_deg": 100.0, "dec_deg": -30.0}]},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["year"], datetime.now(timezone.utc).year)

    def test_explicit_lat_lon_without_site_id(self):
        r = self.client.post(
            "/api/visibility/panels"
            "?lat=-33.87&lon=151.21&min_alt_deg=30",
            json={
                "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                "year": 2026,
            },
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        # No site_id when called via lat/lon path
        self.assertIsNone(body["site"]["id"])


class TestVisibilityPanelsValidation(unittest.TestCase):
    """Every validation branch in the 70-line handler. Each test fires
    BEFORE the heavy astropy compute, so they're all sub-millisecond
    even though the endpoint itself does real astronomy on the happy
    path."""

    def setUp(self):
        _fresh_sites_with_test_site()
        _clear_visibility_cache()
        self.client = app.test_client()

    def test_empty_body_rejects(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            data=b"", content_type="application/json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("panels", r.get_json().get("error", ""))

    def test_missing_panels_key(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"year": 2026},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("panels", r.get_json().get("error", ""))

    def test_empty_panels_list_rejects(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": []},
        )
        self.assertEqual(r.status_code, 400)

    def test_panels_not_a_list_rejects(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": "not a list"},
        )
        self.assertEqual(r.status_code, 400)

    def test_panels_over_cap_rejects(self):
        # Cap is 400 — exceed and expect a clean 400 with a helpful message
        # rather than a slow astropy compute on hundreds of panels.
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": [
                {"ra_deg": 0.1 * i, "dec_deg": -30.0} for i in range(401)
            ]},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("max 400", r.get_json().get("error", ""))

    def test_panel_missing_ra_deg(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": [{"dec_deg": -30.0}]},
        )
        self.assertEqual(r.status_code, 400)
        err = r.get_json().get("error", "")
        self.assertIn("ra_deg", err)

    def test_panel_non_dict(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": ["not a dict"]},
        )
        self.assertEqual(r.status_code, 400)

    def test_panel_ra_out_of_range(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": [{"ra_deg": 400.0, "dec_deg": -30.0}]},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("out of range", r.get_json().get("error", ""))

    def test_panel_dec_out_of_range(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": [{"ra_deg": 100.0, "dec_deg": 91.0}]},
        )
        self.assertEqual(r.status_code, 400)

    def test_panel_dec_negative_out_of_range(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={"panels": [{"ra_deg": 100.0, "dec_deg": -91.0}]},
        )
        self.assertEqual(r.status_code, 400)

    def test_year_below_range(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={
                "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                "year": 1800,
            },
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("year", r.get_json().get("error", ""))

    def test_year_above_range(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={
                "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                "year": 2500,
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_year_non_numeric(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json={
                "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                "year": "twenty twenty six",
            },
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("year", r.get_json().get("error", ""))

    def test_unknown_site_id(self):
        r = self.client.post(
            "/api/visibility/panels?site_id=no-such-site",
            json={"panels": [{"ra_deg": 100.0, "dec_deg": -30.0}]},
        )
        self.assertEqual(r.status_code, 404)
        self.assertIn("site_id", r.get_json().get("error", ""))


class TestVisibilityPanelsCaching(unittest.TestCase):
    """The endpoint memoises per (site, ra, dec, year) — verify the
    second call doesn't recompute (we'd see the call to
    compute_year_visibility hit zero times)."""

    def setUp(self):
        _fresh_sites_with_test_site()
        _clear_visibility_cache()
        self.client = app.test_client()

    def test_second_identical_call_uses_cache(self):
        payload = {
            "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
            "year": 2026,
        }
        # First call: cache miss, computes for real.
        r1 = self.client.post(
            "/api/visibility/panels?site_id=test_south",
            json=payload,
        )
        self.assertEqual(r1.status_code, 200)
        body1 = r1.get_json()

        # Second call: should hit cache → compute_year_visibility never called.
        with mock.patch.object(
            app_module, "compute_year_visibility",
            side_effect=AssertionError("compute_year_visibility called on cache hit"),
        ):
            r2 = self.client.post(
                "/api/visibility/panels?site_id=test_south",
                json=payload,
            )
        self.assertEqual(r2.status_code, 200)
        body2 = r2.get_json()
        # Same bins from cache → same months breakdown.
        self.assertEqual(body1["months"], body2["months"])

    def test_different_year_misses_cache(self):
        # Year differences MUST result in fresh computation — confirm by
        # patching compute_year_visibility to count calls.
        original = app_module.compute_year_visibility
        call_count = {"n": 0}

        def tracking(*args, **kwargs):
            call_count["n"] += 1
            return original(*args, **kwargs)

        with mock.patch.object(app_module, "compute_year_visibility", tracking):
            self.client.post(
                "/api/visibility/panels?site_id=test_south",
                json={
                    "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                    "year": 2026,
                },
            )
            self.client.post(
                "/api/visibility/panels?site_id=test_south",
                json={
                    "panels": [{"ra_deg": 100.0, "dec_deg": -30.0}],
                    "year": 2027,
                },
            )
        self.assertEqual(call_count["n"], 2,
            "different years must both miss cache")


class TestVisibilityPanelsAstropyMissing(unittest.TestCase):
    """If astropy import fails the endpoint should 500 with a friendly
    message, not crash. Patch the import to simulate."""

    def setUp(self):
        _fresh_sites_with_test_site()
        _clear_visibility_cache()
        self.client = app.test_client()

    def test_astropy_unavailable_returns_500(self):
        # Force the inline `import astropy` inside the handler to raise.
        with mock.patch.dict(sys.modules, {"astropy": None}):
            r = self.client.post(
                "/api/visibility/panels?site_id=test_south",
                json={"panels": [{"ra_deg": 100.0, "dec_deg": -30.0}]},
            )
        self.assertEqual(r.status_code, 500)
        self.assertIn("astropy", r.get_json().get("error", "").lower())


if __name__ == "__main__":
    unittest.main()
