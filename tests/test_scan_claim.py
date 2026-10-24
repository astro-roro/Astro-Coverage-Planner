"""The claim and run split of run_scan_now, and what a scan is handed (spec 1.3, 2.5).

Covers the 1a parts of C3, C4 and C10. The route that calls the claim is 1b.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402


class ScanCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-claim-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("FITS_ROOTS", "MANIFEST_PATH", "ACP_SCAN_CRON", "ACP_PACKAGED", "ACP_HOME"):
            os.environ.pop(name, None)
        os.environ["ACP_HOME"] = str(self.tmp / "home")
        self.addCleanup(self._reset)
        self.addCleanup(setattr, app_module, "_run_scan_subprocess",
                        app_module._run_scan_subprocess)
        self.addCleanup(app_module.stop_scan_scheduler)

    def _reset(self):
        with app_module.SCAN_STATE_LOCK:
            app_module._scan_state["running"] = False

    def write_settings(self, data):
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        (home / "settings.json").write_text(json.dumps(data), encoding="utf-8")


class TestClaimAndRun(ScanCase):

    def test_claim_sets_running_and_a_second_claim_is_refused(self):
        self.assertEqual(app_module.claim_scan("test"), "claimed")
        self.assertTrue(app_module.scan_status()["running"])
        self.assertEqual(app_module.claim_scan("test"), "running")

    def test_claimed_run_does_the_scan_and_clears_the_flag(self):
        self.write_settings({"fits_roots": ["/some/root"]})
        calls = []
        app_module._run_scan_subprocess = lambda args: calls.append(args) or 0
        self.assertEqual(app_module.claim_scan("page"), "claimed")
        app_module.run_claimed_scan()
        self.assertEqual(len(calls), 1)
        status = app_module.scan_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["last_exit_code"], 0)
        self.assertEqual(status["last_trigger"], "page")

    def test_c2_two_claims_fired_together_get_one_winner(self):
        self.write_settings({"fits_roots": ["/some/root"]})
        results = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            results.append(app_module.claim_scan("page"))
        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["claimed"] + ["running"] * 7)

    def test_run_scan_now_still_returns_false_while_one_runs(self):
        app_module.claim_scan("test")
        self.assertFalse(app_module.run_scan_now("cron"))

    def test_run_scan_now_still_works_in_source_mode_with_no_roots(self):
        app_module._run_scan_subprocess = lambda args: 0
        self.assertTrue(app_module.run_scan_now("test"))

    def test_c3_packaged_mode_refuses_with_no_roots_and_spawns_nothing(self):
        os.environ["ACP_PACKAGED"] = "1"
        calls = []
        app_module._run_scan_subprocess = lambda args: calls.append(args) or 0
        self.assertEqual(app_module.claim_scan("page"), "no_roots")
        self.assertFalse(app_module.run_scan_now("cron"))
        self.assertEqual(calls, [])
        self.assertFalse(app_module.scan_status()["running"])

    def test_c3_packaged_mode_with_roots_in_the_file_runs(self):
        os.environ["ACP_PACKAGED"] = "1"
        self.write_settings({"fits_roots": ["/some/root"]})
        app_module._run_scan_subprocess = lambda args: 0
        self.assertTrue(app_module.run_scan_now("page"))

    def test_a_page_trigger_refuses_with_no_roots_even_in_source_mode(self):
        self.assertEqual(app_module.claim_scan("page"), "no_roots")
        self.assertEqual(app_module.claim_scan("test"), "claimed")


class TestScanEnvironment(ScanCase):

    def run_real_subprocess(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["env"] = kwargs.get("env")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(app_module.subprocess, "run", fake_run):
            app_module._run_scan_subprocess(["python", "builder.py"])
        return seen["env"]

    def test_c4_roots_from_the_file_reach_the_subprocess(self):
        self.write_settings({"fits_roots": ["/a", "/b"]})
        env = self.run_real_subprocess()
        self.assertEqual(env["FITS_ROOTS"], "/a;/b")

    def test_c4_the_environment_wins_when_both_exist(self):
        self.write_settings({"fits_roots": ["/file"]})
        os.environ["FITS_ROOTS"] = "/env"
        env = self.run_real_subprocess()
        self.assertEqual(env["FITS_ROOTS"], "/env")

    def test_with_nothing_effective_the_builder_keeps_its_own_fallback(self):
        env = self.run_real_subprocess()
        self.assertNotIn("FITS_ROOTS", env)

    def test_the_manifest_the_app_reads_is_what_the_builder_is_told_to_write(self):
        with mock.patch.object(app_module, "MANIFEST_PATH", Path("/the/app/manifest.json")):
            env = self.run_real_subprocess()
        self.assertEqual(env["MANIFEST_PATH"], str(Path("/the/app/manifest.json")))


class TestScheduledScanFromTheFile(ScanCase):

    def test_c10_scan_cron_from_the_settings_file_starts_the_scheduler(self):
        self.write_settings({"scan_cron": "0 3 * * *"})
        self.assertTrue(app_module.start_scan_scheduler())
        self.assertEqual(app_module.scan_status()["cron"], "0 3 * * *")

    def test_c10_the_environment_wins_over_the_file(self):
        self.write_settings({"scan_cron": "0 3 * * *"})
        os.environ["ACP_SCAN_CRON"] = "0 4 * * *"
        self.assertTrue(app_module.start_scan_scheduler())
        self.assertEqual(app_module.scan_status()["cron"], "0 4 * * *")

    def test_c10_with_neither_nothing_starts(self):
        self.assertFalse(app_module.start_scan_scheduler())


if __name__ == "__main__":
    unittest.main()
