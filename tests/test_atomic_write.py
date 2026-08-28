"""Regression tests for _atomic_write_json and the save_* functions built on it.

A plain write_text() truncates the target file before writing the new
content, so a crash mid-write (OOM kill, power loss, container kill)
leaves a half-written file that json.loads() can't parse — the next
load_* call throws and every endpoint touching that store starts 500ing
until someone manually repairs the file on disk. save_plans /
save_destinations / save_sites / save_gear / save_target_overrides /
save_saved_searches all write through _atomic_write_json (temp file +
os.replace) so a failed write can never corrupt the file that's already
on disk.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402


class TestAtomicWriteJson(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.path = self.td / "store.json"

    def test_writes_valid_json(self):
        app_module._atomic_write_json(self.path, {"a": 1}, indent=2)
        self.assertEqual(json.loads(self.path.read_text()), {"a": 1})

    def test_no_leftover_temp_files_on_success(self):
        app_module._atomic_write_json(self.path, {"a": 1}, indent=2)
        leftovers = list(self.td.glob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_existing_file_untouched_when_dumps_fails(self):
        self.path.write_text(json.dumps({"a": 1}))
        with self.assertRaises(TypeError):
            # A set isn't JSON-serialisable — dumps() raises before any
            # file is touched.
            app_module._atomic_write_json(self.path, {"a": {1, 2}}, indent=2)
        self.assertEqual(json.loads(self.path.read_text()), {"a": 1})

    def test_existing_file_untouched_when_replace_fails(self):
        self.path.write_text(json.dumps({"a": 1}))
        with mock.patch("app.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                app_module._atomic_write_json(self.path, {"a": 2}, indent=2)
        # The original content survives — no truncated/partial write landed.
        self.assertEqual(json.loads(self.path.read_text()), {"a": 1})
        # The failed temp file was cleaned up, not left behind.
        leftovers = list(self.td.glob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_allow_nan_false_rejects_nonfinite_without_touching_disk(self):
        self.path.write_text(json.dumps({"a": 1}))
        with self.assertRaises(ValueError):
            app_module._atomic_write_json(
                self.path, {"a": float("nan")}, indent=2, allow_nan=False)
        self.assertEqual(json.loads(self.path.read_text()), {"a": 1})


class TestSaveFunctionsUseAtomicWrite(unittest.TestCase):
    """Each save_* wrapper should route through _atomic_write_json so a
    write failure can't leave that store half-written."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())

    def _assert_atomic(self, path_attr, save_fn, good_payload):
        path = self.td / f"{path_attr}.json"
        setattr(app_module, path_attr, path)
        save_fn(good_payload)
        self.assertEqual(json.loads(path.read_text()), good_payload)

        with mock.patch("app.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_fn({"different": "payload"})
        # Original content survives a failed write.
        self.assertEqual(json.loads(path.read_text()), good_payload)

    def test_save_plans_atomic(self):
        self._assert_atomic(
            "PLANS_PATH", app_module.save_plans, {"version": 1, "plans": []})

    def test_save_destinations_atomic(self):
        self._assert_atomic(
            "DESTINATIONS_PATH", app_module.save_destinations,
            {"version": 1, "destinations": []})

    def test_save_sites_atomic(self):
        self._assert_atomic(
            "SITES_PATH", app_module.save_sites, {"version": 1, "sites": []})

    def test_save_gear_atomic(self):
        self._assert_atomic(
            "GEAR_PATH", app_module.save_gear,
            {"version": 1, "telescopes": [], "cameras": []})

    def test_save_target_overrides_atomic(self):
        self._assert_atomic(
            "TARGET_OVERRIDES_PATH", app_module.save_target_overrides,
            {"version": 1, "overrides": {}})

    def test_save_saved_searches_atomic(self):
        self._assert_atomic(
            "SAVED_SEARCHES_PATH", app_module.save_saved_searches,
            {"version": 1, "searches": []})


if __name__ == "__main__":
    unittest.main()
