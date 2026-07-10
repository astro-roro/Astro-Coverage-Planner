"""Regression tests for RA-seam cluster centre (fix 3) and pointing-only subs
being counted (fix 4).
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
    circular_mean_deg,
    cluster_by_coords,
    read_fits_meta,
)


def _ang_sep(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


class TestCircularMeanRa(unittest.TestCase):

    def test_seam_straddle_centre_near_zero(self):
        ras = [359.9, 0.1, 359.8, 0.2]
        c = circular_mean_deg(ras)
        self.assertLess(_ang_sep(c, 0.0), 1.0,
                        f"seam centre {c:.2f} should sit near 0, not the antipode")

    def test_naive_median_would_give_antipode(self):
        """Document the failure mode the circular mean avoids: a plain
        ``median % 360`` of a seam cluster returns ~180 (antipodal)."""
        ras = np.array([359.9, 0.1, 359.8, 0.2])
        naive = float(np.median(ras)) % 360.0
        self.assertAlmostEqual(naive, 180.0, places=1)
        self.assertGreater(_ang_sep(circular_mean_deg(ras), naive), 90.0)

    def test_non_seam_cluster_matches_plain_mean(self):
        ras = [120.0, 120.1, 119.9, 120.2]
        self.assertLess(_ang_sep(circular_mean_deg(ras), 120.05), 0.1)

    def test_empty_returns_zero(self):
        self.assertEqual(circular_mean_deg([]), 0.0)

    def test_clustered_seam_target_gets_correct_centre(self):
        """End-to-end: cluster members straddling the seam -> centre near 0."""
        members = [
            {"ra_deg": 359.95, "dec_deg": 12.0},
            {"ra_deg": 0.05, "dec_deg": 12.01},
            {"ra_deg": 359.9, "dec_deg": 11.99},
        ]
        clusters = cluster_by_coords(members, radius_arcmin=30.0)
        self.assertEqual(len(clusters), 1)
        idxs = clusters[0]
        ra_c = circular_mean_deg([members[k]["ra_deg"] for k in idxs])
        self.assertLess(_ang_sep(ra_c, 0.0), 0.5)


def _write_pointing_only(path: Path, *, filt="H", exptime=300.0):
    """A light frame with OBJCTRA/OBJCTDEC pointing but NO plate-solve WCS."""
    hdr = fits.Header()
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = 64
    hdr["NAXIS2"] = 64
    hdr["BITPIX"] = 16
    hdr["IMAGETYP"] = "Light"
    hdr["EXPTIME"] = exptime
    hdr["FILTER"] = filt
    hdr["OBJECT"] = "Pointing Target"
    hdr["DATE-OBS"] = "2021-08-01T20:00:00"
    hdr["OBJCTRA"] = "20 45 38.00"   # ~311.4 deg
    hdr["OBJCTDEC"] = "+30 43 00.0"  # ~30.72 deg
    hdr["FOCALLEN"] = 334
    hdr["XPIXSZ"] = 3.8
    # deliberately no CRVAL/CTYPE -> no plate solve
    fits.PrimaryHDU(data=np.zeros((64, 64), dtype=np.int16), header=hdr).writeto(path, overwrite=True)


class TestPointingOnlySubsCounted(unittest.TestCase):

    def test_read_populates_coords_without_wcs(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_0001.fit"
            _write_pointing_only(p)
            meta = read_fits_meta(p)
        self.assertTrue(meta["ok"])
        self.assertFalse(meta["has_wcs"])
        self.assertIsNotNone(meta["ra_deg"])
        self.assertIsNotNone(meta["dec_deg"])

    def test_block_carries_coords_and_enters_clustering(self):
        with tempfile.TemporaryDirectory() as td:
            paths, meta_by_path = [], {}
            for i in range(3):
                p = Path(td) / f"Light_{i:04d}.fit"
                _write_pointing_only(p)
                paths.append(str(p))
                meta_by_path[str(p)] = read_fits_meta(p)
            blocks = build_folder_sub_blocks(td, paths, meta_by_path)
        self.assertEqual(len(blocks), 1)
        b = blocks[0]
        self.assertFalse(b["has_wcs"])          # honestly marked as not solved
        self.assertIsNotNone(b["ra_deg"])       # but has fallback coords
        self.assertEqual(b["n_subs"], 3)
        # The clustering-inclusion predicate (fix 4) keys on coords, not has_wcs:
        included = b.get("ra_deg") is not None and b.get("dec_deg") is not None
        self.assertTrue(included)
        # A pointing-only member clusters by its fallback coords.
        member = {"ra_deg": b["ra_deg"], "dec_deg": b["dec_deg"]}
        self.assertEqual(len(cluster_by_coords([member], radius_arcmin=30.0)), 1)


if __name__ == "__main__":
    unittest.main()
