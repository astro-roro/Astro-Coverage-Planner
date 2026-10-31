"""Draw the ACP icons for a package from the shapes in ``static/favicon.svg``.

Spec: docs/specs/windows-download-and-setup.md, section 3.5.

The build has no SVG renderer, so the two polygons are redrawn here with
Pillow at 8 times size and scaled down. A test compares ``DIAMOND`` and
``STAR`` with the paths in the SVG, so the two cannot drift apart. The files
are build outputs and are not committed.

    python packaging/icons/make_icons.py <output folder> [--icns]

Writes acp.ico, acp.png, acp-tray.png, acp-menubar.png and acp-menubar@2x.png.
With --icns it also writes ACP.icns through ``iconutil``, which exists on a
Mac only.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

# Points in the SVG's 32 by 32 viewBox. Keep identical to static/favicon.svg.
DIAMOND = [(16, 2), (30, 16), (16, 30), (2, 16)]
STAR = [(16, 9), (17.4, 14.6), (23, 16), (17.4, 17.4), (16, 23), (14.6, 17.4),
        (9, 16), (14.6, 14.6)]
VIEWBOX = 32

BLUE = (0x4D, 0xA6, 0xFF, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)
CLEAR = (0, 0, 0, 0)

SUPERSAMPLE = 8
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def _draw(size: int, body, star) -> Image.Image:
    """The diamond and star at ``size`` pixels square, drawn big and scaled down."""
    big = size * SUPERSAMPLE
    scale = big / VIEWBOX
    image = Image.new("RGBA", (big, big), CLEAR)
    draw = ImageDraw.Draw(image)
    draw.polygon([(x * scale, y * scale) for x, y in DIAMOND], fill=body)
    # CLEAR as the star colour punches the star out, which is what a menu bar
    # template wants. ImageDraw writes the colour as given, with no blending.
    draw.polygon([(x * scale, y * scale) for x, y in STAR], fill=star)
    return image.resize((size, size), Image.LANCZOS)


def colour_icon(size: int) -> Image.Image:
    return _draw(size, BLUE, WHITE)


def template_icon(size: int) -> Image.Image:
    """Black on transparent with the star cut out, for the Mac menu bar."""
    return _draw(size, BLACK, CLEAR)


def write_icns(out: Path, tool: str | None = None) -> Path | None:
    """ACP.icns from an iconset, through iconutil. None where iconutil is missing."""
    tool = tool or shutil.which("iconutil")
    if not tool:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "ACP.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            colour_icon(size).save(iconset / f"icon_{size}x{size}.png")
            colour_icon(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
        target = out / "ACP.icns"
        subprocess.run([tool, "-c", "icns", str(iconset), "-o", str(target)], check=True)
    return target


def make_all(out: Path, icns: bool = False) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    ico = out / "acp.ico"
    # Pillow scales the largest image down to every size listed, so the
    # source is the biggest one.
    colour_icon(256).save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    written.append(ico)

    for name, image in (("acp.png", colour_icon(512)),
                        ("acp-tray.png", colour_icon(32)),
                        ("acp-menubar.png", template_icon(22)),
                        ("acp-menubar@2x.png", template_icon(44))):
        path = out / name
        image.save(path)
        written.append(path)

    if icns:
        path = write_icns(out)
        if path is None:
            raise SystemExit("--icns needs iconutil, which only a Mac has")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path, help="folder to write the icons into")
    parser.add_argument("--icns", action="store_true", help="also write ACP.icns (Mac only)")
    args = parser.parse_args(argv)
    for path in make_all(args.out, args.icns):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
