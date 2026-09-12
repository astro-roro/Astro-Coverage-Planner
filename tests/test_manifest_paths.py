"""No endpoint hands an absolute archive path to an unauthenticated caller.

`api_manifest` strips `paths` from each filter block, which reads as if it
prevents this. It does not: `master_files`, `per_master_fov[].path` and every
field inside `folder_sub_buckets` carry the user's real filesystem layout
through untouched, and `/api/target/<id>` returns the raw target with no strip
at all. Proved against a running instance on 2026-09-07.

These tests assert on the whole serialised response rather than on named keys,
so a future key carrying a path fails here too.
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

SENTINEL = "/sentinel-archive-root"

TARGET = {
    "target_id": 1,
    "objects": ["M42"],
    "center_ra_deg": 83.8, "center_dec_deg": -5.4,
    "n_masters": 1,
    "master_files": [f"{SENTINEL}/M42/Ha/master_Ha.fit"],
    "per_master_fov": [{
        "path": f"{SENTINEL}/M42/Ha/master_Ha.fit",
        "telescope": "RedCat 51", "filter": "Ha",
        "fov_arcmin": [10.0, 8.0], "pix_arcsec": 1.46,
    }],
    "filters": {
        "Ha": {
            "total_hours": 2.5,
            "paths": [f"{SENTINEL}/M42/Ha/Light_0001.fit"],
            "folder_sub_buckets": [{
                "bucket": f"{SENTINEL}/M42/Ha",
                "session_root": f"{SENTINEL}/M42",
                "sample_path": f"{SENTINEL}/M42/Ha/Light_0001.fit",
                "files": 30, "hours": 2.5,
            }],
        },
    },
}

MANIFEST = {
    "scan_date": "2026-09-07T00:00:00",
    "scan_roots": [SENTINEL],
    "total_targets": 1,
    "total_integration_hours": 2.5,
    "targets": [TARGET],
    "integrity_flags": {
        "masters_missing_wcs": [f"{SENTINEL}/M42/Ha/master_Ha.fit"],
        "masters_ambiguous_filter": [f"{SENTINEL}/NGC7000/master.fit"],
    },
}


class PathLeakCase(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        td = Path(self._td.name)
        app_module.MANIFEST_PATH = td / "manifest.json"
        app_module._manifest_cache = None
        app_module._manifest_cache_mtime = None
        app_module.MANIFEST_PATH.write_text(json.dumps(MANIFEST), encoding="utf-8")
        self.client = app.test_client()

    def body(self, url: str) -> str:
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.get_data(as_text=True)


class TestNoAbsolutePathsOnTheWire(PathLeakCase):

    def test_manifest_carries_no_archive_root(self):
        self.assertNotIn(SENTINEL, self.body("/api/manifest"))

    def test_target_carries_no_archive_root(self):
        self.assertNotIn(SENTINEL, self.body("/api/target/1"))

    def test_scan_health_examples_carry_no_archive_root(self):
        health = self.client.get("/api/manifest").get_json()["scan_health"]
        self.assertNotIn(SENTINEL, json.dumps(health))
        # The examples still name the file, which is what issue #63 asked for.
        self.assertTrue(health["masters_missing_wcs_examples"])
        self.assertIn("master_Ha.fit", health["masters_missing_wcs_examples"][0])


class TestTheFilenameSurvives(PathLeakCase):
    """Shortening is not deletion: the UI's master file panel still works."""

    def test_master_files_keep_a_readable_relative_path(self):
        t = self.client.get("/api/manifest").get_json()["targets"][0]
        self.assertEqual(t["master_files"], ["M42/Ha/master_Ha.fit"])
        self.assertEqual(t["per_master_fov"][0]["path"], "M42/Ha/master_Ha.fit")
        # Everything alongside the path is untouched.
        self.assertEqual(t["per_master_fov"][0]["telescope"], "RedCat 51")
        self.assertEqual(t["per_master_fov"][0]["fov_arcmin"], [10.0, 8.0])

    def test_bucket_fields_keep_their_counts(self):
        b = self.client.get("/api/manifest").get_json()[
            "targets"][0]["filters"]["Ha"]["folder_sub_buckets"][0]
        self.assertEqual(b["bucket"], "M42/Ha")
        self.assertEqual((b["files"], b["hours"]), (30, 2.5))

    def test_non_path_strings_are_not_mangled(self):
        t = self.client.get("/api/manifest").get_json()["targets"][0]
        self.assertEqual(t["objects"], ["M42"])
        self.assertIn("Ha", t["filters"])


class TestPathsOutsideAnyScanRoot(PathLeakCase):
    """A path matching no scan root still must not ship the whole layout."""

    def setUp(self):
        super().setUp()
        doc = json.loads(json.dumps(MANIFEST))
        doc["scan_roots"] = ["/some/other/root"]
        app_module.MANIFEST_PATH.write_text(json.dumps(doc), encoding="utf-8")
        app_module._manifest_cache = None
        app_module._manifest_cache_mtime = None

    def test_unmatched_path_is_reduced_to_its_tail(self):
        body = self.body("/api/manifest")
        self.assertNotIn(SENTINEL, body)
        t = json.loads(body)["targets"][0]
        self.assertEqual(t["master_files"], ["Ha/master_Ha.fit"])


if __name__ == "__main__":
    unittest.main()
