"""Tests for exposing whether a Target Scheduler database file exists.

Covers /api/sync/config (the global TS_DB_PATH) and the ts_db_available
flag added to local_db destinations in /api/destinations. ACP running away
from NINA (e.g. in Docker) has no such file at all, so the frontend uses
these to hide the "Sync with NINA" button rather than show a modal that
can only fail.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _fresh_temp_paths():
    td = Path(tempfile.mkdtemp())
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    app_module._destinations_cache = None
    app_module._destinations_cache_mtime = None
    return td


class TestSyncConfigEndpoint(unittest.TestCase):
    def setUp(self):
        self.td = _fresh_temp_paths()
        self.client = app.test_client()

    def test_reports_unavailable_when_ts_db_missing(self):
        app_module.TS_DB_PATH = str(self.td / "no-such-file.sqlite")
        r = self.client.get("/api/sync/config")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertFalse(body["ts_db_available"])
        self.assertEqual(body["ts_db_path"], str(self.td / "no-such-file.sqlite"))

    def test_reports_available_when_ts_db_exists(self):
        db_path = self.td / "schedulerdb.sqlite"
        db_path.write_bytes(b"")
        app_module.TS_DB_PATH = str(db_path)
        r = self.client.get("/api/sync/config")
        body = r.get_json()
        self.assertTrue(body["ts_db_available"])

    def test_expands_env_vars_in_path(self):
        db_path = self.td / "schedulerdb.sqlite"
        db_path.write_bytes(b"")
        import os
        os.environ["ACP_TEST_TS_DIR"] = str(self.td)
        app_module.TS_DB_PATH = "$ACP_TEST_TS_DIR/schedulerdb.sqlite"
        try:
            r = self.client.get("/api/sync/config")
            self.assertTrue(r.get_json()["ts_db_available"])
        finally:
            del os.environ["ACP_TEST_TS_DIR"]


class TestDestinationsTsDbAvailable(unittest.TestCase):
    def setUp(self):
        self.td = _fresh_temp_paths()
        self.client = app.test_client()

    def test_local_db_destination_flags_missing_file(self):
        payload = {"destinations": [{
            "id": "workstation",
            "label": "Workstation NINA",
            "kind": "local_db",
            "ts_db_path": str(self.td / "missing.sqlite"),
        }]}
        self.client.post("/api/destinations", json=payload)
        body = self.client.get("/api/destinations").get_json()
        self.assertFalse(body["destinations"][0]["ts_db_available"])

    def test_local_db_destination_flags_existing_file(self):
        db_path = self.td / "present.sqlite"
        db_path.write_bytes(b"")
        payload = {"destinations": [{
            "id": "workstation",
            "label": "Workstation NINA",
            "kind": "local_db",
            "ts_db_path": str(db_path),
        }]}
        self.client.post("/api/destinations", json=payload)
        body = self.client.get("/api/destinations").get_json()
        self.assertTrue(body["destinations"][0]["ts_db_available"])

    def test_shared_file_destination_has_no_ts_db_available_field(self):
        payload = {"destinations": [{
            "id": "victoria",
            "label": "Remote Victoria Observatory",
            "kind": "shared_file",
            "export_path": str(self.td / "pending.json"),
        }]}
        self.client.post("/api/destinations", json=payload)
        body = self.client.get("/api/destinations").get_json()
        self.assertNotIn("ts_db_available", body["destinations"][0])

    def test_empty_destinations_unaffected(self):
        r = self.client.get("/api/destinations")
        self.assertEqual(r.get_json(), {"version": 1, "destinations": []})


if __name__ == "__main__":
    unittest.main()
