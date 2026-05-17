"""Validation tests for /api/plans POST + /api/plans/<id> PUT.

Server-side defence-in-depth: even when the UI validates per-field,
direct API callers (curl, extensions, scripts) can still post garbage.
A bad plan that slips through corrupts plans.json AND every downstream
consumer (nina_ts_sync, priority_tiler, etc.). The validator catches
the cases that would silently break sync to NINA TS.
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


def _fresh_plans_path():
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module._plans_cache = None
    app_module._plans_cache_mtime = None
    # Wipe destinations.json too so the backfill migration doesn't
    # accidentally fire during these tests.
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    app_module._destinations_cache = None
    app_module._destinations_cache_mtime = None
    return app_module.PLANS_PATH


def _valid_plan_payload(plan_id="p1", **overrides):
    base = {
        "id": plan_id,
        "project_name": "Test",
        "target": {
            "name": "Test Target",
            "center_ra_deg": 100.0,
            "center_dec_deg": -30.0,
            "rotation_deg": 0,
            "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 15},
        },
        "telescope_id": "tel-x",
        "camera_id": "cam-y",
        "filter_goals": {"OIII": {"target_hours": 1.5, "sub_exposure_s": 300}},
        "priority": "normal",
        "min_altitude_deg": 30,
        "state": "draft",
    }
    base.update(overrides)
    return base


class TestPlanPostHappyPath(unittest.TestCase):
    """Sanity — the validator must not reject typical valid plans."""

    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()

    def test_full_valid_plan_accepted(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload())
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["id"], "p1")
        self.assertTrue(body["guid"])
        self.assertTrue(body["created_at"])

    def test_minimal_plan_accepted(self):
        # Only id is strictly required; everything else is optional.
        # A draft plan with no target / filters is legitimate.
        r = self.client.post("/api/plans", json={"id": "draft"})
        self.assertEqual(r.status_code, 201)

    def test_dec_at_pole_accepted(self):
        # Boundary case: exactly -90 and +90 are valid.
        for dec in (-90.0, 90.0):
            r = self.client.post("/api/plans", json=_valid_plan_payload(
                plan_id=f"polar_{dec}",
                target={"center_ra_deg": 0.0, "center_dec_deg": dec,
                        "mosaic": {"rows": 1, "cols": 1}},
            ))
            self.assertEqual(r.status_code, 201, f"dec={dec} should be valid")


class TestPlanPostValidation(unittest.TestCase):
    """Each test exercises one rejection branch in _validate_plan_payload."""

    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()

    def _post_expect_400(self, payload, expect_in_error: str = ""):
        r = self.client.post("/api/plans", json=payload)
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        if expect_in_error:
            self.assertIn(expect_in_error, r.get_json().get("error", ""))

    def test_missing_id(self):
        self._post_expect_400({"target": {}}, "id required")

    def test_empty_id(self):
        self._post_expect_400({"id": ""}, "id required")

    def test_id_not_string(self):
        self._post_expect_400({"id": 42}, "id required")

    def test_ra_above_range(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 400.0, "center_dec_deg": 0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "ra_deg")

    def test_ra_below_range(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": -400.0, "center_dec_deg": 0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "ra_deg")

    def test_ra_non_numeric(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": "north", "center_dec_deg": 0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "ra_deg")

    def test_dec_above_range(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": 91.0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "dec_deg")

    def test_dec_below_range(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -91.0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "dec_deg")

    def test_rotation_non_numeric(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "rotation_deg": "north",
            "mosaic": {"rows": 1, "cols": 1},
        }), "rotation_deg")

    def test_mosaic_rows_zero(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "mosaic": {"rows": 0, "cols": 1},
        }), "rows")

    def test_mosaic_rows_negative(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "mosaic": {"rows": -1, "cols": 1},
        }), "rows")

    def test_mosaic_cols_not_integer(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "mosaic": {"rows": 1, "cols": 2.5},
        }), "cols")

    def test_mosaic_overlap_pct_negative(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "mosaic": {"rows": 1, "cols": 1, "overlap_pct": -5},
        }), "overlap_pct")

    def test_mosaic_overlap_pct_too_high(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 150},
        }), "overlap_pct")

    def test_filter_goals_not_dict(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals="not a dict",
        ), "filter_goals")

    def test_filter_goal_value_not_dict(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": "5h"},
        ), "Ha")

    def test_filter_goal_negative_target_hours(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": -3}},
        ), "target_hours")

    def test_filter_goal_non_numeric_target_hours(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": "three"}},
        ), "target_hours")

    def test_filter_goal_zero_sub_exposure(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": 5, "sub_exposure_s": 0}},
        ), "sub_exposure_s")

    def test_filter_goal_negative_sub_exposure(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": 5, "sub_exposure_s": -1}},
        ), "sub_exposure_s")

    def test_target_not_dict(self):
        self._post_expect_400(_valid_plan_payload(
            target="not a dict",
        ), "target")


class TestPlanPutValidation(unittest.TestCase):
    """PUT goes through the same validator. URL plan_id is authoritative
    (overrides whatever id is in the body)."""

    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()
        # Seed with a valid plan to update.
        self.client.post("/api/plans", json=_valid_plan_payload(plan_id="seed"))

    def test_put_with_bad_dec_rejected(self):
        r = self.client.put("/api/plans/seed", json=_valid_plan_payload(
            plan_id="seed",
            target={"center_ra_deg": 100.0, "center_dec_deg": 91.0,
                    "mosaic": {"rows": 1, "cols": 1}},
        ))
        self.assertEqual(r.status_code, 400)

    def test_put_url_id_overrides_body(self):
        # PUT /api/plans/seed with body.id = "other" → URL wins.
        r = self.client.put("/api/plans/seed", json=_valid_plan_payload(
            plan_id="other",
        ))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["id"], "seed")

    def test_put_preserves_guid_across_updates(self):
        original = self.client.get("/api/plans/seed").get_json()
        original_guid = original["guid"]
        original_created = original["created_at"]
        r = self.client.put("/api/plans/seed", json=_valid_plan_payload(
            plan_id="seed", priority="high",
        ))
        self.assertEqual(r.status_code, 200)
        updated = r.get_json()
        self.assertEqual(updated["guid"], original_guid)
        self.assertEqual(updated["created_at"], original_created)
        self.assertNotEqual(updated["updated_at"], original.get("updated_at"))

    def test_put_on_nonexistent_plan_returns_404(self):
        r = self.client.put("/api/plans/does-not-exist",
                            json=_valid_plan_payload(plan_id="does-not-exist"))
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
