"""Regression tests for issue #63: NINA archives with no filter wheel.

Three things went wrong on a NINA layout (``TARGET/DATE/LIGHT/file.fits``) shot
without a filter wheel. The path fallback in ``filter_from_path`` turned the
LIGHT image-type folder into the L filter and split ``L-eXtreme`` on its hyphen
into L. Folder-sub target members dropped the INSTRUME camera so targets built
from lights alone showed no camera, and the gear seed only read cameras from
masters. WBPP's ``masterLight_...`` naming did not match the ``master_`` prefix
check so PixInsight masters were classified as ordinary subs.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_archive_manifest import (  # noqa: E402
    canon_filter,
    classify_by_header,
    filter_from_path,
)
import app as app_module  # noqa: E402
from app import app  # noqa: E402


class TestFilterFromPath(unittest.TestCase):
    def test_light_folder_is_not_a_filter(self):
        p = Path("D:/Astro/NGC 6960/2026-09-01/LIGHT/2026-09-01_22-10-05_300.00s_0001.fits")
        self.assertIsNone(filter_from_path(p))

    def test_dual_band_name_is_not_split_into_l(self):
        p = Path("D:/Astro/NGC 6960/2026-09-01/LIGHT/2026-09-01_L-eXtreme_300.00s_0001.fits")
        self.assertNotEqual(filter_from_path(p), "L")

    def test_real_filter_folder_still_works(self):
        p = Path("D:/Astro/NGC 6960/2026-09-01/LIGHT/Ha/0001.fits")
        self.assertEqual(filter_from_path(p), "Ha")

    def test_underscore_l_in_filename_still_works(self):
        self.assertEqual(filter_from_path(Path("NGC6960_L_300s_0001.fits")), "L")


class TestCanonFilter(unittest.TestCase):
    def test_nofilter_passes_through(self):
        self.assertEqual(canon_filter("NoFilter"), "NoFilter")
        self.assertEqual(canon_filter("No Filter"), "NoFilter")
        self.assertEqual(canon_filter("None"), "NoFilter")

    def test_dual_band_passes_through(self):
        self.assertEqual(canon_filter("L-eXtreme"), "L-eXtreme")


class TestWbppMasterNaming(unittest.TestCase):
    def test_masterlight_prefix_is_master(self):
        p = Path("masterLight_BIN-1_6252x4176_EXPOSURE-300.00s_FILTER-NoFilter_RGB.xisf")
        meta = {"imagetyp": "LIGHT", "exptime": 300.0, "object": "NGC6960",
                "naxis1": 6252, "naxis2": 4176}
        self.assertEqual(classify_by_header(meta, p, size=100_000_000), "master")

    def test_masterflat_is_calibration(self):
        p = Path("masterFlat_BIN-1_6252x4176_FILTER-NoFilter_RGB.xisf")
        meta = {"imagetyp": "FLAT", "exptime": 1.0, "naxis1": 6252, "naxis2": 4176}
        self.assertEqual(classify_by_header(meta, p, size=100_000_000), "calibration")


class TestGearSeedReadsTargetCameras(unittest.TestCase):
    """A target built from lights only has no per_master_fov, but does list
    its cameras. The seed must still pick the camera up."""

    def setUp(self):
        td = Path(tempfile.mkdtemp())
        app_module.MANIFEST_PATH = td / "manifest.json"
        app_module.GEAR_PATH = td / "gear.json"
        for cache in ("_manifest_cache", "_gear_cache",
                      "_manifest_cache_mtime", "_gear_cache_mtime"):
            setattr(app_module, cache, None)
        app_module.MANIFEST_PATH.write_text(json.dumps({"targets": [{
            "target_id": 1,
            "telescopes": ["Askar 103APO"],
            "cameras": ["QHY268C"],
            "per_master_fov": [],
            "filters": {"NoFilter": {"total_hours": 2.0}},
        }]}), encoding="utf-8")
        self.client = app.test_client()

    def test_camera_seeded_from_target_list(self):
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        self.assertIn("QHY268C", r.get_json()["added_cameras"])


if __name__ == "__main__":
    unittest.main()
