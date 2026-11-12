"""A Seestar LP master written as XISF reads as the dual-band filter (issue #181).

PixInsight's WBPP writes its masters as XISF. The Seestar rule that renames LP
to Duo-Band used to run in the FITS reader only, so a master stacked from LP
subs landed in a band of its own called LP. Its hours were then added to the
Ha and OIII hours of the subs it was stacked from. Header values are made up
in the shape of a Seestar S30 Pro file.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_archive_manifest as bam  # noqa: E402
from hours_fixtures import _master, _subs  # noqa: E402

SCOPE = "S30 Pro_0000000000"
EXP = 10.0
N = 360  # 360 x 10 s = 1 hour


def _xisf_master(keywords: dict) -> dict:
    class _FakeXISF:
        def __init__(self, path):
            pass

        def get_images_metadata(self):
            return [{
                "geometry": (64, 48, 3),
                "colorSpace": "RGB",
                "FITSKeywords": {k: [{"value": v, "comment": ""}]
                                 for k, v in keywords.items()},
            }]

    with mock.patch.dict("sys.modules", {"xisf": mock.MagicMock(XISF=_FakeXISF)}):
        return bam.read_xisf_meta(Path("masterLight_BIN-1_FILTER-LP_mono.xisf"))


def _seestar_master(**extra) -> dict:
    kw = {"TELESCOP": SCOPE, "INSTRUME": "imx585", "BAYERPAT": "GRBG",
          "FILTER": "LP", "IMAGETYP": "Master Light", "EXPTIME": EXP,
          "NCOMBINE": N, "OBJECT": "NGC 7000"}
    kw.update(extra)
    return _xisf_master(kw)


def _seestar_sub() -> dict:
    hdr = fits.Header()
    hdr["CREATOR"] = "ZWO Seestar S30 Pro"
    hdr["TELESCOP"] = SCOPE
    hdr["INSTRUME"] = "imx585"
    hdr["BAYERPAT"] = "GRBG"
    hdr["FILTER"] = "LP"
    hdr["IMAGETYP"] = "Light"
    hdr["EXPTIME"] = EXP
    hdr["DATE-OBS"] = "2026-04-27T01:56:12.000000"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "Light_NGC 7000_10.0s_LP_20260427-025612.fit"
        fits.PrimaryHDU(data=np.zeros((16, 16), dtype=np.int16),
                        header=hdr).writeto(p, overwrite=True)
        return bam.read_fits_meta(p)


class TestSeestarXisfMasterFilter(unittest.TestCase):
    def test_lp_master_reads_as_the_same_filter_as_its_subs(self):
        master = _seestar_master()
        sub = _seestar_sub()
        self.assertEqual(sub["filter"], "Duo-Band")
        self.assertEqual(master["filter"], sub["filter"])
        self.assertEqual(bam.bands_for(master["filter"], master["colour"]),
                         ["Ha", "OIII"])

    def test_creator_alone_is_enough(self):
        m = _seestar_master(TELESCOP="", CREATOR="ZWO Seestar S30 Pro")
        self.assertEqual(m["filter"], "Duo-Band")

    def test_lp_master_from_another_camera_is_unchanged(self):
        m = _seestar_master(TELESCOP="Refractor 80", INSTRUME="ASI2600MC")
        self.assertEqual(m["filter"], "LP")

    def test_ircut_master_is_unchanged(self):
        m = _seestar_master(FILTER="IRCUT")
        self.assertEqual(m["filter"], "IRCUT")


class TestSeestarMasterIsNotCountedTwice(unittest.TestCase):
    def test_master_hours_replace_the_subs_hours(self):
        master_meta = _seestar_master()
        sub_meta = _seestar_sub()
        scope, cam = master_meta["telescope"], master_meta["camera"]
        self.assertEqual((scope, cam), (sub_meta["telescope"], sub_meta["camera"]))

        members = [
            _subs(n=N, exptime=EXP, filt=sub_meta["filter"], colour=True,
                  scope=scope, cam=cam, bucket="/data/archive/NGC 7000/lights",
                  session_root="/data/archive/NGC 7000"),
            _master(ncombine=master_meta["ncombine"], exptime=master_meta["exptime"],
                    filt=master_meta["filter"], scope=scope, cam=cam,
                    path="/data/archive/WBPP/master/masterLight_FILTER-LP.xisf",
                    session_root="/data/archive/WBPP"),
        ]
        members[1]["colour"] = master_meta["colour"]

        filters = bam.build_filters_data(members)
        self.assertEqual(set(filters), {"Ha", "OIII"})
        for band in ("Ha", "OIII"):
            self.assertAlmostEqual(filters[band]["headline_hours"], 1.0, places=2)
        total = bam.total_hours_across([{"filters": filters}], "headline_hours")
        self.assertAlmostEqual(total, 2.0, places=1)


if __name__ == "__main__":
    unittest.main()
