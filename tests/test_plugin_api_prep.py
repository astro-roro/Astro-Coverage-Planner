"""Tests for the four NINA-plugin-prep changes tracked in
docs/nina-plugin-api-audit.md: GET /api/version, ?expand= on GET
/api/plans, persisting filter_goals.<f>.sub_exposure_s, and optional
bearer-token auth + CORS on /api/*.

Each test redirects PLANS_PATH / GEAR_PATH / SITES_PATH / MANIFEST_PATH
to a temp dir so the suite never touches real data.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class TestPlansExpand(unittest.TestCase):
    def setUp(self):
        _fresh_state()
        _save_gear()
        _save_sites()
        self.client = app.test_client()
        self.client.post("/api/plans", json=_plan())

    def test_no_expand_unchanged(self):
        baseline = self.client.get("/api/plans").get_json()
        r = self.client.get("/api/plans")
        self.assertEqual(r.get_json(), baseline)
        plan = r.get_json()["plans"][0]
        self.assertNotIn("telescope", plan)
        self.assertNotIn("camera", plan)
        self.assertNotIn("site", plan)
        self.assertNotIn("panels", plan)

    def test_expand_gear(self):
        r = self.client.get("/api/plans?expand=gear")
        plan = r.get_json()["plans"][0]
        self.assertEqual(plan["telescope"]["id"], "tel-1")
        self.assertEqual(plan["camera"]["id"], "cam-1")
        self.assertNotIn("site", plan)
        self.assertNotIn("panels", plan)

    def test_expand_site(self):
        r = self.client.get("/api/plans?expand=site")
        plan = r.get_json()["plans"][0]
        self.assertEqual(plan["site"]["id"], "sydney")

    def test_expand_panels(self):
        r = self.client.get("/api/plans?expand=panels")
        plan = r.get_json()["plans"][0]
        self.assertEqual(len(plan["panels"]), 2)  # rows=1, cols=2
        for panel in plan["panels"]:
            self.assertIn("row", panel)
            self.assertIn("col", panel)
            self.assertIn("ra_deg", panel)
            self.assertIn("dec_deg", panel)

    def test_expand_multiple_tokens(self):
        r = self.client.get("/api/plans?expand=gear,site,panels")
        plan = r.get_json()["plans"][0]
        self.assertIn("telescope", plan)
        self.assertIn("camera", plan)
        self.assertIn("site", plan)
        self.assertIn("panels", plan)

    def test_unknown_expand_token_ignored(self):
        r = self.client.get("/api/plans?expand=bogus")
        self.assertEqual(r.status_code, 200)
        plan = r.get_json()["plans"][0]
        self.assertNotIn("telescope", plan)
        self.assertNotIn("site", plan)
        self.assertNotIn("panels", plan)

    def test_last_modified_header_present(self):
        r = self.client.get("/api/plans")
        self.assertIn("Last-Modified", r.headers)


class TestSubExposurePersists(unittest.TestCase):
    """docs/nina-plugin-api-audit.md found 0/31 real plans had
    sub_exposure_s stored. The validator already accepts it; these
    confirm the save path actually keeps it, both via POST (create)
    and PUT (update)."""

    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_round_trips_through_post_and_get(self):
        r = self.client.post("/api/plans", json=_plan(filter_goals={
            "OIII": {"target_hours": 2.0, "sub_exposure_s": 180},
        }))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["filter_goals"]["OIII"]["sub_exposure_s"], 180)

        r2 = self.client.get("/api/plans/p1")
        self.assertEqual(r2.get_json()["filter_goals"]["OIII"]["sub_exposure_s"], 180)

        r3 = self.client.get("/api/plans")
        plan = next(p for p in r3.get_json()["plans"] if p["id"] == "p1")
        self.assertEqual(plan["filter_goals"]["OIII"]["sub_exposure_s"], 180)

    def test_round_trips_through_put(self):
        self.client.post("/api/plans", json=_plan())
        r = self.client.put("/api/plans/p1", json=_plan(filter_goals={
            "OIII": {"target_hours": 2.0, "sub_exposure_s": 240},
        }))
        self.assertEqual(r.get_json()["filter_goals"]["OIII"]["sub_exposure_s"], 240)


class TestApiAuth(unittest.TestCase):
    """ACP_API_TOKEN unset keeps today's loopback behaviour (every request
    passes); setting it gates /api/* only, leaving the HTML page and
    static files reachable so a stock install doesn't change shape."""

    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_auth_off_when_env_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ACP_API_TOKEN", None)
            r = self.client.get("/api/version")
            self.assertEqual(r.status_code, 200)

    def test_missing_token_rejected_when_set(self):
        with mock.patch.dict(os.environ, {"ACP_API_TOKEN": "secret123"}):
            r = self.client.get("/api/version")
            self.assertEqual(r.status_code, 401)
            self.assertEqual(r.get_json(), {"error": "unauthorized"})

    def test_bad_token_rejected(self):
        with mock.patch.dict(os.environ, {"ACP_API_TOKEN": "secret123"}):
            r = self.client.get("/api/version", headers={"Authorization": "Bearer wrong"})
            self.assertEqual(r.status_code, 401)

    def test_correct_token_accepted(self):
        with mock.patch.dict(os.environ, {"ACP_API_TOKEN": "secret123"}):
            r = self.client.get("/api/version", headers={"Authorization": "Bearer secret123"})
            self.assertEqual(r.status_code, 200)

    def test_index_not_gated(self):
        with mock.patch.dict(os.environ, {"ACP_API_TOKEN": "secret123"}):
            r = self.client.get("/")
            self.assertEqual(r.status_code, 200)

    def test_static_not_gated(self):
        with mock.patch.dict(os.environ, {"ACP_API_TOKEN": "secret123"}):
            r = self.client.get("/static/app.js")
            self.assertEqual(r.status_code, 200)


class TestApiCors(unittest.TestCase):
    """These three used to assert that the API sent a wildcard CORS header and
    answered a preflight. Both were removed on 2026-09-05, because granting a
    preflight for PUT and DELETE lets any page the user visits write to their
    ACP, which runs on the same machine as their browser. The assertions are
    inverted rather than deleted so the change is visible to anyone reading the
    history. The wider case lives in test_no_cross_origin_writes.py."""

    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_no_cors_header_on_api_get(self):
        r = self.client.get("/api/version")
        self.assertIsNone(r.headers.get("Access-Control-Allow-Origin"))

    def test_cors_headers_absent_on_index(self):
        r = self.client.get("/")
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    def test_preflight_grants_nothing(self):
        r = self.client.options("/api/plans", headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "DELETE",
        })
        self.assertIsNone(r.headers.get("Access-Control-Allow-Origin"))
        self.assertIsNone(r.headers.get("Access-Control-Allow-Methods"))


if __name__ == "__main__":
    unittest.main()
