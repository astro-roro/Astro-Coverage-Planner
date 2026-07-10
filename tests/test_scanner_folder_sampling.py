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
import astropy.units as u
from astropy.coordinates import Angle
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
    # Let astropy carry rounding across the sexagesimal fields so a value like
    # dec=30.7 never emits an invalid "... 60.00" seconds component that only
    # parsed before because astropy leniently reinterpreted it on read.
    return Angle(ra_deg, unit=u.deg).to_string(
        unit=u.hourangle, sep=" ", precision=2, pad=True)


def _deg_to_dms(dec_deg: float) -> str:
    return Angle(dec_deg, unit=u.deg).to_string(
        unit=u.deg, sep=" ", precision=2, pad=True, alwayssign=True)


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


class TestNotOkMetaExcluded(unittest.TestCase):
    """A read that raised partway (ok=False) must not leak into counted blocks.

    read_fits_meta sets filter/exptime early, so a later raise leaves ok=False
    with real-looking partial fields. build_folder_sub_blocks must skip those so
    corrupt reads never contribute to block counts or hours.
    """

    def test_ok_false_meta_produces_no_block(self):
        # ok=False but filter/exptime/coords all look real (the leak scenario).
        bad_meta = {
            "ok": False, "filter": "Ha", "exptime": 300.0,
            "ra_deg": 311.4, "dec_deg": 30.7, "has_wcs": False,
            "naxis1": 64, "naxis2": 64,
        }
        blocks = build_folder_sub_blocks(
            "/some/folder", ["/some/folder/Light_0001.fit"],
            {"/some/folder/Light_0001.fit": bad_meta})
        self.assertEqual(blocks, [])

    def test_ok_false_dropped_but_ok_true_kept(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "Light_0001.fit"
            _write_light(good, filt="H", exptime=300.0)
            good_meta = read_fits_meta(good)
            bad = Path(td) / "Light_0002.fit"
            bad_meta = {"ok": False, "filter": "Ha", "exptime": 300.0,
                        "ra_deg": 311.4, "dec_deg": 30.7,
                        "naxis1": 64, "naxis2": 64}
            paths = [str(good), str(bad)]
            meta_by_path = {str(good): good_meta, str(bad): bad_meta}
            blocks = build_folder_sub_blocks(td, paths, meta_by_path)
        # Only the ok=True frame is counted.
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["n_subs"], 1)


if __name__ == "__main__":
    unittest.main()
