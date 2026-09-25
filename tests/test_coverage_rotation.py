"""Coverage is drawn at the angle each master was shot at.

Before this, ``corners_icrs`` was always a box square to north, so a frame
turned 40 degrees on the sky was drawn covering sky it never saw and missing
sky it did. The angle now comes from each master's WCS, in the planner's own
convention (position angle of the image +Y axis, degrees east of north, as
``computePlanCorners`` in static/app.js uses), so plans and coverage compare.

Every header here is synthetic. Nothing reads real image data.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))
from build_archive_manifest import (  # noqa: E402
    fov_corners, needs_rotation_reread, read_fits_meta, target_rotation,
    wcs_rotation_deg,
)
from sanitise_manifest import sanitise_dict  # noqa: E402

SCALE = 1.5 / 3600.0  # 1.5 arcsec per pixel


def _cd(theta_deg: float, flipped: bool = False, s: float = SCALE):
    """CD matrix whose +Y axis sits at position angle theta, east of north.

    +Y on the sky is (east, north) = (sin t, cos t). Normal parity puts +X at
    (-cos t, sin t), which is west when t is 0 (east left, north up). A
    mirrored image puts +X the other way.
    """
    t = np.radians(theta_deg)
    x = (np.cos(t), -np.sin(t)) if flipped else (-np.cos(t), np.sin(t))
    return [[s * x[0], s * np.sin(t)], [s * x[1], s * np.cos(t)]]


def _wcs(theta_deg, *, ra=83.8, dec=-5.4, flipped=False, crpix=(500.5, 400.5)):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = list(crpix)
    w.wcs.cd = _cd(theta_deg, flipped)
    return w


def _angle_close(test, got, want, places=2):
    """Compare angles on the circle, so 359.999 and 0 count as equal."""
    d = (got - want + 180.0) % 360.0 - 180.0
    test.assertAlmostEqual(d, 0.0, places=places, msg=f"got {got}, want {want}")


class TestAngleFromWcs(unittest.TestCase):
    """The angle read off a known CD matrix at the image centre."""

    def _at_centre(self, w):
        # crpix is 1-based; pixel_to_world is 0-based. Centre on CRPIX.
        return wcs_rotation_deg(w, w.wcs.crpix[0] - 1, w.wcs.crpix[1] - 1)

    def test_north_up(self):
        _angle_close(self, self._at_centre(_wcs(0)), 0.0)

    def test_quarter_turn(self):
        _angle_close(self, self._at_centre(_wcs(90)), 90.0)

    def test_forty_five(self):
        _angle_close(self, self._at_centre(_wcs(45)), 45.0)

    def test_arbitrary_angle_past_180(self):
        _angle_close(self, self._at_centre(_wcs(237.5)), 237.5)

    def test_flipped_parity_gives_the_same_y_axis_angle(self):
        """A mirrored image moves +X, not +Y, so the angle does not change."""
        for theta in (0, 30, 120):
            _angle_close(self, self._at_centre(_wcs(theta, flipped=True)), theta)

    def test_near_the_pole(self):
        _angle_close(self, self._at_centre(_wcs(30, ra=10.0, dec=89.7)), 30.0)

    def test_across_ra_zero(self):
        for ra in (359.995, 0.002):
            _angle_close(self, self._at_centre(_wcs(60, ra=ra)), 60.0)

    def test_centre_far_from_crpix_near_the_pole_uses_the_centre(self):
        """Near a pole north swings between CRPIX and the image centre.

        A solver that parks CRPIX in a corner describes the frame at that
        corner. The angle has to be the one at the centre of the picture, which
        a flat reading of the CD matrix gets wrong by a visible amount here.
        """
        w = _wcs(0, ra=0.0, dec=88.0, crpix=(1.0, 1.0))
        # 3000 px at 1.5"/px is 1.25 degrees east-west from the pole-ward corner.
        cx, cy = 3000.0, 2000.0
        got = wcs_rotation_deg(w, cx, cy)
        c0 = w.pixel_to_world(cx, cy)
        c1 = w.pixel_to_world(cx, cy + 1.0)
        _angle_close(self, got, c0.position_angle(c1).deg % 360.0)
        self.assertGreater(abs((got + 180.0) % 360.0 - 180.0), 1.0,
                           "near the pole the centre angle differs from CRPIX's")

    def test_range_is_zero_to_360(self):
        a = self._at_centre(_wcs(-20))
        self.assertGreaterEqual(a, 0.0)
        self.assertLess(a, 360.0)
        _angle_close(self, a, 340.0)


def _write_master(path: Path, header_extra: dict, naxis=(1000, 800)):
    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"], hdr["NAXIS2"] = naxis
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = "Master Light"
    hdr["EXPTIME"] = 300.0
    hdr["NCOMBINE"] = 20
    hdr["FILTER"] = "H"
    hdr["OBJECT"] = "M42"
    hdr["CTYPE1"], hdr["CTYPE2"] = "RA---TAN", "DEC--TAN"
    hdr["CRVAL1"], hdr["CRVAL2"] = 83.8, -5.4
    hdr["CRPIX1"], hdr["CRPIX2"] = naxis[0] / 2 + 1, naxis[1] / 2 + 1
    for k, v in header_extra.items():
        hdr[k] = v
    fits.PrimaryHDU(data=np.zeros((naxis[1], naxis[0]), dtype=np.int16),
                    header=hdr).writeto(path, overwrite=True)


class TestReaderRecordsAngle(unittest.TestCase):

    def test_fits_cd_matrix(self):
        cd = _cd(32.0)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "master_Ha.fits"
            _write_master(p, {"CD1_1": cd[0][0], "CD1_2": cd[0][1],
                              "CD2_1": cd[1][0], "CD2_2": cd[1][1]})
            meta = read_fits_meta(p)
        self.assertTrue(meta["has_wcs"])
        _angle_close(self, meta["rotation_deg"], 32.0)

    def test_fits_cdelt_with_crota2(self):
        """The old CDELT form carries the turn in CROTA2 (sign per the FITS paper)."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "master_Ha.fits"
            _write_master(p, {"CDELT1": -SCALE, "CDELT2": SCALE, "CROTA2": 25.0})
            meta = read_fits_meta(p)
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [83.8, -5.4]
        w.wcs.crpix = [501, 401]
        w.wcs.cdelt = [-SCALE, SCALE]
        w.wcs.crota = [0, 25.0]
        _angle_close(self, meta["rotation_deg"], wcs_rotation_deg(w, 500, 400))
        self.assertGreater(abs(meta["rotation_deg"] % 180.0), 1.0)

    def test_unsolved_master_has_no_angle(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "master_Ha.fits"
            hdr = fits.Header()
            hdr["NAXIS"], hdr["NAXIS1"], hdr["NAXIS2"], hdr["BITPIX"] = 2, 10, 10, 16
            hdr["OBJCTRA"], hdr["OBJCTDEC"] = "05 35 00", "-05 23 00"
            fits.PrimaryHDU(data=np.zeros((10, 10), dtype=np.int16),
                            header=hdr).writeto(p)
            meta = read_fits_meta(p)
        self.assertFalse(meta["has_wcs"])
        self.assertIsNone(meta["rotation_deg"])


def _old_fov_corners(ra_c, dec_c, w, h):
    """The square-to-north corners, as built before rotation existed."""
    out = []
    for dx, dy in [(-1, -1), (-1, 1), (1, 1), (1, -1)]:
        cos_dec = np.cos(np.radians(dec_c))
        out.append([float((ra_c + (dx * w / 2.0) / 60.0 / cos_dec) % 360.0),
                    float(dec_c + (dy * h / 2.0) / 60.0)])
    return out


def _js_plan_corners(ra, dec, w, h, rot):
    """computePlanCorners from static/app.js, transcribed line for line."""
    hw, hh = w / 2 / 60, h / 2 / 60
    r = np.radians(rot)
    cos_d = max(1e-6, np.cos(np.radians(dec)))
    out = []
    for lx, ly in [(-hw, -hh), (-hw, hh), (hw, hh), (hw, -hh)]:
        de = lx * np.cos(r) + ly * np.sin(r)
        dn = -lx * np.sin(r) + ly * np.cos(r)
        out.append([ra + de / cos_d, dec + dn])
    return out


class TestRotatedCorners(unittest.TestCase):

    def test_zero_matches_the_old_corners_exactly(self):
        icrs, _ = fov_corners(83.8, -5.4, 120.0, 80.0)
        self.assertEqual(icrs, _old_fov_corners(83.8, -5.4, 120.0, 80.0))

    def test_rotated_corners_match_the_planner(self):
        for rot in (15.0, 90.0, 137.0):
            icrs, _ = fov_corners(83.8, -5.4, 120.0, 80.0, rot_deg=rot)
            for got, want in zip(icrs, _js_plan_corners(83.8, -5.4, 120.0, 80.0, rot)):
                self.assertAlmostEqual(got[0], want[0], places=9)
                self.assertAlmostEqual(got[1], want[1], places=9)

    def test_quarter_turn_swaps_the_extent(self):
        """A 120 x 60 frame turned 90 degrees spans 60' east-west and 120' north-south."""
        icrs, _ = fov_corners(100.0, 0.0, 120.0, 60.0, rot_deg=90.0)
        ras = [c[0] for c in icrs]
        decs = [c[1] for c in icrs]
        self.assertAlmostEqual((max(ras) - min(ras)) * 60, 60.0, places=6)
        self.assertAlmostEqual((max(decs) - min(decs)) * 60, 120.0, places=6)

    def test_rotation_across_ra_zero_stays_in_range(self):
        icrs, _ = fov_corners(0.1, 20.0, 120.0, 80.0, rot_deg=30.0)
        for ra, _dec in icrs:
            self.assertGreaterEqual(ra, 0.0)
            self.assertLess(ra, 360.0)

    def test_corner_order_keeps_its_winding(self):
        """Turning the box must not mirror it: SW, NW, NE, SE stays one way round."""
        def signed_area(pts):
            return sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1]
                       for i in range(4))
        base = signed_area(fov_corners(100.0, 0.0, 120.0, 80.0)[0])
        for rot in (30.0, 100.0, 170.0):
            turned = signed_area(fov_corners(100.0, 0.0, 120.0, 80.0, rot_deg=rot)[0])
            self.assertEqual(np.sign(turned), np.sign(base))
            self.assertAlmostEqual(turned, base, places=6)


