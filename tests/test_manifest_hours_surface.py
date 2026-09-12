"""What actually reaches the manifest, the API and the sanitiser."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_archive_manifest as bam  # noqa: E402
import sanitise_manifest  # noqa: E402
from hours_fixtures import RIG, _master, _subs  # noqa: E402


class TestEmitIsPureAndRounds(unittest.TestCase):
    def _fd(self):
        return bam.build_filters_data([
            _master(ncombine=7, exptime=317.0, session_root="/S/sess1"),
            _subs(n=7, exptime=317.0, bucket="/S/sess1/og",
                  session_root="/S/sess1"),
        ])

    def test_the_argument_is_not_mutated(self):
        fd = self._fd()
        before = fd["Ha"]["captured_hours"]
        bam.emit_filters_data(fd)
        self.assertEqual(fd["Ha"]["captured_hours"], before)
        self.assertNotEqual(round(before, 2), before)

    def test_every_hours_field_comes_out_at_two_places(self):
        out = bam.emit_filters_data(self._fd())["Ha"]
        for key in ("total_hours", "captured_hours", "accepted_hours",
                    "integrated_hours", "headline_hours", "stale_excess_hours"):
            self.assertEqual(out[key], round(out[key], 2), key)
        for value in out["sources"].values():
            self.assertEqual(value, round(value, 2))
        for row in out["rigs"].values():
            self.assertEqual(row["captured_hours"], round(row["captured_hours"], 2))

    def test_the_band_value_is_the_rounded_unrounded_sum(self):
        fd = self._fd()
        unrounded = fd["Ha"]["captured_hours"]
        self.assertEqual(bam.emit_filters_data(fd)["Ha"]["captured_hours"],
                         round(unrounded, 2))

    def test_the_caps_still_apply(self):
        fd = bam.build_filters_data(
            [_master(ncombine=4, path=f"/A/m{i}.xisf") for i in range(14)])
        self.assertEqual(len(bam.emit_filters_data(fd)["Ha"]["paths"]), 10)

    def test_captured_unattributed_hours_is_seeded(self):
        out = bam.emit_filters_data(self._fd())["Ha"]
        self.assertEqual(out["captured_unattributed_hours"], 0.0)


class TestDbCapturedFloor(unittest.TestCase):
    def _band(self, **over):
        band = {"total_hours": 2.0, "captured_hours": 2.0, "headline_hours": 2.0,
                "headline_basis": "captured", "rigs": {},
                "captured_unattributed_hours": 0.0}
        band.update(over)
        return band

    def test_the_floor_takes_the_larger_of_the_two_views(self):
        band = self._band()
        bam.apply_db_captured_floor(band, 5.0)
        self.assertEqual(band["captured_hours"], 5.0)
        self.assertEqual(band["captured_unattributed_hours"], 3.0)
        self.assertEqual(band["total_hours"], 5.0)

    def test_a_smaller_db_count_changes_nothing(self):
        band = self._band()
        bam.apply_db_captured_floor(band, 1.0)
        self.assertEqual(band["captured_hours"], 2.0)
        self.assertEqual(band["captured_unattributed_hours"], 0.0)

    def test_an_integrated_headline_is_not_moved_by_the_floor(self):
        band = self._band(headline_basis="integrated", headline_hours=3.0,
                          total_hours=3.0)
        bam.apply_db_captured_floor(band, 9.0)
        self.assertEqual(band["headline_hours"], 3.0)
        self.assertEqual(band["captured_hours"], 9.0)

    def test_everything_written_is_rounded_to_two_places(self):
        band = self._band()
        bam.apply_db_captured_floor(band, 5.004999)
        self.assertEqual(band["captured_hours"], 5.0)
        self.assertEqual(band["headline_hours"], 5.0)
        self.assertEqual(band["total_hours"], 5.0)

    def test_rig_rows_never_receive_db_hours(self):
        band = self._band(rigs={RIG: {"captured_hours": 2.0}})
        bam.apply_db_captured_floor(band, 9.0)
        self.assertEqual(band["rigs"][RIG]["captured_hours"], 2.0)

    def test_a_schema_one_band_keeps_the_old_zero_guard(self):
        """Without this a smaller DB count destroys a real master total."""
        band = {"total_hours": 5.0}
        bam.apply_db_captured_floor(band, 1.0)
        self.assertEqual(band["total_hours"], 5.0)

    def test_a_schema_one_band_with_no_hours_still_takes_the_db_figure(self):
        band = {"total_hours": 0.0}
        bam.apply_db_captured_floor(band, 1.0)
        self.assertEqual(band["total_hours"], 1.0)

    def test_a_missing_db_figure_is_harmless(self):
        band = self._band()
        bam.apply_db_captured_floor(band, None)
        self.assertEqual(band["captured_hours"], 2.0)


class TestEndToEnd(unittest.TestCase):
    def test_real_members_reach_the_emitted_band_with_their_rig_intact(self):
        fd = bam.build_filters_data([
            _master(ncombine=36, session_root="/S/sess1"),
            _subs(n=45, bucket="/S/sess1/og", session_root="/S/sess1"),
            _subs(n=5, bucket="/S/sess1/og/rejected", accepted=False,
                  path="/S/sess1/og/rejected/l.fits", session_root="/S/sess1"),
        ])
        band = bam.emit_filters_data(fd)["Ha"]
        self.assertEqual(list(band["rigs"]), [RIG])
        self.assertEqual(band["headline_basis"], "integrated")
        self.assertEqual(band["total_hours"], band["headline_hours"])
        self.assertLess(band["accepted_hours"], band["captured_hours"])


class TestSanitiser(unittest.TestCase):
    def _entry(self):
        return sanitise_manifest._clean_filter_entry(
            {"total_hours": 3.75, "captured_hours": 3.7512, "accepted_hours": 3.2,
             "integrated_hours": 3.0, "headline_basis": "integrated",
             "stale_master": True, "rigs": {RIG: {"captured_hours": 3.75}}})

    def test_the_three_numbers_survive_at_one_decimal(self):
        e = self._entry()
        self.assertEqual(e["captured_hours"], 3.8)
        self.assertEqual(e["integrated_hours"], 3.0)
        self.assertEqual(e["headline_basis"], "integrated")
        self.assertTrue(e["stale_master"])

    def test_gear_identity_never_leaves_through_the_rigs_map(self):
        self.assertNotIn("rigs", self._entry())

    def test_junk_becomes_zero_rather_than_crashing(self):
        e = sanitise_manifest._clean_filter_entry(
            {"total_hours": 1.0, "captured_hours": "lots"})
        self.assertEqual(e["captured_hours"], 0.0)


if __name__ == "__main__":
    unittest.main()
