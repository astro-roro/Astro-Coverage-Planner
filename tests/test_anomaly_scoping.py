"""A tidied archive is not a broken one, and a threshold nobody tests is a
threshold nobody can retune.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_archive_manifest as bam  # noqa: E402
from hours_fixtures import RIG, _master, _subs  # noqa: E402


class TestDeletedSubsAreNotAnAnomaly(unittest.TestCase):
    def test_nine_nights_deleted_and_one_kept_is_not_an_anomaly(self):
        members = [_master(ncombine=36, path=f"/n{i}/m.xisf", session_root=f"/n{i}")
                   for i in range(10)]
        members.append(_subs(n=45, bucket="/n0/og", path="/n0/og/l.fits",
                             session_root="/n0"))
        row = bam.build_filters_data(members)["Ha"]["rigs"][RIG]
        self.assertTrue(row["masters_without_subs"])
        self.assertFalse(row["integration_anomaly"])
        self.assertTrue(row["multi_master"])

    def test_a_real_miscount_with_every_session_covered_is_still_flagged(self):
        row = bam.build_filters_data([
            _master(ncombine=100, path="/n0/m.xisf", session_root="/n0"),
            _subs(n=10, bucket="/n0/og", path="/n0/og/l.fits", session_root="/n0"),
        ])["Ha"]["rigs"][RIG]
        self.assertFalse(row["masters_without_subs"])
        self.assertTrue(row["integration_anomaly"])

    def test_a_master_with_no_session_at_all_counts_as_subs_missing(self):
        row = bam.build_filters_data([
            _master(ncombine=100, path="/n0/m.xisf"),
            _subs(n=10, bucket="/n1/og", path="/n1/og/l.fits", session_root="/n1"),
        ])["Ha"]["rigs"][RIG]
        self.assertTrue(row["masters_without_subs"])
        self.assertFalse(row["integration_anomaly"])


class TestTheAnomalyThresholdIsLoadBearing(unittest.TestCase):
    """100 captured subs: the rule fires above 100 x 1.05 + 0.1 h / exptime."""

    def _row(self, master_subs):
        return bam.build_filters_data([
            _master(ncombine=master_subs, exptime=3600.0, path="/n0/m.xisf",
                    session_root="/n0"),
            _subs(n=100, exptime=3600.0, bucket="/n0/og", path="/n0/og/l.fits",
                  session_root="/n0"),
        ])["Ha"]["rigs"][RIG]

    def test_the_constants_are_named_not_inline(self):
        self.assertEqual(bam.INTEGRATION_ANOMALY_RELATIVE_TOLERANCE, 0.05)
        self.assertEqual(bam.INTEGRATION_ANOMALY_FLOOR_HOURS, 0.1)

    def test_five_percent_over_is_inside_the_band(self):
        self.assertFalse(self._row(105)["integration_anomaly"])

    def test_six_percent_over_is_outside_it(self):
        self.assertTrue(self._row(106)["integration_anomaly"])

    def test_the_floor_covers_a_tiny_stack(self):
        row = bam.build_filters_data([
            _master(ncombine=1, exptime=300.0, path="/n0/m.xisf",
                    session_root="/n0"),
            _subs(n=1, exptime=300.0, bucket="/n0/og", path="/n0/og/l.fits",
                  session_root="/n0"),
        ])["Ha"]["rigs"][RIG]
        self.assertFalse(row["integration_anomaly"])


class TestThereIsNoAcceptanceAnomaly(unittest.TestCase):
    def test_accepted_can_never_exceed_captured_so_the_flag_is_gone(self):
        row = bam.build_filters_data([
            _subs(n=45, bucket="/a/og"),
            _subs(n=5, bucket="/a/og/rejected", accepted=False,
                  path="/a/og/rejected/l.fits"),
        ])["Ha"]["rigs"][RIG]
        self.assertNotIn("acceptance_anomaly", row)
        self.assertLessEqual(row["accepted_hours"], row["captured_hours"])


class TestDateRange(unittest.TestCase):
    def test_a_multi_night_block_reports_its_real_span(self):
        row = bam.build_filters_data([
            _subs(n=45, date="2026-02-14T22:30:00",
                  first="2026-02-10T21:00:00", last="2026-02-18T23:00:00"),
        ])["Ha"]["rigs"][RIG]
        self.assertEqual(row["first_sub_date"], "2026-02-10")
        self.assertEqual(row["last_sub_date"], "2026-02-18")

    def test_a_block_with_only_one_date_uses_it_for_both_ends(self):
        row = bam.build_filters_data([_subs(n=45, date="2026-02-14T22:30:00")]
                                     )["Ha"]["rigs"][RIG]
        self.assertEqual(row["first_sub_date"], "2026-02-14")
        self.assertEqual(row["last_sub_date"], "2026-02-14")

    def test_a_malformed_date_is_none_not_a_mangled_string(self):
        m = _master(ncombine=36, date="not a date")
        row = bam.build_filters_data([m])["Ha"]["rigs"][RIG]
        self.assertIsNone(row["last_master_date"])


class TestTheProducerCarriesTheSpan(unittest.TestCase):
    """The dates must come off the frames, not only off a hand-built fixture."""

    def test_build_folder_sub_blocks_reports_the_first_and_last_frame(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            paths = [f"{td}/l_{i:04d}.fits" for i in range(3)]
            dates = ["2026-02-18T23:00:00", "2026-02-10T21:00:00",
                     "2026-02-14T22:30:00"]
            meta = {p: {"filter": "Ha", "exptime": 300.0, "date_obs": d,
                        "object": "M31", "telescope": "190MN",
                        "camera": "ASI2600MM", "colour": False,
                        "ra_deg": 10.0, "dec_deg": 41.0, "has_wcs": True,
                        "naxis1": 6248, "naxis2": 4176, "pix_arcsec": 1.0}
                    for p, d in zip(paths, dates)}
            for p in paths:
                Path(p).touch()
            blocks = bam.build_folder_sub_blocks(td, paths, meta)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["first_date_obs"][:10], "2026-02-10")
            self.assertEqual(blocks[0]["last_date_obs"][:10], "2026-02-18")


if __name__ == "__main__":
    unittest.main()
