"""Tests for the /api/sync fixes: draft exclusion, destination-scoped
sync, IsMosaic, RA normalisation on export, and scalar sensor fields on
/api/gear.

Each test redirects PLANS_PATH / GEAR_PATH / DESTINATIONS_PATH /
ZIP_OUTPUT_DIR to a temp dir so the suite never touches real data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _fresh_state():
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.ZIP_OUTPUT_DIR = td / "exports"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    app_module.MANIFEST_PATH = td / "manifest.json"
    for cache in ("_plans_cache", "_gear_cache", "_destinations_cache", "_manifest_cache"):
        setattr(app_module, cache, None)
    for cache in ("_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime",
                  "_manifest_cache_mtime"):
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


def _plan(plan_id, **kwargs):
    base = {
        "id": plan_id,
        "guid": f"guid-{plan_id}",
        "project_name": kwargs.pop("project_name", f"Project {plan_id}"),
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
        "state": kwargs.pop("state", "active"),
    }
    if "destination_id" in kwargs:
        base["destination_id"] = kwargs.pop("destination_id")
    base.update(kwargs)
    return base


class TestSyncDraftExclusion(unittest.TestCase):
    """Draft plans never sync: they're work in progress, not committed
    to a session. Legacy plans with no `state` field at all must keep
    syncing (pre-feature data)."""

    def setUp(self):
        _fresh_state()
        _save_gear()
        self.client = app.test_client()

    def test_draft_plan_excluded_from_sync(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", state="active"),
            _plan("p2", state="draft"),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["plan_count"], 1)
        self.assertEqual(body["skipped_draft_count"], 1)

    def test_all_drafts_returns_400_no_plans(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", state="draft"),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no plans", r.get_json().get("error", ""))

    def test_plan_with_no_state_field_still_syncs(self):
        # Plans written before the `state` field existed must not
        # silently stop syncing on upgrade.
        plan = _plan("p1")
        del plan["state"]
        app_module.save_plans({"version": 1, "plans": [plan]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["plan_count"], 1)
        self.assertEqual(body["skipped_draft_count"], 0)

    def test_draft_plan_not_dropped_from_plans_json(self):
        # Syncing must not delete the draft from storage: only exclude
        # it from THIS sync's zip. Regression guard: an earlier version
        # of the filtering logic wrote the filtered subset back over
        # the full plans list.
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", state="active"),
            _plan("p2", state="draft"),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        stored = app_module.load_plans()["plans"]
        self.assertEqual({p["id"] for p in stored}, {"p1", "p2"})

    def test_synced_plan_gets_last_synced_at_draft_does_not(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", state="active"),
            _plan("p2", state="draft"),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200)
        stored = {p["id"]: p for p in app_module.load_plans()["plans"]}
        self.assertIsNotNone(stored["p1"].get("last_synced_at"))
        self.assertIsNone(stored["p2"].get("last_synced_at"))


class TestSyncDestinationScoping(unittest.TestCase):
    """Optional destination_id scopes the sync to one rig's plans and
    paths; omitting it preserves the legacy all-plans/global-paths
    behaviour so the existing NINA plugin keeps working."""

    def setUp(self):
        _fresh_state()
        _save_gear()
        self.client = app.test_client()

    def _write_two_destinations(self):
        app_module.save_destinations({"version": 1, "destinations": [
            {"id": "home", "label": "Home Rig", "kind": "local_db",
             "ts_db_path": "/fake/home/schedulerdb.sqlite"},
            {"id": "remote", "label": "Remote Rig", "kind": "shared_file",
             "export_path": str(Path(tempfile.mkdtemp()) / "remote-sync.zip")},
        ]})

    def test_no_destination_id_syncs_all_plans_legacy_style(self):
        self._write_two_destinations()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", destination_id="home"),
            _plan("p2", destination_id="remote"),
            _plan("p3"),  # no destination_id at all
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["plan_count"], 3)
        self.assertIsNone(body["destination_id"])
        self.assertTrue(body["download_url"])

    def test_destination_id_scopes_to_matching_plans_only(self):
        self._write_two_destinations()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", destination_id="home"),
            _plan("p2", destination_id="remote"),
        ]})
        r = self.client.post("/api/sync", json={"destination_id": "home"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["plan_count"], 1)
        self.assertEqual(body["destination_id"], "home")

    def test_destination_id_via_query_param(self):
        self._write_two_destinations()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", destination_id="home"),
            _plan("p2", destination_id="remote"),
        ]})
        r = self.client.post("/api/sync?destination_id=remote")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["plan_count"], 1)

    def test_shared_file_destination_writes_to_configured_export_path(self):
        self._write_two_destinations()
        dest = app_module.load_destinations()["destinations"][1]
        export_path = Path(dest["export_path"])
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", destination_id="remote"),
        ]})
        r = self.client.post("/api/sync", json={"destination_id": "remote"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(export_path.exists(),
            f"expected sync to write to destination export_path {export_path}")
        with zipfile.ZipFile(export_path) as zf:
            self.assertIn("projects.json", zf.namelist())

    def test_unmatched_destination_returns_no_plans_error(self):
        self._write_two_destinations()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", destination_id="home"),
        ]})
        r = self.client.post("/api/sync", json={"destination_id": "remote"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no plans", r.get_json().get("error", ""))

    def test_unknown_destination_id_returns_400(self):
        self._write_two_destinations()
        app_module.save_plans({"version": 1, "plans": [_plan("p1")]})
        r = self.client.post("/api/sync", json={"destination_id": "does-not-exist"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("destination_id", r.get_json().get("error", ""))

    def test_destination_scoped_and_draft_excluded_together(self):
        self._write_two_destinations()
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", destination_id="home", state="active"),
            _plan("p2", destination_id="home", state="draft"),
            _plan("p3", destination_id="remote", state="active"),
        ]})
        r = self.client.post("/api/sync", json={"destination_id": "home"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["plan_count"], 1)
        self.assertEqual(body["skipped_draft_count"], 1)


class TestSyncIsMosaic(unittest.TestCase):
    """IsMosaic on the exported TS Project must reflect whether the
    project was actually expanded into multiple panel targets."""

    def setUp(self):
        _fresh_state()
        _save_gear()
        self.client = app.test_client()

    def _sync_and_get_projects(self):
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        zip_path = Path(r.get_json()["zip_path"])
        with zipfile.ZipFile(zip_path) as zf:
            return json.loads(zf.read("projects.json"))

    def test_mosaic_project_flagged_true(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", rows=2, cols=2),
        ]})
        projects = self._sync_and_get_projects()
        self.assertEqual(len(projects), 1)
        self.assertTrue(projects[0]["IsMosaic"])
        self.assertEqual(len(projects[0]["Targets"]), 4)

    def test_single_panel_project_flagged_false(self):
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", rows=1, cols=1),
        ]})
        projects = self._sync_and_get_projects()
        self.assertFalse(projects[0]["IsMosaic"])
        self.assertEqual(len(projects[0]["Targets"]), 1)

    def test_project_state_active_for_non_draft(self):
        app_module.save_plans({"version": 1, "plans": [_plan("p1", state="active")]})
        projects = self._sync_and_get_projects()
        self.assertEqual(projects[0]["State"], "Active")


class TestSyncRaNormalisation(unittest.TestCase):
    """RA is validated to [0, 360) on write, but plans written before that
    validation existed may still carry an out-of-range value. Export
    normalises with % 360 as defence rather than crashing or emitting
    garbage RA/15 into TS."""

    def setUp(self):
        _fresh_state()
        _save_gear()
        self.client = app.test_client()

    def test_negative_ra_normalised_on_export(self):
        # Bypass the validator entirely: simulates a plan written by an
        # older ACP version before RA-range validation existed.
        app_module.save_plans({"version": 1, "plans": [
            _plan("p1", ra=-10.0),
        ]})
        r = self.client.post("/api/sync")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        zip_path = Path(r.get_json()["zip_path"])
        with zipfile.ZipFile(zip_path) as zf:
            projects = json.loads(zf.read("projects.json"))
        ra_hours = projects[0]["Targets"][0]["RA"]
        # -10 % 360 = 350 deg -> 350/15 hours.
        self.assertAlmostEqual(ra_hours, 350.0 / 15.0, places=6)
        self.assertGreaterEqual(ra_hours, 0.0)


class TestGearSensorScalars(unittest.TestCase):
    """The NINA companion plugin expects flat sensor_width_px /
    sensor_height_px fields; ACP's frontend uses the sensor_px pair.
    Both must be present on every camera GET /api/gear returns."""

    def setUp(self):
        _fresh_state()
        self.client = app.test_client()

    def test_get_gear_includes_scalar_sensor_fields(self):
        app_module.save_gear({
            "version": 2,
            "telescopes": [],
            "cameras": [{
                "id": "cam-1", "name": "Test Cam",
                "pixel_size_um": 3.76, "sensor_px": [6248, 4176],
                "filters": {},
            }],
        })
        r = self.client.get("/api/gear")
        self.assertEqual(r.status_code, 200)
        cam = r.get_json()["cameras"][0]
        # sensor_px kept for the frontend...
        self.assertEqual(cam["sensor_px"], [6248, 4176])
        # ...and the scalar forms added for the NINA plugin.
        self.assertEqual(cam["sensor_width_px"], 6248)
        self.assertEqual(cam["sensor_height_px"], 4176)

    def test_camera_missing_sensor_px_has_no_scalar_fields(self):
        app_module.save_gear({
            "version": 2,
            "telescopes": [],
            "cameras": [{"id": "cam-1", "name": "No Sensor", "filters": {}}],
        })
        r = self.client.get("/api/gear")
        cam = r.get_json()["cameras"][0]
        self.assertNotIn("sensor_width_px", cam)
        self.assertNotIn("sensor_height_px", cam)

    def test_seeded_camera_has_scalar_sensor_fields(self):
        app_module.MANIFEST_PATH.write_text(json.dumps({"targets": [{
            "target_id": 1,
            "per_master_fov": [{
                "telescope": "Test Scope", "camera": "Seeded Cam",
                "filter": "Ha", "pixel_size_um": 3.76,
                "sensor_px": [6248, 4176],
                "focal_length_mm": 600, "aperture_mm": 130,
            }],
        }]}), encoding="utf-8")
        app_module._manifest_cache = None
        app_module._manifest_cache_mtime = None
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        gear = app_module.load_gear()
        cam = next(c for c in gear["cameras"] if c["name"] == "Seeded Cam")
        self.assertEqual(cam["sensor_width_px"], 6248)
        self.assertEqual(cam["sensor_height_px"], 4176)


if __name__ == "__main__":
    unittest.main()
