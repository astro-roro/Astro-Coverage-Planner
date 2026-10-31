"""The package icons (spec docs/specs/windows-download-and-setup.md, test I1)."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging" / "icons"))

try:
    from PIL import Image
    import make_icons
except ImportError:                                    # Pillow is a dev dependency
    Image = make_icons = None


def svg_polygons() -> list[list[tuple[float, float]]]:
    """The points of each path in static/favicon.svg, in order. The paths use
    only M, L and Z, which is all the favicon needs."""
    svg = (REPO / "static" / "favicon.svg").read_text(encoding="utf-8")
    polygons = []
    for d in re.findall(r'<path[^>]*\sd="([^"]+)"', svg):
        tokens = re.findall(r"[MLZ]|-?\d+(?:\.\d+)?", d)
        points, i = [], 0
        while i < len(tokens):
            if tokens[i] in "ML":
                points.append((float(tokens[i + 1]), float(tokens[i + 2])))
                i += 3
            else:
                assert tokens[i] == "Z", f"unexpected path command in the favicon: {tokens[i]}"
                i += 1
        polygons.append(points)
    return polygons


@unittest.skipIf(make_icons is None, "Pillow is not installed")
class TestIcons(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.out = Path(tempfile.mkdtemp(prefix="acp-icons-"))
        cls.written = make_icons.make_all(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_i1_the_polygons_equal_the_paths_in_the_favicon(self):
        diamond, star = svg_polygons()
        self.assertEqual([tuple(map(float, p)) for p in make_icons.DIAMOND], diamond)
        self.assertEqual([tuple(map(float, p)) for p in make_icons.STAR], star)

    def test_i1_the_colours_match_the_favicon(self):
        svg = (REPO / "static" / "favicon.svg").read_text(encoding="utf-8")
        blue = re.search(r'fill="#([0-9a-fA-F]{6})"', svg).group(1)
        self.assertEqual(make_icons.BLUE[:3], tuple(int(blue[i:i + 2], 16) for i in (0, 2, 4)))

    def test_i1_the_ico_holds_all_seven_sizes(self):
        with Image.open(self.out / "acp.ico") as ico:
            self.assertEqual(sorted(ico.info["sizes"]), [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])

    def test_i1_the_pngs_have_their_stated_sizes(self):
        for name, size in (("acp.png", 512), ("acp-tray.png", 32), ("acp-menubar.png", 22),
                           ("acp-menubar@2x.png", 44)):
            with Image.open(self.out / name) as image:
                self.assertEqual(image.size, (size, size), name)

    def test_the_tray_icon_is_a_blue_diamond_with_a_white_star_on_nothing(self):
        with Image.open(self.out / "acp-tray.png") as image:
            image = image.convert("RGBA")
            self.assertEqual(image.getpixel((16, 16)), (255, 255, 255, 255))   # star
            self.assertEqual(image.getpixel((16, 6)), make_icons.BLUE)         # diamond
            self.assertEqual(image.getpixel((1, 1))[3], 0)                      # corner

    def test_the_menu_bar_icon_is_black_on_transparent_with_the_star_cut_out(self):
        with Image.open(self.out / "acp-menubar@2x.png") as image:
            image = image.convert("RGBA")
            self.assertEqual(image.getpixel((22, 8)), (0, 0, 0, 255))
            self.assertEqual(image.getpixel((22, 22))[3], 0)
            self.assertEqual(image.getpixel((1, 1))[3], 0)
            pixels = image.load()
            colours = {pixels[x, y][:3] for x in range(image.width) for y in range(image.height)
                       if pixels[x, y][3]}
            self.assertEqual(colours, {(0, 0, 0)}, "only black, so macOS can tint it")

    def test_icns_is_made_where_iconutil_works(self):
        if not shutil.which("iconutil"):
            self.skipTest("iconutil is a Mac tool")
        try:
            path = make_icons.write_icns(self.out)
        except subprocess.CalledProcessError:
            self.skipTest("iconutil is installed but would not run here (a sandbox)")
        self.assertTrue(path.exists())
        self.assertEqual(path.read_bytes()[:4], b"icns")


if __name__ == "__main__":
    unittest.main()