def _m(rot, w=120.0, h=80.0, hours=1.0):
    return {"rotation_deg": rot, "fov_arcmin": [w, h], "hours": hours}


class TestTargetRotation(unittest.TestCase):

    def test_agreeing_masters(self):
        r = target_rotation([_m(10.2), _m(10.8), _m(190.5)])
        self.assertEqual(r["rotation_source"], "wcs")
        self.assertAlmostEqual(r["rotation_deg"], 10.5, places=1)
        self.assertEqual(r["footprint_arcmin"], [120.0, 80.0])
        self.assertNotIn("rotation_alternates", r)

    def test_angle_folds_to_under_180(self):
        r = target_rotation([_m(250.0)])
        self.assertAlmostEqual(r["rotation_deg"], 70.0)

    def test_agreeing_across_the_180_seam(self):
        r = target_rotation([_m(179.0), _m(1.0)])
        self.assertEqual(r["rotation_source"], "wcs")
        self.assertAlmostEqual(r["rotation_deg"], 0.0)

    def test_disagreeing_masters_take_the_most_integration(self):
        r = target_rotation([_m(0.0, hours=5.0), _m(0.5, hours=1.0), _m(60.0, hours=10.0)])
        self.assertEqual(r["rotation_source"], "wcs_mixed")
        self.assertAlmostEqual(r["rotation_deg"], 60.0)
        self.assertEqual(len(r["rotation_alternates"]), 1)
        alt = r["rotation_alternates"][0]
        self.assertAlmostEqual(alt["rotation_deg"], 0.1, places=1)
        self.assertEqual(alt["n_masters"], 2)
        self.assertAlmostEqual(alt["hours"], 6.0)

    def test_disagreement_widens_the_box_to_hold_both(self):
        """A 120 x 80 frame at 0 and the same frame at 90 need a 120 x 120 box."""
        r = target_rotation([_m(0.0, hours=2.0), _m(90.0, hours=1.0)])
        self.assertAlmostEqual(r["rotation_deg"], 0.0)
        self.assertEqual(r["footprint_arcmin"], [120.0, 120.0])

    def test_masters_in_the_chosen_group_are_not_inflated(self):
        """Two nights a degree apart are one framing, not a bigger box."""
        r = target_rotation([_m(20.0), _m(21.0)])
        self.assertEqual(r["footprint_arcmin"], [120.0, 80.0])

    def test_two_cameras_at_one_angle_hold_both_frames(self):
        r = target_rotation([_m(20.0, w=100, h=60), _m(20.0, w=80, h=80)])
        self.assertEqual(r["footprint_arcmin"], [100.0, 80.0])

    def test_tie_goes_to_more_masters_when_no_hours(self):
        r = target_rotation([_m(0.0, hours=0), _m(0.5, hours=0), _m(45.0, hours=0)])
        self.assertAlmostEqual(r["rotation_deg"], 0.25)

    def test_some_masters_without_an_angle(self):
        r = target_rotation([_m(30.0), _m(None)])
        self.assertEqual(r["rotation_source"], "wcs_partial")
        self.assertAlmostEqual(r["rotation_deg"], 30.0)

    def test_no_master_has_an_angle_falls_back_to_north_up(self):
        r = target_rotation([_m(None, w=100, h=60), _m(None)])
        self.assertEqual(r["rotation_source"], "none")
        self.assertEqual(r["rotation_deg"], 0.0)
        self.assertEqual(r["footprint_arcmin"], [120.0, 80.0])

    def test_no_masters_at_all(self):
        r = target_rotation([])
        self.assertEqual(r["rotation_source"], "none")
        self.assertEqual(r["rotation_deg"], 0.0)
        self.assertIsNone(r["footprint_arcmin"])


