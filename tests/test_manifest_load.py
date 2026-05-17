"""Tests for manifest load failure modes.

load_manifest() is called by many endpoints (/, /api/manifest,
/api/target/<id>, /api/observability, /api/visibility, /api/gaps,
/api/export/priority, …). If it raises on a malformed file, every
single one of those endpoints crashes with a 500 — even ones that
don't conceptually depend on a clean manifest. These tests pin down
the graceful-degradation contract.
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


class TestLoadManifestUnit(unittest.TestCase):
    def test_missing_file_returns_none(self):
        path = _redirect_manifest()
        # File deliberately not created.
        self.assertFalse(path.exists())
        self.assertIsNone(app_module.load_manifest())

    def test_malformed_json_returns_none_not_raises(self):
        path = _redirect_manifest()
        path.write_text("{not valid json", encoding="utf-8")
        # Must NOT raise — callers expect None or a dict, never an
        # exception bubbling up through every request handler.
        self.assertIsNone(app_module.load_manifest())

    def test_empty_file_returns_none(self):
        path = _redirect_manifest()
        path.write_text("", encoding="utf-8")
        self.assertIsNone(app_module.load_manifest())

    def test_non_utf8_bytes_returns_none(self):
        path = _redirect_manifest()
        path.write_bytes(b"\xff\xfe not utf-8 \x00\x00")
        self.assertIsNone(app_module.load_manifest())

    def test_well_formed_manifest_loads(self):
        path = _redirect_manifest()
        payload = {"targets": [
            {"target_id": 1, "objects": ["NGC 1"],
             "ra_deg": 100.0, "dec_deg": -30.0,
             "filters": {}, "fov_arcmin": [0, 0]},
        ]}
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = app_module.load_manifest()
        self.assertIsNotNone(result)
        self.assertEqual(len(result["targets"]), 1)

    def test_cache_invalidates_on_mtime_change(self):
        import time
        path = _redirect_manifest()
        path.write_text(json.dumps({"targets": []}), encoding="utf-8")
        first = app_module.load_manifest()
        self.assertEqual(first["targets"], [])
        # Bump mtime + rewrite content; cache must refresh.
        time.sleep(0.05)
        path.write_text(json.dumps({
            "targets": [{"target_id": 99}],
        }), encoding="utf-8")
        # Force mtime change to be visible to stat (some filesystems
        # have low-resolution mtime).
        import os
        new_mtime = path.stat().st_mtime + 1
        os.utime(path, (new_mtime, new_mtime))
        second = app_module.load_manifest()
        self.assertEqual(len(second["targets"]), 1)


class TestManifestEndpointsHandleMissingGracefully(unittest.TestCase):
    """When the manifest is missing or unparseable, the endpoints that
    use it must still respond — not 500."""

    def setUp(self):
        _redirect_manifest()  # file deliberately missing
        self.client = app.test_client()

    def test_root_renders_even_without_manifest(self):
        # The root template should render even with no manifest — it's
        # the empty-state for a fresh install.
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_api_manifest_returns_empty_payload_not_500(self):
        # Deliberate design: missing manifest returns 200 with empty
        # totals so the frontend's onboarding banner triggers cleanly.
        r = self.client.get("/api/manifest")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body.get("total_targets"), 0)
        self.assertEqual(body.get("targets"), [])

    def test_api_target_returns_404_not_500(self):
        r = self.client.get("/api/target/1")
        self.assertEqual(r.status_code, 404)


class TestManifestMalformedDoesNotCrash(unittest.TestCase):
    """A garbage manifest.json must not turn into a 500 on every
    request. The graceful path is identical to missing-file: the
    hardened load_manifest treats parse errors as "no manifest"."""

    def setUp(self):
        path = _redirect_manifest()
        path.write_text("{this is not json", encoding="utf-8")
        self.client = app.test_client()

    def test_root_still_renders(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_api_manifest_returns_empty_payload(self):
        # Same contract as missing-file.
        r = self.client.get("/api/manifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json().get("total_targets"), 0)

    def test_api_target_returns_404(self):
        r = self.client.get("/api/target/1")
        self.assertEqual(r.status_code, 404)

    def test_observability_still_works(self):
        # Visibility computation doesn't actually need the manifest —
        # a malformed manifest must not block astronomy queries.
        r = self.client.get(
            "/api/observability?lat=-33.87&lon=151.21&min_alt_deg=30"
        )
        # 200 because it can fall through to empty target list, or
        # whatever sensible default — what matters is no 500.
        self.assertNotEqual(r.status_code, 500,
            f"observability shouldn't crash on bad manifest: {r.get_data(as_text=True)[:200]}")


if __name__ == "__main__":
    unittest.main()
