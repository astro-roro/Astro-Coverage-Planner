"""Tests for the scheduled archive rescan (ACP_SCAN_CRON) and /api/scan/status.

The scheduler lives inside the web process so the Docker image needs no cron
daemon. Two things must hold no matter what: with the variable unset the app
behaves exactly as it always has, and a second scan can never start while one is
still running, because two builders would race over the same manifest and the
same scan cache.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


class ScanScheduleCase(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        self.addCleanup(app_module.stop_scan_scheduler)
        self.addCleanup(self._reset_state)
        self._real_runner = app_module._run_scan_subprocess

        def _restore():
            app_module._run_scan_subprocess = self._real_runner
        self.addCleanup(_restore)

    def _reset_state(self):
        with app_module.SCAN_STATE_LOCK:
            app_module._scan_state.update({
                "running": False,
                "last_start": None,
                "last_finish": None,
                "last_exit_code": None,
                "last_trigger": None,
                "last_error": None,
            })


class TestStatusEndpoint(ScanScheduleCase):

    def test_shape_before_any_scan(self):
        resp = self.client.get("/api/scan/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        for key in ("running", "last_start", "last_finish", "last_exit_code",
                    "cron", "scheduled"):
            self.assertIn(key, body)
        self.assertFalse(body["running"])
        self.assertIsNone(body["last_start"])
        self.assertIsNone(body["last_finish"])
        self.assertIsNone(body["last_exit_code"])
        self.assertIsNone(body["cron"])
        self.assertFalse(body["scheduled"])

    def test_reports_a_finished_scan(self):
        app_module._run_scan_subprocess = lambda args: 0
        self.assertTrue(app_module.run_scan_now("test"))
        body = self.client.get("/api/scan/status").get_json()
        self.assertFalse(body["running"])
        self.assertEqual(body["last_exit_code"], 0)
        self.assertEqual(body["last_trigger"], "test")
        self.assertIsNotNone(body["last_start"])
        self.assertIsNotNone(body["last_finish"])

    def test_reports_a_failed_scan(self):
        app_module._run_scan_subprocess = lambda args: 1
        app_module.run_scan_now("test")
        body = self.client.get("/api/scan/status").get_json()
        self.assertEqual(body["last_exit_code"], 1)

    def test_reports_a_running_scan(self):
        release = threading.Event()
        started = threading.Event()

        def _slow(args):
            started.set()
            release.wait(10)
            return 0

        app_module._run_scan_subprocess = _slow
        worker = threading.Thread(target=app_module.run_scan_now, args=("test",))
        worker.start()
        try:
            self.assertTrue(started.wait(5))
            body = self.client.get("/api/scan/status").get_json()
            self.assertTrue(body["running"])
            self.assertIsNotNone(body["last_start"])
            self.assertIsNone(body["last_finish"])
        finally:
            release.set()
            worker.join(10)


class TestNoDoubleStart(ScanScheduleCase):

    def test_second_scan_is_refused_while_one_runs(self):
        release = threading.Event()
        started = threading.Event()
        calls = []

        def _slow(args):
            calls.append(args)
            started.set()
            release.wait(10)
            return 0

        app_module._run_scan_subprocess = _slow
        worker = threading.Thread(target=app_module.run_scan_now, args=("cron",))
        worker.start()
        try:
            self.assertTrue(started.wait(5))
            self.assertFalse(app_module.run_scan_now("cron"))
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            worker.join(10)
        # Once it finishes, a new scan is allowed again.
        self.assertTrue(app_module.run_scan_now("cron"))
        self.assertEqual(len(calls), 2)

    def test_scheduler_thread_is_not_started_twice(self):
        app_module._run_scan_subprocess = lambda args: 0
        # A fresh manifest, so starting the scheduler runs no catch-up scan.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manifest = Path(tmp.name) / "manifest.json"
        manifest.write_text("{}")
        self.addCleanup(setattr, app_module, "MANIFEST_PATH", app_module.MANIFEST_PATH)
        app_module.MANIFEST_PATH = manifest
        self.assertTrue(app_module.start_scan_scheduler("0 3 * * *"))
        first = app_module._scan_scheduler_thread
        self.assertTrue(first.is_alive())
        self.assertFalse(app_module.start_scan_scheduler("0 4 * * *"))
        self.assertIs(app_module._scan_scheduler_thread, first)
        body = self.client.get("/api/scan/status").get_json()
        self.assertEqual(body["cron"], "0 3 * * *")
        self.assertTrue(body["scheduled"])

    def test_scheduler_fires_the_scan(self):
        fired = threading.Event()

        def _run(args):
            fired.set()
            return 0

        app_module._run_scan_subprocess = _run
        # Cron only resolves to the minute, so the fire time is stubbed to a
        # moment away rather than making the test wait for a real minute
        # boundary. The expression itself is parsed for real elsewhere.
        real_next = app_module._next_fire_time
        self.addCleanup(setattr, app_module, "_next_fire_time", real_next)
        app_module._next_fire_time = (
            lambda expr, now: now + timedelta(milliseconds=200))
        app_module._scan_scheduler_stop.clear()
        thread = threading.Thread(
            target=app_module._scan_scheduler_loop,
            args=("* * * * *",), kwargs={"poll_seconds": 0.05}, daemon=True)
        thread.start()
        try:
            self.assertTrue(fired.wait(20), "scheduler never ran a scan")
        finally:
            app_module._scan_scheduler_stop.set()
            thread.join(10)
            app_module._scan_scheduler_stop.clear()


class TestUnsetAndInvalid(ScanScheduleCase):

    def test_unset_variable_schedules_nothing(self):
        self.assertFalse(app_module.start_scan_scheduler(""))
        self.assertIsNone(app_module._scan_scheduler_thread)
        self.assertFalse(self.client.get("/api/scan/status").get_json()["scheduled"])

    def test_app_still_serves_its_normal_endpoints_with_no_cron(self):
        self.assertIsNone(app_module._scan_scheduler_thread)
        self.assertEqual(self.client.get("/api/manifest").status_code, 200)

    def test_nightly_expression_resolves_to_the_next_3am(self):
        now = datetime(2026, 9, 4, 21, 30, 0)
        self.assertEqual(app_module._next_fire_time("0 3 * * *", now),
                         datetime(2026, 9, 5, 3, 0, 0))

    def test_invalid_expression_is_refused_without_crashing(self):
        self.assertFalse(app_module.start_scan_scheduler("not a cron expression"))
        self.assertIsNone(app_module._scan_scheduler_thread)


class ManifestCase(ScanScheduleCase):
    """Points MANIFEST_PATH (and so scan_progress.json) at a temp folder."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.manifest = self.dir / "manifest.json"
        self.addCleanup(setattr, app_module, "MANIFEST_PATH", app_module.MANIFEST_PATH)
        app_module.MANIFEST_PATH = self.manifest
        self.calls = []

        def _run(args):
            self.calls.append(args)
            return 0
        app_module._run_scan_subprocess = _run

    def write_manifest(self, when: datetime):
        self.manifest.write_text("{}")
        ts = when.timestamp()
        os.utime(self.manifest, (ts, ts))


