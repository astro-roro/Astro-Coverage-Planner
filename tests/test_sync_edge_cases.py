"""Tests for /api/sync edge cases — mosaic with bad gear, strictest-wins
project conflicts, fallback naming.

The happy path is exercised by tests/smoke.py; this file pins down the
warning-emitting paths that smoke.py skips. Lines 2746-2752 and
2878-2885 in app.py were previously uncovered.
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
    """Redirect every persistence path the sync flow touches."""
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.ZIP_OUTPUT_DIR = td / "exports"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for cache in (
        "_plans_cache", "_gear_cache", "_destinations_cache",
    ):
        setattr(app_module, cache, None)
    for cache in (
        "_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime",
    ):
        setattr(app_module, cache, None)
    return td


def _save_gear_with_telescope_and_camera(*, focal_length_mm=600.0):
    app_module.save_gear({
        "version": 2,
        "telescopes": [{
            "id": "tel-1", "name": "Test 600mm",
            "focal_length_mm": focal_length_mm, "aperture_mm": 130,
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


def _save_gear_with_zero_fov_telescope():
    """Gear where the telescope has focal_length_mm = 0. _fov_arcmin
    returns (0, 0), which the sync mosaic path detects and warns about."""
    app_module.save_gear({
        "version": 2,
        "telescopes": [{
            "id": "tel-bad", "name": "Broken Telescope",
            "focal_length_mm": 0, "aperture_mm": 0,
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


def _plan(plan_id, **kwargs):
    base = {
        "id": plan_id,
        "guid": f"guid-{plan_id}",
        "project_name": kwargs.pop("project_name", "Test Project"),
        "target": {
            "name": kwargs.pop("target_name", f"T{plan_id}"),
            "center_ra_deg": kwargs.pop("ra", 100.0),
            "center_dec_deg": kwargs.pop("dec", -30.0),
            "rotation_deg": kwargs.pop("rot", 0.0),
            "mosaic": {
                "rows": kwargs.pop("rows", 1),
                "cols": kwargs.pop("cols", 1),
                "overlap_pct": kwargs.pop("overlap_pct", 15),
            },
        },
        "telescope_id": kwargs.pop("telescope_id", "tel-1"),
        "camera_id": kwargs.pop("camera_id", "cam-1"),
        "filter_goals": kwargs.pop("filter_goals", {
            "OIII": {"target_hours": 1.0, "sub_exposure_s": 300},
        }),
        "priority": kwargs.pop("priority", "normal"),
        "min_altitude_deg": kwargs.pop("min_altitude_deg", 30),
        # Not "draft": this whole file exercises /api/sync, and draft
        # plans are excluded from sync by default (see api_sync). Tests
        # here care about mosaic/warning/naming behaviour, not draft
        # filtering, so the shared helper defaults to committed plans.
        "state": "active",
    }
    base.update(kwargs)
    return base


class TestSyncMosaicWithBadFOV(unittest.TestCase):
    """A 2×2 mosaic with a zero-FOV telescope should emit a
    `mosaic_no_fov` warning and fall back to a single target. The
    sync overall must still succeed (200), not abort."""

    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_zero_fov_telescope_emits_warning_and_falls_back(self):
        _save_gear_with_zero_fov_telescope()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", telescope_id="tel-bad", rows=2, cols=2),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        kinds = [w["kind"] for w in body["warnings"]]
        self.assertIn("mosaic_no_fov", kinds,
            f"expected mosaic_no_fov warning, got {body['warnings']}")
        # Plan still emitted — as a single target, not 4 panels.
        # Project has 1 plan → 1 target after fallback.
        self.assertEqual(body["plan_count"], 1)

    def test_unknown_telescope_id_emits_warning(self):
        _save_gear_with_telescope_and_camera()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", telescope_id="tel-does-not-exist", rows=2, cols=2),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        # Unknown telescope = no FOV resolvable = mosaic_no_fov warning.
        kinds = [w["kind"] for w in body["warnings"]]
        self.assertIn("mosaic_no_fov", kinds)

    def test_single_panel_with_zero_fov_does_not_warn(self):
        # A 1×1 plan doesn't NEED a valid FOV (no mosaic expansion).
        # No warning should fire — only mosaics care.
        _save_gear_with_zero_fov_telescope()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", telescope_id="tel-bad", rows=1, cols=1),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        mosaic_warnings = [w for w in body["warnings"]
                          if w["kind"] == "mosaic_no_fov"]
        self.assertEqual(len(mosaic_warnings), 0,
            "1×1 plan should not trigger mosaic_no_fov warning")


class TestSyncStrictestWinsConflicts(unittest.TestCase):
    """When two plans in the same project disagree on min_altitude /
    meridian_window / priority, the export uses the strictest value
    AND emits a warning so the UI can offer an inline rename."""

    def setUp(self):
        _fresh_state()
        _save_gear_with_telescope_and_camera()
        self.client = app.test_client()

    def test_min_altitude_conflict_takes_strictest(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", project_name="Shared", min_altitude_deg=30),
            _plan("p2", project_name="Shared", min_altitude_deg=45),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        warnings_by_kind = {w["kind"]: w for w in body["warnings"]}
        self.assertIn("min_altitude", warnings_by_kind)
        # Strictest = highest min_altitude = 45.
        self.assertEqual(warnings_by_kind["min_altitude"]["resolved"], 45)

    def test_priority_conflict_takes_highest(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", project_name="Shared", priority="normal"),
            _plan("p2", project_name="Shared", priority="high"),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        kinds = [w["kind"] for w in body["warnings"]]
        self.assertIn("priority", kinds)

    def test_matching_constraints_emit_no_warning(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", project_name="Shared", min_altitude_deg=30,
                  priority="normal"),
            _plan("p2", project_name="Shared", min_altitude_deg=30,
                  priority="normal"),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        conflict_kinds = [w["kind"] for w in body["warnings"]
                         if w["kind"] in ("min_altitude", "priority",
                                          "meridian_window")]
        self.assertEqual(conflict_kinds, [])


class TestSyncProjectNaming(unittest.TestCase):
    """Plans without project_name get auto-named per target — they
    each become their own TS Project rather than landing in an
    "Unassigned" dump."""

    def setUp(self):
        _fresh_state()
        _save_gear_with_telescope_and_camera()
        self.client = app.test_client()

    def test_blank_project_name_autonamed_from_target(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", project_name="", target_name="NGC 7000"),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["project_count"], 1)
        # Project_count = 1 indicates the plan got its own project
        # rather than being grouped under an "Unassigned" catch-all.

    def test_missing_project_name_field_autonamed(self):
        plan = _plan("p1", target_name="M42")
        del plan["project_name"]
        app_module.save_plans({"version": 1, "plans": [plan]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["project_count"], 1)


class TestSyncEmptyAndInvalid(unittest.TestCase):
    def setUp(self):
        _fresh_state()
        _save_gear_with_telescope_and_camera()
        self.client = app.test_client()

    def test_no_plans_returns_400(self):
        app_module.save_plans({"version": 1, "plans": []})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no plans", r.get_json().get("error", ""))


if __name__ == "__main__":
    unittest.main()
