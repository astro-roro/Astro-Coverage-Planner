"""Who may set a destination's export path, and where a sync may write it.

POST /api/destinations stores an export_path and POST /api/sync writes a zip
there. With no token, both must refuse anyone but a local caller. Whoever
calls, the zip may only land inside the exports folder or a folder the owner
listed in export_dirs (the settings file or ACP_EXPORT_DIRS).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

LAN = {"REMOTE_ADDR": "192.0.2.50"}
LAN_HOST = {"Host": "192.0.2.10:5555"}
TOKEN = "export-path-test-token-0123456789"


def _fresh_state() -> Path:
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.ZIP_OUTPUT_DIR = td / "exports"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for cache in ("_plans_cache", "_gear_cache", "_destinations_cache"):
        setattr(app_module, cache, None)
        setattr(app_module, cache + "_mtime", None)
    return td


def _plan(destination_id: str) -> dict:
    return {
        "id": "p1", "guid": "guid-p1", "project_name": "Project p1",
        "target": {"name": "T1", "center_ra_deg": 100.0, "center_dec_deg": -30.0,
                   "rotation_deg": 0.0, "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 15}},
        "telescope_id": "tel-1", "camera_id": "cam-1",
        "filter_goals": {"OIII": {"target_hours": 1.0, "sub_exposure_s": 300}},
        "priority": "normal", "min_altitude_deg": 30, "state": "active",
        "destination_id": destination_id,
    }


def _gear() -> dict:
    return {"version": 2,
            "telescopes": [{"id": "tel-1", "name": "Scope", "focal_length_mm": 600.0}],
            "cameras": [{"id": "cam-1", "name": "Cam", "pixel_size_um": 3.76,
                         "sensor_px": [6248, 4176],
                         "filters": {"OIII": {"ts_template_name": "OIII", "default_sub_s": 300,
                                              "gain": 100, "offset": 50, "bin": 1}}}]}


class _Base(unittest.TestCase):
    def setUp(self):
        self.td = _fresh_state()
        app_module.save_gear(_gear())
        app_module.save_plans({"version": 1, "plans": [_plan("d1")]})
        self.client = app.test_client()
        for var in ("ACP_API_TOKEN", "ACP_EXPORT_DIRS"):
            prev = os.environ.pop(var, None)
            if prev is not None:
                self.addCleanup(os.environ.__setitem__, var, prev)
            else:
                self.addCleanup(os.environ.pop, var, None)

    def _destination(self, export_path: Path):
        app_module.save_destinations({"version": 1, "destinations": [
            {"id": "d1", "label": "Rig", "kind": "shared_file", "export_path": str(export_path)}]})


class TestTokenlessCallerCheck(_Base):
    def test_lan_caller_cannot_set_destinations(self):
        r = self.client.post("/api/destinations", json={"destinations": [
            {"id": "d1", "label": "x", "kind": "shared_file",
             "export_path": str(self.td / "x.zip")}]}, environ_base=LAN)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(app_module.DESTINATIONS_PATH.exists())

    def test_lan_caller_cannot_sync(self):
        self._destination(app_module.ZIP_OUTPUT_DIR / "rig.zip")
        r = self.client.post("/api/sync?destination_id=d1", environ_base=LAN)
        self.assertEqual(r.status_code, 403)
        self.assertFalse((app_module.ZIP_OUTPUT_DIR / "rig.zip").exists())

    def test_local_caller_with_foreign_host_cannot_sync(self):
        r = self.client.post("/api/sync", headers={"Host": "rebound.example"})
        self.assertEqual(r.status_code, 400)

    def test_cross_site_sync_is_refused(self):
        r = self.client.post("/api/sync", headers={"Content-Type": "text/plain",
                                                   "Origin": "https://other.example"})
        self.assertEqual(r.status_code, 403)

    def test_local_caller_can_still_set_destinations_and_sync(self):
        target = app_module.ZIP_OUTPUT_DIR / "rig.zip"
        r = self.client.post("/api/destinations", json={"destinations": [
            {"id": "d1", "label": "Rig", "kind": "shared_file", "export_path": str(target)}]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(target.exists())


class TestWithToken(_Base):
    def setUp(self):
        super().setUp()
        os.environ["ACP_API_TOKEN"] = TOKEN
        self.auth = {**LAN_HOST, "Authorization": "Bearer " + TOKEN}

    def test_plugin_on_the_lan_can_set_destinations_and_sync(self):
        target = app_module.ZIP_OUTPUT_DIR / "rig.zip"
        r = self.client.post("/api/destinations", json={"destinations": [
            {"id": "d1", "label": "Rig", "kind": "shared_file", "export_path": str(target)}]},
            environ_base=LAN, headers=self.auth)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.client.post("/api/sync?destination_id=d1", environ_base=LAN, headers=self.auth)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(target.exists())

    def test_bare_legacy_sync_still_works(self):
        r = self.client.post("/api/sync", environ_base=LAN, headers=self.auth)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

    def test_token_does_not_lift_the_export_folder_limit(self):
        outside = self.td / "outside" / "keep.txt"
        outside.parent.mkdir()
        outside.write_text("keep me")
        self._destination(outside)
        r = self.client.post("/api/sync?destination_id=d1", environ_base=LAN, headers=self.auth)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(outside.read_text(), "keep me")


class TestExportFolderLimit(_Base):
    def test_path_outside_every_export_folder_is_refused(self):
        outside = self.td / "outside" / "keep.txt"
        outside.parent.mkdir()
        outside.write_text("keep me")
        self._destination(outside)
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 400)
        self.assertIn("export_dirs", r.get_json()["error"])
        self.assertEqual(outside.read_text(), "keep me")

    def test_no_folders_are_made_outside(self):
        deep = self.td / "made" / "by" / "sync" / "x.zip"
        self._destination(deep)
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 400)
        self.assertFalse((self.td / "made").exists())

    def test_dot_dot_out_of_the_exports_folder_is_refused(self):
        sneaky = app_module.ZIP_OUTPUT_DIR / ".." / "escaped.zip"
        self._destination(sneaky)
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 400)
        self.assertFalse((self.td / "escaped.zip").exists())

    def test_relative_path_is_refused(self):
        self._destination(Path("relative.zip"))
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 400)

    def test_nested_folder_inside_exports_is_made(self):
        target = app_module.ZIP_OUTPUT_DIR / "rigs" / "a" / "rig.zip"
        self._destination(target)
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(target.exists())

    def test_folder_listed_in_the_environment_is_allowed(self):
        shared = self.td / "shared"
        os.environ["ACP_EXPORT_DIRS"] = str(shared)
        target = shared / "rig.zip"
        self._destination(target)
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(target.exists())

    def test_link_inside_exports_pointing_out_is_refused(self):
        outside = self.td / "outside"
        outside.mkdir()
        app_module.ZIP_OUTPUT_DIR.mkdir(parents=True)
        link = app_module.ZIP_OUTPUT_DIR / "link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("cannot make a directory link here")
        self._destination(link / "rig.zip")
        r = self.client.post("/api/sync?destination_id=d1")
        self.assertEqual(r.status_code, 400)
        self.assertFalse((outside / "rig.zip").exists())

    def test_export_dirs_hidden_from_a_lan_caller(self):
        r = self.client.get("/api/settings", environ_base=LAN)
        self.assertNotIn("value", r.get_json()["keys"]["export_dirs"])


if __name__ == "__main__":
    unittest.main()
