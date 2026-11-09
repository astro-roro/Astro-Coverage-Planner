"""Seestar filter names (issue #181). IRCUT on a colour sensor is one-shot colour
data, and a Seestar's LP is its built-in dual-band filter. Header values are in
the shape of real S30 Pro files with placeholder site and serial fields.
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
    bands_for,
    filter_label,
    read_fits_meta,
)


def _meta(*, filt, creator=None, telescop="S30 Pro_0000000000", bayer="GRBG"):
    hdr = fits.Header()
    if creator:
        hdr["CREATOR"] = creator
    hdr["TELESCOP"] = telescop
    hdr["INSTRUME"] = "imx585"
    if bayer:
        hdr["BAYERPAT"] = bayer
    hdr["FILTER"] = filt
    hdr["IMAGETYP"] = "Light"
    hdr["EXPTIME"] = 10.0
    hdr["DATE-OBS"] = "2026-04-27T01:56:12.000000"
    data = np.zeros((16, 16), dtype=np.int16)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "Light_t_10.0s_x_20260427-025612.fit"
        fits.PrimaryHDU(data=data, header=hdr).writeto(p, overwrite=True)
        return read_fits_meta(p)


class TestIrcut(unittest.TestCase):
    def test_colour_ircut_credits_rgb(self):
        self.assertEqual(bands_for("IRCUT", True), ["R", "G", "B"])

    def test_colour_ircut_shows_as_osc(self):
        self.assertEqual(filter_label("IRCUT", True), "OSC")

    def test_mono_ircut_keeps_its_own_band(self):
        self.assertEqual(bands_for("IRCUT", False), ["IRCUT"])
        self.assertEqual(filter_label("IRCUT", False), "IRCUT")

    def test_ircut_frame_from_a_seestar(self):
        m = _meta(filt="IRCUT", creator="ZWO Seestar S30 Pro")
        self.assertTrue(m["colour"])
        self.assertEqual(bands_for(m["filter"], m["colour"]), ["R", "G", "B"])


class TestSeestarLp(unittest.TestCase):
    def test_seestar_creator_makes_lp_dual_band(self):
        m = _meta(filt="LP", creator="ZWO Seestar S30 Pro")
        self.assertEqual(m["filter"], "Duo-Band")
        self.assertEqual(bands_for(m["filter"], m["colour"]), ["Ha", "OIII"])

    def test_telescope_name_alone_is_enough(self):
        for scope in ("S30 Pro_0000000000", "S50_0000000000", "Seestar S50"):
            m = _meta(filt="LP", telescop=scope)
            self.assertEqual(m["filter"], "Duo-Band", scope)

    def test_lp_on_another_camera_is_unchanged(self):
        m = _meta(filt="LP", telescop="Refractor 80", creator="N.I.N.A.")
        self.assertEqual(m["filter"], "LP")
        self.assertEqual(bands_for(m["filter"], m["colour"]), ["LP"])

    def test_other_seestar_filters_are_unchanged(self):
        m = _meta(filt="IRCUT", creator="ZWO Seestar S30 Pro")
        self.assertEqual(m["filter"], "IRCUT")


if __name__ == "__main__":
    unittest.main()
