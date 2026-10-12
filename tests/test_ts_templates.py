"""POST /api/ts-templates and the GET fallback to the plugin's stored list.

Spec: docs/specs/goal-panel-and-ts-order.md section 2, test T1.
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

TOKEN = "a-perfectly-ordinary-token"


def _tpl(name, filt="R", exp=180):
    return {"name": name, "filter": filt, "default_exposure_s": exp,
            "gain": 100, "offset": 50, "bin": 1}


class TsTemplatesTest(unittest.TestCase):

    def setUp(self):
        td = Path(tempfile.mkdtemp())
        self._prev = (app_module.TS_TEMPLATES_PATH, app_module.TS_DB_PATH,
                      os.environ.get("ACP_API_TOKEN"))
        app_module.TS_TEMPLATES_PATH = td / "ts_templates.json"
        app_module.TS_DB_PATH = str(td / "no-such-schedulerdb.sqlite")
        os.environ.pop("ACP_API_TOKEN", None)
        self.addCleanup(self._restore)
        self.client = app.test_client()

    def _restore(self):
        app_module.TS_TEMPLATES_PATH, app_module.TS_DB_PATH, tok = self._prev
        if tok is None:
            os.environ.pop("ACP_API_TOKEN", None)
        else:
            os.environ["ACP_API_TOKEN"] = tok

    def _post(self, body, headers=None):
        return self.client.post("/api/ts-templates", json=body, headers=headers or {})

    def test_post_then_get_without_database_returns_plugin_list(self):
        r = self._post({"profile_id": "P", "sent_at": "2026-10-12T10:00:00Z",
                        "templates": [_tpl("R"), _tpl("R Stars", exp=15), _tpl("Ha", "Ha", 300)]})
        self.assertEqual(r.status_code, 200)
        data = self.client.get("/api/ts-templates").get_json()
        self.assertEqual(data["source"], "plugin")
        self.assertTrue(data["available"])
        self.assertEqual([t["name"] for t in data["templates"]], ["R", "R Stars", "Ha"])
        self.assertTrue(all(t["profile_id"] == "P" for t in data["templates"]))
        self.assertTrue(data["profiles"]["P"]["received_at"])
        self.assertEqual(data["templates"][1]["default_exposure_s"], 15)

    def test_second_post_replaces_that_profiles_list_only(self):
        self._post({"profile_id": "P", "templates": [_tpl("R"), _tpl("G", "G")]})
        self._post({"profile_id": "Q", "templates": [_tpl("L", "L")]})
        self._post({"profile_id": "P", "templates": [_tpl("B", "B")]})
        data = self.client.get("/api/ts-templates").get_json()
        by_profile = {}
        for t in data["templates"]:
            by_profile.setdefault(t["profile_id"], []).append(t["name"])
        self.assertEqual(by_profile, {"P": ["B"], "Q": ["L"]})

    def test_no_posted_list_keeps_the_unavailable_answer(self):
        data = self.client.get("/api/ts-templates").get_json()
        self.assertFalse(data["available"])
        self.assertEqual(data["templates"], [])

    def test_post_without_token_is_refused_when_auth_is_on(self):
        os.environ["ACP_API_TOKEN"] = TOKEN
        body = {"profile_id": "P", "templates": [_tpl("R")]}
        self.assertEqual(self._post(body).status_code, 401)
        self.assertEqual(self._post(body, {"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertFalse(app_module.TS_TEMPLATES_PATH.exists())
        ok = self._post(body, {"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(ok.status_code, 200)

    def test_bad_bodies_are_400(self):
        for body in ({}, {"profile_id": "P"}, {"profile_id": "P", "templates": [{"filter": "R"}]},
                     {"profile_id": "", "templates": []},
                     {"profile_id": "P", "templates": [{"name": "R", "gain": "x"}]}):
            self.assertEqual(self._post(body).status_code, 400, body)

    def test_database_wins_when_readable(self):
        import sqlite3
        db = Path(tempfile.mkdtemp()) / "schedulerdb.sqlite"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE exposuretemplate (Id INTEGER, profileId TEXT, name TEXT, "
                  "filtername TEXT, defaultexposure REAL, gain INT, offset INT, bin INT)")
        c.execute("INSERT INTO exposuretemplate VALUES (1,'P','DbR','R',60,0,0,1)")
        c.commit(); c.close()
        app_module.TS_DB_PATH = str(db)
        self._post({"profile_id": "P", "templates": [_tpl("PluginR")]})
        data = self.client.get("/api/ts-templates").get_json()
        self.assertEqual(data["source"], "database")
        self.assertEqual([t["name"] for t in data["templates"]], ["DbR"])


if __name__ == "__main__":
    unittest.main()
