"""Regression tests for XISF metadata parity with FITS (audit fix 5).

The XISF reader lacked two things the FITS path had: the OBJCTRA/OBJCTDEC
fallback (so pointing-only WBPP XISF never got coords and vanished) and the
IMAGEW/IMAGEH downsample correction (so its footprint could inflate). Both are
ported. read_xisf_meta imports ``xisf`` lazily, so these tests patch the
``xisf.XISF`` class with a fake serving synthetic image metadata, exercising the
parsing without needing a real .xisf file on disk.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def _fits_keywords(d: dict) -> dict:
    """FITSKeywords shape: key -> list of {value, comment}."""
    return {k: [{"value": v, "comment": ""}] for k, v in d.items()}


def _fake_xisf(meta_kw: dict, *, naxis1: int, naxis2: int):
    class _FakeXISF:
        def __init__(self, path):
            self._path = path

        def get_images_metadata(self):
            return [{
                "geometry": (naxis1, naxis2, 1),
                "FITSKeywords": _fits_keywords(meta_kw),
            }]
    return _FakeXISF


class TestXisfObjctraFallback(unittest.TestCase):
    """A WBPP XISF with only OBJCTRA/OBJCTDEC (no CRVAL) still gets coords."""

    def test_pointing_only_populates_coords(self):
        kw = {
            "FILTER": "H",
            "EXPTIME": 300.0,
            "IMAGETYP": "Light",
            "OBJECT": "Pointing Target",
            "OBJCTRA": "20 45 38.00",   # ~311.41 deg
            "OBJCTDEC": "+30 43 00.0",  # ~30.72 deg
            "FOCALLEN": 334.0,
            "XPIXSZ": 3.8,
        }
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf(kw, naxis1=4656, naxis2=3520))}):
            meta = bam.read_xisf_meta(Path("frame.xisf"))
        self.assertTrue(meta["ok"])
        self.assertFalse(meta["has_wcs"])
        self.assertIsNotNone(meta["ra_deg"])
        self.assertIsNotNone(meta["dec_deg"])
        self.assertAlmostEqual(meta["ra_deg"], 311.41, delta=0.2)
        self.assertAlmostEqual(meta["dec_deg"], 30.72, delta=0.2)


class TestXisfDownsampleCorrection(unittest.TestCase):
    """A downsampled CD-matrix solve records the solved grid so FOV is honest."""

    def _read(self, extra):
        kw = {
            "FILTER": "H",
            "EXPTIME": 180.0,
            "IMAGETYP": "Light",
            "OBJECT": "NGC 6960",
            "CRVAL1": 311.41,
            "CRVAL2": 30.72,
            "CD1_1": -0.002606,   # ~9.38 arcsec/downsampled px
            "CD1_2": 0.0,
            "CD2_1": 0.0,
            "CD2_2": 0.002606,
        }
        kw.update(extra)
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf(kw, naxis1=4656, naxis2=3520))}):
            return bam.read_xisf_meta(Path("frame.xisf"))

    def test_records_solved_grid_when_downsampled(self):
        meta = self._read({"IMAGEW": 1164.0, "IMAGEH": 880.0})
        self.assertTrue(meta["has_wcs"])
        self.assertEqual(meta["wcs_naxis1"], 1164)
        self.assertEqual(meta["wcs_naxis2"], 880)
        fov, pix, method = bam.compute_fov_from_meta(meta)
        # Footprint uses the solved grid, not native NAXIS -> not inflated.
        self.assertAlmostEqual(fov[0], 1164 * pix / 60.0, places=3)
        self.assertLess(fov[0], 250.0)

    def test_no_imagew_leaves_native_grid(self):
        meta = self._read({})
        self.assertTrue(meta["has_wcs"])
        self.assertIsNone(meta["wcs_naxis1"])
        fov, pix, method = bam.compute_fov_from_meta(meta)
        self.assertAlmostEqual(fov[0], 4656 * pix / 60.0, places=3)


if __name__ == "__main__":
    unittest.main()
