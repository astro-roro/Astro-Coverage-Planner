"""Classify a master on evidence of an integration, not on file size.

A registered 6248x4176 sub at drizzle 2x is 411 MB, well past the 200 MB line
that used to mean "master". Ninety of them in one folder were counted as masters,
which inflated the target's hours and skipped the pipeline-stage dedup that would
have dropped their whole stage.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402

BIG = 411 * 1024 * 1024
SMALL = 40 * 1024 * 1024
SUB = Path("/a/Sh2-27/registered/Light_FILTER-L_mono/2023-07-12_0000_120.00s_c_r.xisf")


def _meta(**kw):
    m = {"naxis1": 6248, "naxis2": 4176, "imagetyp": "LIGHT", "ncombine": None}
    m.update(kw)
    return m


class TestMasterClassification(unittest.TestCase):
    def test_big_registered_sub_is_not_a_master(self):
        """The regression: size alone used to promote this to a master."""
        self.assertNotEqual(bam.classify_by_header(_meta(), SUB, BIG), "master")

    def test_subframe_count_makes_it_a_master(self):
        self.assertEqual(
            bam.classify_by_header(_meta(ncombine=40), SUB, BIG), "master")

    def test_master_imagetyp_makes_it_a_master(self):
        self.assertEqual(
            bam.classify_by_header(_meta(imagetyp="Master Light"),
                                   Path("/a/Sh2-27/L.xisf"), SMALL), "master")

    def test_master_folder_makes_it_a_master(self):
        self.assertEqual(
            bam.classify_by_header(_meta(), Path("/a/Sh2-27/master/L.xisf"), SMALL),
            "master")

    def test_size_still_promotes_when_the_header_is_silent(self):
        """No IMAGETYP at all leaves size as the only signal there is."""
        self.assertEqual(
            bam.classify_by_header(_meta(imagetyp=None),
                                   Path("/a/Sh2-27/stack.xisf"), BIG), "master")

    def test_a_single_frame_of_one_is_not_a_master(self):
        self.assertNotEqual(
            bam.classify_by_header(_meta(ncombine=1), SUB, BIG), "master")

    def test_calibration_still_wins_over_size(self):
        self.assertEqual(
            bam.classify_by_header(_meta(imagetyp="FLAT"),
                                   Path("/a/flats/flat_001.xisf"), BIG),
            "calibration")

    def test_masterlight_name_still_recognised(self):
        self.assertEqual(
            bam.classify_by_header(
                _meta(), Path("/a/x/masterLight_FILTER-L_mono.xisf"), SMALL),
            "master")


if __name__ == "__main__":
    unittest.main()


class TestLocalNormalizationReference(unittest.TestCase):
    """WBPP writes its local normalization reference into the master folder.

    It is one registered sub, not an integration, but the folder name used to
    make it a master. 154 of them sat in the scan health count of unsolved
    masters on 2026-10-14.
    """
    REF = Path("/a/Calibrated/190MN/Helix/master/"
               "LN_Reference_Light_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-H_mono.xisf")

    def test_ln_reference_in_master_folder_is_not_a_master(self):
        self.assertEqual(bam.classify_by_header(_meta(), self.REF, SMALL), "processed")

    def test_ln_reference_with_a_master_imagetyp_is_still_not_a_master(self):
        self.assertEqual(
            bam.classify_by_header(_meta(imagetyp="Master Light", ncombine=40), self.REF, BIG),
            "processed")

    def test_the_real_master_beside_it_still_is(self):
        real = self.REF.with_name("masterLight_BIN-1_6248x4176_EXPOSURE-300.00s_FILTER-H_mono.xisf")
        self.assertEqual(bam.classify_by_header(_meta(), real, SMALL), "master")
