"""/api/manifest leaves out what the page never reads, and builds its body once.

The fields dropped here are most of the bytes for a large library. The detail
panel gets master_files from /api/target/<id> instead.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

DROPPED = ("folder_sub_buckets", "per_master_fov", "corners_galactic", "master_files")


def _target(i: int) -> dict:
    return {
        "target_id": i,
        "objects": [f"NGC {i}"],
        "center_ra_deg": 10.0 + i, "center_dec_deg": 5.0,
        "n_masters": 1,
        "master_files": [f"/r/T{i}/Ha/master_Ha.fit"],
        "per_master_fov": [{"path": f"/r/T{i}/Ha/master_Ha.fit", "fov_arcmin": [10.0, 8.0]}],
        "corners_galactic": [[1.0, 2.0], [3.0, 4.0]],
        "filters": {"Ha": {
            "total_hours": 2.5,
            "paths": [f"/r/T{i}/Ha/Light_0001.fit"],
            "folder_sub_buckets": [{"bucket": f"/r/T{i}/Ha", "files": 30, "hours": 2.5}],
        }},
    }


def _manifest(n: int = 3) -> dict:
    return {
        "scan_date": "2026-10-18T00:00:00", "scan_roots": ["/r"],
        "total_targets": n, "total_integration_hours": 7.5,
        "targets": [_target(i) for i in range(1, n + 1)],
    }


class SlimManifestCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._saved = (app_module.MANIFEST_PATH, app_module._manifest_cache,
                       app_module._manifest_cache_mtime, app_module._manifest_body_cache)
        self.addCleanup(self._restore)
        app_module.MANIFEST_PATH = Path(self._td.name) / "manifest.json"
        app_module._manifest_cache = None
        app_module._manifest_cache_mtime = None
        app_module._manifest_body_cache = None
        self.write(_manifest())
        self.client = app.test_client()

    def _restore(self):
        (app_module.MANIFEST_PATH, app_module._manifest_cache,
         app_module._manifest_cache_mtime, app_module._manifest_body_cache) = self._saved

    def write(self, doc: dict):
        app_module.MANIFEST_PATH.write_text(json.dumps(doc), encoding="utf-8")


class TestWhatIsSent(SlimManifestCase):
    def test_unused_fields_are_not_on_the_wire(self):
        body = self.client.get("/api/manifest").get_json()
        for t in body["targets"]:
            for key in DROPPED:
                with self.subTest(target=t["target_id"], key=key):
                    self.assertNotIn(key, t)
            for d in t["filters"].values():
                self.assertNotIn("folder_sub_buckets", d)
                self.assertNotIn("paths", d)

    def test_the_fields_the_page_reads_are_still_there(self):
        t = self.client.get("/api/manifest").get_json()["targets"][0]
        self.assertEqual(t["objects"], ["NGC 1"])
        self.assertEqual(t["filters"]["Ha"]["total_hours"], 2.5)
        self.assertEqual(t["center_ra_deg"], 11.0)

    def test_target_detail_still_has_them_for_the_panel(self):
        t = self.client.get("/api/target/2").get_json()
        self.assertEqual(t["master_files"], ["T2/Ha/master_Ha.fit"])
        self.assertIn("per_master_fov", t)

    def test_the_manifest_in_memory_keeps_them_for_app_py(self):
        self.client.get("/api/manifest")
        loaded = app_module.load_manifest()
        self.assertIn("master_files", loaded["targets"][0])
        self.assertIn("per_master_fov", loaded["targets"][0])
        self.assertIn("folder_sub_buckets", loaded["targets"][0]["filters"]["Ha"])

    def test_a_missing_manifest_is_still_an_empty_one(self):
        app_module.MANIFEST_PATH.unlink()
        app_module._manifest_cache = None
        r = self.client.get("/api/manifest")
        self.assertEqual(r.get_json()["targets"], [])


class TestTheBodyIsBuiltOnce(SlimManifestCase):
    def test_repeat_requests_do_not_rebuild_it(self):
        real = app_module._build_manifest_payload
        with mock.patch.object(app_module, "_build_manifest_payload", side_effect=real) as spy:
            a = self.client.get("/api/manifest").data
            b = self.client.get("/api/manifest").data
            self.assertEqual(a, b)
            self.assertEqual(spy.call_count, 1)

    def test_the_bytes_are_available_to_a_later_hook(self):
        m = app_module.load_manifest()
        self.assertIs(app_module.manifest_body_bytes(m), app_module.manifest_body_bytes(m))

    def test_a_rescan_replaces_it(self):
        first = self.client.get("/api/manifest").get_json()
        self.assertEqual(len(first["targets"]), 3)
        self.write(_manifest(5))
        # Force a different mtime even on a coarse filesystem clock.
        st = app_module.MANIFEST_PATH.stat()
        os.utime(app_module.MANIFEST_PATH, (st.st_atime, st.st_mtime + 5))
        second = self.client.get("/api/manifest").get_json()
        self.assertEqual(len(second["targets"]), 5)

    def test_the_response_keeps_its_content_type_and_newline(self):
        r = self.client.get("/api/manifest")
        self.assertEqual(r.mimetype, "application/json")
        self.assertTrue(r.data.endswith(b"\n"))

    def test_the_body_matches_what_jsonify_would_send(self):
        m = app_module.load_manifest()
        with app.test_request_context():
            expected = app_module.jsonify(app_module._build_manifest_payload(m)).get_data()
        self.assertEqual(self.client.get("/api/manifest").data, expected)


if __name__ == "__main__":
    unittest.main()
