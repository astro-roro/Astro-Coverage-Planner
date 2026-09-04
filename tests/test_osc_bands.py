"""Band model for colour cameras and multi band filters (issue #63 follow-up).

A frame's filter name plus whether the sensor has a Bayer matrix decides which
coverage bands it credits. NoFilter on mono is luminance. NoFilter on a colour
camera is labelled OSC and credits R, G and B at once. A dual band filter
credits Ha and OIII. Anything unknown keeps its own name as a band so it is
still shown, just not planned against.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_archive_manifest import (  # noqa: E402
    bands_for,
    build_filters_data,
    filter_label,
    read_fits_meta,
)


class TestBandsFor(unittest.TestCase):
    def test_nofilter_mono_is_l(self):
        self.assertEqual(bands_for("NoFilter", colour=False), ["L"])
        self.assertEqual(filter_label("NoFilter", colour=False), "NoFilter")

    def test_nofilter_colour_is_rgb_labelled_osc(self):
        self.assertEqual(bands_for("NoFilter", colour=True), ["R", "G", "B"])
        self.assertEqual(filter_label("NoFilter", colour=True), "OSC")

    def test_broadband_lp_filter_behaves_like_nofilter(self):
        self.assertEqual(bands_for("L-Pro", colour=True), ["R", "G", "B"])
        self.assertEqual(bands_for("L-Pro", colour=False), ["L"])
        self.assertEqual(filter_label("L-Pro", colour=True), "L-Pro")

    def test_dual_band_credits_ha_and_oiii(self):
        for name in ("L-eXtreme", "L-eNhance", "L-Ultimate", "NBZ", "ALP-T", "SV220"):
            self.assertEqual(bands_for(name, colour=True), ["Ha", "OIII"], name)

    def test_quad_band_adds_sii(self):
        self.assertEqual(bands_for("Quad-Band", colour=True), ["Ha", "OIII", "SII"])

    def test_single_bands_unchanged(self):
        for name in ("Ha", "OIII", "SII", "L", "R", "G", "B"):
            self.assertEqual(bands_for(name, colour=False), [name])

    def test_unknown_filter_keeps_own_name(self):
        self.assertEqual(bands_for("IR", colour=False), ["IR"])
        self.assertEqual(bands_for("Sodium", colour=True), ["Sodium"])

    def test_missing_filter_is_unknown(self):
        self.assertEqual(bands_for(None, colour=False), ["Unknown"])


class TestColourDetection(unittest.TestCase):
    def _write(self, path, bayer):
        hdr = fits.Header()
        hdr["NAXIS"] = 2; hdr["NAXIS1"] = 8; hdr["NAXIS2"] = 8
        hdr["EXPTIME"] = 300.0
        if bayer:
            hdr["BAYERPAT"] = "RGGB"
        fits.PrimaryHDU(data=np.zeros((8, 8), dtype=np.int16), header=hdr).writeto(path, overwrite=True)

    def test_bayerpat_marks_colour(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "osc.fits"
            self._write(p, bayer=True)
            self.assertTrue(read_fits_meta(p)["colour"])

    def test_no_bayerpat_is_mono(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mono.fits"
            self._write(p, bayer=False)
            self.assertFalse(read_fits_meta(p)["colour"])


class TestBuildFiltersData(unittest.TestCase):
    def test_osc_folder_credits_three_bands_and_records_source(self):
        members = [{
            "role": "folder_sub", "path": "x/sample.fits", "filter": "NoFilter",
            "colour": True, "exptime": 300.0, "ncombine": 12,
            "_folder_sub": {"bucket": "x", "n_subs": 12, "exptime": 300.0},
        }]
        fd = build_filters_data(members)
        for band in ("R", "G", "B"):
            self.assertAlmostEqual(fd[band]["total_hours"], 1.0)
            self.assertEqual(fd[band]["sources"], {"OSC": 1.0})
        self.assertNotIn("NoFilter", fd)
        self.assertNotIn("L", fd)

    def test_dual_band_master_credits_ha_oiii(self):
        members = [{
            "role": "master", "path": "x/masterLight.xisf", "filter": "L-eXtreme",
            "colour": True, "exptime": 300.0, "ncombine": 24,
        }]
        fd = build_filters_data(members)
        self.assertAlmostEqual(fd["Ha"]["total_hours"], 2.0)
        self.assertAlmostEqual(fd["OIII"]["total_hours"], 2.0)
        self.assertEqual(fd["Ha"]["sources"], {"L-eXtreme": 2.0})
        self.assertEqual(fd["Ha"]["files"], 1)

    def test_mono_ha_source_is_itself(self):
        members = [{"role": "master", "path": "m.fits", "filter": "Ha",
                    "colour": False, "exptime": 600.0, "ncombine": 6}]
        fd = build_filters_data(members)
        self.assertEqual(fd["Ha"]["sources"], {"Ha": 1.0})


if __name__ == "__main__":
    unittest.main()
