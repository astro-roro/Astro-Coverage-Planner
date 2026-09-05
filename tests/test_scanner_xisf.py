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


class TestXisfFocalBackfillPairsWithNativeGrid(unittest.TestCase):
    """The backfill hole, XISF side: a downsampled solve grid is recorded but the
    CD matrix is absent, so the only scale is the per-native focal-length one.

    The old code copied the focal (per-native) scale into pix_arcsec and paired
    it with the DOWNSAMPLED grid, undersizing FOV by the downsample ratio. The
    canonical resolver must pair the focal scale with native NAXIS -> full size.
    """

    def test_no_backfill_and_native_size_fov(self):
        focal_pix = 206.265 * 3.8 / 334.0  # ~2.347 arcsec/native px
        kw = {
            "FILTER": "H",
            "EXPTIME": 180.0,
            "IMAGETYP": "Light",
            "OBJECT": "NGC 6960",
            "CRVAL1": 311.41,
            "CRVAL2": 30.72,
            # Downsampled solve grid recorded, but NO CD matrix / CDELT.
            "IMAGEW": 1164.0,
            "IMAGEH": 880.0,
            "FOCALLEN": 334.0,
            "XPIXSZ": 3.8,
        }
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf(kw, naxis1=4656, naxis2=3520))}):
            meta = bam.read_xisf_meta(Path("frame.xisf"))
        self.assertTrue(meta["has_wcs"])
        self.assertEqual(meta["wcs_naxis1"], 1164)
        self.assertEqual(meta["wcs_naxis2"], 880)
        # The WCS scale is absent and MUST NOT be backfilled from focal.
        self.assertIsNone(meta["pix_arcsec"])
        self.assertAlmostEqual(meta["pix_arcsec_focal"], focal_pix, places=6)
        self.assertEqual(meta["fov_method"], "focal_length")
        fov, pix, method = bam.compute_fov_from_meta(meta)
        self.assertEqual(method, "focal_length")
        self.assertAlmostEqual(pix, focal_pix, places=6)
        # Full native footprint (~182 arcmin), not the ~46 arcmin the bug gave.
        self.assertAlmostEqual(fov[0], 4656 * focal_pix / 60.0, places=3)
        self.assertGreater(fov[0], 150.0)


