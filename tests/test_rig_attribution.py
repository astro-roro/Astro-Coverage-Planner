"""A master and the subs that fed it belong to one rig, even when the master
lost its headers on the way through PixInsight.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_archive_manifest as bam  # noqa: E402
from hours_fixtures import RIG, _master, _subs  # noqa: E402

H = 300 / 3600.0


class TestGearPoorMasters(unittest.TestCase):
    def test_a_hinted_master_joins_its_own_subs_rig(self):
        m = _master(ncombine=36, scope=None, cam=None, rig=RIG)
        fd = bam.build_filters_data([m, _subs(n=45)])
        self.assertEqual(list(fd["Ha"]["rigs"]), [RIG])
        self.assertAlmostEqual(fd["Ha"]["headline_hours"], 36 * H, places=6)
        self.assertEqual(fd["Ha"]["headline_basis"], "integrated")

    def test_an_unhinted_gear_poor_master_splits_and_the_headline_shows_it(self):
        fd = bam.build_filters_data([_master(ncombine=36, scope=None, cam=None),
                                     _subs(n=45)])
        self.assertEqual(len(fd["Ha"]["rigs"]), 2)
        self.assertEqual(fd["Ha"]["headline_basis"], "mixed")

    def test_a_header_stripped_master_reads_as_degenerate_gear(self):
        m = _master(ncombine=36, scope="ASI2600MM", cam="ASI2600MM", rig=RIG)
        fd = bam.build_filters_data([m, _subs(n=45)])
        self.assertAlmostEqual(fd["Ha"]["total_hours"], 36 * H, places=6)


class TestRigIdentityIsOnlyScopeAndCamera(unittest.TestCase):
    def test_a_reducer_swap_does_not_split_a_rig(self):
        a = _subs(n=20, bucket="/a/og", path="/a/og/x.fits")
        b = _subs(n=20, bucket="/b/og", path="/b/og/x.fits")
        a["focallen"], b["focallen"] = 1000.0, 714.0
        a["xbinning"], b["xbinning"] = 1, 2
        fd = bam.build_filters_data([a, b])
        self.assertEqual(list(fd["Ha"]["rigs"]), [RIG])
        self.assertAlmostEqual(fd["Ha"]["captured_hours"], 40 * H, places=6)

    def test_a_case_or_spacing_difference_does_not_split_a_rig(self):
        fd = bam.build_filters_data([
            _subs(n=10, bucket="/a/og", path="/a/og/x.fits"),
            _subs(n=10, scope="190mn", cam="asi2600mm", bucket="/b/og",
                  path="/b/og/x.fits")])
        self.assertEqual(len(fd["Ha"]["rigs"]), 1)

    def test_a_vendor_prefixed_camera_lands_on_the_same_rig(self):
        fd = bam.build_filters_data([
            _subs(n=10, bucket="/a/og", path="/a/og/x.fits"),
            _subs(n=10, cam=bam.sanitize_camera("ZWO ASI2600MM"), bucket="/b/og",
                  path="/b/og/x.fits")])
        self.assertEqual(len(fd["Ha"]["rigs"]), 1)


class TestAcceptedAtRigLevel(unittest.TestCase):
    def test_a_rejected_folder_only_lowers_its_own_rig(self):
        fd = bam.build_filters_data([
            _subs(n=40, bucket="/a/og", path="/a/og/x.fits"),
            _subs(n=10, bucket="/a/og", path="/a/og/rej.fits", accepted=False),
            _subs(n=30, scope="RedCat 51", cam="ASI2600MC", bucket="/b/og",
                  path="/b/og/x.fits")])
        rows = fd["Ha"]["rigs"]
        self.assertAlmostEqual(rows[RIG]["accepted_hours"], 40 * H, places=6)
        self.assertEqual(rows[RIG]["accepted_basis"], "rejected_folders")
        self.assertEqual(rows["RedCat 51|ASI2600MC"]["accepted_basis"], "no_rejects")

    def test_the_band_basis_reports_that_something_was_rejected(self):
        fd = bam.build_filters_data([
            _subs(n=40, bucket="/a/og", path="/a/og/x.fits"),
            _subs(n=10, bucket="/a/og", path="/a/og/rej.fits", accepted=False)])
        self.assertEqual(fd["Ha"]["accepted_basis"], "rejected_folders")


class TestHoursComeFromTheBlock(unittest.TestCase):
    def test_a_mixed_exposure_block_uses_its_own_total(self):
        s = _subs(n=10, exptime=300.0, total_hours=1.25)
        self.assertAlmostEqual(
            bam.build_filters_data([s])["Ha"]["captured_hours"], 1.25, places=6)


class TestBandLevelAggregates(unittest.TestCase):
    def test_master_depth_unknown_is_any_rig(self):
        fd = bam.build_filters_data([
            _master(ncombine=36),
            _master(ncombine=None, scope="RedCat 51", cam="ASI2600MC",
                    path="/b/m.xisf")])
        self.assertTrue(fd["Ha"]["master_depth_unknown"])

    def test_sources_in_a_mixed_band_credits_each_rig_on_its_own_basis(self):
        fd = bam.build_filters_data([
            _master(ncombine=36),
            _subs(n=24, scope="RedCat 51", cam="ASI2600MC", bucket="/b/og",
                  path="/b/og/l.fits")])
        self.assertAlmostEqual(fd["Ha"]["sources"]["Ha"], (36 + 24) * H, places=6)


if __name__ == "__main__":
    unittest.main()