class TestRefreshRotationSelection(unittest.TestCase):
    """--refresh-rotation re-reads only solved masters cached before angles."""

    def _meta(self, **over):
        base = {"ok": True, "has_wcs": True, "imagetyp": "Master Light",
                "ncombine": 20, "exptime": 300.0, "filter": "Ha"}
        base.update(over)
        return base

    def test_old_solved_master_is_reread(self):
        self.assertTrue(needs_rotation_reread(
            self._meta(), Path("/a/master/masterLight_Ha.fits"), 50_000_000))

    def test_master_already_carrying_an_angle_is_not(self):
        self.assertFalse(needs_rotation_reread(
            self._meta(rotation_deg=12.0), Path("/a/master/masterLight_Ha.fits"), 50_000_000))
        self.assertFalse(needs_rotation_reread(
            self._meta(rotation_deg=None), Path("/a/master/masterLight_Ha.fits"), 50_000_000))

    def test_unsolved_master_is_not(self):
        self.assertFalse(needs_rotation_reread(
            self._meta(has_wcs=False), Path("/a/master/masterLight_Ha.fits"), 50_000_000))

    def test_sub_is_not(self):
        self.assertFalse(needs_rotation_reread(
            self._meta(imagetyp="Light", ncombine=None),
            Path("/a/lights/Light_0001.fits"), 30_000_000))


