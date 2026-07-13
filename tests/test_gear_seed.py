"""Tests for /api/gear/seed and the underlying dedupe logic.

Seed reads the manifest, extracts telescope + camera names, and merges
them into gear.json — skipping anything that fuzzy-matches existing
gear. The fuzzy matcher strips brand prefixes ("ZWO ASI2600MM Pro"
should NOT seed a duplicate alongside "ASI2600MM Pro") and the slug-id
generator falls back to `-2`, `-3` suffixes on id collisions.

Lines 1043-1107 in app.py were ~17 uncovered lines before this test.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _redirect_state():
    td = Path(tempfile.mkdtemp())
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module.GEAR_PATH = td / "gear.json"
    for cache in ("_manifest_cache", "_gear_cache"):
        setattr(app_module, cache, None)
    for cache in ("_manifest_cache_mtime", "_gear_cache_mtime"):
        setattr(app_module, cache, None)
    return td


def _write_manifest(telescope_camera_filter_triples):
    """Write a minimal manifest where each triple becomes one target."""
    targets = []
    for i, (tel, cam, filt) in enumerate(telescope_camera_filter_triples):
        targets.append({
            "target_id": i + 1,
            "telescopes": [tel],
            "cameras": [cam],
            "per_master_fov": [{
                "telescope": tel,
                "camera": cam,
                "filter": filt,
                "pix_arcsec": 1.0,
                "fov_arcmin": [60.0, 40.0],
                "pixel_size_um": 3.76,
                "sensor_px": [6248, 4176],
                "focal_length_mm": 600.0,
                "aperture_mm": 130.0,
            }],
        })
    app_module.MANIFEST_PATH.write_text(
        json.dumps({"targets": targets}), encoding="utf-8"
    )


class TestSeedAgainstEmptyGear(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()

    def test_no_manifest_returns_empty(self):
        # No manifest at all — seed returns empty lists, doesn't crash.
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["added_telescopes"], [])
        self.assertEqual(body["added_cameras"], [])

    def test_empty_manifest_returns_empty(self):
        app_module.MANIFEST_PATH.write_text(
            json.dumps({"targets": []}), encoding="utf-8"
        )
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["added_telescopes"], [])

    def test_new_telescope_and_camera_added(self):
        _write_manifest([("RASA 11", "ASI6200MM Pro", "Ha")])
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("RASA 11", body["added_telescopes"])
        self.assertIn("ASI6200MM Pro", body["added_cameras"])
        # Persisted to gear.json with sensible defaults.
        gear = json.loads(app_module.GEAR_PATH.read_text(encoding="utf-8"))
        tel_names = {t["name"] for t in gear["telescopes"]}
        self.assertIn("RASA 11", tel_names)


class TestSeedDedupe(unittest.TestCase):
    """The dedupe paths inside _seed_gear_from_manifest — fuzzy name
    match, id collision, idempotent re-seeding."""

    def setUp(self):
        _redirect_state()
        self.client = app.test_client()

    def test_idempotent_double_seed(self):
        _write_manifest([("RASA 11", "ASI6200MM Pro", "Ha")])
        first = self.client.post("/api/gear/seed").get_json()
        self.assertEqual(len(first["added_telescopes"]), 1)
        # Second seed with the same manifest — nothing new added.
        second = self.client.post("/api/gear/seed").get_json()
        self.assertEqual(second["added_telescopes"], [])
        self.assertEqual(second["added_cameras"], [])

    def test_fuzzy_match_brand_prefix(self):
        # Pre-seed gear.json with "ASI2600MM Pro" — manifest later has
        # "ZWO ASI2600MM Pro". The _NORM_STRIP regex drops "zwo" so
        # both normalise to the same key and dedupe.
        app_module.save_gear({
            "version": 2,
            "telescopes": [],
            "cameras": [{
                "id": "asi2600mm-pro",
                "name": "ASI2600MM Pro",
                "pixel_size_um": 3.76, "sensor_px": [6248, 4176],
                "filters": {},
            }],
        })
        _write_manifest([("RASA 11", "ZWO ASI2600MM Pro", "Ha")])
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        added = r.get_json()["added_cameras"]
        self.assertNotIn("ZWO ASI2600MM Pro", added,
            "fuzzy matcher should have deduped against existing ASI2600MM Pro")

    def test_id_collision_falls_through_to_dash_n(self):
        # Pre-seed gear with id "rasa-11" but a different name. New
        # telescope with the same slug should rename to "rasa-11-2".
        app_module.save_gear({
            "version": 2,
            "telescopes": [{
                "id": "rasa-11",
                "name": "Different Telescope",
                "focal_length_mm": 600, "aperture_mm": 130,
            }],
            "cameras": [],
        })
        _write_manifest([("RASA 11", "ASI6200MM Pro", "Ha")])
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        self.assertIn("RASA 11", r.get_json()["added_telescopes"])
        gear = json.loads(app_module.GEAR_PATH.read_text(encoding="utf-8"))
        ids = {t["id"] for t in gear["telescopes"]}
        self.assertIn("rasa-11", ids)     # the original
        self.assertIn("rasa-11-2", ids,   # the new one with collision suffix
            f"expected rasa-11-2 in {ids}")

    def test_multiple_collisions_chain_to_dash_3(self):
        # Pre-seed two telescopes with ids "x" and "x-2"; the new one
        # gets "x-3".
        app_module.save_gear({
            "version": 2,
            "telescopes": [
                {"id": "x",   "name": "Different A",
                 "focal_length_mm": 600, "aperture_mm": 130},
                {"id": "x-2", "name": "Different B",
                 "focal_length_mm": 600, "aperture_mm": 130},
            ],
            "cameras": [],
        })
        _write_manifest([("X", "ASI6200MM Pro", "Ha")])
        self.client.post("/api/gear/seed")
        gear = json.loads(app_module.GEAR_PATH.read_text(encoding="utf-8"))
        ids = {t["id"] for t in gear["telescopes"]}
        self.assertIn("x-3", ids, f"expected x-3 in {ids}")


class TestNormGearName(unittest.TestCase):
    """Unit tests on the fuzzy-matcher itself — pins down the
    brand-stripping behaviour that the dedupe path relies on."""

    def test_brand_prefix_stripped(self):
        # Per the regex: zwo / qhy / celestron / svbony / skywatcher /
        # williams optics / askar / takahashi all strip out.
        self.assertEqual(
            app_module._norm_gear_name("ZWO ASI2600MM Pro"),
            app_module._norm_gear_name("ASI2600MM Pro"),
        )

    def test_focal_ratio_stripped(self):
        # "f/2", "f2.2" etc. are stripped so "RASA f/2" == "RASA".
        self.assertEqual(
            app_module._norm_gear_name("RASA f/2"),
            app_module._norm_gear_name("RASA"),
        )

    def test_unit_suffixes_stripped_when_standalone(self):
        # The regex uses \b boundaries — "mm" / "inch" / "in" only strip
        # as standalone words, not when glued to a digit like "600mm".
        # That mirrors what users actually type: "RASA 11 inch" vs
        # "ASI2600MM" (the MM is part of the model name).
        self.assertEqual(
            app_module._norm_gear_name("RASA 11 inch"),
            app_module._norm_gear_name("RASA 11"),
        )

    def test_punctuation_collapsed(self):
        # Hyphens, commas, exclamations all become spaces and stripped.
        self.assertEqual(
            app_module._norm_gear_name("ASI-600!"),
            app_module._norm_gear_name("ASI 600"),
        )

    def test_pro_suffix_stripped(self):
        # "Pro" is one of the common suffixes the regex drops, so
        # "ASI2600MM Pro" matches plain "ASI2600MM".
        self.assertEqual(
            app_module._norm_gear_name("ASI2600MM Pro"),
            app_module._norm_gear_name("ASI2600MM"),
        )

    def test_empty_string_normalises_to_empty(self):
        self.assertEqual(app_module._norm_gear_name(""), "")
        self.assertEqual(app_module._norm_gear_name("   "), "")


if __name__ == "__main__":
    unittest.main()
