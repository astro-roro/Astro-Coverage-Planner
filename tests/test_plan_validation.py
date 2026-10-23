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
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

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


def _row(**overrides):
    row = {"id": "row-oiii", "filter": "OIII", "template": "OIII 3nm",
           "template_sub_s": 300, "unit": "h", "target_hours": 1.5}
    row.update(overrides)
    return row


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
        "filter_rows": [_row()],
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







    # filter_rows (docs/specs/goal-panel-and-ts-order.md section 3)

    def test_rows_not_a_list(self):
        self._post_expect_400(_valid_plan_payload(filter_rows="R"), "filter_rows")

    def test_row_not_an_object(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=["5h"]), "filter_rows[0]")

    def test_v1_same_filter_and_template_twice_rejected(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=[
            _row(id="a", filter="R", template="R Stars", template_sub_s=15, unit="fr", target_frames=20),
            _row(id="b", filter="R", template="R Stars", template_sub_s=15, unit="fr", target_frames=40),
        ]), "R Stars")

    def test_v1_same_filter_with_two_templates_saves(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(filter_rows=[
            _row(id="a", filter="R", template="R", template_sub_s=180),
            _row(id="b", filter="R", template="R Stars", template_sub_s=15, unit="fr",
                 target_frames=20, target_hours=None),
        ]))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))

    def test_duplicate_row_id_rejected(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=[
            _row(id="a"), _row(id="a", filter="Ha", template="Ha 3nm"),
        ]), "id")

    def test_row_without_id_gets_one(self):
        row = _row()
        del row["id"]
        r = self.client.post("/api/plans", json=_valid_plan_payload(filter_rows=[row]))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertTrue(r.get_json()["filter_rows"][0]["id"])

    def test_v2_frame_row_without_target_frames_rejected(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=[
            _row(unit="fr")]), "target_frames")

    def test_v2_fractional_target_frames_rejected(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=[
            _row(unit="fr", target_frames=2.5)]), "target_frames")

    def test_v2_empty_template_rejected(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=[
            _row(template="")]), "template")

    def test_unknown_unit_rejected(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=[_row(unit="min")]), "unit")

    def test_hour_row_without_target_hours_rejected(self):
        row = _row()
        del row["target_hours"]
        self._post_expect_400(_valid_plan_payload(filter_rows=[row]), "target_hours")

    def test_negative_target_hours_rejected(self):
        self._post_expect_400(_valid_plan_payload(filter_rows=[_row(target_hours=-3)]), "target_hours")

    def test_nan_target_hours_rejected(self):
        # A NaN comparison is silently False, which is how a NaN goal once
        # passed validation and poisoned plans.json.
        self._post_expect_400(_valid_plan_payload(
            filter_rows=[_row(target_hours=float("nan"))]), "target_hours")

    def test_negative_target_frames_rejected(self):
        self._post_expect_400(_valid_plan_payload(
            filter_rows=[_row(unit="fr", target_frames=-1)]), "target_frames")

    def test_zero_or_nan_template_sub_rejected(self):
        for bad in (0, -1, float("nan"), float("inf"), "300"):
            with self.subTest(bad=bad):
                self._post_expect_400(_valid_plan_payload(
                    filter_rows=[_row(template_sub_s=bad)]), "template_sub_s")

    def test_negative_or_nan_actual_hours_rejected(self):
        for bad in (-1, float("nan")):
            with self.subTest(bad=bad):
                self._post_expect_400(_valid_plan_payload(
                    filter_rows=[_row(actual_hours=bad)]), "actual_hours")

    def test_filter_the_camera_lacks_rejected(self):
        gear = Path(tempfile.mkdtemp()) / "gear.json"
        gear.write_text(json.dumps({"cameras": [{
            "id": "cam-y", "name": "Cam", "filters": {"OIII": {}, "Ha": {}},
        }]}), encoding="utf-8")
        saved = app_module.GEAR_PATH
        app_module.GEAR_PATH = gear
        app_module._gear_cache = None
        try:
            self._post_expect_400(_valid_plan_payload(filter_rows=[
                _row(filter="SII", template="SII 3nm")]), "SII")
            r = self.client.post("/api/plans", json=_valid_plan_payload())
            self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        finally:
            app_module.GEAR_PATH = saved
            app_module._gear_cache = None

    def test_filter_goals_sent_by_a_client_are_not_stored(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(
            filter_goals={"Ha": {"target_hours": 5, "sub_exposure_s": 300}}))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        stored = json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))["plans"][0]
        self.assertNotIn("filter_goals", stored)
        self.assertEqual([row["filter"] for row in stored["filter_rows"]], ["OIII"])

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
        # Pin the clock one second past the seed's timestamp. Without this
        # the update can land in the same clock tick as the seed on Windows,
        # where Python 3.12's wall clock only advances every ~16 ms.
        later = datetime.fromisoformat(original["updated_at"]) + timedelta(seconds=1)

        class _Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return later if tz is None else later.astimezone(tz)

        with mock.patch.object(app_module, "datetime", _Later):
            r = self.client.put("/api/plans/seed", json=_valid_plan_payload(
                plan_id="seed", priority="high",
            ))
        self.assertEqual(r.status_code, 200)
        updated = r.get_json()
        self.assertEqual(updated["guid"], original_guid)
        self.assertEqual(updated["created_at"], original_created)
        self.assertNotEqual(updated["updated_at"], original.get("updated_at"))
        self.assertEqual(updated["updated_at"], later.isoformat())

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
        # The healed goal then went through the filter row migration, which
        # reads a missing goal as 0.
        self.assertEqual(plans["poisoned"]["filter_rows"][0]["target_hours"], 0.0)
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


