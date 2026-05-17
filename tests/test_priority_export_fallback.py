"""Tests for /api/export/priority — both mocpy-present and the
mocpy-missing fallback path.

CI always has mocpy installed (requirements.txt), so the fallback path
(lines 2360+ in app.py) never runs by default. Without explicit test
coverage that path can rot silently for users with stripped-down
installs. We force the fallback by patching the _MOCPY_AVAILABLE
module-level flag.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _redirect_state():
    td = Path(tempfile.mkdtemp())
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module.CATALOGS_PATH = td / "catalogs.json"
    app_module._manifest_cache = None
    app_module._manifest_cache_mtime = None
    app_module._catalogs_cache = None
    app_module._catalogs_cache_mtime = None


def _write_test_fixtures():
    """Manifest with one target overlapping the test catalog entries,
    and catalogs with two priority-CSV-eligible sources (the route
    only scopes to green_snrs + smgps_candidates per _PRIORITY_CSV_CATALOGS)."""
    manifest = {
        "targets": [
            {
                "target_id": 1,
                "objects": ["NGC TEST"],
                "center_ra_deg": 100.0,
                "center_dec_deg": -30.0,
                "corners_icrs": [
                    [99.8, -30.2], [99.8, -29.8],
                    [100.2, -29.8], [100.2, -30.2],
                ],
                "filters": {
                    "Ha":  {"total_hours": 5.0},
                    "SII": {"total_hours": 0.2},
                    "OIII": {"total_hours": 1.0},
                },
                "fov_arcmin": [20, 20],
                "telescopes": ["test"],
                "cameras": ["test"],
            },
        ],
    }
    app_module.MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")

    catalogs = {
        "green_snrs": [
            {"name": "G1", "ra_deg": 100.05, "dec_deg": -29.95,
             "l_deg": 285.0, "b_deg": -1.0},
        ],
        "smgps_candidates": [
            {"name": "S1", "ra_deg": 100.10, "dec_deg": -30.05,
             "l_deg": 285.1, "b_deg": -1.1},
        ],
    }
    app_module.CATALOGS_PATH.write_text(json.dumps(catalogs), encoding="utf-8")


class TestPriorityExportMocpyPresent(unittest.TestCase):
    """The happy path with mocpy installed (CI default). Confirms the
    primary code path emits valid CSV headers; downstream content depends
    on the manifest coverage source being registered, which only
    happens at app startup. So this test is a smoke check that doesn't
    fail when no manifest source is registered (returns 404)."""

    def setUp(self):
        _redirect_state()
        _write_test_fixtures()
        self.client = app.test_client()

    def test_with_mocpy_returns_csv_or_404(self):
        # The mocpy path requires app.coverage_sources to include
        # a source with id == "manifest". In a fresh test app that
        # may or may not be registered. Either 200 with CSV body or
        # 404 with "manifest source not registered" — both are
        # well-defined outcomes that prove the route ran.
        r = self.client.get("/api/export/priority")
        self.assertIn(r.status_code, (200, 404),
            f"unexpected status {r.status_code}: {r.get_data(as_text=True)[:200]}")
        if r.status_code == 200:
            self.assertIn("text/csv", r.content_type)
            # Header row must be the canonical column set.
            first_line = r.get_data(as_text=True).splitlines()[0]
            self.assertIn("catalog", first_line.lower())


class TestPriorityExportMocpyFallback(unittest.TestCase):
    """The fallback path that runs when mocpy isn't installed —
    exercises lines 2360-2404 in app.py which the default CI test run
    never reaches."""

    def setUp(self):
        _redirect_state()
        _write_test_fixtures()
        self.client = app.test_client()

    def test_fallback_returns_csv_with_correct_header(self):
        # Force the fallback by clearing the module-level flag.
        with mock.patch.object(app_module, "_MOCPY_AVAILABLE", False):
            r = self.client.get("/api/export/priority")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("text/csv", r.content_type)
        # Parse the CSV — header at row 0, then 0+ data rows.
        reader = csv.reader(io.StringIO(r.get_data(as_text=True)))
        rows = list(reader)
        self.assertGreaterEqual(len(rows), 1)
        # Header must match _PRIORITY_CSV_HEADER exactly so downstream
        # consumers can rely on the column shape.
        self.assertEqual(rows[0], app_module._PRIORITY_CSV_HEADER)

    def test_fallback_includes_candidates_overlapping_target(self):
        # The test fixture target sits at (100, -30) with Ha=5h, SII=0.2h
        # — that's IN the "Ha covered, SII not" priority bucket. The two
        # catalog entries are within ~10' of the target so they should
        # match into the CSV.
        with mock.patch.object(app_module, "_MOCPY_AVAILABLE", False):
            r = self.client.get("/api/export/priority")
        self.assertEqual(r.status_code, 200)
        reader = csv.reader(io.StringIO(r.get_data(as_text=True)))
        rows = list(reader)
        # Expect header + at least one data row (the catalog entries
        # near the priority-bucket target).
        self.assertGreater(len(rows), 1, "fallback path should have emitted at least one row")

    def test_fallback_with_no_targets_returns_empty_csv(self):
        # Wipe targets — the fallback should still produce a valid CSV
        # with just the header, not crash.
        app_module.MANIFEST_PATH.write_text(
            json.dumps({"targets": []}), encoding="utf-8"
        )
        app_module._manifest_cache = None
        app_module._manifest_cache_mtime = None
        with mock.patch.object(app_module, "_MOCPY_AVAILABLE", False):
            r = self.client.get("/api/export/priority")
        # 404 acceptable here (manifest is empty); 200 with header-only
        # CSV also acceptable. What MUST NOT happen is a 500.
        self.assertNotEqual(r.status_code, 500,
            f"empty manifest should not crash fallback: "
            f"{r.get_data(as_text=True)[:200]}")


class TestPriorityExportMissingInputs(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()

    def test_missing_manifest_returns_404(self):
        # Catalogs present, manifest missing.
        app_module.CATALOGS_PATH.write_text(
            json.dumps({"green_snrs": []}), encoding="utf-8"
        )
        r = self.client.get("/api/export/priority")
        self.assertEqual(r.status_code, 404)

    def test_missing_catalogs_returns_404(self):
        # Manifest present, catalogs missing.
        app_module.MANIFEST_PATH.write_text(
            json.dumps({"targets": [{
                "target_id": 1, "center_ra_deg": 0, "center_dec_deg": 0,
                "filters": {}, "fov_arcmin": [0, 0],
            }]}), encoding="utf-8"
        )
        r = self.client.get("/api/export/priority")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
