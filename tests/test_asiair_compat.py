"""Tests for ASIAIR / third-party FITS compatibility.

Covers two fixes:
  1. WCS centre coordinates on downsampled plate-solves (IMAGEW/IMAGEH).
  2. Fallback target-name extraction from filenames when OBJECT is missing.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
import astropy.units as u

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import object_from_filename, read_fits_meta  # noqa: E402


# ---------------------------------------------------------------------------
# object_from_filename
# ---------------------------------------------------------------------------

class TestCatalogNamesExtracted(unittest.TestCase):
    CASES = [
        ("Light_NGC6960_180.0s_Bin1_H_gain200_20210730", "NGC 6960"),
        ("Light_IC1396_300s_Bin1_Ha", "IC 1396"),
        ("Light_M31_60s_Bin1_L_gain100", "M31"),
        ("Light_M42_120s", "M42"),
        ("Light_Sh2-155_300s_Bin1_SII", "Sh2-155"),
        ("Light_Sh2_155_300s", "Sh2-155"),
        ("Light_Abell39_600s", "Abell 39"),
        ("Light_vdB142_300s", "vdB 142"),
        ("Light_LDN1235_600s", "LDN 1235"),
        ("Light_RCW36_120s", "RCW 36"),
        ("ngc6960_test", "NGC 6960"),
        ("Light_ngc7000_300s", "NGC 7000"),
        ("Light_ic5146_300s", "IC 5146"),
    ]

    def test_all(self):
        for stem, expected in self.CASES:
            with self.subTest(stem=stem):
                self.assertEqual(object_from_filename(stem), expected)


class TestNonTargetsReturnNone(unittest.TestCase):
    CASES = [
        "ASI1600MM-Cool_test",
        "M1600_not_messier",
        "dark_frame_300s",
        "flat_R_1s",
        "bias_0001",
    ]

    def test_all(self):
        for stem in self.CASES:
            with self.subTest(stem=stem):
                self.assertIsNone(object_from_filename(stem))

    def test_messier_not_matched_in_camera_model(self):
        """ASI1600MM contains M1600 — must not produce a false Messier match."""
        self.assertIsNone(object_from_filename("Light_ASI1600MM_300s"))


# ---------------------------------------------------------------------------
# WCS centre on downsampled plate-solves
# ---------------------------------------------------------------------------

def _make_fits_with_downsampled_wcs(
    path: Path,
    *,
    naxis1: int = 4656,
    naxis2: int = 3520,
    imagew: int = 1164,
    imageh: int = 880,
    target_ra: float = 311.41,
    target_dec: float = 30.72,
):
    """Create a minimal FITS file whose WCS was solved on a 4x-downsampled frame."""
    pix_scale_deg = 0.002606  # ~9.38 arcsec/px in downsampled space

    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = naxis1
    hdr["NAXIS2"] = naxis2
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = "Light"
    hdr["EXPTIME"] = 180.0
    hdr["FILTER"] = "H"
    hdr["OBJECT"] = ""
    hdr["INSTRUME"] = "ZWO ASI1600MM-Cool"
    hdr["CREATOR"] = "ZWO ASIAIR"
    hdr["FOCALLEN"] = 334
    hdr["XPIXSZ"] = 3.8
    hdr["YPIXSZ"] = 3.8
    hdr["RA"] = target_ra
    hdr["DEC"] = target_dec
    hdr["DATE-OBS"] = "2021-07-30T10:53:29"
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["CRVAL1"] = target_ra
    hdr["CRVAL2"] = target_dec
    hdr["CRPIX1"] = imagew / 2.0
    hdr["CRPIX2"] = imageh / 2.0
    hdr["CD1_1"] = -pix_scale_deg
    hdr["CD1_2"] = 0.0
    hdr["CD2_1"] = 0.0
    hdr["CD2_2"] = pix_scale_deg
    hdr["IMAGEW"] = float(imagew)
    hdr["IMAGEH"] = float(imageh)

    data = np.zeros((naxis2, naxis1), dtype=np.int16)
    hdu = fits.PrimaryHDU(data=data, header=hdr)
    hdu.writeto(path, overwrite=True)


class TestDownsampledWCS(unittest.TestCase):

    def test_uses_imagew_imageh(self):
        """Centre coordinates must use IMAGEW/IMAGEH when they differ from NAXIS."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_NGC6960_180s.fit"
            _make_fits_with_downsampled_wcs(p, target_ra=311.41, target_dec=30.72)
            meta = read_fits_meta(p)

        self.assertTrue(meta["ok"])
        self.assertTrue(meta["has_wcs"])
        result = SkyCoord(meta["ra_deg"], meta["dec_deg"], unit="deg")
        target = SkyCoord(311.41, 30.72, unit="deg")
        sep = result.separation(target)
        self.assertLess(sep.deg, 0.1,
                        f"Separation {sep.deg:.2f} deg — should be < 0.1 deg")

    def test_full_resolution_wcs_unaffected(self):
        """When IMAGEW == NAXIS1, behaviour is unchanged."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_NGC6960_180s.fit"
            _make_fits_with_downsampled_wcs(
                p, naxis1=1164, naxis2=880, imagew=1164, imageh=880,
                target_ra=100.0, target_dec=-20.0,
            )
            meta = read_fits_meta(p)

        self.assertTrue(meta["ok"])
        result = SkyCoord(meta["ra_deg"], meta["dec_deg"], unit="deg")
        target = SkyCoord(100.0, -20.0, unit="deg")
        self.assertLess(result.separation(target).deg, 0.1)

    def test_no_imagew_falls_back_to_naxis(self):
        """Without IMAGEW/IMAGEH, the code uses NAXIS (pre-existing behaviour)."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_M42_120s.fit"
            target_ra, target_dec = 83.82, -5.39
            pix_scale = 206.265 * 3.8 / 334 / 3600

            hdr = fits.Header()
            hdr["NAXIS"] = 2
            hdr["NAXIS1"] = 1164
            hdr["NAXIS2"] = 880
            hdr["BITPIX"] = 16
            hdr["IMAGETYP"] = "Light"
            hdr["EXPTIME"] = 120.0
            hdr["FILTER"] = "L"
            hdr["INSTRUME"] = "ZWO ASI1600MM-Cool"
            hdr["FOCALLEN"] = 334
            hdr["XPIXSZ"] = 3.8
            hdr["CTYPE1"] = "RA---TAN"
            hdr["CTYPE2"] = "DEC--TAN"
            hdr["CRVAL1"] = target_ra
            hdr["CRVAL2"] = target_dec
            hdr["CRPIX1"] = 582.0
            hdr["CRPIX2"] = 440.0
            hdr["CD1_1"] = -pix_scale
            hdr["CD1_2"] = 0.0
            hdr["CD2_1"] = 0.0
            hdr["CD2_2"] = pix_scale
            hdr["DATE-OBS"] = "2021-12-01T22:00:00"

            data = np.zeros((880, 1164), dtype=np.int16)
            hdu = fits.PrimaryHDU(data=data, header=hdr)
            hdu.writeto(p, overwrite=True)

            meta = read_fits_meta(p)

        self.assertTrue(meta["ok"])
        result = SkyCoord(meta["ra_deg"], meta["dec_deg"], unit="deg")
        target = SkyCoord(target_ra, target_dec, unit="deg")
        self.assertLess(result.separation(target).deg, 0.1)


