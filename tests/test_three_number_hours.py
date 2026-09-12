"""Captured, accepted and integrated are three different questions, answered
per rig. The headline is whichever one the evidence supports.
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


def _band(members, band="Ha"):
    return bam.build_filters_data(members)[band]


class TestTheThreeNumbersStaySeparate(unittest.TestCase):
    def test_a_master_never_counts_as_captured_or_accepted(self):
        b = _band([_master(ncombine=36)])
        self.assertEqual(b["captured_hours"], 0.0)
        self.assertEqual(b["accepted_hours"], 0.0)
        self.assertAlmostEqual(b["integrated_hours"], 36 * H, places=6)

    def test_a_sub_block_never_counts_as_integrated(self):
        b = _band([_subs(n=45)])
        self.assertEqual(b["integrated_hours"], 0.0)
        self.assertAlmostEqual(b["captured_hours"], 45 * H, places=6)

    def test_accepted_equals_captured_when_nothing_was_rejected(self):
        b = _band([_subs(n=45)])
        self.assertAlmostEqual(b["accepted_hours"], b["captured_hours"], places=9)

    def test_a_rejected_block_is_captured_but_not_accepted(self):
        b = _band([_subs(n=40, bucket="/S/sess1/og"),
                   _subs(n=5, bucket="/S/sess1/og", accepted=False,
                         path="/S/sess1/og/rej.fits")])
        self.assertAlmostEqual(b["captured_hours"], 45 * H, places=6)
        self.assertAlmostEqual(b["accepted_hours"], 40 * H, places=6)

    def test_accepted_never_exceeds_captured(self):
        b = _band([_subs(n=45), _master(ncombine=100)])
        self.assertLessEqual(b["accepted_hours"], b["captured_hours"] + 1e-9)


class TestHeadline(unittest.TestCase):
    def test_a_master_with_a_subframe_count_sets_an_integrated_headline(self):
        b = _band([_master(ncombine=36), _subs(n=45)])
        self.assertEqual(b["headline_basis"], "integrated")
        self.assertAlmostEqual(b["headline_hours"], 36 * H, places=6)

    def test_subs_alone_give_a_captured_headline(self):
        b = _band([_subs(n=45)])
        self.assertEqual(b["headline_basis"], "captured")
        self.assertAlmostEqual(b["headline_hours"], 45 * H, places=6)

    def test_a_master_with_no_subframe_count_does_not_claim_integration(self):
        b = _band([_master(ncombine=None), _subs(n=45)])
        self.assertEqual(b["headline_basis"], "captured")
        self.assertAlmostEqual(b["headline_hours"], 45 * H, places=6)
        self.assertTrue(b["master_depth_unknown"])

    def test_a_master_with_no_ncombine_still_counts_one_exposure_and_says_so(self):
        b = _band([_master(ncombine=None)])
        self.assertAlmostEqual(b["integrated_hours"], H, places=6)
        self.assertTrue(b["master_depth_unknown"])

    def test_a_master_with_no_exposure_contributes_nothing_but_is_flagged(self):
        b = _band([_master(ncombine=36, exptime=None), _subs(n=45)])
        self.assertEqual(b["integrated_hours"], 0.0)
        self.assertTrue(b["master_depth_unknown"])
        self.assertEqual(b["headline_basis"], "captured")

    def test_no_evidence_at_all_is_basis_none_and_zero(self):
        b = _band([_master(ncombine=None, exptime=None)])
        self.assertEqual(b["headline_basis"], "none")
        self.assertEqual(b["headline_hours"], 0.0)

    def test_total_hours_is_the_headline(self):
        b = _band([_master(ncombine=36), _subs(n=45)])
        self.assertEqual(b["total_hours"], b["headline_hours"])

    def test_the_band_headline_is_the_sum_across_rigs(self):
        b = _band([_master(ncombine=36),
                   _subs(n=24, scope="RedCat 51", cam="ASI2600MC",
                         bucket="/B/sess1/og", path="/B/sess1/og/l.fits")])
        self.assertEqual(b["headline_basis"], "mixed")
        self.assertAlmostEqual(b["headline_hours"], (36 + 24) * H, places=6)


class TestRigRows(unittest.TestCase):
    def test_rig_rows_carry_their_counts_and_master_files(self):
        row = _band([_master(ncombine=36), _subs(n=45),
                     _subs(n=20, bucket="/S/sess2/og",
                           path="/S/sess2/og/l.fits")])["rigs"][RIG]
        self.assertEqual(row["n_masters"], 1)
        self.assertEqual(row["n_captured_blocks"], 2)
        self.assertEqual(row["n_subs"], 65)
        self.assertEqual(row["master_files"], ["/A/M31/Ha/master_Ha.xisf"])

    def test_master_files_is_capped_at_ten(self):
        ms = [_master(ncombine=4, path=f"/A/m{i}.xisf") for i in range(14)]
        self.assertEqual(len(_band(ms)["rigs"][RIG]["master_files"]), 10)

    def test_an_unknown_rig_renders_its_halves_as_none(self):
        row = _band([_subs(n=10, scope=None, cam=None)])["rigs"]["?|?"]
        self.assertIsNone(row["telescope"])
        self.assertIsNone(row["camera"])

    def test_rigs_are_emitted_in_sorted_order_so_the_manifest_diffs_stably(self):
        b = _band([_subs(n=10, scope="Zeta", bucket="/z/og", path="/z/og/l.fits"),
                   _subs(n=10, scope="Alpha", bucket="/a/og", path="/a/og/l.fits")])
        self.assertEqual(list(b["rigs"]), sorted(b["rigs"]))

    def test_the_smallest_spelling_of_a_grouped_rig_is_the_one_emitted(self):
        b = _band([_subs(n=10, scope="ZWO Scope", bucket="/a/og",
                         path="/a/og/l.fits"),
                   _subs(n=10, scope="zwo scope", bucket="/b/og",
                         path="/b/og/l.fits")])
        self.assertEqual(list(b["rigs"]), ["ZWO Scope|ASI2600MM"])

    def test_a_rig_row_headline_is_never_mixed(self):
        b = _band([_master(ncombine=36), _subs(n=45)])
        for row in b["rigs"].values():
            self.assertIn(row["headline_basis"], ("integrated", "captured", "none"))


class TestMultiBandCredits(unittest.TestCase):
    def test_a_dual_band_master_credits_both_bands_in_full(self):
        fd = bam.build_filters_data([_master(ncombine=36, filt="L-eXtreme")])
        for band in ("Ha", "OIII"):
            self.assertAlmostEqual(fd[band]["integrated_hours"], 36 * H, places=6)

    def test_an_osc_block_credits_r_g_and_b_in_full(self):
        fd = bam.build_filters_data([_subs(n=36, filt=None, colour=True)])
        for band in ("R", "G", "B"):
            self.assertAlmostEqual(fd[band]["captured_hours"], 36 * H, places=6)
            self.assertAlmostEqual(fd[band]["accepted_hours"], 36 * H, places=6)


class TestSourcesAndBucketRows(unittest.TestCase):
    def test_sources_is_left_unrounded_for_the_emit_step(self):
        b = _band([_subs(n=7, exptime=317.0)])
        self.assertAlmostEqual(b["sources"]["Ha"], 7 * 317 / 3600.0, places=9)

    def test_an_unaccepted_block_still_counts_as_a_sub_folder(self):
        b = _band([_subs(n=45), _subs(n=5, accepted=False, bucket="/S/sess1/og",
                                      path="/S/sess1/og/r.fits")])
        self.assertEqual(b["sub_folders"], 2)

    def test_a_neither_flag_block_inflates_nothing(self):
        b = _band([_subs(n=45), _subs(n=99, captured=False, accepted=False,
                                      bucket="/S/sess1/calibrated",
                                      path="/S/sess1/calibrated/l.fits")])
        self.assertEqual(b["sub_folders"], 1)
        self.assertEqual(b["n_subs"], 45)
        self.assertAlmostEqual(b["captured_hours"], 45 * H, places=6)

    def test_the_bucket_row_still_names_its_session_root(self):
        b = _band([_subs(n=45, session_root="/S/sess1")])
        self.assertEqual(b["folder_sub_buckets"][0]["session_root"], "/S/sess1")


if __name__ == "__main__":
    unittest.main()
