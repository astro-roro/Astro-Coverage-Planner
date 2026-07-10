"""Regression tests for FOV on downsampled plate solves (audit fix 2).

``compute_fov_from_meta`` multiplied native NAXIS by a pixel scale that, on a
downsampled solve, is per DOWNSAMPLED pixel, inflating the footprint by the
downsample ratio. The solved grid (IMAGEW/IMAGEH) is now recorded as
``wcs_naxis1/2`` and used with the CD-matrix scale, so the footprint matches the
true field of view.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import compute_fov_from_meta, read_fits_meta  # noqa: E402


def _write_downsampled_solve_no_focal(path: Path, *, naxis1=4656, naxis2=3520,
                                      imagew=1164, imageh=880):
    """Downsampled (4x) CD-matrix solve with NO FOCALLEN/XPIXSZ.

    Without a focal-length cross-check the CD-matrix (downsampled) scale is the
    only pixel scale, so ``compute_fov_from_meta`` takes the CD_matrix path where
    the inflation bug lived. pix_scale ~ 9.38 arcsec per downsampled pixel.
    """
    pix_scale_deg = 0.002606  # ~9.38 arcsec/downsampled px
    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = naxis1
    hdr["NAXIS2"] = naxis2
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = "Light"
    hdr["EXPTIME"] = 180.0
    hdr["FILTER"] = "H"
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["CRVAL1"] = 311.41
    hdr["CRVAL2"] = 30.72
    hdr["CRPIX1"] = imagew / 2.0
    hdr["CRPIX2"] = imageh / 2.0
    hdr["CD1_1"] = -pix_scale_deg
    hdr["CD1_2"] = 0.0
    hdr["CD2_1"] = 0.0
    hdr["CD2_2"] = pix_scale_deg
    hdr["IMAGEW"] = float(imagew)
    hdr["IMAGEH"] = float(imageh)
    fits.PrimaryHDU(data=np.zeros((naxis2, naxis1), dtype=np.int16), header=hdr).writeto(path, overwrite=True)


class TestDownsampledFovNotInflated(unittest.TestCase):

    def test_records_solved_grid(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_NGC6960_180s.fit"
            _write_downsampled_solve_no_focal(p)
            meta = read_fits_meta(p)
        self.assertTrue(meta["ok"] and meta["has_wcs"])
        # The solved grid (IMAGEW/IMAGEH), smaller than NAXIS, must be recorded.
        self.assertEqual(meta["wcs_naxis1"], 1164)
        self.assertEqual(meta["wcs_naxis2"], 880)

    def test_fov_uses_solved_grid_not_native(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_NGC6960_180s.fit"
            _write_downsampled_solve_no_focal(p)
            meta = read_fits_meta(p)
        fov, pix, method = compute_fov_from_meta(meta)
        self.assertEqual(method, "CD_matrix")
        # Correct footprint = solved grid x scale: 1164 * 9.38" / 60 ~ 182 arcmin.
        # The bug produced native 4656 * 9.38" / 60 ~ 728 arcmin (4x inflated).
        self.assertAlmostEqual(fov[0], 1164 * pix / 60.0, places=3)
        self.assertLess(fov[0], 250.0, f"footprint {fov[0]:.0f}' looks inflated")
        self.assertGreater(fov[0], 150.0)

    def test_full_resolution_solve_unchanged(self):
        """When IMAGEW == NAXIS the footprint uses native NAXIS (no regression)."""
        meta = {"naxis1": 1000, "naxis2": 800, "pix_arcsec": 2.0,
                "pix_arcsec_focal": None, "wcs_naxis1": None, "wcs_naxis2": None}
        fov, pix, method = compute_fov_from_meta(meta)
        self.assertEqual(method, "CD_matrix")
        self.assertAlmostEqual(fov[0], 1000 * 2.0 / 60.0, places=6)
        self.assertAlmostEqual(fov[1], 800 * 2.0 / 60.0, places=6)

    def test_focal_method_uses_native_grid(self):
        """Focal-length scale is per native pixel, so it pairs with native NAXIS
        even when a downsampled solve grid was recorded."""
        meta = {"naxis1": 4656, "naxis2": 3520, "pix_arcsec": 9.38,
                "pix_arcsec_focal": 2.35, "wcs_naxis1": 1164, "wcs_naxis2": 880}
        fov, pix, method = compute_fov_from_meta(meta)
        # ratio 9.38/2.35 > 1.5 -> focal override -> native grid + native pix.
        self.assertEqual(method, "focal_length_override")
        self.assertAlmostEqual(pix, 2.35, places=6)
        self.assertAlmostEqual(fov[0], 4656 * 2.35 / 60.0, places=3)


def _write_downsampled_grid_but_wcs_scale_fails(path: Path, *, naxis1=4656,
                                                naxis2=3520, imagew=1164,
                                                imageh=880):
    """Downsampled solve grid recorded, but the WCS pixel scale can't be computed.

    CRVAL1/2 + IMAGEW/IMAGEH are present (so ``wcs_naxis1/2`` get recorded from
    the smaller solve grid), but there is no CTYPE and no CD/CDELT, so the WCS
    operation raises into the blanket except and ``pix_arcsec`` is never set. The
    only pixel scale left is the per-native focal-length one (FOCALLEN+XPIXSZ).
    This is the exact backfill hole: the old code copied the focal (per-native)
    scale into pix_arcsec and then paired it with the DOWNSAMPLED grid,
    undersizing FOV by the downsample ratio.
    """
    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = naxis1
    hdr["NAXIS2"] = naxis2
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = "Light"
    hdr["EXPTIME"] = 180.0
    hdr["FILTER"] = "H"
    hdr["OBJECT"] = "NGC 6960"
    hdr["CRVAL1"] = 311.41
    hdr["CRVAL2"] = 30.72
    hdr["IMAGEW"] = float(imagew)
    hdr["IMAGEH"] = float(imageh)
    hdr["FOCALLEN"] = 334
    hdr["XPIXSZ"] = 3.8  # focal scale = 206.265 * 3.8 / 334 ~ 2.347 arcsec/native px
    fits.PrimaryHDU(data=np.zeros((naxis2, naxis1), dtype=np.int16), header=hdr).writeto(path, overwrite=True)


class TestFocalBackfillPairsWithNativeGrid(unittest.TestCase):
    """The backfill hole: focal (per-native) scale must pair with the native grid.

    When the WCS pixel-scale computation fails but a downsampled solve grid was
    recorded, the only scale left is the focal-length one. It must be paired with
    native NAXIS, so FOV comes out at the full native size, not undersized by the
    downsample ratio.
    """

    def test_no_backfill_and_native_size_fov(self):
        focal_pix = 206.265 * 3.8 / 334.0  # ~2.347 arcsec/native px
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_NGC6960_180s.fit"
            _write_downsampled_grid_but_wcs_scale_fails(p)
            meta = read_fits_meta(p)
        # The downsampled solve grid was recorded...
        self.assertEqual(meta["wcs_naxis1"], 1164)
        self.assertEqual(meta["wcs_naxis2"], 880)
        # ...but the WCS scale is absent and MUST NOT be backfilled from focal.
        self.assertIsNone(meta["pix_arcsec"])
        self.assertAlmostEqual(meta["pix_arcsec_focal"], focal_pix, places=6)
        # Canonical pairing resolves to focal scale + native grid.
        self.assertEqual(meta["fov_method"], "focal_length")
        fov, pix, method = compute_fov_from_meta(meta)
        self.assertEqual(method, "focal_length")
        self.assertAlmostEqual(pix, focal_pix, places=6)
        # Full native footprint: 4656 * 2.347" / 60 ~ 182 arcmin. The bug paired
        # the focal scale with the 1164 grid -> ~46 arcmin (4x undersized).
        self.assertAlmostEqual(fov[0], 4656 * focal_pix / 60.0, places=3)
        self.assertGreater(fov[0], 150.0, f"footprint {fov[0]:.0f}' looks undersized")


if __name__ == "__main__":
    unittest.main()
