"""A footprint corner past the pole must clamp, not crash the whole scan.

``main`` offsets the four corners of a field on a flat tangent plane, which is
what the planner draws. A wide field centred near a pole puts a corner past
plus or minus 90 degrees in that approximation, and astropy refuses the
latitude. A full archive scan died that way at step 5 on 2026-09-11, after four
hours of header reads, losing every target rather than one footprint.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import fov_corners  # noqa: E402


class TestFovCorners(unittest.TestCase):
    def test_a_field_over_the_south_pole_clamps_and_logs(self):
        """The real crash: dec -88.3 with a 4 degree field reached -91.67."""
        lines = []
        icrs, gal = fov_corners(120.0, -88.3, 240.0, 240.0,
                                log=lines.append, label="polar field")
        self.assertEqual(len(icrs), 4)
        self.assertEqual(len(gal), 4)
        decs = [d for _, d in icrs]
        self.assertEqual(min(decs), -90.0, "the low corners clamp to the pole")
        self.assertTrue(any("clamped" in line for line in lines),
                        "a clamp must be reported, not applied silently")
        self.assertTrue(any("polar field" in line for line in lines),
                        "the log names the target so it can be chased")

    def test_a_field_over_the_north_pole_clamps(self):
        icrs, _ = fov_corners(15.0, 89.5, 120.0, 120.0)
        self.assertEqual(max(d for _, d in icrs), 90.0)

    def test_every_returned_latitude_is_a_legal_latitude(self):
        """The property the crash violated, over a spread of centres and sizes."""
        for dec in (-90.0, -89.9, -45.0, 0.0, 45.0, 89.9, 90.0):
            for size in (1.0, 30.0, 600.0, 3000.0):
                with self.subTest(dec=dec, size=size):
                    icrs, gal = fov_corners(200.0, dec, size, size)
                    for _, d in icrs:
                        self.assertGreaterEqual(d, -90.0)
                        self.assertLessEqual(d, 90.0)
                    self.assertEqual(len(gal), 4)

    def test_right_ascension_stays_in_range_across_the_seam(self):
        """A field straddling RA 0 must not report a negative or 400 degree corner."""
        for ra in (0.0, 0.5, 359.5, 360.0):
            with self.subTest(ra=ra):
                icrs, _ = fov_corners(ra, 10.0, 120.0, 120.0)
                for r, _ in icrs:
                    self.assertGreaterEqual(r, 0.0)
                    self.assertLess(r, 360.0)

    def test_an_ordinary_field_is_unchanged_and_logs_nothing(self):
        """Guard the common path: no clamp, corners where the old code put them."""
        lines = []
        icrs, gal = fov_corners(251.94, -12.07, 60.0, 40.0, log=lines.append)
        self.assertEqual(lines, [])
        decs = sorted(d for _, d in icrs)
        self.assertAlmostEqual(decs[0], -12.07 - 20.0 / 60.0, places=6)
        self.assertAlmostEqual(decs[-1], -12.07 + 20.0 / 60.0, places=6)
        ras = sorted(r for r, _ in icrs)
        half_ra = (30.0 / 60.0) / abs(np.cos(np.radians(-12.07)))
        self.assertAlmostEqual(ras[0], 251.94 - half_ra, places=6)
        self.assertAlmostEqual(ras[-1], 251.94 + half_ra, places=6)

    def test_a_centre_exactly_on_the_pole_does_not_divide_by_zero(self):
        icrs, _ = fov_corners(45.0, 90.0, 60.0, 60.0)
        self.assertEqual(len(icrs), 4)
        for r, d in icrs:
            self.assertEqual(r, 45.0)
            self.assertGreaterEqual(d, -90.0)
            self.assertLessEqual(d, 90.0)


if __name__ == "__main__":
    unittest.main()
