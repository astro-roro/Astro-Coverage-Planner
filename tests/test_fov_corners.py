"""Footprint corners must be a true rectangle on the sky at any declination.

``fov_corners`` used to offset the corners on a flat plane (RA offset divided
by cos(dec)) and clamp anything past a pole. A full archive scan died on an
unclamped corner at step 5 on 2026-09-11. After the clamp went in, the two
Snapshot targets at Dec -89.24 and -89.87 drew as crossed triangles, because
the flat offsets wrapped in RA and the clamp squashed them. The corners now
come from a gnomonic (TAN) projection, checked here against astropy's own TAN
WCS, which is an independent implementation of the same projection.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
from astropy.wcs import WCS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_archive_manifest import fov_corners, tangent_offset_to_radec  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "tangent_corners.json"


def _unit(ra, dec):
    a, d = np.radians(ra), np.radians(dec)
    return np.array([np.cos(d) * np.cos(a), np.cos(d) * np.sin(a), np.sin(d)])


def _sep_arcmin(p, q):
    """Great-circle distance between two [ra, dec] points, in arcmin."""
    u, v = _unit(*p), _unit(*q)
    return float(np.degrees(np.arctan2(np.linalg.norm(np.cross(u, v)), u @ v))) * 60


def _astropy_corners(ra, dec, w, h, rot):
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [1.0, 1.0]
    wcs.wcs.cdelt = [1.0, 1.0]
    r = np.radians(rot)
    out = []
    for dx, dy in [(-1, -1), (-1, 1), (1, 1), (1, -1)]:
        lx, ly = dx * w / 120.0, dy * h / 120.0
        e = lx * np.cos(r) + ly * np.sin(r)
        n = -lx * np.sin(r) + ly * np.cos(r)
        c_ra, c_dec = wcs.wcs_pix2world([[e, n]], 0)[0]
        out.append([float(c_ra), float(c_dec)])
    return out


def _on_tangent_plane(ra0, dec0, pts):
    """Project points onto the tangent plane at (ra0, dec0), in degrees."""
    c = _unit(ra0, dec0)
    east = np.cross([0.0, 0.0, 1.0], c)
    if np.linalg.norm(east) < 1e-12:  # centre on a pole: north runs along ra0
        a = np.radians(ra0)
        east = np.array([-np.sin(a), np.cos(a), 0.0])
    east /= np.linalg.norm(east)
    north = np.cross(c, east)
    out = []
    for p in pts:
        v = _unit(*p)
        out.append((np.degrees(v @ east / (v @ c)), np.degrees(v @ north / (v @ c))))
    return out


def _is_simple_quad(xy):
    """True when the quad's edges do not cross, so it draws as one outline."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    signs = [np.sign(cross(xy[i], xy[(i + 1) % 4], xy[(i + 2) % 4])) for i in range(4)]
    return len(set(signs)) == 1 and signs[0] != 0


def _flat_corners(ra_c, dec_c, w, h, rot=0.0):
    """The flat-offset corners the builder wrote before 2026-09-25."""
    r = np.radians(rot)
    out = []
    for dx, dy in [(-1, -1), (-1, 1), (1, 1), (1, -1)]:
        lx, ly = dx * w / 2.0, dy * h / 2.0
        e = lx * np.cos(r) + ly * np.sin(r)
        n = -lx * np.sin(r) + ly * np.cos(r)
        out.append([(ra_c + e / 60.0 / np.cos(np.radians(dec_c))) % 360.0, dec_c + n / 60.0])
    return out


