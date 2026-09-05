"""Tests for POST /api/plans/<id>/progress.

The plugin reports what a session actually acquired. Hours only ever go
up unless the caller says `force`, because NINA's acquired count legitimately
drops when frames are culled and quietly rewinding a plan the user has
watched fill up is worse than being a session stale.
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


def _redirect_state():
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for name in ("_plans_cache", "_gear_cache", "_destinations_cache",
                 "_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime"):
        setattr(app_module, name, None)
    return td


def _write_plans(plans):
    app_module.PLANS_PATH.write_text(
        json.dumps({"version": 1, "plans": plans}), encoding="utf-8")
    app_module._plans_cache = None
    app_module._plans_cache_mtime = None


def _plan(goals, plan_id="p1"):
    return {
        "id": plan_id,
        "project_name": "Test",
        "target": {"name": "M42", "center_ra_deg": 83.8, "center_dec_deg": -5.4},
        "telescope_id": "tel-540",
        "camera_id": "cam-2600",
        "filter_goals": goals,
        "updated_at": "2020-01-01T00:00:00+00:00",
    }


class TestProgressUpdates(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()
        _write_plans([_plan({
            "Ha": {"target_hours": 6.0, "sub_exposure_s": 300, "actual_hours": 1.0},
            "OIII": {"target_hours": 6.0, "sub_exposure_s": 300},
        })])

    def _post(self, body, plan_id="p1"):
        return self.client.post(f"/api/plans/{plan_id}/progress", json=body)

    def test_raises_actual_hours(self):
        r = self._post({"filters": {"Ha": {"acquired_hours": 2.5, "acquired_count": 30}},
                        "source": "ts", "at": "2026-09-04T11:00:00+00:00"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["updated"], {"Ha": 2.5})
        self.assertEqual(body["plan"]["filter_goals"]["Ha"]["actual_hours"], 2.5)
        # The updated plan comes back, and the write landed.
        stored = self.client.get("/api/plans/p1").get_json()
        self.assertEqual(stored["filter_goals"]["Ha"]["actual_hours"], 2.5)
        self.assertNotEqual(stored["updated_at"], "2020-01-01T00:00:00+00:00")

    def test_first_report_on_a_goal_with_no_actual_hours(self):
        self._post({"filters": {"OIII": {"acquired_hours": 0.75}}})
        stored = self.client.get("/api/plans/p1").get_json()
        self.assertEqual(stored["filter_goals"]["OIII"]["actual_hours"], 0.75)

    def test_several_filters_at_once(self):
        body = self._post({"filters": {"Ha": {"acquired_hours": 3.0},
                                       "OIII": {"acquired_hours": 1.5}}}).get_json()
        self.assertEqual(body["updated"], {"Ha": 3.0, "OIII": 1.5})

    def test_refuses_to_lower_without_force(self):
        body = self._post({"filters": {"Ha": {"acquired_hours": 0.25}}}).get_json()
        self.assertEqual(body["updated"], {})
        self.assertEqual(body["not_lowered"], ["Ha"])
        self.assertEqual(body["plan"]["filter_goals"]["Ha"]["actual_hours"], 1.0)
        self.assertEqual(
            self.client.get("/api/plans/p1").get_json()["updated_at"],
            "2020-01-01T00:00:00+00:00")

    def test_force_lowers(self):
        body = self._post({"filters": {"Ha": {"acquired_hours": 0.25}},
                           "force": True}).get_json()
        self.assertEqual(body["updated"], {"Ha": 0.25})
        self.assertEqual(body["not_lowered"], [])
        self.assertEqual(
            self.client.get("/api/plans/p1").get_json()["filter_goals"]["Ha"]["actual_hours"],
            0.25)

    def test_equal_value_is_not_a_lowering(self):
        body = self._post({"filters": {"Ha": {"acquired_hours": 1.0}}}).get_json()
        self.assertEqual(body["updated"], {"Ha": 1.0})
        self.assertEqual(body["not_lowered"], [])

    def test_unknown_filters_are_listed_and_ignored(self):
        body = self._post({"filters": {"Ha": {"acquired_hours": 2.0},
                                       "SII": {"acquired_hours": 4.0}}}).get_json()
        self.assertEqual(body["unknown_filters"], ["SII"])
        self.assertEqual(body["updated"], {"Ha": 2.0})
        self.assertNotIn("SII", body["plan"]["filter_goals"])

    def test_branded_filter_name_matches_the_plan_goal(self):
        body = self._post({"filters": {"Antlia Ha": {"acquired_hours": 2.0}}}).get_json()
        self.assertEqual(body["updated"], {"Ha": 2.0})
        self.assertEqual(body["unknown_filters"], [])

    def test_partial_lowering_still_applies_the_rest(self):
        body = self._post({"filters": {"Ha": {"acquired_hours": 0.1},
                                       "OIII": {"acquired_hours": 2.0}}}).get_json()
        self.assertEqual(body["updated"], {"OIII": 2.0})
        self.assertEqual(body["not_lowered"], ["Ha"])

    def test_acquired_count_is_not_written_to_the_plan(self):
        """The plan schema carries hours, not counts; the sync builder
        derives the count from hours and sub exposure."""
        body = self._post({"filters": {"Ha": {"acquired_hours": 2.0,
                                              "acquired_count": 24}}}).get_json()
        self.assertNotIn("actual_count", body["plan"]["filter_goals"]["Ha"])
        self.assertNotIn("acquired_count", body["plan"]["filter_goals"]["Ha"])

    def test_unknown_plan_is_404(self):
        self.assertEqual(
            self._post({"filters": {"Ha": {"acquired_hours": 1.0}}}, "nope").status_code, 404)


class TestProgressValidation(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()
        _write_plans([_plan({"Ha": {"target_hours": 6.0, "actual_hours": 1.0}})])

    def _reject(self, body, fragment):
        r = self.client.post("/api/plans/p1/progress", json=body)
        self.assertEqual(r.status_code, 400, r.get_json())
        self.assertIn(fragment, r.get_json()["error"])

    def test_filters_required(self):
        self._reject({}, "filters")
        self._reject({"filters": {}}, "filters")
        self._reject({"filters": ["Ha"]}, "filters")

    def test_acquired_hours_required_and_finite(self):
        self._reject({"filters": {"Ha": {}}}, "acquired_hours")
        self._reject({"filters": {"Ha": {"acquired_hours": -1}}}, "acquired_hours")
        self._reject({"filters": {"Ha": {"acquired_hours": float("inf")}}}, "acquired_hours")
        self._reject({"filters": {"Ha": {"acquired_hours": "2.0h"}}}, "acquired_hours")

    def test_acquired_count_must_be_a_non_negative_integer(self):
        self._reject({"filters": {"Ha": {"acquired_hours": 1.0, "acquired_count": -3}}},
                     "acquired_count")
        self._reject({"filters": {"Ha": {"acquired_hours": 1.0, "acquired_count": 1.5}}},
                     "acquired_count")

    def test_force_must_be_a_boolean(self):
        self._reject({"filters": {"Ha": {"acquired_hours": 1.0}}, "force": "yes"}, "force")

    def test_source_and_at_must_be_strings(self):
        self._reject({"filters": {"Ha": {"acquired_hours": 1.0}}, "source": 3}, "source")
        self._reject({"filters": {"Ha": {"acquired_hours": 1.0}}, "at": 3}, "at")

    def test_nothing_is_written_when_validation_fails(self):
        self.client.post("/api/plans/p1/progress",
                         json={"filters": {"Ha": {"acquired_hours": float("nan")}}})
        self.assertEqual(
            self.client.get("/api/plans/p1").get_json()["filter_goals"]["Ha"]["actual_hours"],
            1.0)


if __name__ == "__main__":
    unittest.main()