class TestOldManifests(unittest.TestCase):
    """A manifest written before rotation existed loads and draws as before."""

    OLD_TARGET = {
        "target_id": 7, "objects": ["M42"],
        "center_ra_deg": 83.8, "center_dec_deg": -5.4,
        "center_l_deg": 209.0, "center_b_deg": -19.4,
        "fov_arcmin": [120.0, 80.0], "pix_arcsec": 1.5,
        "corners_icrs": _old_fov_corners(83.8, -5.4, 120.0, 80.0),
        "corners_galactic": [[0, 0], [0, 1], [1, 1], [1, 0]],
        "telescopes": ["RedCat 51"],
        "filters": {"Ha": {"total_hours": 3.0, "files": 1}},
    }

    def test_coverage_source_returns_the_same_polygon(self):
        import app as app_module
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest.json"
            p.write_text(json.dumps({"targets": [self.OLD_TARGET]}), encoding="utf-8")
            src = app_module.JsonManifestSource(
                source_id="t", label="t", color="#fff", attribution="",
                enabled_default=True, path=p)
            regions = list(src.coverage())
        self.assertEqual(len(regions), 1)
        self.assertEqual([list(v) for v in regions[0]["vertices"]],
                         self.OLD_TARGET["corners_icrs"])

    def test_sanitiser_adds_no_angle_to_an_old_target(self):
        out = sanitise_dict({"targets": [dict(self.OLD_TARGET)]})
        self.assertNotIn("rotation_deg", out["targets"][0])
        self.assertEqual(out["targets"][0]["corners_icrs"], self.OLD_TARGET["corners_icrs"])

    def test_sanitiser_keeps_the_angle_on_a_new_target(self):
        t = dict(self.OLD_TARGET, rotation_deg=33.0, rotation_source="wcs")
        out = sanitise_dict({"targets": [t]})
        self.assertEqual(out["targets"][0]["rotation_deg"], 33.0)