class TestXisfOffCenterCrpix(unittest.TestCase):
    """CRVAL is the sky position AT CRPIX, not necessarily the image centre.

    A plate solver is free to put its reference pixel anywhere in the
    frame (astrometry.net in particular doesn't guarantee CRPIX is
    centred). Using CRVAL directly as "the pointing" — as the old code
    did — silently mis-points every target whose solve didn't happen to
    centre CRPIX, by however far off-centre CRPIX was. This reproduces
    that with CRPIX pinned to a corner of the frame, far from centre.
    """

    def test_crval_at_corner_still_resolves_to_frame_centre(self):
        naxis1, naxis2 = 4656, 3520
        pix_scale_deg = 0.002606  # ~9.38 arcsec/px
        # CRPIX at pixel (1,1) (bottom-left corner, FITS 1-indexed): CRVAL
        # is the sky position of that CORNER, not of the frame centre.
        kw = {
            "FILTER": "H",
            "EXPTIME": 180.0,
            "IMAGETYP": "Light",
            "OBJECT": "NGC 6960",
            "CRVAL1": 311.41,
            "CRVAL2": 30.72,
            "CRPIX1": 1.0,
            "CRPIX2": 1.0,
            "CTYPE1": "RA---TAN",
            "CTYPE2": "DEC--TAN",
            "CD1_1": -pix_scale_deg,
            "CD1_2": 0.0,
            "CD2_1": 0.0,
            "CD2_2": pix_scale_deg,
        }
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf(kw, naxis1=naxis1, naxis2=naxis2))}):
            meta = bam.read_xisf_meta(Path("frame.xisf"))

        self.assertTrue(meta["ok"])
        self.assertTrue(meta["has_wcs"])
        # The frame centre is naxis/2 px away from CRPIX (corner) in each
        # axis. Using CRVAL directly (the old bug) would report the
        # corner's sky position unchanged; the fix must move substantially
        # toward the true image-centre coordinate instead. Ground truth
        # computed independently via astropy's own WCS, not by re-deriving
        # the implementation's arithmetic by hand.
        from astropy.coordinates import SkyCoord
        from astropy.wcs import WCS as _WCS
        import astropy.units as u
        w = _WCS(naxis=2)
        w.wcs.crval = [311.41, 30.72]
        w.wcs.crpix = [1.0, 1.0]
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.cd = [[-pix_scale_deg, 0.0], [0.0, pix_scale_deg]]
        expected = w.pixel_to_world(naxis1 / 2.0, naxis2 / 2.0)

        corner = SkyCoord(311.41 * u.deg, 30.72 * u.deg)
        reported = SkyCoord(meta["ra_deg"] * u.deg, meta["dec_deg"] * u.deg)
        half_frame_deg = (naxis1 / 2.0) * pix_scale_deg
        # Must have moved well away from the corner CRVAL (the pre-fix
        # value) — a real image-centre correction, not noise.
        self.assertGreater(corner.separation(reported).deg, half_frame_deg * 0.5)
        # And must match the independently-computed expected centre tightly.
        self.assertLess(expected.separation(reported).arcsec, 1.0)

    def test_crpix_missing_falls_back_to_crval(self):
        """No CRPIX at all (older synthetic/legacy headers): keep the old
        CRVAL-as-centre approximation rather than crashing or dropping
        coords entirely."""
        kw = {
            "FILTER": "H",
            "EXPTIME": 180.0,
            "IMAGETYP": "Light",
            "OBJECT": "NGC 6960",
            "CRVAL1": 311.41,
            "CRVAL2": 30.72,
            "CD1_1": -0.002606,
            "CD1_2": 0.0,
            "CD2_1": 0.0,
            "CD2_2": 0.002606,
        }
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf(kw, naxis1=4656, naxis2=3520))}):
            meta = bam.read_xisf_meta(Path("frame.xisf"))
        self.assertTrue(meta["has_wcs"])
        self.assertAlmostEqual(meta["ra_deg"], 311.41, places=6)
        self.assertAlmostEqual(meta["dec_deg"], 30.72, places=6)


if __name__ == "__main__":
    unittest.main()


def _fake_xisf_with_props(meta_kw: dict, props: dict, *, naxis1: int, naxis2: int):
    """Fake XISF carrying both FITS keywords and XISF properties."""
    class _FakeXISF:
        def __init__(self, path):
            self._path = path

        def get_images_metadata(self):
            return [{
                "geometry": (naxis1, naxis2, 1),
                "FITSKeywords": _fits_keywords(meta_kw),
                "XISFProperties": {k: {"id": k, "value": v} for k, v in props.items()},
            }]
    return _FakeXISF


