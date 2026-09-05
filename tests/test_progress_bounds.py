"""Bounds on the progress endpoint's acquired hours.

Progress only ever raises a goal unless `force` is passed, so a single absurd
value writes a number the endpoint can never correct again. An audit on
2026-09-05 showed 1e300 was accepted, after which an honest 3.0 was refused as a
lowering, and that a numeric string was quietly coerced. Both are refused now.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

PLAN = {
    "id": "p1",
    "project_name": "QA",
    "filter_goals": {"Ha": {"target_hours": 6.0, "actual_hours": 0.0}},
    "target": {"name": "NGC 6744", "center_ra_deg": 287.4, "center_dec_deg": -63.9},
}


class ProgressBoundsCase(unittest.TestCase):
    def setUp(self):
        td = Path(tempfile.mkdtemp())
        app_module.PLANS_PATH = td / "plans.json"
        for cache in ("_plans_cache", "_plans_cache_mtime"):
            setattr(app_module, cache, None)
        app_module.PLANS_PATH.write_text(
            json.dumps({"version": 1, "plans": [json.loads(json.dumps(PLAN))]}),
            encoding="utf-8",
        )
        self.client = app.test_client()

    def post(self, hours):
        return self.client.post(
            "/api/plans/p1/progress",
            json={"filters": {"Ha": {"acquired_hours": hours}}},
        )

    def actual(self):
        body = self.client.get("/api/plans").get_json()
        return body["plans"][0]["filter_goals"]["Ha"]["actual_hours"]


class TestAbsurdValuesRefused(ProgressBoundsCase):
    def test_enormous_finite_value_is_refused(self):
        self.assertEqual(self.post(1e300).status_code, 400)
        self.assertEqual(self.actual(), 0.0)

    def test_an_honest_value_still_lands_afterwards(self):
        """The point of the bound: one absurd report must not poison the goal
        for every honest one that follows."""
        self.post(1e300)
        self.assertEqual(self.post(3.0).status_code, 200)
        self.assertAlmostEqual(self.actual(), 3.0)

    def test_just_over_the_cap_is_refused(self):
        self.assertEqual(self.post(app_module.MAX_ACQUIRED_HOURS + 1).status_code, 400)

    def test_the_cap_itself_is_allowed(self):
        self.assertEqual(self.post(app_module.MAX_ACQUIRED_HOURS).status_code, 200)


class TestTypesNotCoerced(ProgressBoundsCase):
    def test_numeric_string_is_refused_rather_than_coerced(self):
        self.assertEqual(self.post("1e6").status_code, 400)
        self.assertEqual(self.actual(), 0.0)

    def test_plain_numeric_string_is_refused_too(self):
        self.assertEqual(self.post("3.0").status_code, 400)

    def test_boolean_is_refused(self):
        self.assertEqual(self.post(True).status_code, 400)

    def test_none_is_refused(self):
        self.assertEqual(self.post(None).status_code, 400)


class TestOrdinaryValuesStillWork(ProgressBoundsCase):
    def test_a_normal_report_lands(self):
        self.assertEqual(self.post(2.5).status_code, 200)
        self.assertAlmostEqual(self.actual(), 2.5)

    def test_an_integer_is_accepted(self):
        self.assertEqual(self.post(4).status_code, 200)
        self.assertAlmostEqual(self.actual(), 4.0)

    def test_zero_is_accepted(self):
        self.assertEqual(self.post(0).status_code, 200)

    def test_negative_is_still_refused(self):
        self.assertEqual(self.post(-1.0).status_code, 400)


if __name__ == "__main__":
    unittest.main()
