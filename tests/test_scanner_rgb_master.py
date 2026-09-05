"""A plate-solved RGB stack must read as solved (issue #63).

A debayered colour master is a 3-plane image, so ``WCS(header)`` builds a
3-axis projection and ``pixel_to_world(x, y)`` raises for want of a third
coordinate. The scanner swallowed that exception, so every solved colour master
looked unsolved: no footprint, and dropped from clustering entirely.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402

CRVAL1, CRVAL2 = 251.9375, -12.0669
PIX_DEG = 0.000866  # ~3.1 arcsec/px


def _solved_header() -> fits.Header:
    h = fits.Header()
    h["CTYPE1"] = "RA---TAN"
    h["CTYPE2"] = "DEC--TAN"
    h["CRVAL1"] = CRVAL1
    h["CRVAL2"] = CRVAL2
    h["CRPIX1"] = 32.0
    h["CRPIX2"] = 24.0
    h["CD1_1"] = -PIX_DEG
    h["CD1_2"] = 0.0
    h["CD2_1"] = 0.0
    h["CD2_2"] = PIX_DEG
    h["OBJECT"] = "Sh2-27"
    h["FILTER"] = "NoFilter"
    h["EXPTIME"] = 180.0
    h["IMAGETYP"] = "Master Light"
    h["INSTRUME"] = "QHY268C"
    h["FOCALLEN"] = 250.0
    h["XPIXSZ"] = 3.76
    return h


def _write(path: Path, data: np.ndarray) -> Path:
    fits.PrimaryHDU(data=data, header=_solved_header()).writeto(path, overwrite=True)
    return path


class TestRgbMasterWcs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_three_plane_rgb_master_is_solved(self):
        """The regression: a 3-plane stack used to fall through as unsolved."""
        p = _write(self.tmp / "rgb.fits", np.zeros((3, 48, 64), dtype="float32"))
        meta = bam.read_fits_meta(p)
        self.assertTrue(meta["ok"])
        self.assertTrue(meta["has_wcs"], "RGB master must read as plate solved")
        self.assertAlmostEqual(meta["ra_deg"], CRVAL1, delta=0.05)
        self.assertAlmostEqual(meta["dec_deg"], CRVAL2, delta=0.05)
        self.assertAlmostEqual(meta["pix_arcsec"], PIX_DEG * 3600.0, delta=0.05)

    def test_rgb_master_reads_as_colour(self):
        p = _write(self.tmp / "rgb_colour.fits", np.zeros((3, 48, 64), dtype="float32"))
        meta = bam.read_fits_meta(p)
        self.assertTrue(meta["colour"])
        self.assertEqual(meta["naxis1"], 64)
        self.assertEqual(meta["naxis2"], 48)

    def test_mono_master_still_solved(self):
        """Guard the 2-plane path the fix routes through .celestial."""
        p = _write(self.tmp / "mono.fits", np.zeros((48, 64), dtype="float32"))
        meta = bam.read_fits_meta(p)
        self.assertTrue(meta["has_wcs"])
        self.assertAlmostEqual(meta["ra_deg"], CRVAL1, delta=0.05)

    def test_rgb_and_mono_agree_on_pointing(self):
        """Channel count must not shift where the frame points."""
        rgb = bam.read_fits_meta(
            _write(self.tmp / "a.fits", np.zeros((3, 48, 64), dtype="float32")))
        mono = bam.read_fits_meta(
            _write(self.tmp / "b.fits", np.zeros((48, 64), dtype="float32")))
        self.assertAlmostEqual(rgb["ra_deg"], mono["ra_deg"], places=6)
        self.assertAlmostEqual(rgb["dec_deg"], mono["dec_deg"], places=6)
        self.assertAlmostEqual(rgb["pix_arcsec"], mono["pix_arcsec"], places=6)


if __name__ == "__main__":
    unittest.main()
