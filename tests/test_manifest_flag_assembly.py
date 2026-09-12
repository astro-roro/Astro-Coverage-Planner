"""The flags and totals the scanner writes, tested as computed values."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_archive_manifest as bam  # noqa: E402
import sanitise_manifest  # noqa: E402
from app import _without_archive_paths  # noqa: E402


def _target(tid, filters):
    return {"target_id": tid, "objects": ["M31"], "filters": filters}


HA_STALE = {
    "total_hours": 3.0, "captured_hours": 4.0, "accepted_hours": 3.75,
    "integrated_hours": 3.0, "headline_basis": "integrated",
    "stale_master": True, "stale_excess_hours": 0.75,
    "rigs": {"190MN|ASI2600MM": {
        "telescope": "190MN", "camera": "ASI2600MM",
        "captured_hours": 4.0, "accepted_hours": 3.75, "integrated_hours": 3.0,
        "headline_hours": 3.0, "headline_basis": "integrated",
        "stale_master": True, "stale_excess_hours": 0.75, "stale_basis": "accepted",
        "integration_anomaly": False, "masters_without_subs": False,
        "multi_master": False, "master_files": ["/A/M31/Ha/m.xisf"]}},
}

OIII_ANOM = {
    "total_hours": 8.0, "captured_hours": 1.0, "accepted_hours": 1.0,
    "integrated_hours": 8.0, "headline_basis": "integrated",
    "stale_master": False, "stale_excess_hours": 0.0,
    "rigs": {"190MN|ASI2600MM": {
        "telescope": "190MN", "camera": "ASI2600MM",
        "captured_hours": 1.0, "accepted_hours": 1.0, "integrated_hours": 8.0,
        "headline_hours": 8.0, "headline_basis": "integrated",
        "stale_master": False, "stale_excess_hours": 0.0, "stale_basis": None,
        "integration_anomaly": True, "masters_without_subs": False,
        "multi_master": False, "master_files": []}},
}


class TestFlagAssembly(unittest.TestCase):
    def _flags(self):
        return bam.build_integrity_hour_flags(
            [_target(1, {"Ha": HA_STALE}), _target(2, {"OIII": OIII_ANOM})],
            uncoordinated_captured_hours=1.25)

    def test_a_stale_rig_becomes_one_row_naming_its_target_band_and_rig(self):
        rows = self._flags()["stale_masters"]
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["target_id"], r["band"], r["rig"]),
                         (1, "Ha", "190MN|ASI2600MM"))
        self.assertEqual(r["integrated_hours"], 3.0)
        self.assertEqual(r["excess_hours"], 0.75)

    def test_a_stale_row_references_the_accepted_hours_the_rule_compared(self):
        r = self._flags()["stale_masters"][0]
        self.assertEqual(r["reference_hours"], 3.75)
        self.assertEqual(r["basis"], "accepted")

    def test_an_anomaly_row_references_the_captured_hours_it_exceeded(self):
        r = self._flags()["integration_anomalies"][0]
        self.assertEqual(r["target_id"], 2)
        self.assertEqual(r["reference_hours"], 1.0)
        self.assertEqual(r["basis"], "captured")
        self.assertNotEqual(r["reference_hours"], r["integrated_hours"])

    def test_both_row_kinds_share_one_shape(self):
        f = self._flags()
        self.assertEqual(set(f["integration_anomalies"][0]),
                         set(f["stale_masters"][0]))

    def test_uncoordinated_hours_are_carried_through(self):
        self.assertEqual(self._flags()["uncoordinated_captured_hours"], 1.25)

    def test_the_accepted_known_fraction_is_gone(self):
        self.assertNotIn("accepted_known_fraction", self._flags())

    def test_an_archive_with_no_rig_rows_reports_empty_lists(self):
        flags = bam.build_integrity_hour_flags([], uncoordinated_captured_hours=0.0)
        self.assertEqual(flags["stale_masters"], [])
        self.assertEqual(flags["integration_anomalies"], [])


class TestRootTotals(unittest.TestCase):
    def test_the_two_new_totals_are_gross_band_sums(self):
        targets = [_target(1, {"Ha": HA_STALE}), _target(2, {"OIII": OIII_ANOM})]
        self.assertEqual(bam.total_hours_across(targets, "captured_hours"), 5.0)
        self.assertEqual(bam.total_hours_across(targets, "integrated_hours"), 11.0)

    def test_a_v1_band_missing_the_key_contributes_zero_not_a_crash(self):
        self.assertEqual(
            bam.total_hours_across([_target(3, {"Ha": {"total_hours": 2.0}})],
                                   "captured_hours"), 0.0)


class TestRigKeysAreSanitisedLikeValues(unittest.TestCase):
    def test_a_path_shaped_rig_key_is_shortened_on_the_way_out(self):
        payload = {"rigs": {"/sentinel-archive-root/profiles/rig.json|cam": {}}}
        out = _without_archive_paths(payload, ["/sentinel-archive-root"])
        self.assertNotIn("/sentinel-archive-root", json.dumps(out))

    def test_the_sanitiser_refuses_a_path_shaped_key(self):
        with self.assertRaises(RuntimeError):
            sanitise_manifest.validate_no_paths({"Z:/Astro/rig|cam": 1})


if __name__ == "__main__":
    unittest.main()
