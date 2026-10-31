"""Turn ESA's Gaia EDR3 colour sky picture into the HiPS folder ACP shows when
every survey server is down (static/offline-sky/).

    python scripts/make_gaia_hips.py Gaia_EDR3_flux_cartesian_4k.png
    python scripts/make_gaia_hips.py <image> --order 3 --out /tmp/sky

The input is "The colour of the sky from Gaia's Early Data Release 3" (4000 x
2000 RGB). It is equirectangular in Galactic coordinates with the Galactic
centre in the middle and longitude increasing to the left. The Magellanic
Clouds sit lower right at l = 280 and 303, b = -33 and -44, which is how the
orientation was checked.

Licence of the picture, and so of the folder this writes: CC BY-SA 3.0 IGO.
Credit: ESA/Gaia/DPAC. Acknowledgement: A. Moitinho. That licence covers the
sky folder only; ACP's code stays GPL.

Needs numpy, astropy, astropy-healpix and Pillow. Output is deterministic: the
same image and options give the same bytes, and nothing is stamped with the
time. The finest order is sampled from the picture; each coarser order is the
2 x 2 average of the one below it, as HiPS generators do.

The picture is 0.09 degrees a pixel and a 512 px tile at order 2 is already
about 3 times finer than that, so the default stops at order 2 (252 tiles).
Order 3 adds 768 upscaled tiles; the size line at the end says whether it is
worth it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

TILE = 512
TILE_ORDER = 9                      # 512 = 2 ** 9, so a tile is nested pixels 9 orders down
CREDIT = "ESA/Gaia/DPAC; CC BY-SA 3.0 IGO. Acknowledgement: A. Moitinho"
TITLE = "Gaia EDR3 sky in colour (ESA)"
LICENCE_URL = "https://creativecommons.org/licenses/by-sa/3.0/igo/"
SOURCE_URL = "https://www.esa.int/ESA_Multimedia/Images/2020/12/The_colour_of_the_sky_from_Gaia_s_Early_Data_Release_3"
RELEASE_DATE = "2020-12-03T00:00Z"  # fixed so the output does not change between runs


def compact_even_bits(k: np.ndarray) -> np.ndarray:
    """Pack bits 0, 2, 4 ... of k into consecutive bits (the inverse of a Z-order interleave)."""
    out = np.zeros_like(k)
    for i in range(TILE_ORDER):
        out |= ((k >> (2 * i)) & 1) << i
    return out


def tile_layout() -> tuple[np.ndarray, np.ndarray]:
    """For every nested pixel k inside a tile, its (row, column) in the JPEG.

    Even bits of k count rows down from the top of the file and odd bits count
    columns from the left. This is the layout the Python `hips` package reads
    real HiPS JPEG tiles with (its quadrant order is 0 top left, 1 bottom left,
    2 top right, 3 bottom right). It could not be checked against a real tile
    offline: open the result in Aladin and look for seams at tile edges.
    """
    k = np.arange(TILE * TILE, dtype=np.int64)
    return compact_even_bits(k), compact_even_bits(k >> 1)


def galactic_matrix() -> np.ndarray:
    """3 x 3 rotation taking ICRS unit vectors to Galactic ones, read from astropy."""
    from astropy.coordinates import CartesianRepresentation, SkyCoord
    basis = CartesianRepresentation([1, 0, 0], [0, 1, 0], [0, 0, 1])
    gal = SkyCoord(basis, frame="icrs").galactic.cartesian
    return np.array([gal.x.value, gal.y.value, gal.z.value])   # column j = image of basis j


def sample(image: np.ndarray, l_deg: np.ndarray, b_deg: np.ndarray) -> np.ndarray:
    """Bilinear lookup in the plate carree picture (centre l = 0, l grows to the left)."""
    h, w, _ = image.shape
    lc = (l_deg + 180.0) % 360.0 - 180.0
    x = (0.5 - lc / 360.0) * w - 0.5
    y = np.clip((0.5 - b_deg / 180.0) * h - 0.5, 0, h - 1)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.minimum(np.floor(y).astype(np.int64), h - 2)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    x0 %= w
    x1 = (x0 + 1) % w
    top = image[y0, x0] * (1 - fx) + image[y0, x1] * fx
    bottom = image[y0 + 1, x0] * (1 - fx) + image[y0 + 1, x1] * fx
    return top * (1 - fy) + bottom * fy


def leaf_tile(image, rot, rows, cols, order: int, ipix: int) -> np.ndarray:
    from astropy_healpix import healpix_to_lonlat
    nside = 2 ** (order + TILE_ORDER)
    idx = ipix * TILE * TILE + np.arange(TILE * TILE, dtype=np.int64)
    lon, lat = healpix_to_lonlat(idx, nside, order="nested")
    lon, lat = lon.rad, lat.rad
    v = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    g = rot @ v
    b = np.degrees(np.arcsin(np.clip(g[2], -1, 1)))
    l = np.degrees(np.arctan2(g[1], g[0]))
    out = np.empty((TILE, TILE, 3), dtype=np.float32)
    out[rows, cols] = sample(image, l, b)
    return out


def merge_children(children: list[np.ndarray]) -> np.ndarray:
    """A parent tile from its four children, each shrunk 2 x 2 into one quadrant."""
    half = TILE // 2
    out = np.empty((TILE, TILE, 3), dtype=np.float32)
    # child 0 top left, 1 bottom left, 2 top right, 3 bottom right (see tile_layout)
    spots = [(0, 0), (half, 0), (0, half), (half, half)]
    for child, (r, c) in zip(children, spots):
        small = child.reshape(half, 2, half, 2, 3).mean(axis=(1, 3))
        out[r:r + half, c:c + half] = small
    return out


def write_tile(root: Path, order: int, ipix: int, data: np.ndarray, quality: int) -> int:
    from PIL import Image
    folder = root / f"Norder{order}" / f"Dir{(ipix // 10000) * 10000}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Npix{ipix}.jpg"
    img = Image.fromarray(np.clip(data + 0.5, 0, 255).astype(np.uint8), "RGB")
    img.save(path, "JPEG", quality=quality, optimize=True)
    return path.stat().st_size


def build(image, rot, rows, cols, root, order, max_order, ipix, quality, stats):
    """Write tile (order, ipix) and return its pixels, building children first."""
    if order == max_order:
        data = leaf_tile(image, rot, rows, cols, order, ipix)
    else:
        kids = [build(image, rot, rows, cols, root, order + 1, max_order, ipix * 4 + i, quality, stats)
                for i in range(4)]
        data = merge_children(kids)
    stats["tiles"] += 1
    stats["bytes"] += write_tile(root, order, ipix, data, quality)
    return data


PROPERTIES = f"""\
creator_did = ivo://acp/offline-sky/gaia-edr3
obs_title = {TITLE}
obs_description = ESA's picture of the colour of the sky from Gaia's Early Data Release 3, resampled into HiPS tiles. Low resolution, built into ACP for use when the survey servers are down.
obs_copyright = {CREDIT}
obs_copyright_url = {SOURCE_URL}
obs_ack = {CREDIT}
hips_copyright = {CREDIT}
hips_creator = Astro Coverage Planner (scripts/make_gaia_hips.py)
hips_version = 1.4
hips_release_date = {RELEASE_DATE}
hips_status = public master clonableOnce
hips_frame = equatorial
hips_order = {{order}}
hips_tile_width = {TILE}
hips_tile_format = jpeg
dataproduct_type = image
client_category = Image/Optical/Colour
"""

LICENCE_TEXT = f"""\
This folder is a HiPS sky built from ESA's "The colour of the sky from Gaia's
Early Data Release 3".