class TestObjectFallback(unittest.TestCase):

    def test_object_from_filename_when_header_empty(self):
        """When OBJECT is empty, the target name is parsed from the filename."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_NGC6960_180.0s_Bin1_H_gain200_20210730.fit"
            hdr = fits.Header()
            hdr["NAXIS"] = 2
            hdr["NAXIS1"] = 100
            hdr["NAXIS2"] = 100
            hdr["BITPIX"] = 16
            hdr["IMAGETYP"] = "Light"
            hdr["EXPTIME"] = 180.0
            hdr["FILTER"] = "H"
            hdr["DATE-OBS"] = "2021-07-30T10:53:29"

            data = np.zeros((100, 100), dtype=np.int16)
            hdu = fits.PrimaryHDU(data=data, header=hdr)
            hdu.writeto(p, overwrite=True)

            meta = read_fits_meta(p)

        self.assertTrue(meta["ok"])
        self.assertEqual(meta["object"], "NGC 6960")

    def test_object_from_header_takes_precedence(self):
        """If OBJECT is present in the header, filename parsing is not used."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_NGC6960_180s.fit"
            hdr = fits.Header()
            hdr["NAXIS"] = 2
            hdr["NAXIS1"] = 100
            hdr["NAXIS2"] = 100
            hdr["BITPIX"] = 16
            hdr["OBJECT"] = "Western Veil"
            hdr["IMAGETYP"] = "Light"
            hdr["EXPTIME"] = 180.0
            hdr["DATE-OBS"] = "2021-07-30T10:53:29"

            data = np.zeros((100, 100), dtype=np.int16)
            hdu = fits.PrimaryHDU(data=data, header=hdr)
            hdu.writeto(p, overwrite=True)

            meta = read_fits_meta(p)

        self.assertEqual(meta["object"], "Western Veil")


if __name__ == "__main__":
    unittest.main()