class TestBuilderEndToEnd(unittest.TestCase):
    """The real builder over a synthetic archive of two turned masters."""

    def setUp(self):
        import os
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)
        self.archive = self.tmp / "archive"
        self.manifest = self.tmp / "manifest.json"
        self.cache = self.tmp / "scan_cache.json"
        d = self.archive / "M42" / "master"
        d.mkdir(parents=True)
        for filt, theta in (("H", 32.0), ("O", 212.3)):
            cd = _cd(theta)
            _write_master(d / f"masterLight_{filt}.fits",
                          {"FILTER": filt, "CD1_1": cd[0][0], "CD1_2": cd[0][1],
                           "CD2_1": cd[1][0], "CD2_2": cd[1][1]})
        self.env = dict(os.environ, FITS_ROOTS=str(self.archive),
                        MANIFEST_PATH=str(self.manifest), ACP_SCAN_CACHE=str(self.cache),
                        PIPELINE_DB=str(self.tmp / "missing.db"),
                        FULL_MASTERS=str(self.tmp / "missing_masters"),
                        PYTHONIOENCODING="utf-8")

    def run_scan(self, *args):
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "build_archive_manifest.py"), *args],
            env=self.env, capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def target(self):
        m = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(m["targets"]), 1)
        return m["targets"][0]

    def test_manifest_carries_the_angle_and_turned_corners(self):
        self.run_scan()
        t = self.target()
        self.assertEqual(t["rotation_source"], "wcs")
        self.assertAlmostEqual(t["rotation_deg"], 32.15, delta=0.1)
        self.assertEqual(sorted(round(p["rotation_deg"]) for p in t["per_master_fov"]),
                         [32, 212])
        want, _ = fov_corners(t["center_ra_deg"], t["center_dec_deg"],
                              *t["footprint_arcmin"], rot_deg=t["rotation_deg"])
        self.assertEqual(t["corners_icrs"], want)
        self.assertNotEqual(t["corners_icrs"],
                            _old_fov_corners(t["center_ra_deg"], t["center_dec_deg"],
                                             *t["footprint_arcmin"]))

    def _age_the_cache(self):
        """Make the cache look written by the release before angles existed."""
        doc = json.loads(self.cache.read_text(encoding="utf-8"))
        doc["reader_hash"] = "previous release"
        for ent in doc["entries"].values():
            ent["meta"].pop("rotation_deg", None)
        self.cache.write_text(json.dumps(doc), encoding="utf-8")

    def test_refresh_rotation_rereads_only_old_masters(self):
        self.run_scan()
        first = self.target()
        self._age_the_cache()
        out = self.run_scan("--refresh-rotation").stdout
        self.assertIn("keeping it anyway for --refresh-rotation", out)
        self.assertIn("re-reading 2 solved master(s)", out)
        self.assertEqual(self.target()["corners_icrs"], first["corners_icrs"])
        # The cache now carries angles, so a normal scan is warm.
        out = self.run_scan().stdout
        self.assertIn("scan cache: 2 hit(s), 0 file(s) to read", out)

    def test_stale_cache_without_the_flag_still_goes_cold(self):
        self.run_scan()
        self._age_the_cache()
        out = self.run_scan().stdout
        self.assertIn("different reader code, scanning cold", out)


if __name__ == "__main__":
    unittest.main()
