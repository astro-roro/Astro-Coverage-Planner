"""Regression tests for per-file header classification (audit fix 1).

The scanner used to header-read ONE representative file per folder and let every
sibling inherit that file's filter / exposure / IMAGETYP. A folder mixing
filters or exposures reported as a single block, and a mis-named calibration
frame among lights inherited a light neighbour's classification. Every file is
now read on its own header, so ``build_folder_sub_blocks`` splits a folder into
one block per real (filter, exptime) and ``classify_by_header`` sees each file's
own IMAGETYP.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import (  # noqa: E402
    build_folder_sub_blocks,
    classify_by_header,
    read_fits_meta,
)


def _write_light(path: Path, *, filt: str, exptime: float, imagetyp: str = "Light",
                 ra: float = 311.4, dec: float = 30.7, naxis: int = 64):
    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = naxis
    hdr["NAXIS2"] = naxis
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = imagetyp
    hdr["EXPTIME"] = exptime
    hdr["FILTER"] = filt
    hdr["OBJECT"] = "Test Target"
    hdr["DATE-OBS"] = "2021-07-30T10:53:29"
    hdr["OBJCTRA"] = _deg_to_hms(ra)
    hdr["OBJCTDEC"] = _deg_to_dms(dec)
    fits.PrimaryHDU(data=np.zeros((naxis, naxis), dtype=np.int16), header=hdr).writeto(path, overwrite=True)


def _deg_to_hms(ra_deg: float) -> str:
    h = ra_deg / 15.0
    hh = int(h); mm = int((h - hh) * 60); ss = (((h - hh) * 60) - mm) * 60
    return f"{hh:02d} {mm:02d} {ss:05.2f}"


def _deg_to_dms(dec_deg: float) -> str:
    sign = "+" if dec_deg >= 0 else "-"
    d = abs(dec_deg)
    dd = int(d); mm = int((d - dd) * 60); ss = (((d - dd) * 60) - mm) * 60
    return f"{sign}{dd:02d} {mm:02d} {ss:05.2f}"


class TestMixedFilterFolder(unittest.TestCase):
    """A folder of 2x Ha@300 + 2x OIII@600 + 1x SII@600 -> three honest blocks."""

    def _build(self, td):
        specs = [
            ("Light_0001.fit", "H", 300.0),
            ("Light_0002.fit", "H", 300.0),
            ("Light_0003.fit", "O", 600.0),
            ("Light_0004.fit", "O", 600.0),
            ("Light_0005.fit", "S", 600.0),
        ]
        paths = []
        meta_by_path = {}
        for name, filt, exp in specs:
            p = Path(td) / name
            _write_light(p, filt=filt, exptime=exp)
            paths.append(str(p))
            meta_by_path[str(p)] = read_fits_meta(p)
        return build_folder_sub_blocks(td, paths, meta_by_path)

    def test_splits_into_three_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            blocks = self._build(td)
        by_key = {(b["filter"], round(b["exptime"], 1)): b for b in blocks}
        self.assertEqual(set(by_key), {("Ha", 300.0), ("OIII", 600.0), ("SII", 600.0)})

    def test_per_block_counts_and_hours(self):
        with tempfile.TemporaryDirectory() as td:
            blocks = self._build(td)
        by_key = {(b["filter"], round(b["exptime"], 1)): b for b in blocks}
        self.assertEqual(by_key[("Ha", 300.0)]["n_subs"], 2)
        self.assertAlmostEqual(by_key[("Ha", 300.0)]["total_hours"], 600.0 / 3600.0, places=4)
        self.assertEqual(by_key[("OIII", 600.0)]["n_subs"], 2)
        self.assertAlmostEqual(by_key[("OIII", 600.0)]["total_hours"], 1200.0 / 3600.0, places=4)
        self.assertEqual(by_key[("SII", 600.0)]["n_subs"], 1)
        self.assertAlmostEqual(by_key[("SII", 600.0)]["total_hours"], 600.0 / 3600.0, places=4)

    def test_no_filter_wins_the_whole_folder(self):
        """The old single-sample design would collapse all five subs to ONE block."""
        with tempfile.TemporaryDirectory() as td:
            blocks = self._build(td)
        self.assertEqual(len(blocks), 3)
        self.assertNotEqual(len(blocks), 1)


class TestMisnamedCalibrationClassifiedByOwnHeader(unittest.TestCase):
    """A frame named like a light but carrying IMAGETYP=FLAT is calibration.

    Previously it could inherit a light neighbour's classification via the folder
    sample; now classify_by_header reads its own header.
    """

    def test_flat_header_beats_light_name(self):
        p = Path("Light_NGC6960_1s.fit")  # name looks like a light
        meta = {"imagetyp": "FLAT", "exptime": 1.0, "object": "NGC6960",
                "naxis1": 64, "naxis2": 64}
        self.assertEqual(classify_by_header(meta, p, size=100_000), "calibration")

    def test_light_header_stays_sub(self):
        p = Path("Light_NGC6960_300s.fit")
        meta = {"imagetyp": "LIGHT", "exptime": 300.0, "object": "NGC6960",
                "naxis1": 64, "naxis2": 64}
        self.assertEqual(classify_by_header(meta, p, size=100_000), "sub")


if __name__ == "__main__":
    unittest.main()