Credit: {CREDIT}
Source: {SOURCE_URL}
Licence: CC BY-SA 3.0 IGO, {LICENCE_URL}
Changes: the equirectangular picture was resampled into HEALPix (HiPS) tiles in
equatorial coordinates, with the coarser orders averaged from the finer ones,
and saved as JPEG. Made by scripts/make_gaia_hips.py.

The licence applies to this folder (static/offline-sky) only. The rest of
Astro Coverage Planner is licensed under the GNU General Public License v3 or
later (see LICENSE at the top of the repository).
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("image", type=Path, help="Gaia_EDR3_flux_cartesian_4k.png")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "static" / "offline-sky")
    ap.add_argument("--order", type=int, default=2, choices=(1, 2, 3, 4), help="finest HiPS order (default 2)")
    ap.add_argument("--quality", type=int, default=75, help="JPEG quality (default 75; 85 made 14.1 MB at order 2)")
    args = ap.parse_args(argv)

    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    image = np.asarray(Image.open(args.image).convert("RGB"), dtype=np.float32)
    if image.shape[1] != 2 * image.shape[0]:
        print(f"expected a 2:1 picture, got {image.shape[1]} x {image.shape[0]}", file=sys.stderr)
        return 1

    rot, (rows, cols) = galactic_matrix(), tile_layout()
    args.out.mkdir(parents=True, exist_ok=True)
    stats = {"tiles": 0, "bytes": 0}
    for ipix in range(12):
        build(image, rot, rows, cols, args.out, 0, args.order, ipix, args.quality, stats)
        print(f"  base tile {ipix + 1} of 12 done", flush=True)
    (args.out / "properties").write_text(PROPERTIES.format(order=args.order), encoding="utf-8", newline="\n")
    (args.out / "LICENSE.txt").write_text(LICENCE_TEXT, encoding="utf-8", newline="\n")
    total = sum(p.stat().st_size for p in args.out.rglob("*") if p.is_file())
    print(f"{stats['tiles']} tiles, {total / 1_000_000:.2f} MB in {args.out} (order 0 to {args.order})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
