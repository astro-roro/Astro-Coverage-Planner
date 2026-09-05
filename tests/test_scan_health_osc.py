"""Scan health surfaced in the app, and colour camera follow-through.

The scanner already writes integrity flags nobody opens. The manifest API now
passes a slim scan_health block through so the rail can show it. On the gear
side, a colour camera is detected from its name when the Bayer keyword is
missing, and the gear seed builds a camera's filter list from the real filter
labels behind each band (OSC, L-eXtreme) rather than the bands themselves.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_archive_manifest import _camera_name_is_colour, read_fits_meta  # noqa: E402
import app as app_module  # noqa: E402
from app import app  # noqa: E402


class TestCameraNameColour(unittest.TestCase):
    def test_colour_suffixes(self):
        for name in ("QHY268C", "ASI2600MC", "ZWO ASI2600MC Pro", "Player One Poseidon-C", "QHY 128C"):
            self.assertIs(_camera_name_is_colour(name), True, name)

    def test_mono_suffixes(self):
        for name in ("QHY268M", "ASI2600MM", "ZWO ASI2600MM Pro", "QHY600M", "Poseidon-M"):
            self.assertIs(_camera_name_is_colour(name), False, name)

    def test_unknown_names(self):
        for name in ("Nikon D850", "Canon EOS R", "", None):
            self.assertIsNone(_camera_name_is_colour(name), name)


class TestColourFromNameFallback(unittest.TestCase):
    def test_instrume_suffix_marks_colour_without_bayerpat(self):
        hdr = fits.Header()
        hdr["NAXIS"] = 2; hdr["NAXIS1"] = 8; hdr["NAXIS2"] = 8
        hdr["EXPTIME"] = 300.0
        hdr["INSTRUME"] = "QHY268C"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.fits"
            fits.PrimaryHDU(data=np.zeros((8, 8), dtype=np.int16), header=hdr).writeto(p)
            self.assertTrue(read_fits_meta(p)["colour"])


def _redirect_state():
    td = Path(tempfile.mkdtemp())
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module.GEAR_PATH = td / "gear.json"
    for cache in ("_manifest_cache", "_gear_cache", "_manifest_cache_mtime", "_gear_cache_mtime"):
        setattr(app_module, cache, None)
    return td


OSC_TARGET = {
    "target_id": 1,
    "telescopes": ["Askar 103APO"],
    "cameras": ["QHY268C"],
    "colour_cameras": ["QHY268C"],
    "per_master_fov": [],
    "filters": {
        "R": {"total_hours": 3.5, "files": 1, "paths": ["x"], "sources": {"OSC": 3.5}},
        "G": {"total_hours": 3.5, "files": 1, "paths": ["x"], "sources": {"OSC": 3.5}},
        "B": {"total_hours": 3.5, "files": 1, "paths": ["x"], "sources": {"OSC": 3.5}},
        "Ha": {"total_hours": 2.0, "files": 1, "paths": ["x"], "sources": {"L-eXtreme": 2.0}},
        "OIII": {"total_hours": 2.0, "files": 1, "paths": ["x"], "sources": {"L-eXtreme": 2.0}},
    },
}


class TestScanHealthApi(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()

    def test_scan_health_passed_through(self):
        app_module.MANIFEST_PATH.write_text(json.dumps({
            "scan_date": "2026-09-04",
            "targets": [OSC_TARGET],
            "integrity_flags": {
                "sii_ha_correlation_suspects": [{"a": 1}],
                "masters_missing_wcs": ["m1", "m2"],
                "masters_ambiguous_filter": [],
                "session_dedup_hours_dropped": 1.5,
                "content_dedup_hours_dropped": 0.0,
                "unrecognised_filter_names": [{"name": "IDAS + RED", "frames": 40}],
            },
        }), encoding="utf-8")
        body = self.client.get("/api/manifest").get_json()
        h = body["scan_health"]
        self.assertEqual(h["sii_ha_suspects"], 1)
        self.assertEqual(h["masters_missing_wcs"], 2)
        self.assertEqual(h["masters_ambiguous_filter"], 0)
        self.assertAlmostEqual(h["dedup_hours_dropped"], 1.5)
        self.assertEqual(h["unrecognised_filters"], [{"name": "IDAS + RED", "frames": 40}])

    def test_scan_health_absent_on_old_manifest(self):
        app_module.MANIFEST_PATH.write_text(json.dumps({"targets": [OSC_TARGET]}), encoding="utf-8")
        body = self.client.get("/api/manifest").get_json()
        self.assertIsNone(body["scan_health"])


class TestGearSeedOsc(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        app_module.MANIFEST_PATH.write_text(json.dumps({"targets": [OSC_TARGET]}), encoding="utf-8")
        self.client = app.test_client()

    def test_colour_camera_seeds_real_filter_labels(self):
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        gear = json.loads(app_module.GEAR_PATH.read_text(encoding="utf-8"))
        cam = next(c for c in gear["cameras"] if c["name"] == "QHY268C")
        self.assertTrue(cam["colour"])
        self.assertEqual(sorted(cam["filters"]), ["L-eXtreme", "OSC"])
        self.assertEqual(cam["filters"]["OSC"]["default_sub_s"], 120)
        self.assertEqual(cam["filters"]["L-eXtreme"]["default_sub_s"], 300)


if __name__ == "__main__":
    unittest.main()
