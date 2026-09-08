"""/api/scan/status must not republish the scanner's stderr.

_run_scan_subprocess kept the last line of the failed builder's output in
_scan_state["last_error"], and scan_status() returned the state dict whole.
The last line of a Python traceback is the exception message, and for the
common scan failures that message is a filesystem path. The endpoint has no
authentication in the stock configuration, so anyone who could reach the port
could read it. Confirmed against a running instance on 2026-09-07:

    {"last_error": "[   0.0s] ERROR: no valid roots to scan. Set FITS_ROOTS
     (e.g. FITS_ROOTS='D:/Astro/Images;E:/Archive') or ..."}

Nothing in the frontend ever read the field, so nothing lost it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

SECRET_PATH = "/Volumes/PhotoNAS/rohan-private/Astro"


class ScanFailureCase(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        self._real_run = app_module.subprocess.run
        self.addCleanup(setattr, app_module.subprocess, "run", self._real_run)
        with app_module.SCAN_STATE_LOCK:
            app_module._scan_state["last_error"] = None
            app_module._scan_state["last_error_kind"] = None

    def fail_with(self, stderr_tail: str, exit_code: int = 1):
        """Stub the subprocess, not the wrapper: the real capture code is what
        leaked, so the test has to run it."""
        app_module.subprocess.run = lambda *a, **k: SimpleNamespace(
            returncode=exit_code, stderr=stderr_tail, stdout="")
        app_module.run_scan_now("test")
        return self.client.get("/api/scan/status").get_json()


class TestNoStderrOnTheWire(ScanFailureCase):

    def test_a_path_in_the_traceback_does_not_reach_the_endpoint(self):
        body = self.fail_with(
            f"PermissionError: [Errno 13] Permission denied: '{SECRET_PATH}/M42'")
        self.assertNotIn(SECRET_PATH, str(body))

    def test_the_configured_roots_do_not_reach_the_endpoint(self):
        body = self.fail_with(
            "ERROR: no valid roots to scan. Set FITS_ROOTS "
            f"(e.g. FITS_ROOTS='{SECRET_PATH}')")
        self.assertNotIn(SECRET_PATH, str(body))

    def test_the_stderr_still_reaches_the_log(self):
        with self.assertLogs(level="ERROR") as caught:
            self.fail_with(f"PermissionError: {SECRET_PATH}/M42")
        self.assertIn(SECRET_PATH, "\n".join(caught.output))


class TestTheCallerStillLearnsWhatHappened(ScanFailureCase):

    def test_the_exit_code_is_reported(self):
        body = self.fail_with("boom", exit_code=3)
        self.assertEqual(body["last_exit_code"], 3)

    def test_a_permission_failure_is_named_as_one(self):
        body = self.fail_with(f"PermissionError: [Errno 13] Permission denied: '{SECRET_PATH}'")
        self.assertEqual(body["last_error_kind"], "permission_denied")
        self.assertIn("could not read", body["last_error"])

    def test_a_missing_roots_failure_is_named_as_one(self):
        body = self.fail_with("ERROR: no valid roots to scan. Set FITS_ROOTS")
        self.assertEqual(body["last_error_kind"], "no_archive_roots")

    def test_an_unrecognised_failure_falls_back_to_crashed(self):
        body = self.fail_with("Traceback (most recent call last): ZeroDivisionError")
        self.assertEqual(body["last_error_kind"], "crashed")

    def test_a_clean_run_clears_the_error(self):
        self.fail_with("boom")
        app_module.subprocess.run = lambda *a, **k: SimpleNamespace(
            returncode=0, stderr="", stdout="")
        app_module.run_scan_now("test")
        body = self.client.get("/api/scan/status").get_json()
        self.assertIsNone(body["last_error"])
        self.assertIsNone(body["last_error_kind"])


if __name__ == "__main__":
    unittest.main()
