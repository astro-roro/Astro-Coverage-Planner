"""A stacked master must count its whole integration, not one exposure.

WBPP writes no NCOMBINE onto a master. It records the subframe count in a
HISTORY card as ``ImageIntegration.numberOfImages: N``, so a scanner that only
reads NCOMBINE credits the master a single exposure. An archive kept as masters
alone then under-reports its hours by the stack depth.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


class TestNcombineFromHistory(unittest.TestCase):
    def test_reads_pixinsight_card(self):
        self.assertEqual(
            bam.ncombine_from_history(["ImageIntegration.numberOfImages: 40"]), 40)

    def test_ignores_other_integration_cards(self):
        lines = [
            "Integration with ImageIntegration process",
            "ImageIntegration.pixelCombination: Average",
            "ImageIntegration.totalPixels: 1043665920",
            "ImageIntegration.numberOfImages: 40",
        ]
        self.assertEqual(bam.ncombine_from_history(lines), 40)

    def test_last_run_wins(self):
        """A twice-integrated file carries the later count last."""
        self.assertEqual(bam.ncombine_from_history([
            "ImageIntegration.numberOfImages: 12",
            "ImageIntegration.numberOfImages: 40",
        ]), 40)

    def test_zero_is_not_a_count(self):
        self.assertIsNone(
            bam.ncombine_from_history(["ImageIntegration.numberOfImages: 0"]))

    def test_no_history_returns_none(self):
        self.assertIsNone(bam.ncombine_from_history([]))
        self.assertIsNone(bam.ncombine_from_history(None))

    def test_unrelated_history_returns_none(self):
        self.assertIsNone(bam.ncombine_from_history(
            ["DynamicCrop applied", "HistogramTransformation applied"]))


class TestFitsMasterNcombine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, *, ncombine=None, history=()):
        h = fits.Header()
        h["EXPTIME"] = 120.0
        h["FILTER"] = "L"
        h["IMAGETYP"] = "Master Light"
        if ncombine is not None:
            h["NCOMBINE"] = ncombine
        for line in history:
            h.add_history(line)
        p = self.tmp / name
        fits.PrimaryHDU(data=np.zeros((8, 8), dtype="float32"),
                        header=h).writeto(p, overwrite=True)
        return p

    def test_history_supplies_the_count(self):
        meta = bam.read_fits_meta(
            self._write("m.fits", history=["ImageIntegration.numberOfImages: 40"]))
        self.assertEqual(meta["ncombine"], 40)

    def test_explicit_keyword_wins_over_history(self):
        meta = bam.read_fits_meta(self._write(
            "m2.fits", ncombine=12,
            history=["ImageIntegration.numberOfImages: 40"]))
        self.assertEqual(meta["ncombine"], 12)

    def test_no_count_anywhere_stays_none(self):
        self.assertIsNone(bam.read_fits_meta(self._write("m3.fits"))["ncombine"])

    def test_hours_reflect_the_whole_stack(self):
        meta = bam.read_fits_meta(
            self._write("m4.fits", history=["ImageIntegration.numberOfImages: 40"]))
        meta["path"] = str(self.tmp / "m4.fits")
        hours = bam.build_filters_data([meta])["L"]["total_hours"]
        self.assertAlmostEqual(hours, 40 * 120.0 / 3600.0, places=4)


class TestXisfMasterNcombine(unittest.TestCase):
    def test_history_comment_field_is_read(self):
        """The xisf library leaves a HISTORY value empty and fills the comment."""
        class _FakeXISF:
            def __init__(self, path):
                pass

            def get_images_metadata(self):
                return [{
                    "geometry": (64, 48, 1),
                    "FITSKeywords": {
                        "EXPTIME": [{"value": 120.0, "comment": ""}],
                        "FILTER": [{"value": "L", "comment": ""}],
                        "IMAGETYP": [{"value": "Master Light", "comment": ""}],
                        "HISTORY": [
                            {"value": "", "comment": "Integration with PixInsight"},
                            {"value": "", "comment":
                                "ImageIntegration.numberOfImages: 40"},
                        ],
                    },
                }]

        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(XISF=_FakeXISF)}):
            meta = bam.read_xisf_meta(Path("masterLight.xisf"))
        self.assertEqual(meta["ncombine"], 40)


if __name__ == "__main__":
    unittest.main()
