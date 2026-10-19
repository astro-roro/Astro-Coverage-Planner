"""Tests for POST /api/visibility/panels/batch.

The planner used to send one /api/visibility/panels request per plan. The
batch route takes every plan's panels in one request and must return, for each
plan, exactly what the single route returns for those panels.
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

SITE_QS = "?site_id=test_south"

PLANS = [
    {"id": "p1", "panels": [{"ra_deg": 100.0, "dec_deg": -30.0},
                            {"ra_deg": 100.0, "dec_deg": -30.5}]},
    {"id": "p2", "panels": [{"ra_deg": 200.0, "dec_deg": -20.0}]},
    # Shares a panel centre with p1, to prove a repeated point is fine.
    {"id": "p3", "panels": [{"ra_deg": 100.0, "dec_deg": -30.0},
                            {"ra_deg": 10.0, "dec_deg": 40.0}]},
]


def _fresh_site():
    td = Path(tempfile.mkdtemp())
    app_module.SITES_PATH = td / "sites.json"
    app_module._sites_cache = None
    app_module._sites_cache_mtime = None
    app_module.SITES_PATH.write_text(json.dumps({
        "version": 1,
        "sites": [{"id": "test_south", "name": "Test", "lat": -33.87,
                   "lon": 151.21, "elev_m": 50, "min_alt_deg": 30}],
    }), encoding="utf-8")


class BatchCase(unittest.TestCase):
    def setUp(self):
        _fresh_site()
        app_module._visibility_cache.clear()
        self.client = app.test_client()

    def batch(self, plans, qs=SITE_QS, **extra):
        return self.client.post(f"/api/visibility/panels/batch{qs}",
                                json={"plans": plans, **extra})


class TestBatchMatchesSingle(BatchCase):
    def test_each_plan_gets_what_the_single_route_returns(self):
        r = self.batch(PLANS, year=2026)
        self.assertEqual(r.status_code, 200)
        results = r.get_json()["results"]
        self.assertEqual(set(results), {"p1", "p2", "p3"})
        for pl in PLANS:
            with self.subTest(plan=pl["id"]):
                app_module._visibility_cache.clear()
                single = self.client.post(f"/api/visibility/panels{SITE_QS}",
                                          json={"panels": pl["panels"], "year": 2026})
                self.assertEqual(results[pl["id"]], single.get_json())

    def test_one_astropy_pass_covers_every_miss(self):
        real = app_module.compute_year_visibility
        with mock.patch.object(app_module, "compute_year_visibility", side_effect=real) as spy:
            self.assertEqual(self.batch(PLANS).status_code, 200)
            self.assertEqual(spy.call_count, 1)
            # The shared point is computed once, not twice.
            self.assertEqual(len(spy.call_args.args[0]), 4)

    def test_a_second_call_is_served_from_cache(self):
        self.batch(PLANS)
        real = app_module.compute_year_visibility
        with mock.patch.object(app_module, "compute_year_visibility", side_effect=real) as spy:
            self.batch(PLANS)
            self.assertEqual(spy.call_count, 0)

    def test_the_single_route_still_works(self):
        r = self.client.post(f"/api/visibility/panels{SITE_QS}",
                             json={"panels": PLANS[0]["panels"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["panel_count"], 2)


class TestBatchValidation(BatchCase):
    def test_an_empty_list_is_refused(self):
        self.assertEqual(self.batch([]).status_code, 400)

    def test_a_plan_without_an_id_is_refused(self):
        r = self.batch([{"panels": [{"ra_deg": 1, "dec_deg": 1}]}])
        self.assertEqual(r.status_code, 400)

    def test_a_plan_without_panels_is_refused(self):
        self.assertEqual(self.batch([{"id": "a", "panels": []}]).status_code, 400)

    def test_a_bad_panel_names_its_plan(self):
        r = self.batch([{"id": "a", "panels": [{"ra_deg": 1, "dec_deg": 99}]}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("plan 0", r.get_json()["error"])

    def test_the_cap_is_on_the_total_across_plans(self):
        p = [{"ra_deg": 1.0 + i * 0.01, "dec_deg": 1.0} for i in range(250)]
        r = self.batch([{"id": "a", "panels": p}, {"id": "b", "panels": p}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("400", r.get_json()["error"])

    def test_four_hundred_in_total_is_accepted(self):
        p = [{"ra_deg": 1.0 + i * 0.01, "dec_deg": 1.0} for i in range(200)]
        r = self.batch([{"id": "a", "panels": p}, {"id": "b", "panels": p}])
        self.assertEqual(r.status_code, 200)

    def test_an_unknown_site_is_refused(self):
        self.assertEqual(self.batch(PLANS, qs="?site_id=nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()
