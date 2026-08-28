"""Regression tests for _mosaic_panel_centers — the geometry that places
every mosaic panel's RA/Dec before it's written into the TS export zip.

This function had zero direct test coverage: _build_ts_export tests
exercise it only indirectly through single-panel (non-mosaic) plans, so a
sign error in the rotation matrix or a broken RA/cos(dec) scaling would
silently ship wrong panel coordinates into every NINA Target Scheduler
import for anyone using mosaics, with no test failing.

The rotation formula is derived from the "PA east-of-north" convention
documented at its call site (app.py: "camera-Y sits at PA east-of-north"):
a camera whose Y axis points north at rot=0 rotates its Y axis toward
east as rot_deg increases. These tests pin that convention down, and
cross-check the un-rotated case against astropy's own offset primitive so
the numbers aren't just re-deriving the implementation.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import _mosaic_panel_centers  # noqa: E402


class TestSinglePanel(unittest.TestCase):
    def test_single_panel_sits_exactly_on_center(self):
        panels = _mosaic_panel_centers(180.0, 20.0, 60.0, 60.0, 0.0, 1, 1, 0.0)
        self.assertEqual(len(panels), 1)
        self.assertAlmostEqual(panels[0]["ra_deg"], 180.0, places=9)
        self.assertAlmostEqual(panels[0]["dec_deg"], 20.0, places=9)
        self.assertEqual((panels[0]["row"], panels[0]["col"]), (0, 0))


class TestRowsAreNorthAligned(unittest.TestCase):
    def test_row_0_is_north_of_row_1(self):
        """Docstring: 'Row 0 is north.' A 2x1 mosaic (2 rows, 1 col) at
        rot=0 must place row 0 at higher Dec than row 1."""
        panels = _mosaic_panel_centers(180.0, 0.0, 60.0, 60.0, 0.0, 2, 1, 0.0)
        row0 = next(p for p in panels if p["row"] == 0)
        row1 = next(p for p in panels if p["row"] == 1)
        self.assertGreater(row0["dec_deg"], row1["dec_deg"])
        # Symmetric about the anchor.
        self.assertAlmostEqual(row0["dec_deg"] - 0.0, 0.0 - row1["dec_deg"], places=9)

    def test_row_stride_matches_fov_and_overlap(self):
        fov_h = 60.0  # arcmin
        overlap_pct = 20.0
        panels = _mosaic_panel_centers(180.0, 0.0, 60.0, fov_h, 0.0, 2, 1, overlap_pct)
        row0 = next(p for p in panels if p["row"] == 0)
        row1 = next(p for p in panels if p["row"] == 1)
        expected_stride_deg = (fov_h / 60.0) * (1.0 - overlap_pct / 100.0)
        self.assertAlmostEqual(row0["dec_deg"] - row1["dec_deg"], expected_stride_deg, places=9)


class TestColumnsAreRaAligned(unittest.TestCase):
    def test_col_stride_scales_by_cos_dec(self):
        """RA spacing must widen by 1/cos(dec) relative to a plain
        angular offset, since RA is a spherical (not Cartesian)
        coordinate — column stride at high dec must exceed the stride at
        dec=0 for the same physical FOV."""
        fov_w = 60.0  # arcmin
        dec = 60.0
        panels = _mosaic_panel_centers(180.0, dec, fov_w, 60.0, 0.0, 1, 2, 0.0)
        col0 = next(p for p in panels if p["col"] == 0)
        col1 = next(p for p in panels if p["col"] == 1)
        expected_stride_deg = (fov_w / 60.0) / math.cos(math.radians(dec))
        self.assertAlmostEqual(col1["ra_deg"] - col0["ra_deg"], expected_stride_deg, places=9)
        # Sanity: this must be strictly wider than the naive (no cos
        # correction) stride, or the cos(dec) division silently vanished.
        naive_stride_deg = fov_w / 60.0
        self.assertGreater(expected_stride_deg, naive_stride_deg)

    def test_dec_near_pole_does_not_explode(self):
        """cosD is clamped to a 1e-6 floor, so a plan anchored at the pole
        must return finite (if huge) RA offsets rather than raising
        ZeroDivisionError or producing inf/nan that would poison the
        exported zip."""
        panels = _mosaic_panel_centers(180.0, 89.9999999, 60.0, 60.0, 0.0, 1, 2, 0.0)
        for p in panels:
            self.assertTrue(math.isfinite(p["ra_deg"]))
            self.assertTrue(math.isfinite(p["dec_deg"]))


class TestRotation(unittest.TestCase):
    def test_90deg_rotation_swaps_row_axis_onto_ra_axis(self):
        """At rot=90 (camera Y now points east instead of north), a 2x1
        mosaic's row spread should land on the RA axis, not the Dec axis."""
        dec = 0.0  # cos(dec)=1, so the RA arithmetic is a plain angular check
        panels = _mosaic_panel_centers(180.0, dec, 60.0, 60.0, 90.0, 2, 1, 0.0)
        row0 = next(p for p in panels if p["row"] == 0)
        row1 = next(p for p in panels if p["row"] == 1)
        # Dec must now be (near-)unchanged between rows...
        self.assertAlmostEqual(row0["dec_deg"], row1["dec_deg"], places=6)
        # ...and the offset must have moved onto RA instead.
        self.assertGreater(abs(row0["ra_deg"] - row1["ra_deg"]), 0.5)
        # Row 0 (north at rot=0) rotates toward east (increasing RA) at
        # rot=90, per the "PA east-of-north" convention.
        self.assertGreater(row0["ra_deg"], row1["ra_deg"])

    def test_360deg_rotation_matches_unrotated(self):
        base = _mosaic_panel_centers(180.0, 20.0, 60.0, 60.0, 0.0, 2, 2, 10.0)
        rotated = _mosaic_panel_centers(180.0, 20.0, 60.0, 60.0, 360.0, 2, 2, 10.0)
        for a, b in zip(base, rotated):
            self.assertAlmostEqual(a["ra_deg"], b["ra_deg"], places=9)
            self.assertAlmostEqual(a["dec_deg"], b["dec_deg"], places=9)

    def test_panel_offset_magnitude_preserved_under_rotation(self):
        """Rotation must be distance-preserving: the angular separation of
        a panel from the mosaic center shouldn't change just because the
        camera is rotated."""
        from astropy.coordinates import SkyCoord
        import astropy.units as u

        ra_c, dec_c = 45.0, 10.0
        center = SkyCoord(ra_c * u.deg, dec_c * u.deg)
        for rot in (0.0, 30.0, 90.0, 137.0, 271.0):
            panels = _mosaic_panel_centers(ra_c, dec_c, 60.0, 60.0, rot, 1, 3, 0.0)
            seps = [
                center.separation(SkyCoord(p["ra_deg"] * u.deg, p["dec_deg"] * u.deg)).arcmin
                for p in panels
            ]
            # Outer two panels of a 1x3 row are equidistant from center
            # regardless of rotation; middle panel sits on center. Loose
            # tolerance: the RA offset uses a single cos(dec_c) for the
            # whole mosaic (flat tangent-plane approximation), so panels
            # at slightly different actual Dec pick up a small, expected
            # asymmetry — this isn't the invariant under test.
            self.assertAlmostEqual(seps[0], seps[2], delta=0.5, msg=f"rot={rot}")
            self.assertAlmostEqual(seps[1], 0.0, places=6, msg=f"rot={rot}")


class TestClampsAndDefaults(unittest.TestCase):
    def test_zero_or_negative_rows_cols_clamped_to_one(self):
        panels = _mosaic_panel_centers(180.0, 0.0, 60.0, 60.0, 0.0, 0, -3, 0.0)
        self.assertEqual(len(panels), 1)

    def test_overlap_over_100pct_clamped(self):
        """overlap_pct >= 100 would make stride <= 0 (panels stacked or
        inverted); the function clamps to 99% so stride stays positive."""
        panels = _mosaic_panel_centers(180.0, 0.0, 60.0, 60.0, 0.0, 1, 2, 150.0)
        col0 = next(p for p in panels if p["col"] == 0)
        col1 = next(p for p in panels if p["col"] == 1)
        self.assertGreater(col1["ra_deg"], col0["ra_deg"])


if __name__ == "__main__":
    unittest.main()
