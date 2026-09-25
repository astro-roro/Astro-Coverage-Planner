"""Tests for PUT /api/projects/<project_name>/settings.

docs/specs/ts-project-settings.md section 1: state, priority and
minimum_time_min apply to every plan sharing a project_name, saved in one
write so a group can never end up half-saved.
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
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    app_module._destinations_cache = None
    app_module._destinations_cache_mtime = None
    return app_module.PLANS_PATH


def _plan(plan_id, project_name, **overrides):
    p = {
        "id": plan_id,
        "project_name": project_name,
        "state": "active",
        "priority": "normal",
        "minimum_time_min": 0,
        "target": {"name": plan_id, "center_ra_deg": 10.68, "center_dec_deg": 41.27,
                   "rotation_deg": 0, "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 15}},
    }
    p.update(overrides)
    return p


class TestProjectSettings(unittest.TestCase):
    def setUp(self):
        self.plans_path = _fresh_plans_path()
        self.client = app.test_client()
        app_module.save_plans({"version": 1, "settings_migrated": 1, "plans": [
            _plan("m31-l", "M31"),
            _plan("m31-ha", "M31"),
            _plan("m31-oiii", "M31"),
            _plan("m42-l", "M42"),
        ]})

    def test_sets_state_on_every_m31_plan_and_no_other(self):
        r = self.client.put("/api/projects/M31/settings", json={"state": "inactive"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["plan_count"], 3)

        stored = {p["id"]: p for p in app_module.load_plans()["plans"]}
        self.assertEqual(stored["m31-l"]["state"], "inactive")
        self.assertEqual(stored["m31-ha"]["state"], "inactive")
        self.assertEqual(stored["m31-oiii"]["state"], "inactive")
        self.assertEqual(stored["m42-l"]["state"], "active")

    def test_unknown_project_returns_404(self):
        r = self.client.put("/api/projects/NGC7000/settings", json={"state": "inactive"})
        self.assertEqual(r.status_code, 404)

    def test_saves_all_three_fields_together(self):
        r = self.client.put("/api/projects/M31/settings", json={
            "state": "draft", "priority": "high", "minimum_time_min": 45,
        })
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        stored = {p["id"]: p for p in app_module.load_plans()["plans"]}
        for pid in ("m31-l", "m31-ha", "m31-oiii"):
            self.assertEqual(stored[pid]["state"], "draft")
            self.assertEqual(stored[pid]["priority"], "high")
            self.assertEqual(stored[pid]["minimum_time_min"], 45)

    def test_omitted_field_is_left_unchanged(self):
        r = self.client.put("/api/projects/M31/settings", json={"priority": "low"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        stored = {p["id"]: p for p in app_module.load_plans()["plans"]}
        self.assertEqual(stored["m31-l"]["priority"], "low")
        self.assertEqual(stored["m31-l"]["state"], "active")  # untouched

    def test_bad_state_rejected(self):
        r = self.client.put("/api/projects/M31/settings", json={"state": "paused"})
        self.assertEqual(r.status_code, 400)
        stored = {p["id"]: p for p in app_module.load_plans()["plans"]}
        self.assertEqual(stored["m31-l"]["state"], "active", "a rejected save must write nothing")

    def test_negative_minimum_time_rejected(self):
        r = self.client.put("/api/projects/M31/settings", json={"minimum_time_min": -5})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
