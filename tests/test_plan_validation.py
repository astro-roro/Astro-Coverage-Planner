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

    def test_ra_zero_accepted(self):
        # 0.0 is the lower (inclusive) bound of the [0, 360) range.
        r = self.client.post("/api/plans", json=_valid_plan_payload(
            target={"center_ra_deg": 0.0, "center_dec_deg": 0.0,
                    "mosaic": {"rows": 1, "cols": 1}},
        ))
        self.assertEqual(r.status_code, 201)

    def test_ra_just_under_360_accepted(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(
            target={"center_ra_deg": 359.999, "center_dec_deg": 0.0,
                    "mosaic": {"rows": 1, "cols": 1}},
        ))
        self.assertEqual(r.status_code, 201)

    def test_ra_small_negative_normalised(self):
        # Aladin's seam-drag and manual entry both legitimately produce a
        # small negative RA; it's normalised into [0, 360) rather than
        # rejected (-0.5 -> 359.5), matching what the old validator used
        # to reject with a 400.
        r = self.client.post("/api/plans", json=_valid_plan_payload(
            target={"center_ra_deg": -0.5, "center_dec_deg": 0.0,
                    "mosaic": {"rows": 1, "cols": 1}},
        ))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertAlmostEqual(r.get_json()["target"]["center_ra_deg"], 359.5)

    def test_ra_at_360_normalised_to_zero(self):
        # Range is conceptually half-open: 360 and 0 are the same point,
        # so exactly 360.0 normalises down to 0.0 rather than 400ing.
        r = self.client.post("/api/plans", json=_valid_plan_payload(
            target={"center_ra_deg": 360.0, "center_dec_deg": 0.0,
                    "mosaic": {"rows": 1, "cols": 1}},
        ))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertAlmostEqual(r.get_json()["target"]["center_ra_deg"], 0.0)

    def test_ra_large_negative_normalised(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(
            target={"center_ra_deg": -400.0, "center_dec_deg": 0.0,
                    "mosaic": {"rows": 1, "cols": 1}},
        ))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertAlmostEqual(r.get_json()["target"]["center_ra_deg"], 320.0)

    def test_ra_large_positive_normalised(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(
            target={"center_ra_deg": 400.0, "center_dec_deg": 0.0,
                    "mosaic": {"rows": 1, "cols": 1}},
        ))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertAlmostEqual(r.get_json()["target"]["center_ra_deg"], 40.0)


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

    def test_ra_nan_rejected(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": float("nan"), "center_dec_deg": 0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "ra_deg")

    def test_ra_infinity_rejected(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": float("inf"), "center_dec_deg": 0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "ra_deg")

    def test_ra_negative_infinity_rejected(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": float("-inf"), "center_dec_deg": 0,
            "mosaic": {"rows": 1, "cols": 1},
        }), "ra_deg")

    def test_dec_nan_rejected(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": float("nan"),
            "mosaic": {"rows": 1, "cols": 1},
        }), "dec_deg")

    def test_rotation_nan_rejected(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "rotation_deg": float("nan"),
            "mosaic": {"rows": 1, "cols": 1},
        }), "rotation_deg")

    def test_rotation_infinity_rejected(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "rotation_deg": float("inf"),
            "mosaic": {"rows": 1, "cols": 1},
        }), "rotation_deg")

    def test_mosaic_overlap_pct_nan_rejected(self):
        self._post_expect_400(_valid_plan_payload(target={
            "center_ra_deg": 100.0, "center_dec_deg": -30.0,
            "mosaic": {"rows": 1, "cols": 1, "overlap_pct": float("nan")},
        }), "overlap_pct")

    def test_filter_goal_target_hours_nan_rejected(self):
        # The old `th < 0` check was silently False for NaN, so this
        # previously passed validation and poisoned plans.json: every
        # later /api/sync then 500'd on int(math.ceil(nan)).
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": float("nan")}},
        ), "target_hours")

    def test_filter_goal_target_hours_infinity_rejected(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": float("inf")}},
        ), "target_hours")

    def test_filter_goal_sub_exposure_nan_rejected(self):
        # Same silent-False-comparison bug as target_hours, on the
        # `se <= 0` check.
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": 5, "sub_exposure_s": float("nan")}},
        ), "sub_exposure_s")

    def test_filter_goal_sub_exposure_infinity_rejected(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": 5, "sub_exposure_s": float("inf")}},
        ), "sub_exposure_s")

    def test_filter_goal_actual_hours_nan_rejected(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": 5, "actual_hours": float("nan")}},
        ), "actual_hours")

    def test_filter_goal_actual_hours_negative_rejected(self):
        self._post_expect_400(_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": 5, "actual_hours": -1}},
        ), "actual_hours")

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


