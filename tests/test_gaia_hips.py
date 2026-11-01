"""scripts/make_gaia_hips.py: the parts that need only numpy and astropy.

The tile writer and the HEALPix lookup need Pillow and astropy-healpix, which
the Brutix build container has, so they are not run here.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_gaia_hips as g  # noqa: E402


class TestTileLayout(unittest.TestCase):
    def test_every_pixel_has_its_own_spot(self):
        rows, cols = g.tile_layout()
        self.assertEqual(len(set(zip(rows.tolist(), cols.tolist()))), g.TILE * g.TILE)
        self.assertEqual((rows.max(), cols.max()), (g.TILE - 1, g.TILE - 1))

    def test_the_first_four_pixels_run_top_left_bottom_left_top_right_bottom_right(self):
        rows, cols = g.tile_layout()
        self.assertEqual(list(zip(rows[:4], cols[:4])), [(0, 0), (1, 0), (0, 1), (1, 1)])

    def test_a_parent_tile_puts_each_child_where_the_layout_puts_its_pixels(self):
        # The pixel with the child's index in the top two bits must land in that
        # child's quadrant, or the lower orders would not match the finest one.
        rows, cols = g.tile_layout()
        half = g.TILE // 2
        for child in range(4):
            k = child << (2 * g.TILE_ORDER - 2)
            quadrant = (rows[k] // half, cols[k] // half)
            self.assertEqual(quadrant, (child % 2, child // 2))
        kids = [np.full((g.TILE, g.TILE, 3), 10.0 * (i + 1), dtype=np.float32) for i in range(4)]
        merged = g.merge_children(kids)
        for child in range(4):
            r, c = rows[child << (2 * g.TILE_ORDER - 2)], cols[child << (2 * g.TILE_ORDER - 2)]
            self.assertEqual(merged[r, c, 0], 10.0 * (child + 1))


class TestSampling(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((2000, 4000, 3), dtype=np.float32)

    def test_the_galactic_centre_reads_the_middle_of_the_picture(self):
        self.image[999:1001, 1999:2001] = 255
        out = g.sample(self.image, np.array([0.0]), np.array([0.0]))
        self.assertGreater(out[0, 0], 200)

    def test_longitude_grows_to_the_left_so_the_lmc_sits_lower_right(self):
        # LMC: l = 280, b = -33. Picture pixel x = 2888, y = 1366.
        self.image[1365:1368, 2887:2890] = 255
        out = g.sample(self.image, np.array([280.0, 80.0]), np.array([-33.0, -33.0]))
        self.assertGreater(out[0, 0], 200)
        self.assertLess(out[1, 0], 1, "l = 80 is on the other side, upper left of the LMC's spot")

    def test_it_wraps_at_l_180(self):
        self.image[:, 0] = 255
        out = g.sample(self.image, np.array([180.0]), np.array([0.0]))
        self.assertGreater(out[0, 0], 100)

    def test_the_rotation_takes_the_galactic_centre_to_l_0_b_0(self):
        rot = g.galactic_matrix()
        ra, dec = np.radians(266.405), np.radians(-28.936)
        v = np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])
        gv = rot @ v
        self.assertAlmostEqual(float(np.degrees(np.arctan2(gv[1], gv[0]))), 0.0, places=2)
        self.assertAlmostEqual(float(np.degrees(np.arcsin(gv[2]))), 0.0, places=2)


class TestWrittenText(unittest.TestCase):
    def test_properties_carry_what_the_brief_asks_for(self):
        text = g.PROPERTIES.format(order=2)
        for line in ("hips_frame = equatorial", "hips_order = 2", "hips_tile_format = jpeg",
                     "hips_tile_width = 512", "obs_title = Gaia EDR3 sky in colour (ESA)",
                     f"obs_copyright = {g.CREDIT}", f"hips_copyright = {g.CREDIT}"):
            self.assertIn(line, text)

    def test_the_licence_file_names_the_licence_and_the_gpl_split(self):
        self.assertIn("CC BY-SA 3.0 IGO", g.LICENCE_TEXT)
        self.assertIn("GNU General Public License", g.LICENCE_TEXT)


if __name__ == "__main__":
    unittest.main()
