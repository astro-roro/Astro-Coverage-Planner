"""The scan route, scan progress and the catalogue download (spec
docs/specs/windows-download-and-setup.md, tests C1 to C12).

The subprocess is stubbed everywhere except C11 and the progress test, which run
the real builder on a small archive of real FITS files.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import app as app_module  # noqa: E402
from test_settings import EnvCase, REMOTE  # noqa: E402

ENDPOINTS = (
    ("PUT", "/api/settings", {"fits_roots": []}),
    ("POST", "/api/settings/check-path", {"path": "/tmp"}),
    ("POST", "/api/settings/adopt-env", {}),
    ("POST", "/api/scan/start", {}),
    ("POST", "/api/catalogues/fetch", {}),
)


class ScanCase(EnvCase):

    def setUp(self):
        super().setUp()
        self.root = self.folder("archive")
        self.write_settings({"fits_roots": [self.root]})
        self.addCleanup(setattr, app_module, "_run_scan_subprocess",
                        app_module._run_scan_subprocess)
        self.addCleanup(setattr, app_module, "_run_catalogue_subprocess",
                        app_module._run_catalogue_subprocess)
        with app_module.SCAN_STATE_LOCK:
            app_module._catalogue_state.update(running=False, finished=False, error_kind=None)
        self.addCleanup(self._reset_catalogues)

    def _reset_catalogues(self):
        with app_module.SCAN_STATE_LOCK:
            app_module._catalogue_state.update(running=False, finished=False, error_kind=None)

    def start(self, **kwargs):
        return self.client.post("/api/scan/start", json={}, **kwargs)

    def wait_idle(self, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not app_module.scan_status()["running"]:
                return
            time.sleep(0.02)
        self.fail("scan still running")


class TestScanStart(ScanCase):

    def test_c1_start_with_roots_returns_202_and_status_shows_running(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        app_module._run_scan_subprocess = lambda args: gate.wait(10) and 0
        resp = self.start()
        self.assertEqual(resp.status_code, 202)
        status = self.client.get("/api/scan/status").get_json()
        self.assertTrue(status["running"])
        self.assertEqual(status["last_trigger"], "page")
        gate.set()
        self.wait_idle()
        self.assertEqual(self.client.get("/api/scan/status").get_json()["last_exit_code"], 0)

    def test_c2_two_requests_fired_together_give_one_202_and_one_409(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        spawned = []

        def block(args):
            spawned.append(args)
            gate.wait(10)
            return 0
        app_module._run_scan_subprocess = block
        codes = []
        barrier = threading.Barrier(2)

        def go():
            client = app_module.app.test_client()
            barrier.wait()
            codes.append(client.post("/api/scan/start", json={}).status_code)
        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(codes), [202, 409])
        gate.set()
        self.wait_idle()
        self.assertEqual(len(spawned), 1)

    def test_c3_no_roots_returns_400_and_spawns_nothing(self):
        self.write_settings({})
        spawned = []
        app_module._run_scan_subprocess = lambda args: spawned.append(args) or 0
        resp = self.start()
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (400, "no_archive_roots"))
        self.assertEqual(spawned, [])
        self.assertFalse(app_module.scan_status()["running"])

    def test_c3_packaged_mode_refuses_in_run_scan_now_itself(self):
        self.write_settings({})
        os.environ["ACP_PACKAGED"] = "1"
        spawned = []
        app_module._run_scan_subprocess = lambda args: spawned.append(args) or 0
        self.assertFalse(app_module.run_scan_now("cron"))
        self.assertEqual(spawned, [])

    def test_a_thread_that_cannot_start_gives_the_slot_back(self):
        with mock.patch.object(app_module.threading.Thread, "start", side_effect=RuntimeError("no")):
            resp = self.start()
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(app_module.scan_status()["running"])
        app_module._run_scan_subprocess = lambda args: 0
        self.assertEqual(self.start().status_code, 202)
        self.wait_idle()

    def test_c4_subprocess_gets_roots_and_manifest_from_the_file_and_env_wins(self):
        seen = []
        app_module._run_scan_subprocess = lambda args: seen.append(app_module._scan_subprocess_env()) or 0
        self.assertEqual(self.start().status_code, 202)
        self.wait_idle()
        self.assertEqual(seen[0]["FITS_ROOTS"], self.root)
        self.assertEqual(seen[0]["MANIFEST_PATH"], str(app_module.MANIFEST_PATH))
        other = self.folder("from-env")
        os.environ["FITS_ROOTS"] = other
        self.assertEqual(self.start().status_code, 202)
        self.wait_idle()
        self.assertEqual(seen[1]["FITS_ROOTS"], other)

    def test_c5_success_sets_exit_code_zero_and_failure_gives_a_kind_with_no_path(self):
        app_module._record_scan_error(None)
        app_module._run_scan_subprocess = lambda args: 0
        self.start()
        self.wait_idle()
        status = self.client.get("/api/scan/status").get_json()
        self.assertEqual(status["last_exit_code"], 0)
        self.assertIsNone(status["last_error_kind"])

        def fail(args):
            app_module._record_scan_error(f"PermissionError: [Errno 13] Permission denied: '{self.root}/secret-9f3'")
            return 1
        app_module._run_scan_subprocess = fail
        self.start()
        self.wait_idle()
        resp = self.client.get("/api/scan/status")
        status = resp.get_json()
        self.assertEqual(status["last_exit_code"], 1)
        self.assertEqual(status["last_error_kind"], "permission_denied")
        for needle in (self.root, "9f3", "Errno", str(self.tmp)):
            self.assertNotIn(needle, resp.get_data(as_text=True))

    def test_the_no_roots_message_points_at_settings(self):
        self.assertIn("Settings", app_module._SCAN_ERROR_MESSAGES["no_archive_roots"])
        self.assertNotIn("FITS_ROOTS", app_module._SCAN_ERROR_MESSAGES["no_archive_roots"])


class TestProgress(ScanCase):

    def progress_file(self) -> Path:
        return app_module.MANIFEST_PATH.with_name("scan_progress.json")

    def test_status_shows_stage_and_count_from_the_file(self):
        self.progress_file().write_text(json.dumps(
            {"stage": "reading", "files_seen": 12340, "updated": "x"}), encoding="utf-8")
        self.assertEqual(self.client.get("/api/scan/status").get_json()["progress"],
                         {"stage": "reading", "files_seen": 12340})

    def test_c6_a_missing_or_corrupt_progress_file_leaves_status_valid(self):
        self.assertIsNone(self.client.get("/api/scan/status").get_json()["progress"])
        for text in ("{not json", "[]", '"x"', '{"stage": "mystery", "files_seen": 3}',
                     '{"stage": "reading", "files_seen": "many"}',
                     '{"stage": "reading", "files_seen": -1}', '{"stage": "reading", "files_seen": true}'):
            self.progress_file().write_text(text, encoding="utf-8")
            resp = self.client.get("/api/scan/status")
            self.assertEqual(resp.status_code, 200, text)
            self.assertIsNone(resp.get_json()["progress"], text)

    def test_c6_a_path_in_the_progress_file_never_reaches_the_response(self):
        self.progress_file().write_text(json.dumps(
            {"stage": "listing", "files_seen": 5, "path": "/home/me/secret-9f3"}), encoding="utf-8")
        self.assertNotIn("9f3", self.client.get("/api/scan/status").get_data(as_text=True))

    def test_a_new_scan_clears_the_last_scans_progress(self):
        self.progress_file().write_text(json.dumps({"stage": "writing", "files_seen": 9}), encoding="utf-8")
        seen = []
        app_module._run_scan_subprocess = lambda args: seen.append(self.progress_file().exists()) or 0
        self.start()
        self.wait_idle()
        self.assertEqual(seen, [False])

    def test_c6_the_writer_never_writes_a_path_and_a_failed_write_does_not_raise(self):
        import build_archive_manifest as bam
        path = self.tmp / "p" / "scan_progress.json"
        path.parent.mkdir()
        writer = bam.ScanProgress(path, clock=iter(range(0, 1000, 3)).__next__)
        writer.update("listing", 7)
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc), ["files_seen", "stage", "updated"])
        self.assertEqual((doc["stage"], doc["files_seen"]), ("listing", 7))
        with mock.patch.object(bam.os, "replace", side_effect=PermissionError("held by a reader")):
            writer.update("reading", 8)
            writer.update("matching", 8)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["stage"], "listing")
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_c6_the_writer_is_throttled_to_one_write_in_two_seconds_per_stage(self):
        import build_archive_manifest as bam
        path = self.tmp / "scan_progress.json"
        now = [100.0]
        writer = bam.ScanProgress(path, clock=lambda: now[0])
        writer.update("listing", 1)
        now[0] = 100.5
        writer.update("listing", 99)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["files_seen"], 1)
        now[0] = 102.5
        writer.update("listing", 120)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["files_seen"], 120)
        now[0] = 102.6
        writer.update("reading", 0)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["stage"], "reading")


class TestRealScan(ScanCase):

    def test_c11_a_real_scan_leaves_the_archive_unchanged_and_ends_with_progress(self):
        sys.path.insert(0, str(REPO / "tests" / "fixtures"))
        from make_hostile_archive import build_archive
        archive = self.tmp / "hostile"
        archive.mkdir()
        build_archive(archive)

        def snapshot():
            out = {}
            for dirpath, _dirs, files in os.walk(archive):
                for name in files:
                    p = Path(dirpath) / name
                    st = p.lstat()
                    body = os.readlink(p) if p.is_symlink() else p.read_bytes()
                    out[str(p)] = (st.st_size, st.st_mtime_ns, body)
            return out
        before = snapshot()
        out_dir = self.tmp / "out"
        out_dir.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("ACP_") and k != "FITS_ROOTS"}
        env.update({"FITS_ROOTS": str(archive), "MANIFEST_PATH": str(out_dir / "manifest.json"),
                    "ACP_HOME": str(self.tmp / "home"), "ACP_DATA_DIR": str(out_dir)})
        proc = subprocess.run([sys.executable, str(REPO / "scripts" / "build_archive_manifest.py")],
                              cwd=str(REPO), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        self.assertEqual(snapshot(), before)
        progress = json.loads((out_dir / "scan_progress.json").read_text(encoding="utf-8"))
        self.assertEqual(progress["stage"], "writing")
        self.assertGreater(progress["files_seen"], 0)
        self.assertNotIn(str(archive), json.dumps(progress))
        self.assertTrue((out_dir / "manifest.json").exists())


class TestRestrictedRoutes(ScanCase):

    def send(self, method, path, body, **kwargs):
        return self.client.open(path, method=method, json=body, **kwargs)

    def test_c7_non_json_returns_415_for_every_route(self):
        for method, path, _ in ENDPOINTS:
            resp = self.client.open(path, method=method, data="x=1",
                                    content_type="application/x-www-form-urlencoded")
            self.assertEqual(resp.status_code, 415, path)

    def test_c7_a_mismatched_origin_returns_403_for_every_route(self):
        for method, path, body in ENDPOINTS:
            resp = self.send(method, path, body, headers={"Origin": "http://evil.example"})
            self.assertEqual(resp.status_code, 403, path)

    def test_c7_a_foreign_host_returns_400_for_every_route(self):
        for method, path, body in ENDPOINTS:
            resp = self.send(method, path, body, headers={"Host": "evil.example:5555"})
            self.assertEqual((resp.status_code, resp.get_json()["error"]), (400, "bad_host"), path)

    def test_c7_check_path_refuses_before_touching_the_path(self):
        with mock.patch.object(app_module.acp_settings, "check_path",
                               side_effect=AssertionError("touched the path")), \
                mock.patch.object(app_module.acp_settings, "check_directory",
                                  side_effect=AssertionError("touched the path")):
            for kwargs in ({"headers": {"Origin": "http://evil.example"}},
                           {"headers": {"Host": "evil.example"}},
                           {"environ_base": REMOTE},
                           {"data": "path=%5C%5Cevil%5Cshare", "content_type": "text/plain"}):
                if "data" in kwargs:
                    resp = self.client.post("/api/settings/check-path", **kwargs)
                else:
                    resp = self.client.post("/api/settings/check-path",
                                            json={"path": "\\\\evil\\share"}, **kwargs)
                self.assertIn(resp.status_code, (400, 403, 415), kwargs)

    def test_c8_without_a_token_a_remote_client_gets_403_on_every_route(self):
        spawned = []
        app_module._run_scan_subprocess = lambda args: spawned.append(args) or 0
        for method, path, body in ENDPOINTS:
            for kwargs in ({"environ_base": REMOTE},
                           {"headers": {"X-Forwarded-For": "203.0.113.9"}}):
                resp = self.send(method, path, body, **kwargs)
                self.assertEqual(resp.status_code, 403, (path, kwargs))
        self.assertEqual(spawned, [])
        self.assertEqual(self.client.get("/setup", environ_base=REMOTE).status_code, 200)
        text = self.client.get("/api/settings", environ_base=REMOTE).get_data(as_text=True)
        self.assertNotIn(self.root, text)

    def test_c9_with_a_token_bearer_or_cookie_is_authorised_and_neither_gets_401(self):
        os.environ["ACP_API_TOKEN"] = "t" * 24
        app_module._run_scan_subprocess = lambda args: 0
        for method, path, body in ENDPOINTS:
            self.assertEqual(self.send(method, path, body).status_code, 401, path)
        ok = self.start(headers={"Authorization": "Bearer " + "t" * 24}, environ_base=REMOTE)
        self.assertEqual(ok.status_code, 202)
        self.wait_idle()
        self.client.post("/login", data={"token": "t" * 24, "next": "/"})
        self.assertEqual(self.start(environ_base=REMOTE).status_code, 202)
        self.wait_idle()


class TestScheduled(ScanCase):

    def test_c10_scan_cron_from_the_file_starts_the_scheduler(self):
        self.addCleanup(app_module.stop_scan_scheduler)
        self.write_settings({"fits_roots": [self.root], "scan_cron": "0 3 * * *"})
        self.assertTrue(app_module.start_scan_scheduler())
        self.assertEqual(app_module.scan_status()["cron"], "0 3 * * *")
        self.assertTrue(app_module.scan_status()["scheduled"])


class TestCatalogues(ScanCase):

    def test_c12_fetch_runs_once_at_a_time_and_status_holds_no_paths(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        spawned = []

        def block(args):
            spawned.append(args)
            gate.wait(10)
            return 0
        app_module._run_catalogue_subprocess = block
        first = self.client.post("/api/catalogues/fetch", json={})
        second = self.client.post("/api/catalogues/fetch", json={})
        self.assertEqual((first.status_code, second.status_code), (202, 409))
        status = self.client.get("/api/catalogues/status")
        self.assertTrue(status.get_json()["running"])
        gate.set()
        end = time.monotonic() + 5
        while self.client.get("/api/catalogues/status").get_json()["running"] and time.monotonic() < end:
            time.sleep(0.02)
        body = self.client.get("/api/catalogues/status")
        self.assertEqual(len(spawned), 1)
        self.assertTrue(body.get_json()["finished"])
        self.assertIsNone(body.get_json()["error_kind"])
        text = body.get_data(as_text=True)
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(str(app_module.REPO_ROOT), text)

    def test_c12_the_script_writes_into_the_data_directory(self):
        seen = {}

        def capture(args):
            seen["args"] = args
            seen["catalogs"] = os.environ.get("CATALOGS_PATH")
            return 0
        os.environ["ACP_DATA_DIR"] = str(self.tmp / "data")
        data_path = app_module.acp_settings.data_path("catalogs.json")
        self.assertEqual(data_path.parent, self.tmp / "data")
        # The subprocess is handed the app's own catalogue path.
        captured_env = {}

        def fake_run(args, **kwargs):
            captured_env.update(kwargs["env"])
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(app_module.subprocess, "run", fake_run):
            self.assertEqual(app_module._run_catalogue_subprocess.__name__, "_run_catalogue_subprocess")
            app_module._run_catalogue_subprocess(["x"])
        self.assertEqual(captured_env["CATALOGS_PATH"], str(app_module.CATALOGS_PATH))

    def test_failure_kinds_are_a_kind_and_never_the_scripts_words(self):
        for code, kind in ((3, "network"), (1, "crashed")):
            app_module._run_catalogue_subprocess = lambda args, code=code: code
            self.assertEqual(self.client.post("/api/catalogues/fetch", json={}).status_code, 202)
            end = time.monotonic() + 5
            while self.client.get("/api/catalogues/status").get_json()["running"] and time.monotonic() < end:
                time.sleep(0.02)
            body = self.client.get("/api/catalogues/status").get_json()
            self.assertEqual((body["finished"], body["error_kind"]), (False, kind))

    def test_a_script_that_cannot_run_reports_could_not_start(self):
        def boom(args):
            raise OSError("secret-9f3 missing")
        app_module._run_catalogue_subprocess = boom
        self.client.post("/api/catalogues/fetch", json={})
        end = time.monotonic() + 5
        while self.client.get("/api/catalogues/status").get_json()["running"] and time.monotonic() < end:
            time.sleep(0.02)
        resp = self.client.get("/api/catalogues/status")
        self.assertEqual(resp.get_json()["error_kind"], "could_not_start")
        self.assertNotIn("9f3", resp.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
