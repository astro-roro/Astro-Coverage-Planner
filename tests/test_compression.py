"""The after_request hook that gzips and ETags JSON and text responses.

The manifest is the response that matters: it is megabytes of JSON for a
large library and Waitress compresses nothing. These tests write a manifest
big enough to cross the size floor and check the four contracts: compressed
when the client accepts gzip, untouched when it does not, a 304 on a
matching ETag, and small bodies left alone.
"""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _fresh_manifest(n_targets: int) -> None:
    td = Path(tempfile.mkdtemp())
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module._manifest_cache = None
    app_module._manifest_cache_mtime = None
    targets = [
        {"target_id": i + 1, "name": f"Target number {i}",
         "center_ra_deg": float(i % 360), "center_dec_deg": -30.0, "filters": {}}
        for i in range(n_targets)
    ]
    app_module.MANIFEST_PATH.write_text(json.dumps({"targets": targets}), encoding="utf-8")


class TestCompression(unittest.TestCase):
    def setUp(self):
        _fresh_manifest(200)
        self.client = app.test_client()

    def test_compressed_when_client_accepts_gzip(self):
        plain = self.client.get("/api/manifest", headers={"Accept-Encoding": "identity"})
        r = self.client.get("/api/manifest", headers={"Accept-Encoding": "gzip, deflate"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", r.headers.get("Vary", ""))
        self.assertLess(len(r.data), len(plain.data) // 3)
        self.assertEqual(gzip.decompress(r.data), plain.data)
        self.assertEqual(int(r.headers["Content-Length"]), len(r.data))

    def test_untouched_when_client_does_not_accept_gzip(self):
        r = self.client.get("/api/manifest", headers={"Accept-Encoding": "identity"})
        self.assertIsNone(r.headers.get("Content-Encoding"))
        self.assertEqual(len(json.loads(r.data)["targets"]), 200)
        self.assertIn("Accept-Encoding", r.headers.get("Vary", ""))

    def test_gzip_refused_with_q_zero(self):
        r = self.client.get("/api/manifest", headers={"Accept-Encoding": "gzip;q=0"})
        self.assertIsNone(r.headers.get("Content-Encoding"))

    def test_304_on_matching_etag(self):
        first = self.client.get("/api/manifest", headers={"Accept-Encoding": "gzip"})
        etag = first.headers["ETag"]
        self.assertFalse(etag.startswith("W/"))
        again = self.client.get(
            "/api/manifest", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.data, b"")

    def test_etag_differs_between_encodings_and_stale_etag_gets_body(self):
        gz = self.client.get("/api/manifest", headers={"Accept-Encoding": "gzip"})
        ident = self.client.get("/api/manifest", headers={"Accept-Encoding": "identity"})
        self.assertNotEqual(gz.headers["ETag"], ident.headers["ETag"])
        r = self.client.get(
            "/api/manifest",
            headers={"Accept-Encoding": "gzip", "If-None-Match": ident.headers["ETag"]})
        self.assertEqual(r.status_code, 200)

    def test_etag_changes_when_body_changes(self):
        a = self.client.get("/api/manifest", headers={"Accept-Encoding": "gzip"})
        _fresh_manifest(201)
        b = self.client.get("/api/manifest", headers={"Accept-Encoding": "gzip"})
        self.assertNotEqual(a.headers["ETag"], b.headers["ETag"])

    def test_small_body_left_alone(self):
        _fresh_manifest(0)
        r = self.client.get("/api/manifest", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertLess(len(r.data), 2048)
        self.assertIsNone(r.headers.get("Content-Encoding"))
        self.assertIsNone(r.headers.get("ETag"))

    def test_non_200_left_alone(self):
        r = self.client.get("/api/target/999999", headers={"Accept-Encoding": "gzip"})
        self.assertNotEqual(r.status_code, 200)
        self.assertIsNone(r.headers.get("Content-Encoding"))
        self.assertIsNone(r.headers.get("ETag"))

    def test_static_files_pass_through_unchanged(self):
        r = self.client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.headers.get("Content-Encoding"))
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))
        etag = r.headers["ETag"]
        again = self.client.get(
            "/static/app.js", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
        self.assertEqual(again.status_code, 304)
        r.close()
        again.close()


if __name__ == "__main__":
    unittest.main()
