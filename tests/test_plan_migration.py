"""Tests for the one-shot draft->active migration in load_plans().

docs/specs/ts-project-settings.md section 1: every plan left as `state:
"draft"` under the old meaning becomes `"active"` the first time ACP
loads plans.json after this version starts, because the NINA plugin
never honoured draft and 46 of Rohan's 47 draft plans were already live
in TS. Guarded by `settings_migrated: 1` on the plans.json document so a
second load changes nothing, and backed up first because it's the one
step here that's hard to undo by hand.
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


def _plan(plan_id, **overrides):
    p = {
        "id": plan_id,
        "project_name": f"Project {plan_id}",
        "target": {"name": plan_id, "center_ra_deg": 100.0, "center_dec_deg": -30.0,
                   "rotation_deg": 0, "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 15}},
    }
    p.update(overrides)
    return p


class TestDraftToActiveMigration(unittest.TestCase):
    def setUp(self):
        self.plans_path = _fresh_plans_path()
        self.client = app.test_client()

    def test_three_drafts_and_one_no_state_load_as_four_active(self):
        self.plans_path.write_text(json.dumps({"version": 1, "plans": [
            _plan("p1", state="draft"),
            _plan("p2", state="draft"),
            _plan("p3", state="draft"),
            _plan("p4"),  # no state field at all: already active, untouched by the rule
        ]}), encoding="utf-8")

        data = app_module.load_plans()
        by_id = {p["id"]: p for p in data["plans"]}
        self.assertEqual(by_id["p1"].get("state"), "active")
        self.assertEqual(by_id["p2"].get("state"), "active")
        self.assertEqual(by_id["p3"].get("state"), "active")
        # p4 had no state at all; the migration only rewrites explicit
        # "draft", so it stays as it was (absent, which reads as active).
        self.assertNotIn("state", by_id["p4"])

    def test_a_backup_file_exists(self):
        self.plans_path.write_text(json.dumps({"version": 1, "plans": [
            _plan("p1", state="draft"),
        ]}), encoding="utf-8")
        app_module.load_plans()
        backups = list(self.plans_path.parent.glob("plans.json.bak-*"))
        self.assertEqual(len(backups), 1, "expected exactly one timestamped backup")
        backed_up = json.loads(backups[0].read_text(encoding="utf-8"))
        self.assertEqual(backed_up["plans"][0]["state"], "draft",
                          "backup must hold the pre-migration content")

    def test_loading_a_second_time_changes_nothing(self):
        self.plans_path.write_text(json.dumps({"version": 1, "plans": [
            _plan("p1", state="draft"),
        ]}), encoding="utf-8")
        app_module.load_plans()
        first = json.loads(self.plans_path.read_text(encoding="utf-8"))
        self.assertEqual(first.get("settings_migrated"), 1)

        # Force a re-read (bypass the mtime cache the way a fresh process
        # start would) and confirm the guard stops a second migration.
        app_module._plans_cache = None
        app_module._plans_cache_mtime = None
        backups_before = list(self.plans_path.parent.glob("plans.json.bak-*"))
        app_module.load_plans()
        second = json.loads(self.plans_path.read_text(encoding="utf-8"))
        self.assertEqual(second, first)
        backups_after = list(self.plans_path.parent.glob("plans.json.bak-*"))
        self.assertEqual(len(backups_after), len(backups_before),
                          "second load must not take another backup")

    def test_migrated_flag_survives_a_later_plan_save(self):
        # Every write path reconstructs the plans.json document; if any of
        # them dropped unrecognised top-level keys, settings_migrated
        # would vanish on the very next save and the migration would
        # wrongly re-fire on a plan a user deliberately left as draft.
        self.plans_path.write_text(json.dumps({"version": 1, "plans": [
            _plan("p1", state="draft"),
        ]}), encoding="utf-8")
        app_module.load_plans()
        r = self.client.post("/api/plans", json={
            "id": "p2", "project_name": "New", "state": "draft",
        })
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        on_disk = json.loads(self.plans_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk.get("settings_migrated"), 1)
        # p2 was created *after* the migration ran, as a deliberate new
        # draft, so it must stay draft rather than being swept up.
        by_id = {p["id"]: p for p in on_disk["plans"]}
        self.assertEqual(by_id["p2"]["state"], "draft")


if __name__ == "__main__":
    unittest.main()