class TestPolarFields(unittest.TestCase):
    SNAPSHOT_FOV = (323.0, 216.0)

    def assert_true_rectangle(self, ra, dec, w, h, rot):
        icrs, gal = fov_corners(ra, dec, w, h, rot_deg=rot)
        self.assertEqual(len(icrs), 4)
        self.assertEqual(len(gal), 4)
        for got, want in zip(icrs, _astropy_corners(ra, dec, w, h, rot)):
            self.assertLess(_sep_arcmin(got, want), 1e-6)
        for r, d in icrs:
            self.assertGreaterEqual(r, 0.0)
            self.assertLess(r, 360.0)
            self.assertGreaterEqual(d, -90.0)
            self.assertLessEqual(d, 90.0)
        xy = _on_tangent_plane(ra, dec, icrs)
        self.assertTrue(_is_simple_quad(xy), f"outline crosses itself: {icrs}")
        # Opposite sides match and the diagonals match: a rectangle, not a kite.
        self.assertAlmostEqual(_sep_arcmin(icrs[0], icrs[1]), _sep_arcmin(icrs[3], icrs[2]), places=6)
        self.assertAlmostEqual(_sep_arcmin(icrs[0], icrs[3]), _sep_arcmin(icrs[1], icrs[2]), places=6)
        self.assertAlmostEqual(_sep_arcmin(icrs[0], icrs[2]), _sep_arcmin(icrs[1], icrs[3]), places=6)
        return icrs, xy

    def test_a_field_centred_on_the_pole(self):
        for rot in (0.0, 37.0, 90.0):
            with self.subTest(rot=rot):
                icrs, _ = self.assert_true_rectangle(120.0, -90.0, *self.SNAPSHOT_FOV, rot)
                decs = [d for _, d in icrs]
                self.assertAlmostEqual(max(decs), min(decs), places=9,
                                       msg="every corner sits the same distance from the pole")

    def test_the_two_snapshot_targets_draw_as_rectangles(self):
        """The fields from the bug report, which drew as crossed triangles."""
        for dec in (-89.24, -89.87):
            for rot in (0.0, 63.0):
                with self.subTest(dec=dec, rot=rot):
                    self.assert_true_rectangle(250.0, dec, *self.SNAPSHOT_FOV, rot)

    def test_a_field_containing_the_pole_off_centre(self):
        """The pole lands inside the drawn box, and the box wraps all 360 of RA."""
        for pole_dec in (-90.0, 90.0):
            dec = -89.24 if pole_dec < 0 else 89.24
            with self.subTest(pole=pole_dec):
                icrs, xy = self.assert_true_rectangle(45.0, dec, *self.SNAPSHOT_FOV, 112.0)
                pole = _on_tangent_plane(45.0, dec, [[0.0, pole_dec]])[0]
                inside = all(
                    np.sign((xy[(i + 1) % 4][0] - xy[i][0]) * (pole[1] - xy[i][1])
                            - (xy[(i + 1) % 4][1] - xy[i][1]) * (pole[0] - xy[i][0]))
                    == np.sign((xy[1][0] - xy[0][0]) * (xy[2][1] - xy[0][1])
                               - (xy[1][1] - xy[0][1]) * (xy[2][0] - xy[0][0]))
                    for i in range(4))
                self.assertTrue(inside, "the pole must lie inside the footprint")
                ras = sorted(r for r, _ in icrs)
                gaps = [b - a for a, b in zip(ras, ras[1:])] + [ras[0] + 360 - ras[-1]]
                self.assertLess(max(gaps), 180.0, "corners circle the pole in RA")

    def test_a_high_field_across_ra_zero(self):
        icrs, _ = self.assert_true_rectangle(359.2, 78.0, 300.0, 200.0, 20.0)
        ras = [r for r, _ in icrs]
        self.assertTrue(any(r < 30 for r in ras) and any(r > 330 for r in ras),
                        "the box straddles RA 0 and each corner is wrapped into range")

    def test_no_pole_warning_any_more(self):
        lines = []
        fov_corners(120.0, -88.3, 240.0, 240.0, log=lines.append, label="polar field")
        self.assertEqual(lines, [])

    def test_every_returned_latitude_is_a_legal_latitude(self):
        """The property the 2026-09-11 crash violated, over a spread of centres and sizes."""
        for dec in (-90.0, -89.9, -45.0, 0.0, 45.0, 89.9, 90.0):
            for size in (1.0, 30.0, 600.0, 3000.0):
                with self.subTest(dec=dec, size=size):
                    icrs, gal = fov_corners(200.0, dec, size, size)
                    for _, d in icrs:
                        self.assertGreaterEqual(d, -90.0)
                        self.assertLessEqual(d, 90.0)
                    self.assertEqual(len(gal), 4)


class TestModerateDeclinations(unittest.TestCase):
    def test_a_typical_field_lands_where_the_flat_code_put_it(self):
        """Away from the poles the old and new corners differ by arcseconds.

        The flat code bent the box slightly, and the bend grows with
        declination, so an exact match is neither possible nor wanted. For a
        60 by 40 arcmin field up to Dec 60 the two agree to under 30 arcsec,
        well under a hundredth of the field's diagonal.
        """
        for dec in (-60.0, -30.0, -12.07, 0.0, 30.0, 60.0):
            for rot in (0.0, 33.0, 137.0):
                with self.subTest(dec=dec, rot=rot):
                    new, _ = fov_corners(251.94, dec, 60.0, 40.0, rot_deg=rot)
                    for got, old in zip(new, _flat_corners(251.94, dec, 60.0, 40.0, rot)):
                        self.assertLess(_sep_arcmin(got, old) * 60, 30.0)

    def test_corner_order_is_sw_nw_ne_se(self):
        icrs, _ = fov_corners(100.0, 20.0, 60.0, 40.0)
        (sw, nw, ne, se) = icrs
        self.assertLess(sw[1], nw[1])
        self.assertLess(se[1], ne[1])
        self.assertLess(sw[0], se[0])
        self.assertLess(nw[0], ne[0])

    def test_right_ascension_stays_in_range_across_the_seam(self):
        for ra in (0.0, 0.5, 359.5, 360.0):
            with self.subTest(ra=ra):
                icrs, _ = fov_corners(ra, 10.0, 120.0, 120.0)
                for r, _ in icrs:
                    self.assertGreaterEqual(r, 0.0)
                    self.assertLess(r, 360.0)

    def test_offset_helper_returns_the_centre_for_no_offset(self):
        for dec in (-90.0, -45.0, 0.0, 89.9):
            ra, d = tangent_offset_to_radec(33.0, dec, 0.0, 0.0)
            self.assertAlmostEqual(d, dec, places=9)
            if abs(dec) < 90:
                self.assertAlmostEqual(ra, 33.0, places=9)


class TestSharedFixture(unittest.TestCase):
    """static/sky-geometry.mjs is tested against the same file in node."""

    def test_the_builder_still_produces_the_fixture(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(doc["cases"]), 5)
        for c in doc["cases"]:
            with self.subTest(case=c["name"]):
                got, _ = fov_corners(c["ra"], c["dec"], c["w"], c["h"], rot_deg=c["rot"])
                for p, q in zip(got, c["corners"]):
                    self.assertLess(_sep_arcmin(p, q), 1e-6)


if __name__ == "__main__":
    unittest.main()
