"""Bounds on what an unauthenticated caller can make ACP write to disk.

Nothing capped the size of a request body, and /api/plans/match stored the
raw submitted object under a caller-chosen profile name with no ceiling on
how many profiles it kept. Against a running instance on 2026-09-07 a single
POST carrying a two megabyte junk field wrote all two megabytes into
data/fingerprints.json, and fifty distinct profile names left fifty entries.
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

FP = {
    "profile_name": "Rig A",
    "camera": {"name": "QHY268M", "sensor_px": [6280, 4210], "pixel_size_um": 3.76},
    "filters": ["Ha", "OIII", "SII"],
    "mount": {"name": "EQ6-R"},
    "focal_length_mm": 530,
}


class LimitsCase(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        td = Path(self._td.name)
        for name in ("FINGERPRINTS_PATH", "PLANS_PATH", "GEAR_PATH", "SITES_PATH"):
            setattr(app_module, name, td / f"{name.lower()}.json")
        for cache in ("_fingerprints_cache", "_fingerprints_cache_mtime",
                      "_plans_cache", "_plans_cache_mtime",
                      "_gear_cache", "_gear_cache_mtime"):
            if hasattr(app_module, cache):
                setattr(app_module, cache, None)
        self.client = app.test_client()

    def post(self, body):
        return self.client.post("/api/plans/match", json=body)

    def stored(self) -> dict:
        return json.loads(app_module.FINGERPRINTS_PATH.read_text(encoding="utf-8"))


class TestBodySizeCap(LimitsCase):

    def test_an_oversized_body_is_refused(self):
        fat = dict(FP)
        fat["junk"] = "A" * (app_module.MAX_BODY_BYTES + 1024)
        self.assertEqual(self.post(fat).status_code, 413)

    def test_nothing_is_written_for_a_refused_body(self):
        fat = dict(FP)
        fat["junk"] = "A" * (app_module.MAX_BODY_BYTES + 1024)
        self.post(fat)
        self.assertFalse(app_module.FINGERPRINTS_PATH.exists())

    def test_a_realistic_fingerprint_is_well_under_the_cap(self):
        self.assertEqual(self.post(FP).status_code, 200)

    def test_a_four_hundred_panel_mosaic_is_well_under_the_cap(self):
        # /api/visibility/panels caps panels at 400, so that is the largest
        # legitimate body ACP accepts. It must not trip the size limit.
        panels = [{"ra_deg": 83.0 + i * 0.01, "dec_deg": -5.0 + i * 0.01}
                  for i in range(400)]
        body = json.dumps({"panels": panels, "year": 2026})
        self.assertLess(len(body), app_module.MAX_BODY_BYTES)


class TestFingerprintStore(LimitsCase):

    def test_only_the_normalised_fingerprint_reaches_disk(self):
        noisy = dict(FP)
        noisy["secret_note"] = "the path to my archive"
        self.assertEqual(self.post(noisy).status_code, 200)
        self.assertNotIn("secret_note", json.dumps(self.stored()))

    def test_the_stored_shape_still_drives_the_rail(self):
        self.post(FP)
        entry = self.stored()["profiles"]["Rig A"]
        self.assertEqual(entry["fingerprint"]["camera"]["name"], "QHY268M")
        self.assertEqual(entry["fingerprint"]["focal_length_mm"], 530.0)

    def test_the_profile_count_is_capped(self):
        for i in range(app_module.MAX_FINGERPRINT_PROFILES + 15):
            f = dict(FP)
            f["profile_name"] = f"profile-{i:03d}"
            self.assertEqual(self.post(f).status_code, 200)
        profiles = self.stored()["profiles"]
        self.assertEqual(len(profiles), app_module.MAX_FINGERPRINT_PROFILES)

    def test_eviction_drops_the_oldest_and_keeps_the_newest(self):
        for i in range(app_module.MAX_FINGERPRINT_PROFILES + 5):
            f = dict(FP)
            f["profile_name"] = f"profile-{i:03d}"
            self.post(f)
        profiles = self.stored()["profiles"]
        self.assertNotIn("profile-000", profiles)
        last = f"profile-{app_module.MAX_FINGERPRINT_PROFILES + 4:03d}"
        self.assertIn(last, profiles)

    def test_the_observing_site_never_reaches_disk(self):
        """The plugin sends site.lat/lon with every fingerprint. Storing the
        body verbatim wrote the user's coordinates into a file that
        /api/fingerprints then served to anyone who could reach the port.
        Proved against a running instance on 2026-09-07."""
        withsite = dict(FP)
        withsite["site"] = {"lat": -33.87, "lon": 151.21, "elev_m": 40}
        withsite["rotation_deg"] = 12.3
        self.assertEqual(self.post(withsite).status_code, 200)
        for token in ("-33.87", "151.21", "rotation_deg", "site"):
            self.assertNotIn(token, json.dumps(self.stored()), token)
        self.assertNotIn(token, self.client.get(
            "/api/fingerprints").get_data(as_text=True))

    def test_the_reported_plugin_version_is_kept(self):
        withver = dict(FP)
        withver["nina_version"] = "3.3.0.1041"
        self.post(withver)
        self.assertEqual(
            self.stored()["profiles"]["Rig A"]["fingerprint"]["nina_version"],
            "3.3.0.1041")

    def test_the_same_profile_reporting_again_does_not_grow_the_store(self):
        for _ in range(20):
            self.post(FP)
        self.assertEqual(len(self.stored()["profiles"]), 1)


if __name__ == "__main__":
    unittest.main()
