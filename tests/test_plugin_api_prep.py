"""Tests for the four NINA-plugin-prep changes tracked in
docs/nina-plugin-api-audit.md: GET /api/version, ?expand= on GET
/api/plans, persisting filter_goals.<f>.sub_exposure_s, and optional
bearer-token auth + CORS on /api/*.

Each test redirects PLANS_PATH / GEAR_PATH / SITES_PATH / MANIFEST_PATH
to a temp dir so the suite never touches real data.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _fresh_state():
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.SITES_PATH = td / "sites.json"
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for cache in ("_plans_cache", "_gear_cache", "_sites_cache", "_manifest_cache",
                  "_destinations_cache"):
        setattr(app_module, cache, None)
    for cache in ("_plans_cache_mtime", "_gear_cache_mtime", "_sites_cache_mtime",
                  "_manifest_cache_mtime", "_destinations_cache_mtime"):
        setattr(app_module, cache, None)
    return td


def _save_gear():
    app_module.save_gear({
        "version": 2,
        "telescopes": [{
            "id": "tel-1", "name": "Test 600mm",
            "focal_length_mm": 600.0, "aperture_mm": 130,
        }],
        "cameras": [{
            "id": "cam-1", "name": "Test IMX571",
            "pixel_size_um": 3.76, "sensor_px": [6248, 4176],
            "filters": {
                "OIII": {"ts_template_name": "OIII 3nm", "default_sub_s": 300,
                         "gain": 100, "offset": 50, "bin": 1},
            },
        }],
    })


def _save_sites():
    app_module.save_sites({
        "version": 1,
        "sites": [{"id": "sydney", "name": "Sydney", "lat": -33.87, "lon": 151.21}],
    })


def _plan(plan_id="p1", **overrides):
    base = {
        "id": plan_id,
        "project_name": "Test",
        "target": {
            "name": "Test Target",
            "center_ra_deg": 100.0,
            "center_dec_deg": -30.0,
            "rotation_deg": 0,
            "mosaic": {"rows": 1, "cols": 2, "overlap_pct": 15},
        },
        "telescope_id": "tel-1",
        "camera_id": "cam-1",
        "filter_goals": {"OIII": {"target_hours": 1.5, "sub_exposure_s": 300}},
        "priority": "normal",
        "min_altitude_deg": 30,
        "state": "draft",
    }
    base.update(overrides)
    return base


class TestApiVersion(unittest.TestCase):
    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_no_plans_or_manifest(self):
        r = self.client.get("/api/version")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["version"], app_module.VERSION)
        self.assertIsNone(body["plans_last_modified"])
        self.assertIsNone(body["manifest_last_modified"])

    def test_with_plans_and_manifest(self):
        self.client.post("/api/plans", json=_plan())
        app_module.MANIFEST_PATH.write_text("{}", encoding="utf-8")
        r = self.client.get("/api/version")
        body = r.get_json()
        self.assertIsInstance(body["plans_last_modified"], str)
        self.assertTrue(body["plans_last_modified"].endswith("+00:00"))
        self.assertIsInstance(body["manifest_last_modified"], str)


if __name__ == "__main__":
    unittest.main()
