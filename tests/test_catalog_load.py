"""Tests for catalog load failure modes.

load_catalogs() backs every catalogue overlay (Messier, SMGPS, IPHAS,
…) and the /api/catalogs endpoint. A malformed catalogs.json would
crash every catalogue-rendering request — these tests pin down the
graceful-degradation contract introduced 2026-05-17.
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


def _redirect_catalogs():
    td = Path(tempfile.mkdtemp())
    app_module.CATALOGS_PATH = td / "catalogs.json"
    app_module._catalogs_cache = None
    app_module._catalogs_cache_mtime = None
    return app_module.CATALOGS_PATH


class TestLoadCatalogsUnit(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self):
        path = _redirect_catalogs()
        self.assertFalse(path.exists())
        self.assertEqual(app_module.load_catalogs(), {})

    def test_malformed_json_returns_empty_dict_not_raises(self):
        path = _redirect_catalogs()
        path.write_text("{this isn't json", encoding="utf-8")
        # Must NOT raise — /api/catalogs would crash otherwise.
        self.assertEqual(app_module.load_catalogs(), {})

    def test_top_level_not_object_returns_empty(self):
        # fetch_catalogs.py writes a dict; if something corrupted the file
        # to e.g. a top-level list, we shouldn't propagate that shape into
        # downstream consumers.
        path = _redirect_catalogs()
        path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        self.assertEqual(app_module.load_catalogs(), {})

    def test_top_level_null_returns_empty(self):
        path = _redirect_catalogs()
        path.write_text("null", encoding="utf-8")
        self.assertEqual(app_module.load_catalogs(), {})

    def test_empty_file_returns_empty_dict(self):
        path = _redirect_catalogs()
        path.write_text("", encoding="utf-8")
        self.assertEqual(app_module.load_catalogs(), {})

    def test_non_utf8_bytes_returns_empty(self):
        path = _redirect_catalogs()
        path.write_bytes(b"\xff\xfe garbage \x00")
        self.assertEqual(app_module.load_catalogs(), {})

    def test_valid_catalog_loads(self):
        path = _redirect_catalogs()
        payload = {"messier": [
            {"name": "M31", "ra_deg": 10.7, "dec_deg": 41.3},
            {"name": "M42", "ra_deg": 83.8, "dec_deg": -5.4},
        ]}
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = app_module.load_catalogs()
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result["messier"]), 2)

    def test_catalog_with_partially_bad_entries_loads_through(self):
        # Per-entry validation is NOT load_catalogs' job — it just
        # guarantees top-level structure. Consumers handle bad entries.
        path = _redirect_catalogs()
        path.write_text(json.dumps({"messier": [
            {"name": "M31", "ra_deg": 10.7, "dec_deg": 41.3},
            {"name": "Bad — no coords"},   # missing ra/dec
            {"ra_deg": "not a number"},    # bad type
        ]}), encoding="utf-8")
        result = app_module.load_catalogs()
        self.assertEqual(len(result["messier"]), 3)

    def test_cache_invalidates_on_mtime_change(self):
        import os
        import time
        path = _redirect_catalogs()
        path.write_text(json.dumps({"a": []}), encoding="utf-8")
        first = app_module.load_catalogs()
        self.assertEqual(set(first.keys()), {"a"})
        time.sleep(0.05)
        path.write_text(json.dumps({"a": [], "b": []}), encoding="utf-8")
        new_mtime = path.stat().st_mtime + 1
        os.utime(path, (new_mtime, new_mtime))
        second = app_module.load_catalogs()
        self.assertEqual(set(second.keys()), {"a", "b"})


class TestCatalogsEndpointGraceful(unittest.TestCase):
    """The /api/catalogs endpoint must respond even when the source
    file is broken — the frontend renders empty overlays from {} the
    same way it would from no file at all."""

    def setUp(self):
        self.client = app.test_client()

    def test_missing_file_returns_200_empty(self):
        _redirect_catalogs()
        r = self.client.get("/api/catalogs")
        self.assertEqual(r.status_code, 200)
        # Body merges file-loaded + extension-registered sources. With
        # no file present, only extension sources contribute — should
        # be a dict either way (potentially empty).
        self.assertIsInstance(r.get_json(), dict)

    def test_malformed_file_returns_200_empty(self):
        path = _redirect_catalogs()
        path.write_text("{this isn't json", encoding="utf-8")
        r = self.client.get("/api/catalogs")
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.get_json(), dict)


if __name__ == "__main__":
    unittest.main()
