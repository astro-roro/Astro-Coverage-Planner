"""Tests for the destinations API + one-shot plan-backfill migration.

Each test redirects DESTINATIONS_PATH + PLANS_PATH to a temp file so
the suite never touches the user's real data.
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


def _fresh_temp_paths():
    """Redirect every path the destinations + plans code touches to a
    fresh temp dir so each test starts from a clean slate."""
    td = Path(tempfile.mkdtemp())
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    app_module.PLANS_PATH = td / "plans.json"
    app_module._destinations_cache = None
    app_module._destinations_cache_mtime = None
    app_module._plans_cache = None
    app_module._plans_cache_mtime = None
    return td


class TestDestinationsEndpoint(unittest.TestCase):
    def setUp(self):
        _fresh_temp_paths()
        self.client = app.test_client()

    def test_get_returns_empty_when_no_file(self):
        r = self.client.get("/api/destinations")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body, {"version": 1, "destinations": []})

    def test_post_then_get_round_trips_shared_file(self):
        payload = {"destinations": [{
            "id": "victoria",
            "label": "Remote Victoria Observatory",
            "kind": "shared_file",
            "export_path": "/mnt/singularity/acp-sync/victoria/pending.json",
            "acquired_path": "/mnt/singularity/acp-sync/victoria/acquired.json",
            "notes": "110mm + 6200MM",
        }]}
        r = self.client.post("/api/destinations", json=payload)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        r = self.client.get("/api/destinations")
        body = r.get_json()
        self.assertEqual(len(body["destinations"]), 1)
        d = body["destinations"][0]
        self.assertEqual(d["id"], "victoria")
        self.assertEqual(d["kind"], "shared_file")
        self.assertEqual(d["export_path"], "/mnt/singularity/acp-sync/victoria/pending.json")
        self.assertEqual(d["acquired_path"], "/mnt/singularity/acp-sync/victoria/acquired.json")
        self.assertEqual(d["notes"], "110mm + 6200MM")

    def test_post_local_db_destination(self):
        payload = {"destinations": [{
            "id": "workstation",
            "label": "Workstation NINA",
            "kind": "local_db",
            "ts_db_path": "C:/Users/me/AppData/Local/NINA/SchedulerPlugin/schedulerdb.sqlite",
        }]}
        r = self.client.post("/api/destinations", json=payload)
        self.assertEqual(r.status_code, 200)
        body = self.client.get("/api/destinations").get_json()
        d = body["destinations"][0]
        self.assertEqual(d["kind"], "local_db")
        self.assertEqual(d["ts_db_path"], "C:/Users/me/AppData/Local/NINA/SchedulerPlugin/schedulerdb.sqlite")
        # Make sure the shared_file-only fields aren't accidentally
        # carried through on a local_db destination.
        self.assertNotIn("export_path", d)
        self.assertNotIn("acquired_path", d)

    def test_post_rejects_unknown_kind(self):
        r = self.client.post("/api/destinations", json={"destinations": [{
            "id": "x", "label": "X", "kind": "magic_pipe",
        }]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("kind", r.get_json().get("error", ""))

    def test_post_rejects_shared_file_without_export_path(self):
        r = self.client.post("/api/destinations", json={"destinations": [{
            "id": "x", "label": "X", "kind": "shared_file",
        }]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("export_path", r.get_json().get("error", ""))

    def test_post_rejects_local_db_without_ts_db_path(self):
        r = self.client.post("/api/destinations", json={"destinations": [{
            "id": "x", "label": "X", "kind": "local_db",
        }]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("ts_db_path", r.get_json().get("error", ""))

    def test_post_rejects_duplicate_ids(self):
        r = self.client.post("/api/destinations", json={"destinations": [
            {"id": "a", "label": "A", "kind": "shared_file", "export_path": "/x"},
            {"id": "a", "label": "B", "kind": "shared_file", "export_path": "/y"},
        ]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("duplicate", r.get_json().get("error", ""))

    def test_empty_list_is_valid(self):
        # Allows the user to clear all destinations and revert to the
        # legacy single-DB nina_ts_sync flow.
        r = self.client.post("/api/destinations", json={"destinations": []})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/destinations").get_json()["destinations"], [])


class TestPlanBackfillMigration(unittest.TestCase):
    """When destinations.json appears (with ≥1 entry) and existing
    plans lack a destination_id, the one-shot migration assigns them
    to the first destination and sets backfilled_at on destinations.json
    so the migration never re-runs.
    """

    def setUp(self):
        _fresh_temp_paths()
        self.client = app.test_client()

    def _write_plans(self, plans):
        app_module.save_plans({"version": 1, "plans": plans})

    def _write_dests(self, dests, backfilled_at=None):
        doc = {"version": 1, "destinations": dests}
        if backfilled_at:
            doc["backfilled_at"] = backfilled_at
        app_module.save_destinations(doc)

    def test_backfill_assigns_unassigned_plans_to_first_destination(self):
        self._write_plans([
            {"id": "p1", "guid": "g1"},
            {"id": "p2", "guid": "g2", "destination_id": "manual"},
        ])
        self._write_dests([
            {"id": "victoria", "label": "V", "kind": "shared_file", "export_path": "/x"},
            {"id": "traveling", "label": "T", "kind": "shared_file", "export_path": "/y"},
        ])
        # Trigger load — migration runs lazily on first load_plans call.
        body = self.client.get("/api/plans").get_json()
        plans_by_id = {p["id"]: p for p in body["plans"]}
        self.assertEqual(plans_by_id["p1"]["destination_id"], "victoria")
        # Explicit destination_id must NOT be overridden.
        self.assertEqual(plans_by_id["p2"]["destination_id"], "manual")

    def test_backfill_runs_only_once(self):
        self._write_plans([{"id": "p1"}])
        self._write_dests([
            {"id": "victoria", "label": "V", "kind": "shared_file", "export_path": "/x"},
        ])
        # First load: migrates.
        self.client.get("/api/plans")
        # User explicitly unassigns p1 (e.g. by editing it).
        app_module.save_plans({"version": 1, "plans": [
            {"id": "p1", "destination_id": None},
        ]})
        # Second load: must NOT re-backfill.
        body = self.client.get("/api/plans").get_json()
        self.assertIsNone(body["plans"][0].get("destination_id"))

    def test_no_backfill_when_destinations_empty(self):
        self._write_plans([{"id": "p1"}])
        # destinations.json absent — load_destinations returns empty list.
        body = self.client.get("/api/plans").get_json()
        self.assertNotIn("destination_id", body["plans"][0])

    def test_backfill_sets_backfilled_at_flag_on_destinations(self):
        self._write_plans([{"id": "p1"}])
        self._write_dests([
            {"id": "victoria", "label": "V", "kind": "shared_file", "export_path": "/x"},
        ])
        self.client.get("/api/plans")
        dests = app_module.load_destinations()
        self.assertIn("backfilled_at", dests)
        self.assertTrue(dests["backfilled_at"])


class TestDestinationsPreservesBackfillFlag(unittest.TestCase):
    """POSTing the destinations list (e.g. user adds a second destination
    in the editor) must NOT clobber the existing backfilled_at flag —
    otherwise the next load_plans would re-backfill plans the user has
    explicitly unassigned."""

    def setUp(self):
        _fresh_temp_paths()
        self.client = app.test_client()

    def test_post_preserves_backfilled_at(self):
        app_module.save_destinations({
            "version": 1,
            "destinations": [{"id": "v", "label": "V", "kind": "shared_file", "export_path": "/x"}],
            "backfilled_at": "2026-05-17T00:00:00Z",
        })
        self.client.post("/api/destinations", json={"destinations": [
            {"id": "v", "label": "V", "kind": "shared_file", "export_path": "/x"},
            {"id": "t", "label": "T", "kind": "shared_file", "export_path": "/y"},
        ]})
        dests = app_module.load_destinations()
        self.assertEqual(dests["backfilled_at"], "2026-05-17T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
