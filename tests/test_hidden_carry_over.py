"""Hidden marks carry over onto the surviving target when two targets merge
in a rescan (scripts/target_ids.py, TARGET_STORES entry for data/hidden.json).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import target_ids as ti  # noqa: E402


class TestHiddenMarksCarryOverOnMerge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.hidden_path = self.tmp_path / "hidden.json"
        self.store = ti.TargetStore("hidden marks", lambda: self.hidden_path,
                                     "targets", ti._combine_hidden)

    def _write(self, data):
        self.hidden_path.write_text(json.dumps(data))

    def _read(self):
        return json.loads(self.hidden_path.read_text())

    def test_hidden_retired_id_moves_to_survivor(self):
        self._write({"version": 1, "plans": {}, "projects": {},
                     "targets": {"4": {"hidden_at": "2026-09-01T00:00:00+00:00"}}})
        moved = ti.carry_over_merges({4: 9}, [self.store], log=lambda s: None)
        data = self._read()
        self.assertEqual(moved, {"hidden marks": 1})
        self.assertNotIn("4", data["targets"])
        self.assertEqual(data["targets"]["9"],
                         {"hidden_at": "2026-09-01T00:00:00+00:00"})

    def test_both_hidden_stays_one_entry(self):
        self._write({"version": 1, "plans": {}, "projects": {},
                     "targets": {
                         "4": {"hidden_at": "2026-09-01T00:00:00+00:00"},
                         "9": {"hidden_at": "2026-09-10T00:00:00+00:00"},
                     }})
        moved = ti.carry_over_merges({4: 9}, [self.store], log=lambda s: None)
        data = self._read()
        self.assertEqual(moved, {"hidden marks": 1})
        self.assertEqual(len(data["targets"]), 1)
        self.assertNotIn("4", data["targets"])
        self.assertEqual(data["targets"]["9"],
                         {"hidden_at": "2026-09-10T00:00:00+00:00"})

    def test_plans_and_projects_untouched(self):
        self._write({"version": 1,
                     "plans": {"plan-1": {"hidden_at": "2026-09-01T00:00:00+00:00"}},
                     "projects": {"M31": {"hidden_at": "2026-09-02T00:00:00+00:00"}},
                     "targets": {"4": {"hidden_at": "2026-09-01T00:00:00+00:00"}}})
        ti.carry_over_merges({4: 9}, [self.store], log=lambda s: None)
        data = self._read()
        self.assertEqual(data["plans"], {"plan-1": {"hidden_at": "2026-09-01T00:00:00+00:00"}})
        self.assertEqual(data["projects"], {"M31": {"hidden_at": "2026-09-02T00:00:00+00:00"}})

    def test_neither_hidden_is_a_no_op(self):
        self._write({"version": 1, "plans": {}, "projects": {}, "targets": {}})
        moved = ti.carry_over_merges({4: 9}, [self.store], log=lambda s: None)
        data = self._read()
        self.assertEqual(moved, {"hidden marks": 0})
        self.assertEqual(data["targets"], {})

    def test_missing_file_is_a_no_op(self):
        missing = ti.TargetStore("hidden marks", lambda: self.tmp_path / "nope.json",
                                 "targets", ti._combine_hidden)
        moved = ti.carry_over_merges({4: 9}, [missing], log=lambda s: None)
        self.assertEqual(moved, {})
        self.assertFalse((self.tmp_path / "nope.json").exists())


if __name__ == "__main__":
    unittest.main()
