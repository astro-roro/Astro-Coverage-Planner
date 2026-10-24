"""Findings from the independent review of phases 1a and 1b, kept as tests.

The first three come from the reviewer's proof tests: an offline saved folder
must not stop other edits, an offline catalogue download must not overwrite the
catalogues the user already has, and the read-only page is judged by the
`writable` flag (see test_setup.py, P9), not by HTML the template always holds.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import acp_settings  # noqa: E402
import app as app_module  # noqa: E402
from test_settings import EnvCase  # noqa: E402


class TestOfflineFolderDoesNotBlockOtherEdits(EnvCase):

    def test_removing_a_healthy_folder_while_another_is_offline(self):
        nas = self.folder("nas-share")
        local = self.folder("local-drive")
        self.assertEqual(self.put({"fits_roots": [nas, local]}).status_code, 200)
        shutil.rmtree(nas)
        resp = self.put({"fits_roots": [nas]})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(self.read_settings()["fits_roots"], [nas])

    def test_adding_a_folder_while_another_is_offline(self):
        nas = self.folder("nas-share")
        self.assertEqual(self.put({"fits_roots": [nas]}).status_code, 200)
        shutil.rmtree(nas)
        new = self.folder("new-drive")
        resp = self.put({"fits_roots": [nas, new]})
        self.assertEqual(resp.status_code, 200, resp.get_json())

    def test_a_new_folder_is_still_checked_while_another_is_offline(self):
        nas = self.folder("nas-share")
        self.assertEqual(self.put({"fits_roots": [nas]}).status_code, 200)
        shutil.rmtree(nas)
        resp = self.put({"fits_roots": [nas, str(self.tmp / "typo")]})
        self.assertEqual((resp.status_code, resp.get_json()["error"], resp.get_json()["index"]),
                         (400, "not_found", 1))

    def test_a_save_does_not_wait_on_a_saved_folder_that_hangs(self):
        nas = self.folder("nas-share")
        self.assertEqual(self.put({"fits_roots": [nas]}).status_code, 200)
        local = self.folder("local-drive")
        with mock.patch.object(acp_settings, "READ_TIMEOUT", 5.0), \
                mock.patch.object(acp_settings, "_probe_directory",
                                  lambda text: "ok" if text == local else time.sleep(30) or "ok"):
            began = time.monotonic()
            resp = self.put({"fits_roots": [nas, local]})
            took = time.monotonic() - began
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertLess(took, 2.0)


class TestOfflineCatalogueFetchKeepsTheOldFile(unittest.TestCase):

    GOOD = {"green_snrs": [{"name": "G1"}] * 3, "anderson_hii": [{"name": "H"}] * 5,
            "sharpless": [{"name": "Sh2-1"}], "eso_pne": [{"name": "PN"}],
            "messier": [{"name": "M1"}], "smgps_candidates": []}

    def setUp(self):
        import fetch_catalogs as fc
        self.fc = fc
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-catalogs-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / "catalogs.json"
        for name, value in (("CACHE_PATH", self.target), ("CACHE_DIR", self.tmp),
                            ("fetch_messier", lambda: [{"name": "M1"}])):
            patcher = mock.patch.object(fc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def offline(self, *names):
        def boom(*_a, **_k):
            raise ConnectionError("no route to host")
        for name in names:
            patcher = mock.patch.object(self.fc, name, boom)
            patcher.start()
            self.addCleanup(patcher.stop)

    ALL = ("fetch_green_2019", "fetch_anderson_hii", "fetch_optional",
           "fetch_sharpless", "fetch_eso_pne")

    def test_an_offline_fetch_leaves_an_existing_file_alone(self):
        self.target.write_text(json.dumps(self.GOOD), encoding="utf-8")
        before = self.target.read_text(encoding="utf-8")
        self.offline(*self.ALL)
        with self.assertRaises(SystemExit) as exit_:
            self.fc.main()
        self.assertEqual(exit_.exception.code, 3)
        self.assertEqual(self.target.read_text(encoding="utf-8"), before)

    def test_an_offline_first_fetch_writes_nothing(self):
        self.offline(*self.ALL)
        with self.assertRaises(SystemExit):
            self.fc.main()
        self.assertFalse(self.target.exists())

    def test_one_table_that_fails_keeps_its_old_entries(self):
        self.target.write_text(json.dumps(self.GOOD), encoding="utf-8")
        self.offline("fetch_anderson_hii", "fetch_optional")
        with mock.patch.object(self.fc, "fetch_green_2019", lambda: [{"name": "G9"}]), \
                mock.patch.object(self.fc, "fetch_sharpless", lambda: [{"name": "S9"}]), \
                mock.patch.object(self.fc, "fetch_eso_pne", lambda: [{"name": "P9"}]):
            self.fc.main()
        after = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(len(after["anderson_hii"]), 5)
        self.assertEqual(after["green_snrs"], [{"name": "G9"}])


class TestSmallerFindings(EnvCase):

    def test_windows_device_and_extended_paths_are_invalid(self):
        for text in ("\\\\.\\pipe\\name", "\\\\?\\C:\\Astro", "//./pipe/name"):
            cleaned, code = acp_settings.validate_roots([text])
            self.assertEqual(code, "invalid", text)
        self.assertIsNone(acp_settings.validate_roots(["\\\\nas\\astro"])[1])

    def test_a_new_scan_clears_the_last_scans_progress_before_the_answer(self):
        path = app_module.MANIFEST_PATH.with_name("scan_progress.json")
        path.write_text(json.dumps({"stage": "writing", "files_seen": 9}), encoding="utf-8")
        self.write_settings({"fits_roots": [self.folder()]})
        self.assertEqual(app_module.claim_scan("page"), "claimed")
        self.assertFalse(path.exists())
        self.assertIsNone(app_module.scan_status()["progress"])

    def test_a_catalogue_download_that_runs_too_long_reports_timed_out(self):
        def slow(args):
            raise subprocess.TimeoutExpired(args, 1800)
        self.addCleanup(setattr, app_module, "_run_catalogue_subprocess",
                        app_module._run_catalogue_subprocess)
        self.addCleanup(app_module._catalogue_state.update, running=False, finished=False,
                        error_kind=None)
        app_module._run_catalogue_subprocess = slow
        self.assertEqual(self.client.post("/api/catalogues/fetch", json={}).status_code, 202)
        end = time.monotonic() + 5
        while app_module.catalogue_status()["running"] and time.monotonic() < end:
            time.sleep(0.02)
        self.assertEqual(app_module.catalogue_status()["error_kind"], "timed_out")


if __name__ == "__main__":
    unittest.main()