class TestXisfAstrometricProperties(unittest.TestCase):
    """PixInsight writes its plate solve as XISF properties, not FITS keywords."""

    def test_solution_properties_read_as_solved(self):
        sol = bam.xisf_astrometric_solution({
            "Observation:Center:RA": {"value": 311.41},
            "Observation:Center:Dec": {"value": 30.72},
            "PCL:AstrometricSolution:ProjectionSystem": {"value": "Gnomonic"},
            "PCL:AstrometricSolution:LinearTransformationMatrix": {
                "value": [[-0.000583, 0.0], [0.0, 0.000583]]},
        })
        self.assertTrue(sol["solved"])
        self.assertAlmostEqual(sol["ra_deg"], 311.41, places=3)
        self.assertAlmostEqual(sol["dec_deg"], 30.72, places=3)
        self.assertAlmostEqual(sol["pix_arcsec"], 2.099, places=2)

    def test_centre_without_solution_is_not_solved(self):
        """Observation:Center alone can come from the mount, not a solve."""
        sol = bam.xisf_astrometric_solution({
            "Observation:Center:RA": {"value": 10.0},
            "Observation:Center:Dec": {"value": -20.0},
        })
        self.assertFalse(sol["solved"])
        self.assertAlmostEqual(sol["ra_deg"], 10.0)
        self.assertAlmostEqual(sol["dec_deg"], -20.0)

    def test_no_properties_returns_no_coords(self):
        sol = bam.xisf_astrometric_solution({})
        self.assertIsNone(sol["ra_deg"])
        self.assertFalse(sol["solved"])

    def test_out_of_range_values_rejected(self):
        sol = bam.xisf_astrometric_solution({
            "Observation:Center:RA": {"value": 999.0},
            "Observation:Center:Dec": {"value": 120.0},
        })
        self.assertIsNone(sol["ra_deg"])

    def test_flat_six_element_matrix(self):
        """Some writers flatten the 2x3 transformation into one vector."""
        sol = bam.xisf_astrometric_solution({
            "Observation:Center:RA": {"value": 100.0},
            "Observation:Center:Dec": {"value": 5.0},
            "PCL:AstrometricSolution:LinearTransformationMatrix": {
                "value": [-0.000583, 0.0, 12.0, 0.0, 0.000583, 34.0]},
        })
        self.assertAlmostEqual(sol["pix_arcsec"], 2.099, places=2)

    def test_solved_master_gets_has_wcs(self):
        """End to end: a PixInsight master with no CRVAL still reads as solved."""
        kw = {"FILTER": "L", "EXPTIME": 300.0, "IMAGETYP": "Light",
              "OBJECT": "NGC 7000", "NCOMBINE": 40}
        props = {
            "Observation:Center:RA": 311.41,
            "Observation:Center:Dec": 30.72,
            "PCL:AstrometricSolution:ProjectionSystem": "Gnomonic",
            "PCL:AstrometricSolution:LinearTransformationMatrix":
                [[-0.000583, 0.0], [0.0, 0.000583]],
        }
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf_with_props(kw, props, naxis1=4656, naxis2=3520))}):
            meta = bam.read_xisf_meta(Path("masterLight.xisf"))
        self.assertTrue(meta["ok"])
        self.assertTrue(meta["has_wcs"])
        self.assertAlmostEqual(meta["ra_deg"], 311.41, places=2)
        self.assertAlmostEqual(meta["pix_arcsec"], 2.099, places=2)

    def test_unsolved_centre_keeps_has_wcs_false(self):
        kw = {"FILTER": "L", "EXPTIME": 300.0, "IMAGETYP": "Light",
              "OBJECT": "NGC 7000"}
        props = {"Observation:Center:RA": 311.41, "Observation:Center:Dec": 30.72}
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf_with_props(kw, props, naxis1=4656, naxis2=3520))}):
            meta = bam.read_xisf_meta(Path("masterLight.xisf"))
        self.assertFalse(meta["has_wcs"])
        self.assertAlmostEqual(meta["ra_deg"], 311.41, places=2)

    def test_fits_crval_still_wins_over_properties(self):
        kw = {"FILTER": "L", "EXPTIME": 300.0, "IMAGETYP": "Light",
              "CRVAL1": 50.0, "CRVAL2": 10.0, "CRPIX1": 2328.0, "CRPIX2": 1760.0,
              "CD1_1": -0.000583, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 0.000583}
        props = {"Observation:Center:RA": 311.41, "Observation:Center:Dec": 30.72,
                 "PCL:AstrometricSolution:ProjectionSystem": "Gnomonic"}
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf_with_props(kw, props, naxis1=4656, naxis2=3520))}):
            meta = bam.read_xisf_meta(Path("masterLight.xisf"))
        self.assertTrue(meta["has_wcs"])
        self.assertAlmostEqual(meta["ra_deg"], 50.0, places=1)

    def test_instrument_properties_backfill_focal_and_pixel_size(self):
        """PixInsight keeps focal length in metres and pixel size in microns."""
        kw = {"FILTER": "L", "EXPTIME": 300.0, "IMAGETYP": "Light"}
        props = {"Observation:Center:RA": 311.41, "Observation:Center:Dec": 30.72,
                 "Instrument:Telescope:FocalLength": 0.334,
                 "Instrument:Sensor:XPixelSize": 3.8}
        with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(
                XISF=_fake_xisf_with_props(kw, props, naxis1=4656, naxis2=3520))}):
            meta = bam.read_xisf_meta(Path("masterLight.xisf"))
        self.assertAlmostEqual(meta["focallen"], 334.0, places=1)
        self.assertAlmostEqual(meta["xpixsz"], 3.8, places=2)
        self.assertAlmostEqual(meta["pix_arcsec_focal"], 206.265 * 3.8 / 334.0, places=3)
