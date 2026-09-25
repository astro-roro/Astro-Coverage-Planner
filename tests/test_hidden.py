"""Tests for GET/POST /api/hidden and hidden targets leaving gap finding.

Hidden marks live in their own data/hidden.json, keyed by plan id, project
name and target_id, so they carry across rescans and never touch
plans.json.
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


def _fresh_paths():
    td = Path(tempfile.mkdtemp())
    app_module.HIDDEN_PATH = td / "hidden.json"
    app_module._hidden_cache = None
    app_module._hidden_cache_mtime = None
    app_module.PLANS_PATH = td / "plans.json"
    app_module._plans_cache = None
    app_module._plans_cache_mtime = None
    return td


class TestHiddenRoutes(unittest.TestCase):
    def setUp(self):
        self.td = _fresh_paths()
        self.client = app.test_client()

    def test_empty_when_no_file(self):
        r = self.client.get("/api/hidden")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), {"version": 1, "plans": {}, "projects": {}, "targets": {}})

    def test_hide_and_unhide_each_kind(self):
        for kind, key, stored in (("plans", "m31-l", "m31-l"),
                                  ("projects", "Test shots", "Test shots"),
                                  ("targets", 42, "42"),
                                  ("targets", "43", "43")):
            r = self.client.post("/api/hidden", json={"kind": kind, "key": key, "hidden": True})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
            self.assertIn(stored, r.get_json()[kind])
            on_disk = json.loads(app_module.HIDDEN_PATH.read_text(encoding="utf-8"))
            self.assertIn(stored, on_disk[kind])
            self.assertIn("hidden_at", on_disk[kind][stored])

        r = self.client.post("/api/hidden", json={"kind": "targets", "key": 42, "hidden": False})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("42", r.get_json()["targets"])
        self.assertIn("43", r.get_json()["targets"])

    def test_unhide_of_something_not_hidden_is_fine(self):
        r = self.client.post("/api/hidden", json={"kind": "plans", "key": "nope", "hidden": False})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["plans"], {})

    def test_rejects_bad_payloads(self):
        bad = [
            [],
            {"kind": "stars", "key": "x", "hidden": True},
            {"kind": "plans", "key": "", "hidden": True},
            {"kind": "plans", "key": "   ", "hidden": True},
            {"kind": "plans", "key": "x" * 201, "hidden": True},
            {"kind": "plans", "key": 5, "hidden": True},
            {"kind": "targets", "key": "abc", "hidden": True},
            {"kind": "targets", "key": True, "hidden": True},
            {"kind": "targets", "key": -3, "hidden": True},
            {"kind": "targets", "key": "²", "hidden": True},
            {"kind": "plans", "key": "x", "hidden": "yes"},
            {"kind": "plans", "key": "x"},
        ]
        for body in bad:
            r = self.client.post("/api/hidden", json=body)
            self.assertEqual(r.status_code, 400, body)
        self.assertFalse(app_module.HIDDEN_PATH.exists())

    def test_does_not_touch_plans_json(self):
        app_module.save_plans({"version": 1, "settings_migrated": 1, "plans": [
            {"id": "p1", "project_name": "Tests", "state": "draft"}]})
        before = app_module.PLANS_PATH.read_bytes()
        self.client.post("/api/hidden", json={"kind": "plans", "key": "p1", "hidden": True})
        self.client.post("/api/hidden", json={"kind": "projects", "key": "Tests", "hidden": True})
        self.assertEqual(app_module.PLANS_PATH.read_bytes(), before)

    def test_unreadable_file_means_nothing_hidden(self):
        app_module.HIDDEN_PATH.write_text("{not json", encoding="utf-8")
        r = self.client.get("/api/hidden")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["targets"], {})
        # And the next write replaces it with a good file.
        r = self.client.post("/api/hidden", json={"kind": "targets", "key": 1, "hidden": True})
        self.assertEqual(r.status_code, 200)
        self.assertIn("1", json.loads(app_module.HIDDEN_PATH.read_text(encoding="utf-8"))["targets"])

    def test_write_is_atomic(self):
        # A write goes through the shared temp-file-and-rename helper, so no
        # stray temp file is left beside hidden.json.
        self.client.post("/api/hidden", json={"kind": "targets", "key": 9, "hidden": True})
        leftovers = [p.name for p in self.td.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


@unittest.skipUnless(app_module._MOCPY_AVAILABLE, "mocpy not installed")
class TestHiddenTargetsLeaveGaps(unittest.TestCase):
    def setUp(self):
        self.td = _fresh_paths()
        self.manifest = self.td / "manifest.json"
        box = lambda ra, dec: [[ra - 1, dec - 1], [ra + 1, dec - 1], [ra + 1, dec + 1], [ra - 1, dec + 1]]
        self.manifest.write_text(json.dumps({"targets": [
            {"target_id": 1, "corners_icrs": box(10, 10), "filters": {"Ha": {"total_hours": 2}}},
            {"target_id": 2, "corners_icrs": box(50, -20), "filters": {"Ha": {"total_hours": 3}}},
        ]}), encoding="utf-8")
        self.src = app_module.JsonManifestSource(
            source_id="manifest", label="t", color="", attribution="", enabled_default=True,
            path=self.manifest, hidden_ids=app_module.hidden_target_ids)

    def test_hidden_target_drops_out_of_coverage_moc(self):
        full = self.src.coverage_moc("Ha").sky_fraction
        app.test_client().post("/api/hidden", json={"kind": "targets", "key": 2, "hidden": True})
        part = self.src.coverage_moc("Ha").sky_fraction
        self.assertLess(part, full)
        self.assertGreater(part, 0)
        app.test_client().post("/api/hidden", json={"kind": "targets", "key": 2, "hidden": False})
        self.assertEqual(self.src.coverage_moc("Ha").sky_fraction, full)

    def test_friend_manifest_ignores_hidden_marks(self):
        friend = app_module.JsonManifestSource(
            source_id="f", label="f", color="", attribution="", enabled_default=False,
            path=self.manifest)
        full = friend.coverage_moc("Ha").sky_fraction
        app.test_client().post("/api/hidden", json={"kind": "targets", "key": 2, "hidden": True})
        self.assertEqual(friend.coverage_moc("Ha").sky_fraction, full)


if __name__ == "__main__":
    unittest.main()