class TestCatchUp(ManifestCase):
    """A restart that kills the nightly scan, or ACP being down at the fire
    time, used to leave the archive a day stale. The scheduler now runs one
    scan at startup when the manifest is older than the last fire time."""

    NOW = datetime(2026, 11, 4, 11, 45, 0)
    CRON = "30 11 * * *"   # last fired at 11:30 on NOW's day

    def test_missing_manifest_is_overdue(self):
        self.assertTrue(app_module._scan_overdue(self.CRON, self.NOW))

    def test_manifest_older_than_the_last_fire_is_overdue(self):
        self.write_manifest(datetime(2026, 11, 3, 11, 40, 0))
        self.assertTrue(app_module._scan_overdue(self.CRON, self.NOW))

    def test_manifest_newer_than_the_last_fire_is_not_overdue(self):
        self.write_manifest(datetime(2026, 11, 4, 11, 31, 0))
        self.assertFalse(app_module._scan_overdue(self.CRON, self.NOW))

    def test_catch_up_runs_a_scan_when_stale(self):
        self.write_manifest(datetime.now() - timedelta(days=2))
        self.assertTrue(app_module._catch_up_scan("0 3 * * *"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(app_module.scan_status()["last_trigger"], "catchup")

    def test_catch_up_does_nothing_when_fresh(self):
        self.write_manifest(datetime.now())
        self.assertFalse(app_module._catch_up_scan("0 3 * * *"))
        self.assertEqual(self.calls, [])

    def test_scheduler_start_catches_up_once(self):
        # The next regular fire is pushed out of reach, so any second scan
        # could only come from a repeated catch-up.
        real_next = app_module._next_fire_time
        self.addCleanup(setattr, app_module, "_next_fire_time", real_next)
        app_module._next_fire_time = lambda expr, now: now + timedelta(days=1)
        self.write_manifest(datetime.now() - timedelta(days=2))
        self.assertTrue(app_module.start_scan_scheduler("0 3 * * *"))
        end = time.monotonic() + 5
        while not self.calls and time.monotonic() < end:
            time.sleep(0.02)
        self.assertEqual(len(self.calls), 1)
        # A second start is refused, and the running loop does not rescan.
        self.assertFalse(app_module.start_scan_scheduler("0 3 * * *"))
        time.sleep(0.3)
        self.assertEqual(len(self.calls), 1)

    def test_dead_scan_progress_is_cleared_at_startup(self):
        progress = self.dir / "scan_progress.json"
        progress.write_text('{"stage": "reading", "files_seen": 110129}')
        self.assertIsNotNone(app_module.scan_status()["progress"])
        saved = os.environ.pop("ACP_SKIP_STARTUP_MIGRATION", None)
        try:
            app_module.clear_dead_scan_progress()
            app_module.clear_dead_scan_progress()   # nothing left: still fine
        finally:
            if saved is not None:
                os.environ["ACP_SKIP_STARTUP_MIGRATION"] = saved
        self.assertFalse(progress.exists())
        self.assertIsNone(app_module.scan_status()["progress"])


class TestPublishAfterScan(ManifestCase):
    """A clean scan pushes the live page when ACP_PUBLISH_DEST is set, so the
    page no longer waits for a publish that runs before the scan."""

    def setUp(self):
        super().setUp()
        self.published = []
        real = app_module.publish_live_page
        self.addCleanup(setattr, app_module, "publish_live_page", real)

        def _publish():
            self.published.append(True)
            return {"ok": True, "projects": 1}, 200
        app_module.publish_live_page = _publish
        saved = os.environ.get("ACP_PUBLISH_DEST")

        def _restore_env():
            os.environ.pop("ACP_PUBLISH_DEST", None)
            if saved is not None:
                os.environ["ACP_PUBLISH_DEST"] = saved
        self.addCleanup(_restore_env)
        os.environ["ACP_PUBLISH_DEST"] = "u@h:/srv/live/shooting.json"

    def test_publishes_after_a_clean_scan(self):
        self.assertTrue(app_module.run_scan_now("test"))
        self.assertEqual(len(self.published), 1)

    def test_no_publish_after_a_failed_scan(self):
        app_module._run_scan_subprocess = lambda args: 1
        app_module.run_scan_now("test")
        self.assertEqual(self.published, [])

    def test_no_publish_when_not_configured(self):
        os.environ.pop("ACP_PUBLISH_DEST", None)
        app_module.run_scan_now("test")
        self.assertEqual(self.published, [])

    def test_publish_that_raises_leaves_the_scan_result_alone(self):
        def _boom():
            self.published.append(True)
            raise OSError("ssh: connect to host h port 22: timed out")
        app_module.publish_live_page = _boom
        with self.assertLogs(level="WARNING"):
            self.assertTrue(app_module.run_scan_now("test"))
        body = self.client.get("/api/scan/status").get_json()
        self.assertEqual(body["last_exit_code"], 0)
        self.assertFalse(body["running"])
        self.assertEqual(len(self.published), 1, "a failed publish is not retried")

    def test_failed_upload_leaves_the_scan_result_alone(self):
        def _bad():
            self.published.append(True)
            return {"ok": False}, 502
        app_module.publish_live_page = _bad
        self.assertTrue(app_module.run_scan_now("test"))
        body = self.client.get("/api/scan/status").get_json()
        self.assertEqual(body["last_exit_code"], 0)
        self.assertEqual(len(self.published), 1)


class TestLogging(unittest.TestCase):

    def setUp(self):
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        lib = logging.getLogger("paramiko")
        saved_lib = lib.level

        def _restore():
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)
            lib.setLevel(saved_lib)
        self.addCleanup(_restore)

    def test_info_reaches_the_log_and_libraries_stay_quiet(self):
        root = logging.getLogger()
        root.handlers[:] = []
        root.setLevel(logging.WARNING)
        logging.getLogger("paramiko").setLevel(logging.NOTSET)
        app_module._configure_logging()
        self.assertEqual(root.level, logging.INFO)
        self.assertTrue(root.handlers)
        self.assertEqual(logging.getLogger("paramiko").getEffectiveLevel(), logging.WARNING)

    def test_existing_setup_is_left_alone(self):
        root = logging.getLogger()
        marker = logging.NullHandler()
        root.handlers[:] = [marker]
        root.setLevel(logging.ERROR)
        app_module._configure_logging()
        self.assertEqual(root.handlers, [marker])
        self.assertEqual(root.level, logging.ERROR)


if __name__ == "__main__":
    unittest.main()
