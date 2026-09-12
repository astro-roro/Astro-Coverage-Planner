"""Stale means the master is behind the frames you kept.

The rule, from the product owner: accepted exceeds integrated by more than the
smaller of 20 percent of integrated and 2.0 hours.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_archive_manifest as bam  # noqa: E402
from hours_fixtures import RIG, _master, _subs  # noqa: E402


def _row(integrated_subs, accepted_subs, exptime=3600.0):
    """One rig: a master of N subs, and M accepted subs of the same length."""
    members = [_master(ncombine=integrated_subs, exptime=exptime,
                       session_root="/S/sess1"),
               _subs(n=accepted_subs, exptime=exptime, bucket="/S/sess1/og",
                     session_root="/S/sess1")]
    return bam.build_filters_data(members)["Ha"]["rigs"][RIG]


class TestTheConstants(unittest.TestCase):
    def test_the_tolerance_is_twenty_percent(self):
        self.assertEqual(bam.STALE_RELATIVE_TOLERANCE, 0.20)

    def test_the_cap_is_two_hours(self):
        self.assertEqual(bam.STALE_ABSOLUTE_CAP_HOURS, 2.0)

    def test_the_old_absolute_floor_is_gone(self):
        self.assertFalse(hasattr(bam, "STALE_ABSOLUTE_FLOOR_HOURS"))


class TestTheRelativeTermBinds_OnASmallStack(unittest.TestCase):
    """5 h integrated: 20 percent is 1.0 h, which is smaller than the 2.0 h cap,
    so 1.0 h is the threshold. Removing the min() or widening the percentage
    must fail one of these.
    """

    def test_just_inside_the_band_is_not_stale(self):
        self.assertFalse(_row(5, 6)["stale_master"])

    def test_just_outside_the_band_is_stale(self):
        row = _row(5, 7)
        self.assertTrue(row["stale_master"])
        self.assertAlmostEqual(row["stale_excess_hours"], 2.0, places=4)
        self.assertEqual(row["stale_basis"], "accepted")


class TestTheCapBinds_OnALargeStack(unittest.TestCase):
    """40 h integrated: 20 percent is 8.0 h, so the 2.0 h cap is the smaller
    term and binds instead. A rule that used only the percentage would call
    both of these fine.
    """

    def test_two_hours_over_a_forty_hour_stack_is_not_yet_stale(self):
        self.assertFalse(_row(40, 42)["stale_master"])

    def test_three_hours_over_a_forty_hour_stack_is_stale(self):
        self.assertTrue(_row(40, 43)["stale_master"])

    def test_a_percentage_only_rule_would_have_missed_it(self):
        row = _row(40, 43)
        self.assertLess(row["stale_excess_hours"],
                        row["integrated_hours"] * bam.STALE_RELATIVE_TOLERANCE)


class TestWhatIsNotStale(unittest.TestCase):
    def test_a_keep_rate_is_not_staleness(self):
        row = bam.build_filters_data([
            _master(ncombine=36, session_root="/S/sess1"),
            _subs(n=45, bucket="/S/sess1/og", session_root="/S/sess1"),
            _subs(n=9, bucket="/S/sess1/og/rejected", accepted=False,
                  path="/S/sess1/og/rejected/l.fits", session_root="/S/sess1"),
        ])["Ha"]["rigs"][RIG]
        self.assertAlmostEqual(row["accepted_hours"], row["integrated_hours"],
                               places=6)
        self.assertFalse(row["stale_master"])

    def test_no_master_is_never_stale(self):
        row = bam.build_filters_data([_subs(n=45)])["Ha"]["rigs"][RIG]
        self.assertFalse(row["stale_master"])
        self.assertEqual(row["stale_excess_hours"], 0.0)
        self.assertIsNone(row["stale_basis"])

    def test_a_master_with_no_hours_is_not_stale_it_is_depth_unknown(self):
        row = bam.build_filters_data([
            _master(ncombine=None, exptime=None, session_root="/S/sess1"),
            _subs(n=45, bucket="/S/sess1/og", session_root="/S/sess1"),
        ])["Ha"]["rigs"][RIG]
        self.assertFalse(row["stale_master"])
        self.assertTrue(row["master_depth_unknown"])

    def test_nothing_is_clamped_to_make_the_numbers_agree(self):
        row = _row(5, 7)
        self.assertGreater(row["accepted_hours"], row["integrated_hours"])


class TestBandLevel(unittest.TestCase):
    def test_the_band_is_stale_when_any_rig_is_and_sums_the_excess(self):
        band = bam.build_filters_data([
            _master(ncombine=5, exptime=3600.0, session_root="/S/sess1"),
            _subs(n=7, exptime=3600.0, bucket="/S/sess1/og",
                  session_root="/S/sess1"),
            _master(ncombine=10, exptime=3600.0, scope="RedCat 51",
                    cam="ASI2600MC", path="/b/m.xisf", session_root="/S/sess2"),
            _subs(n=10, exptime=3600.0, scope="RedCat 51", cam="ASI2600MC",
                  bucket="/S/sess2/og", path="/S/sess2/og/l.fits",
                  session_root="/S/sess2"),
        ])["Ha"]
        self.assertTrue(band["stale_master"])
        self.assertAlmostEqual(band["stale_excess_hours"], 2.0, places=4)

    def test_the_band_carries_the_same_flag_set_as_a_rig_row(self):
        band = bam.build_filters_data([_master(ncombine=36), _subs(n=45)])["Ha"]
        for key in ("stale_master", "stale_excess_hours", "stale_basis",
                    "integration_anomaly", "masters_without_subs", "multi_master"):
            self.assertIn(key, band)


if __name__ == "__main__":
    unittest.main()
