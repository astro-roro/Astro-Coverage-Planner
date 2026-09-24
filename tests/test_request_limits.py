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
from flask import Flask, jsonify, request  # noqa: E402


# A stand-in for the extension's upload route (spec: docs/specs/ts-upload-import.md,
# "Core change: a bigger limit for one route"). The real route lives in the
# extension repo, which core's test suite does not load, so this proves the
# mechanism core exposes on a fresh Flask app rather than the shared `app`
# singleton: by the time this module runs, another test module may already
# have served a request through `app`, and Flask refuses new routes after
# that. The mechanism itself is app.py's own errorhandler(413) and
# MAX_BODY_BYTES config, reused unchanged below.
_BIG_ROUTE_LIMIT = 5 * 1024 * 1024


def _make_stand_in_app() -> Flask:
    stand_in = Flask(__name__)
    stand_in.config["MAX_CONTENT_LENGTH"] = app_module.MAX_BODY_BYTES

    @stand_in.errorhandler(413)
    def _too_large(_exc):
        limit = request.max_content_length
        return jsonify({"error": f"request body larger than {limit} bytes",
                        "max_bytes": limit}), 413

    @stand_in.route("/api/ext/nina-ts-sync/import/uploads", methods=["POST"])
    def _upload():
        request.max_content_length = _BIG_ROUTE_LIMIT
        request.get_data()  # triggers the 413 check against the raised limit
        return "ok", 200

    @stand_in.route("/api/plans/match", methods=["POST"])
    def _ordinary_route():
        request.get_data()  # global MAX_CONTENT_LENGTH applies; route sets nothing
        return "ok", 200

    return stand_in

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


class TestPerRouteLimit(unittest.TestCase):
    """A route can raise its own body limit above the global 1 MB cap.

    Spec: docs/specs/ts-upload-import.md, "Core change: a bigger limit for
    one route". The TS upload route (in the extension repo) needs to accept
    files up to ACP_TS_UPLOAD_MAX_BYTES (default 64 MB). This exercises the
    mechanism core provides for that: Flask 3.1's per-request
    request.max_content_length, and the 413 handler reading it instead of
    the global constant. See _make_stand_in_app above for why this runs on
    a separate Flask app rather than the shared `app` singleton.
    """

    def setUp(self):
        self.client = _make_stand_in_app().test_client()

    def test_a_route_that_raises_its_own_limit_accepts_a_5mb_body(self):
        body = b"A" * (5 * 1024 * 1024)
        self.assertGreater(len(body), app_module.MAX_BODY_BYTES)
        r = self.client.post("/api/ext/nina-ts-sync/import/uploads", data=body,
                              content_type="application/octet-stream")
        self.assertEqual(r.status_code, 200)

    def test_the_raised_route_still_refuses_a_body_over_its_own_limit(self):
        body = b"A" * (_BIG_ROUTE_LIMIT + 1024)
        r = self.client.post("/api/ext/nina-ts-sync/import/uploads", data=body,
                              content_type="application/octet-stream")
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.get_json()["max_bytes"], _BIG_ROUTE_LIMIT)

    def test_every_other_route_still_refuses_1_1_mb_with_413(self):
        body = b"A" * int(app_module.MAX_BODY_BYTES * 1.1)
        r = self.client.post("/api/plans/match", data=body,
                              content_type="application/octet-stream")
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.get_json()["max_bytes"], app_module.MAX_BODY_BYTES)

    def test_the_upload_routes_413_body_names_its_own_limit_not_the_global_one(self):
        body = b"A" * (_BIG_ROUTE_LIMIT + 1024)
        r = self.client.post("/api/ext/nina-ts-sync/import/uploads", data=body,
                              content_type="application/octet-stream")
        payload = r.get_json()
        self.assertEqual(payload["max_bytes"], _BIG_ROUTE_LIMIT)
        self.assertNotEqual(payload["max_bytes"], app_module.MAX_BODY_BYTES)
        self.assertIn(str(_BIG_ROUTE_LIMIT), payload["error"])


if __name__ == "__main__":
    unittest.main()
