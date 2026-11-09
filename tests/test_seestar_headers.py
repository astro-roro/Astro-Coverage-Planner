"""Seestar frames carry pointing as plain RA and DEC in decimal degrees and no
WCS (issue #181). The header values below are copied in shape from real S30 Pro
files; site, serial and name fields are placeholders.
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
    compute_fov_from_meta,
    read_fits_meta,
    target_rotation,
)


def _write_seestar(path: Path, *, ra=11.083335, dec=41.400278, eqmode=1, extra=None):
    hdr = fits.Header()
    hdr["CREATOR"] = "ZWO Seestar S30 Pro"
    hdr["TELESCOP"] = "S30 Pro_0000000000"
    hdr["INSTRUME"] = "imx585"
    hdr["BAYERPAT"] = "GRBG"
    hdr["FILTER"] = "IRCUT"
    hdr["IMAGETYP"] = "Light"
    hdr["EXPTIME"] = 30.0
    hdr["EQMODE"] = eqmode
    hdr["FOCALLEN"] = 160.0
    hdr["XPIXSZ"] = 2.90000009536743
    hdr["YPIXSZ"] = 2.90000009536743
    hdr["RA"] = ra
    hdr["DEC"] = dec
    hdr["SITELONG"] = 0.0
    hdr["SITELAT"] = 0.0
    hdr["DATE-OBS"] = "2026-08-11T01:23:23.612690"
    for k, v in (extra or {}).items():
        hdr[k] = v
    # Real frames are 2160 x 3840. A zero array that size compresses to nothing
    # on disk, so write the real shape.
    data = np.zeros((3840, 2160), dtype=np.int16)
    fits.PrimaryHDU(data=data, header=hdr).writeto(path, overwrite=True)


class TestSeestarPointing(unittest.TestCase):

    def _meta(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "Light_target_30.0s_IRCUT_20260811-032402.fit"
            _write_seestar(p, **kw)
            return read_fits_meta(p)

    def test_degrees_read_as_pointing(self):
        m = self._meta()
        self.assertTrue(m["ok"])
        self.assertAlmostEqual(m["ra_deg"], 11.083335, places=5)
        self.assertAlmostEqual(m["dec_deg"], 41.400278, places=5)
        self.assertFalse(m["has_wcs"])
        self.assertIsNone(m["rotation_deg"])

    def test_field_size_from_optics(self):
        m = self._meta()
        fov, pix, method = compute_fov_from_meta(m)
        self.assertEqual(method, "focal_length")
        self.assertAlmostEqual(pix, 3.74, places=2)
        # 2160 x 3840 pixels is about 2.24 x 3.99 degrees, in arcmin.
        self.assertAlmostEqual(fov[0] / 60.0, 2.24, delta=0.03)
        self.assertAlmostEqual(fov[1] / 60.0, 3.99, delta=0.03)

    def test_unknown_angle_draws_at_zero_and_is_flagged_none(self):
        m = self._meta()
        fov, _pix, _method = compute_fov_from_meta(m)
        r = target_rotation([{"rotation_deg": None, "fov_arcmin": fov, "hours": 1.0}])
        self.assertEqual(r["rotation_source"], "none")
        self.assertEqual(r["rotation_deg"], 0.0)

    def test_alt_az_frame_reads_the_same(self):
        m = self._meta(eqmode=0, ra=314.40417, dec=44.458889)
        self.assertAlmostEqual(m["ra_deg"], 314.40417, places=4)

    def test_block_carries_coords(self):
        with tempfile.TemporaryDirectory() as td:
            paths, metas = [], {}
            for i in range(2):
                p = Path(td) / f"Light_t_30.0s_IRCUT_{i}.fit"
                _write_seestar(p)
                paths.append(str(p))
                metas[str(p)] = read_fits_meta(p)
            blocks = build_folder_sub_blocks(td, paths, metas)
        self.assertEqual(len(blocks), 1)
        self.assertAlmostEqual(blocks[0]["ra_deg"], 11.083335, places=5)
        self.assertFalse(blocks[0]["has_wcs"])

    def test_out_of_range_or_text_values_are_ignored(self):
        for ra, dec in ((400.0, 10.0), (10.0, 95.0), ("20 45 38", "+30 43 00")):
            m = self._meta(ra=ra, dec=dec)
            self.assertIsNone(m.get("ra_deg"), (ra, dec))

    def test_real_wcs_wins_over_plain_ra_dec(self):
        extra = {"CRVAL1": 100.0, "CRVAL2": 20.0, "CRPIX1": 1080.0, "CRPIX2": 1920.0,
                 "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
                 "CD1_1": -0.001, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 0.001}
        m = self._meta(extra=extra)
        self.assertTrue(m["has_wcs"])
        self.assertAlmostEqual(m["ra_deg"], 100.0, places=2)


if __name__ == "__main__":
    unittest.main()
