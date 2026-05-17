"""Tests for /api/observability time/location parameter edge cases.

The endpoint clamps lat/lon/height via _clamped_float (silently snaps
to defaults on bad input) but explicitly 400s on bad time strings.
That divergence is a deliberate design choice — pinning it here so it
doesn't drift.
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


def _redirect_manifest():
    td = Path(tempfile.mkdtemp())
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module._manifest_cache = None
    app_module._manifest_cache_mtime = None
    return app_module.MANIFEST_PATH


def _write_manifest(targets=None):
    if targets is None:
        targets = [{
            "target_id": 1, "center_ra_deg": 100.0,
            "center_dec_deg": -30.0,
        }]
    app_module.MANIFEST_PATH.write_text(
        json.dumps({"targets": targets}), encoding="utf-8"
    )


class TestClampedFloat(unittest.TestCase):
    """Unit tests on _clamped_float — the helper sits on every
    coordinate parameter the API accepts, so a regression here would
    bleed into observability + visibility + every other site-aware
    endpoint."""

    def test_returns_default_when_param_absent(self):
        with app.test_request_context("/?nope=1"):
            self.assertEqual(
                app_module._clamped_float("missing", 42.0, 0.0, 100.0),
                42.0,
            )

    def test_returns_default_when_param_unparseable(self):
        with app.test_request_context("/?lat=abc"):
            self.assertEqual(
                app_module._clamped_float("lat", 19.82, -90.0, 90.0),
                19.82,
            )

    def test_clamps_to_default_when_out_of_range_high(self):
        # 95 > 90 → snap to default, not to the bound.
        with app.test_request_context("/?lat=95"):
            self.assertEqual(
                app_module._clamped_float("lat", 19.82, -90.0, 90.0),
                19.82,
            )

    def test_clamps_to_default_when_out_of_range_low(self):
        with app.test_request_context("/?lat=-95"):
            self.assertEqual(
                app_module._clamped_float("lat", 19.82, -90.0, 90.0),
                19.82,
            )

    def test_rejects_nan(self):
        # The `v != v` check filters NaN — float("nan") would slip past
        # numeric range checks otherwise.
        with app.test_request_context("/?lat=NaN"):
            self.assertEqual(
                app_module._clamped_float("lat", 19.82, -90.0, 90.0),
                19.82,
            )

    def test_accepts_valid_value(self):
        with app.test_request_context("/?lat=51.5"):
            self.assertAlmostEqual(
                app_module._clamped_float("lat", 19.82, -90.0, 90.0),
                51.5,
            )

    def test_accepts_value_exactly_at_bound(self):
        with app.test_request_context("/?lat=90"):
            self.assertEqual(
                app_module._clamped_float("lat", 19.82, -90.0, 90.0),
                90.0,
            )
        with app.test_request_context("/?lat=-90"):
            self.assertEqual(
                app_module._clamped_float("lat", 19.82, -90.0, 90.0),
                -90.0,
            )

    def test_integer_input_returns_float(self):
        with app.test_request_context("/?lat=50"):
            v = app_module._clamped_float("lat", 19.82, -90.0, 90.0)
            self.assertIsInstance(v, float)
            self.assertEqual(v, 50.0)


class TestObservabilityHappyPath(unittest.TestCase):
    def setUp(self):
        _redirect_manifest()
        _write_manifest()
        self.client = app.test_client()

    def test_returns_altaz_for_target(self):
        # No time arg → uses "now". Just need to confirm we get a
        # well-formed response with one altaz entry.
        r = self.client.get("/api/observability")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(len(body["targets"]), 1)
        entry = body["targets"][0]
        self.assertEqual(entry["target_id"], 1)
        self.assertIn("alt_deg", entry)
        self.assertIn("az_deg", entry)
        # Az is always in [0, 360], alt in [-90, 90].
        self.assertGreaterEqual(entry["az_deg"], 0.0)
        self.assertLessEqual(entry["az_deg"], 360.0)
        self.assertGreaterEqual(entry["alt_deg"], -90.0)
        self.assertLessEqual(entry["alt_deg"], 90.0)

    def test_explicit_iso_time_accepted(self):
        r = self.client.get(
            "/api/observability?time=2026-05-17T12:00:00"
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["time"], "2026-05-17T12:00:00")

    def test_explicit_lat_lon_accepted(self):
        # Mauna Kea defaults; override to Siding Spring-ish.
        r = self.client.get(
            "/api/observability?lat=-31.27&lon=149.07&height=1165"
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertAlmostEqual(body["lat"], -31.27, places=2)
        self.assertAlmostEqual(body["lon"], 149.07, places=2)


class TestObservabilityTimeValidation(unittest.TestCase):
    """The time argument goes through astropy's Time parser. Junk input
    must 400 cleanly, not 500."""

    def setUp(self):
        _redirect_manifest()
        _write_manifest()
        self.client = app.test_client()

    def test_garbage_time_returns_400(self):
        r = self.client.get("/api/observability?time=banana")
        self.assertEqual(r.status_code, 400)
        self.assertIn("invalid time", r.get_json().get("error", ""))

    def test_empty_time_string_falls_back_to_now(self):
        # `request.args.get("time") or now()` — empty string is falsy,
        # so it should use the current time and succeed.
        r = self.client.get("/api/observability?time=")
        self.assertEqual(r.status_code, 200)

    def test_partial_iso_returns_400_or_succeeds(self):
        # Astropy's Time accepts some partial formats, rejects others.
        # The contract is "well-defined response", not a specific status.
        r = self.client.get("/api/observability?time=2026")
        self.assertIn(r.status_code, (200, 400))

    def test_far_future_time_accepted(self):
        # Astropy handles years up to ~9999. We don't want to clamp this.
        r = self.client.get("/api/observability?time=2099-01-01T00:00:00")
        self.assertEqual(r.status_code, 200)


class TestObservabilityCoordinateClamping(unittest.TestCase):
    """Out-of-range coordinates are silently snapped to defaults —
    confirm we don't 400 (that's the divergence from time handling)."""

    def setUp(self):
        _redirect_manifest()
        _write_manifest()
        self.client = app.test_client()

    def test_lat_out_of_range_snaps_to_default(self):
        r = self.client.get("/api/observability?lat=999")
        self.assertEqual(r.status_code, 200)
        # Snapped back to Mauna Kea default (19.82).
        self.assertAlmostEqual(r.get_json()["lat"], 19.82)

    def test_lon_out_of_range_snaps_to_default(self):
        r = self.client.get("/api/observability?lon=999")
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.get_json()["lon"], -155.47)

    def test_garbage_lat_snaps_to_default(self):
        r = self.client.get("/api/observability?lat=hello")
        self.assertEqual(r.status_code, 200)
        self.assertAlmostEqual(r.get_json()["lat"], 19.82)

    def test_height_below_dead_sea_clamps(self):
        # -500 < -430 lower bound → snaps to default 4205.
        r = self.client.get("/api/observability?height=-500")
        self.assertEqual(r.status_code, 200)

    def test_height_above_space_clamps(self):
        # 99999 > 9000 → snaps to default.
        r = self.client.get("/api/observability?height=99999")
        self.assertEqual(r.status_code, 200)


class TestObservabilityMissingManifest(unittest.TestCase):
    def setUp(self):
        _redirect_manifest()  # creates path but doesn't write file
        self.client = app.test_client()

    def test_no_manifest_returns_404(self):
        r = self.client.get("/api/observability")
        self.assertEqual(r.status_code, 404)
        self.assertIn("manifest not found", r.get_json().get("error", ""))

    def test_empty_manifest_returns_200_empty(self):
        _write_manifest(targets=[])
        r = self.client.get("/api/observability")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["targets"], [])


if __name__ == "__main__":
    unittest.main()