class TestSavePlansNaNTripwire(unittest.TestCase):
    """save_plans() itself refuses to write NaN/Infinity (allow_nan=False),
    even if some future code path calls it with data that bypassed
    _validate_plan_payload. Belt-and-braces so a non-finite value can
    never reach plans.json silently."""

    def setUp(self):
        _fresh_plans_path()

    def test_nan_in_plan_raises_on_save(self):
        bad = {"version": 1, "plans": [{
            "id": "p1",
            "filter_goals": {"Ha": {"target_hours": float("nan")}},
        }]}
        with self.assertRaises(ValueError):
            app_module.save_plans(bad)

    def test_infinity_in_plan_raises_on_save(self):
        bad = {"version": 1, "plans": [{
            "id": "p1",
            "target": {"center_ra_deg": float("inf")},
        }]}
        with self.assertRaises(ValueError):
            app_module.save_plans(bad)


class TestLegacyNaNHealingOnLoad(unittest.TestCase):
    """A plans.json written before allow_nan=False existed can still carry
    NaN/Infinity. Every write path re-serialises the FULL plan list with
    allow_nan=False, so one poisoned plan used to 500 every subsequent
    POST/PUT/DELETE/sync, and GET handed the browser a NaN literal its own
    JSON.parse can't handle either. load_plans() must heal those fields to
    null in memory (and log a warning) so legacy files work immediately."""

    def setUp(self):
        self.plans_path = _fresh_plans_path()
        # Python's json.dump writes NaN/Infinity by default (allow_nan=True):
        # that's exactly how a pre-fix plans.json could have gotten here.
        self.plans_path.write_text(json.dumps({
            "version": 1,
            "plans": [
                {
                    "id": "poisoned",
                    "target": {
                        "center_ra_deg": float("nan"),
                        "center_dec_deg": -30.0,
                    },
                    "filter_goals": {
                        "Ha": {"target_hours": float("inf"), "sub_exposure_s": 300},
                    },
                },
                {
                    "id": "clean",
                    "target": {"center_ra_deg": 100.0, "center_dec_deg": -30.0},
                },
            ],
        }), encoding="utf-8")
        self.client = app.test_client()

    def test_load_plans_heals_nonfinite_fields_to_none(self):
        data = app_module.load_plans()
        plans = {p["id"]: p for p in data["plans"]}
        self.assertIsNone(plans["poisoned"]["target"]["center_ra_deg"])
        self.assertIsNone(plans["poisoned"]["filter_goals"]["Ha"]["target_hours"])
        # Untouched fields on the same plan survive.
        self.assertEqual(plans["poisoned"]["target"]["center_dec_deg"], -30.0)
        # The clean plan is unaffected.
        self.assertEqual(plans["clean"]["target"]["center_ra_deg"], 100.0)

    def test_load_plans_logs_a_warning_naming_plan_and_field(self):
        with self.assertLogs(level="WARNING") as cm:
            app_module._plans_cache = None
            app_module._plans_cache_mtime = None
            app_module.load_plans()
        joined = "\n".join(cm.output)
        self.assertIn("poisoned", joined)
        self.assertIn("target.center_ra_deg", joined)
        self.assertIn("filter_goals.Ha.target_hours", joined)

    def test_get_plans_returns_strict_parseable_json(self):
        r = self.client.get("/api/plans")
        self.assertEqual(r.status_code, 200)
        raw = r.get_data(as_text=True)
        self.assertNotIn("NaN", raw)
        self.assertNotIn("Infinity", raw)
        # json.loads() with default strict=True must not choke, matching
        # the browser's JSON.parse.
        reparsed = json.loads(raw)
        plans = {p["id"]: p for p in reparsed["plans"]}
        self.assertIsNone(plans["poisoned"]["target"]["center_ra_deg"])

    def test_post_after_healing_succeeds_and_persists_clean_data(self):
        # Touching any plan re-serialises the FULL list; before the fix
        # this 500'd because the poisoned plan was still in memory.
        r = self.client.post("/api/plans", json=_valid_plan_payload(plan_id="new"))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        on_disk = json.loads(self.plans_path.read_text(encoding="utf-8"))
        poisoned = next(p for p in on_disk["plans"] if p["id"] == "poisoned")
        self.assertIsNone(poisoned["target"]["center_ra_deg"])

    def test_put_after_healing_succeeds(self):
        r = self.client.put("/api/plans/clean", json=_valid_plan_payload(plan_id="clean"))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_delete_after_healing_succeeds(self):
        r = self.client.delete("/api/plans/clean")
        self.assertEqual(r.status_code, 204, r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
