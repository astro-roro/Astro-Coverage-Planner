"""Tests for the scheduled archive rescan (ACP_SCAN_CRON) and /api/scan/status.

The scheduler lives inside the web process so the Docker image needs no cron
daemon. Two things must hold no matter what: with the variable unset the app
behaves exactly as it always has, and a second scan can never start while one is
still running, because two builders would race over the same manifest and the
same scan cache.
"""
from __future__ import annotations

import sys
import threading
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


if __name__ == "__main__":
    unittest.main()