class TestPublicPageFields(unittest.TestCase):
    """visibility / is_current / public_blurb feed the live page publisher
    (docs/specs/shooting-page.md). Absent means private, so a bad value
    must be rejected rather than coerced."""

    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()

    def test_visibility_accepts_private_and_public(self):
        for v in ("private", "public"):
            r = self.client.post("/api/plans", json=_valid_plan_payload(plan_id=v, visibility=v))
            self.assertEqual(r.status_code, 201, v)

    def test_visibility_rejects_other_values(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(visibility="friends"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("visibility", r.get_json()["error"])

    def test_is_current_must_be_bool(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(is_current="yes"))
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/plans", json=_valid_plan_payload(is_current=True))
        self.assertEqual(r.status_code, 201)

    def test_public_blurb_capped_at_500(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(public_blurb="x" * 501))
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/plans", json=_valid_plan_payload(public_blurb="x" * 500))
        self.assertEqual(r.status_code, 201)


class TestVoteSectionFields(unittest.TestCase):
    """public_section / vote_goals / vote_baseline (docs/specs/shooting-page.md).
    The baseline is the server's: set on entering the vote, cleared on leaving,
    never taken from the client."""

    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()
        patcher = mock.patch.object(app_module, "load_manifest", return_value={"targets": []})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, **kw):
        return self.client.post("/api/plans", json=_valid_plan_payload(**kw))

    def test_section_accepts_current_and_vote(self):
        for v in ("current", "vote"):
            self.assertEqual(self._post(plan_id=v, public_section=v).status_code, 201, v)

    def test_section_rejects_other_values(self):
        r = self._post(public_section="maybe")
        self.assertEqual(r.status_code, 400)
        self.assertIn("public_section", r.get_json()["error"])

    def test_goals_reject_negative_zero_and_non_numbers(self):
        for bad in ({"OIII": -1}, {"OIII": 0}, {"OIII": "2"}, {"OIII": True}, ["OIII"]):
            r = self._post(public_section="vote", vote_goals=bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("vote_goals", r.get_json()["error"])

    def test_goals_accept_positive_numbers(self):
        self.assertEqual(self._post(public_section="vote", vote_goals={"OIII": 2.5}).status_code, 201)

    def test_old_plan_without_fields_still_saves(self):
        r = self._post()
        self.assertEqual(r.status_code, 201)
        self.assertNotIn("vote_baseline", r.get_json())

    def test_baseline_set_on_entering_vote_and_cleared_on_leaving(self):
        row = _row(actual_hours=3.0)
        self.assertEqual(self._post(filter_rows=[row]).status_code, 201)
        r = self._post(filter_rows=[row], public_section="vote", vote_goals={"OIII": 2})
        self.assertEqual(r.get_json()["vote_baseline"], {"OIII": 3.0})
        # More hours arrive, then the plan is saved again still in the vote.
        row2 = _row(actual_hours=4.5)
        r = self._post(filter_rows=[row2], public_section="vote", vote_goals={"OIII": 2})
        self.assertEqual(r.get_json()["vote_baseline"], {"OIII": 3.0})
        r = self._post(filter_rows=[row2], public_section="current", vote_goals={"OIII": 2})
        self.assertNotIn("vote_baseline", r.get_json())
        self.assertNotIn("vote_baseline", self.client.get("/api/plans/p1").get_json())

    def test_baseline_from_client_is_ignored(self):
        r = self._post(filter_rows=[_row(actual_hours=3.0)], public_section="vote",
                       vote_goals={"OIII": 2}, vote_baseline={"OIII": 0})
        self.assertEqual(r.get_json()["vote_baseline"], {"OIII": 3.0})

    def test_baseline_taken_again_on_re_entering_vote(self):
        self._post(filter_rows=[_row(actual_hours=3.0)], public_section="vote", vote_goals={"OIII": 2})
        self._post(filter_rows=[_row(actual_hours=3.0)], public_section="current")
        r = self._post(filter_rows=[_row(actual_hours=5.0)], public_section="vote", vote_goals={"OIII": 2})
        self.assertEqual(r.get_json()["vote_baseline"], {"OIII": 5.0})

    def test_baseline_negative_value_rejected(self):
        r = self._post(public_section="vote", vote_baseline={"OIII": -1})
        self.assertEqual(r.status_code, 400)


class TestPlanState(unittest.TestCase):
    """`state` is one of the four TS project states (draft, active,
    inactive, closed), per docs/specs/ts-project-settings.md section 1.
    "parked" is a held PR #97 value that never shipped; it is accepted on
    write and normalised to "inactive" rather than rejected, so an old
    client that still sends it doesn't 400."""

    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()

    def test_each_of_the_four_states_saves(self):
        for state in ("draft", "active", "inactive", "closed"):
            r = self.client.post("/api/plans", json=_valid_plan_payload(plan_id=state, state=state))
            self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["state"], state)

    def test_unknown_state_rejected(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(state="paused"))
        self.assertEqual(r.status_code, 400)
        self.assertIn("state", r.get_json()["error"])

    def test_no_state_field_still_accepted(self):
        payload = _valid_plan_payload()
        del payload["state"]
        r = self.client.post("/api/plans", json=payload)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertNotIn("state", r.get_json())

    def test_parked_saves_as_inactive(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(state="parked"))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["state"], "inactive")


class TestMinimumTimeMin(unittest.TestCase):
    """`minimum_time_min`: integer minutes, ≥ 0, absent means 0. Same
    defence-in-depth shape as min_altitude_deg."""

    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()

    def test_negative_rejected(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(minimum_time_min=-1))
        self.assertEqual(r.status_code, 400)
        self.assertIn("minimum_time_min", r.get_json()["error"])

    def test_non_integer_rejected(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(minimum_time_min=1.5))
        self.assertEqual(r.status_code, 400)

    def test_string_rejected(self):
        r = self.client.post("/api/plans", json=_valid_plan_payload(minimum_time_min="x"))
        self.assertEqual(r.status_code, 400)

    def test_zero_and_sixty_accepted(self):
        for v in (0, 60):
            r = self.client.post("/api/plans", json=_valid_plan_payload(plan_id=f"p{v}", minimum_time_min=v))
            self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
            self.assertEqual(r.get_json()["minimum_time_min"], v)


if __name__ == "__main__":
    unittest.main()
