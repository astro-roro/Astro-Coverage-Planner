"""The packaged launcher, the quit route and the tray (spec
docs/specs/windows-download-and-setup.md, tests L1, L2, T1 to T4).

Logic runs in this process with the browser, the tray library and the dialogs
stubbed. Anything that depends on ``app.py`` reading its environment at import
runs in a subprocess with the real launcher, as the spec asks.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

import launcher  # noqa: E402
import app as app_module  # noqa: E402
from test_settings import EnvCase  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------
# Environment, lock, port
# --------------------------------------------------------------------------

class TestPrepareEnvironment(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-launcher-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)

    def test_sets_what_app_py_reads_before_it_is_imported(self):
        env = {"ACP_HOME": str(self.tmp / "cfg"), "HOST": "0.0.0.0"}
        folder = launcher.prepare_environment(env, "linux")
        self.assertEqual(folder, self.tmp / "cfg")
        self.assertEqual(env["ACP_PACKAGED"], "1")
        self.assertEqual(env["ACP_BIND_HOST"], "127.0.0.1")
        self.assertEqual(env["ACP_DATA_DIR"], str(self.tmp / "cfg" / "data"))
        self.assertEqual(env["HOST"], "0.0.0.0", "HOST is left alone and ignored")
        self.assertTrue((self.tmp / "cfg" / "logs").is_dir())

    def test_a_data_dir_the_user_set_is_kept(self):
        env = {"ACP_HOME": str(self.tmp / "cfg"), "ACP_DATA_DIR": str(self.tmp / "mine")}
        launcher.prepare_environment(env, "linux")
        self.assertEqual(env["ACP_DATA_DIR"], str(self.tmp / "mine"))

    def test_the_per_os_folder_is_used_without_acp_home(self):
        env = {"XDG_CONFIG_HOME": str(self.tmp / "xdg")}
        folder = launcher.prepare_environment(env, "linux")
        self.assertEqual(folder, self.tmp / "xdg" / "acp")

    def test_no_config_folder_is_a_clear_error(self):
        with mock.patch.object(launcher.acp_settings, "config_dir", return_value=None):
            with self.assertRaises(launcher.LauncherError):
                launcher.prepare_environment({}, "linux")


@unittest.skipIf(sys.platform == "win32", "the Windows lock is checked on a Windows runner")
class TestInstanceLock(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-lock-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)

    def test_l1_a_held_lock_refuses_a_second_holder_and_records_the_port(self):
        first = launcher.InstanceLock(self.tmp / "acp.lock")
        second = launcher.InstanceLock(self.tmp / "acp.lock")
        self.assertTrue(first.acquire(5555))
        self.assertFalse(second.acquire(6000))
        self.assertEqual(second.recorded_port(), 5555)
        first.release()
        self.assertTrue(second.acquire(6000))
        self.assertEqual(second.recorded_port(), 6000)
        second.release()

    def test_an_unreadable_port_file_gives_none(self):
        lock = launcher.InstanceLock(self.tmp / "acp.lock")
        (self.tmp / "acp.lock").write_text("banana")
        self.assertIsNone(lock.recorded_port())


class TestPortInUse(unittest.TestCase):

    def test_a_listening_socket_counts_as_in_use(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            self.assertTrue(launcher.port_in_use(s.getsockname()[1]))
        self.assertFalse(launcher.port_in_use(free_port()))


# --------------------------------------------------------------------------
# The browser
# --------------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestBrowser(unittest.TestCase):

    def test_l1_the_browser_opens_only_after_the_server_answers(self):
        clock = FakeClock()
        events = []
        answers = iter([False, False, False, True])

        def probe(port):
            events.append("probe")
            return next(answers)

        launcher.open_when_ready(5555, 90, opener=lambda url: events.append(("open", url)),
                                 env={}, platform="win32", probe=probe, sleep=clock.sleep,
                                 clock=clock)
        self.assertEqual(events, ["probe"] * 4 + [("open", "http://127.0.0.1:5555/")])

    def test_l1_acp_no_browser_suppresses_it(self):
        opened = []
        launcher.open_when_ready(5555, 90, opener=opened.append, env={"ACP_NO_BROWSER": "1"},
                                 platform="win32", probe=lambda p: True)
        self.assertEqual(opened, [])

    def test_after_the_wait_it_opens_anyway_and_says_so(self):
        clock = FakeClock()
        opened = []
        with self.assertLogs("acp.launcher", "WARNING") as logs:
            launcher.open_when_ready(5555, 90, opener=opened.append, env={}, platform="win32",
                                     probe=lambda p: False, sleep=clock.sleep, clock=clock)
        self.assertEqual(opened, ["http://127.0.0.1:5555/"])
        self.assertIn("http://127.0.0.1:5555/", logs.output[0])
        self.assertGreaterEqual(clock.now, 90)

    def test_linux_without_a_display_prints_the_address_and_the_ssh_forward(self):
        opened = []
        with mock.patch("builtins.print") as printed:
            launcher.open_when_ready(5555, 0, opener=opened.append, env={}, platform="linux",
                                     probe=lambda p: True)
        self.assertEqual(opened, [])
        text = " ".join(str(c.args[0]) for c in printed.call_args_list)
        self.assertIn("http://127.0.0.1:5555/", text)
        self.assertIn("ssh -L 5555:127.0.0.1:5555", text)


# --------------------------------------------------------------------------
# The startup path, in this process
# --------------------------------------------------------------------------

class TestRun(unittest.TestCase):
    """_run with the lock held or the port busy never reaches the server."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-run-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.env = {"ACP_HOME": str(self.tmp), "PORT": str(free_port())}

    def test_l1_a_held_lock_opens_the_browser_at_the_recorded_port_and_starts_nothing(self):
        holder = launcher.InstanceLock(self.tmp / "acp.lock", "linux")
        if sys.platform == "win32":
            self.skipTest("POSIX lock")
        self.assertTrue(holder.acquire(5999))
        self.addCleanup(holder.release)
        with mock.patch.object(launcher, "open_when_ready") as opened, \
                mock.patch.object(launcher, "make_server",
                                  side_effect=AssertionError("started a server")):
            code = launcher._run(self.env, "linux", lambda url: None, self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(opened.call_args.args[0], 5999)

    @unittest.skipIf(sys.platform == "win32", "POSIX lock")
    def test_l1_a_busy_port_stops_with_a_message_naming_it(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            port = s.getsockname()[1]
            self.env["PORT"] = str(port)
            with mock.patch.object(launcher, "make_server",
                                   side_effect=AssertionError("started a server")):
                with self.assertRaises(launcher.LauncherError) as caught:
                    launcher._run(self.env, "linux", lambda url: None, self.tmp)
        self.assertIn(str(port), str(caught.exception))
        # And the lock is given back, so the next try is not told ACP is running.
        again = launcher.InstanceLock(self.tmp / "acp.lock", "linux")
        self.assertTrue(again.acquire(port))
        again.release()

    def test_l2_the_server_binds_loopback_even_when_host_says_otherwise(self):
        with mock.patch.dict(os.environ, {"HOST": "0.0.0.0"}):
            server = launcher.make_server(app_module.app, free_port())
        self.addCleanup(launcher.close_server, server)
        self.assertEqual(server.effective_host, "127.0.0.1")

    def test_a_port_that_cannot_be_bound_names_itself(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            with mock.patch("waitress.create_server", side_effect=OSError("in use")):
                with self.assertRaises(launcher.LauncherError) as caught:
                    launcher.make_server(app_module.app, port)
        self.assertIn(str(port), str(caught.exception))


# --------------------------------------------------------------------------
# Shutdown and the quit route
# --------------------------------------------------------------------------

class FakeServer:
    """Stands in for a waitress server: run() blocks until close()."""

    def __init__(self):
        self.closed = threading.Event()
        self.asyncore = mock.Mock()
        self._map = {}
        self.task_dispatcher = mock.Mock()
        self.run_thread = None

    def run(self):
        self.run_thread = threading.current_thread()
        self.closed.wait(10)

    def close(self):
        self.closed.set()


class FakeLock:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


class TestShutdown(unittest.TestCase):

    def make(self, scanning=False):
        self.server = FakeServer()
        self.lock = FakeLock()
        self.state = {"scanning": scanning}
        exits = []
        runtime = launcher.Runtime(self.server, self.lock, lambda: self.state["scanning"],
                                   exit_fn=exits.append)
        self.exits = exits
        runtime.start()
        self.addCleanup(self.server.close)
        return runtime

    def test_shutdown_stops_the_server_thread_and_releases_the_lock(self):
        runtime = self.make()
        self.assertTrue(runtime.shutdown(delay=0))
        self.assertTrue(runtime.stopped.wait(5))
        self.assertTrue(self.lock.released)
        self.assertFalse(runtime.thread.is_alive())

    def test_shutdown_refuses_while_a_scan_runs(self):
        runtime = self.make(scanning=True)
        self.assertFalse(runtime.shutdown(delay=0))
        self.assertFalse(runtime.stopped.wait(0.3))
        self.assertFalse(self.lock.released)
        self.state["scanning"] = False
        self.assertTrue(runtime.shutdown(delay=0))
        self.assertTrue(runtime.stopped.wait(5))

    def test_a_second_shutdown_is_harmless(self):
        runtime = self.make()
        self.assertTrue(runtime.shutdown(delay=0))
        self.assertTrue(runtime.shutdown(delay=0))
        self.assertTrue(runtime.stopped.wait(5))

    def test_a_server_that_will_not_stop_ends_the_process_after_logging_it(self):
        runtime = self.make()
        runtime.server.close = lambda: None           # the loop never ends
        with mock.patch.object(launcher, "STOP_JOIN_SECONDS", 0.2):
            runtime.shutdown(delay=0)
            self.assertTrue(runtime.stopped.wait(5))
            deadline = time.monotonic() + 3
            while not self.exits and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertEqual(self.exits, [0])
        self.server.closed.set()


class TestQuitRoute(EnvCase):

    def setUp(self):
        super().setUp()
        os.environ["ACP_PACKAGED"] = "1"
        self.addCleanup(app_module.set_quit_hook, None)

    def quit(self, **kwargs):
        return self.client.post("/api/quit", json={}, **kwargs)

    def test_l2_the_route_does_not_exist_outside_packaged_mode(self):
        os.environ.pop("ACP_PACKAGED")
        self.assertEqual(self.quit().status_code, 404)

    def test_l2_it_returns_409_during_a_scan_and_200_otherwise(self):
        scanning = {"now": True}
        runtime = launcher.Runtime(FakeServer(), FakeLock(), lambda: scanning["now"],
                                   exit_fn=lambda code: None)
        runtime.start()
        self.addCleanup(runtime.server.close)
        app_module.set_quit_hook(runtime.shutdown)
        resp = self.quit()
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (409, "scan_running"))
        scanning["now"] = False
        self.assertEqual(self.quit().status_code, 200)
        self.assertTrue(runtime.stopped.wait(5))

    def test_without_a_registered_launcher_it_says_unavailable(self):
        self.assertEqual(self.quit().status_code, 503)

    def test_c7_non_json_is_415_foreign_origin_403_foreign_host_400(self):
        app_module.set_quit_hook(lambda: self.fail("must not be reached"))
        resp = self.client.post("/api/quit", data="x=1",
                                content_type="application/x-www-form-urlencoded")
        self.assertEqual(resp.status_code, 415)
        self.assertEqual(self.quit(headers={"Origin": "http://evil.example"}).status_code, 403)
        resp = self.quit(headers={"Host": "evil.example:5555"})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (400, "bad_host"))

    def test_c8_a_remote_client_gets_403(self):
        app_module.set_quit_hook(lambda: self.fail("must not be reached"))
        resp = self.quit(environ_base={"REMOTE_ADDR": "203.0.113.9"})
        self.assertEqual(resp.status_code, 403)

    def test_a_packaged_build_answers_to_loopback_host_names_only_on_every_route(self):
        for path in ("/api/version", "/api/scan/status", "/setup"):
            resp = self.client.get(path, headers={"Host": "evil.example"})
            self.assertEqual((resp.status_code, path), (400, path))
            resp = self.client.get(path, headers={"Host": "localhost:5555"})
            self.assertEqual((resp.status_code, path), (200, path))

    def test_a_source_install_does_not_apply_the_host_check_to_every_route(self):
        os.environ.pop("ACP_PACKAGED")
        resp = self.client.get("/api/version", headers={"Host": "acp.example.net"})
        self.assertEqual(resp.status_code, 200)


# --------------------------------------------------------------------------
# The tray (pystray stubbed)
# --------------------------------------------------------------------------

class FakeMenuItem:
    def __init__(self, text, action=None, default=False, enabled=True, **kwargs):
        self._text, self.action, self.default, self._enabled = text, action, default, enabled

    @property
    def text(self):
        return self._text(self) if callable(self._text) else self._text

    @property
    def enabled(self):
        return self._enabled(self) if callable(self._enabled) else self._enabled


class FakePystray:
    class Menu:
        def __init__(self, *items):
            self.items = items

    MenuItem = FakeMenuItem

    class Icon:
        last = None

        def __init__(self, name, image, title, menu=None):
            self.menu, self.updates, self.stopped = menu, 0, False
            self.run_thread = None
            self._stop = threading.Event()
            FakePystray.Icon.last = self

        def run(self):
            self.run_thread = threading.current_thread()
            self._stop.wait(10)

        def stop(self):
            self.stopped = True
            self._stop.set()

        def update_menu(self):
            self.updates += 1


class TestTray(unittest.TestCase):

    def make(self, scan):
        self.scan = scan
        self.opened = []
        self.server = FakeServer()
        self.lock = FakeLock()
        self.runtime = launcher.Runtime(self.server, self.lock,
                                        lambda: bool(self.scan.get("running")),
                                        exit_fn=lambda code: None)
        tray = launcher.Tray(self.runtime, lambda: self.scan, lambda: self.opened.append(1),
                             FakePystray, image=object(), interval=0.05)
        self.runtime.tray = tray
        self.addCleanup(self.server.close)
        self.addCleanup(tray.stop)
        return tray

    def items(self, tray):
        return {i.text if not callable(i._text) else "status": i for i in tray.icon.menu.items}

    def test_t1_open_acp_is_the_default_item_and_opens_the_browser(self):
        tray = self.make({"running": False})
        items = tray.icon.menu.items
        self.assertEqual([i.text for i in items][0], "Open ACP")
        self.assertTrue(items[0].default)
        items[0].action(tray.icon, items[0])
        self.assertEqual(self.opened, [1])

    def test_t1_the_status_line_follows_the_scan_state(self):
        tray = self.make({"running": False})
        status = tray.icon.menu.items[1]
        self.assertEqual(status.text, "ACP is running")
        self.assertFalse(status.enabled)
        self.scan.update(running=True, progress={"stage": "reading", "files_seen": 12340})
        self.assertEqual(status.text, "Scanning: reading, 12,340 files")
        self.scan.update(progress=None)
        self.assertEqual(status.text, "Scanning")

    def test_t1_quit_is_disabled_during_a_scan_and_enabled_after(self):
        tray = self.make({"running": True})
        quit_item = tray.icon.menu.items[2]
        self.assertEqual(quit_item.text, "Quit ACP")
        self.assertFalse(quit_item.enabled)
        self.scan["running"] = False
        self.assertTrue(quit_item.enabled)

    def test_t1_the_menu_refreshes_only_when_the_text_changes(self):
        tray = self.make({"running": False})
        poller = threading.Thread(target=tray._poll, daemon=True)
        poller.start()
        time.sleep(0.3)
        settled = tray.icon.updates
        self.assertEqual(settled, 1)
        self.scan.update(running=True, progress={"stage": "listing", "files_seen": 5})
        time.sleep(0.3)
        self.assertEqual(tray.icon.updates, 2)

    def test_t2_quit_runs_the_same_shutdown_on_the_main_thread_tray(self):
        tray = self.make({"running": False})
        self.runtime.start()
        quit_item = tray.icon.menu.items[2]
        threading.Timer(0.2, quit_item.action, args=(tray.icon, quit_item)).start()
        tray.run()                                   # returns once Quit stops the icon
        self.assertIs(tray.icon.run_thread, threading.current_thread())
        self.assertTrue(self.runtime.stopped.wait(5))
        self.assertTrue(tray.icon.stopped)
        self.assertTrue(self.lock.released)
        self.assertFalse(self.runtime.thread.is_alive())
        self.assertIsNot(self.server.run_thread, threading.current_thread())

    def test_t2_quit_during_a_scan_does_nothing(self):
        tray = self.make({"running": True})
        self.runtime.start()
        tray.icon.menu.items[2].action(tray.icon, None)
        self.assertFalse(self.runtime.stopped.wait(0.3))
        self.assertFalse(tray.icon.stopped)

    def test_the_image_depends_on_the_platform(self):
        self.assertEqual(launcher.tray_image_name("darwin"), "acp-menubar.png")
        self.assertEqual(launcher.tray_image_name("win32"), "acp-tray.png")
        self.assertEqual(launcher.tray_image_name("linux"), "acp-tray.png")


class TestTrayFallback(unittest.TestCase):

    def runtime(self):
        return launcher.Runtime(FakeServer(), FakeLock(), lambda: False, exit_fn=lambda c: None)

    def test_t3_no_tray_reasons_are_logged_and_give_none(self):
        cases = [
            ("win32", {"ACP_NO_TRAY": "1"}, lambda: True, "ACP_NO_TRAY"),
            ("linux", {}, lambda: True, "no DISPLAY"),
            ("linux", {"DISPLAY": ":0"}, lambda: False, "no system tray"),
        ]
        for platform, env, probe, expect in cases:
            with self.subTest(platform=platform, env=env):
                with self.assertLogs("acp.launcher", "INFO") as logs:
                    tray = launcher.load_tray(platform, env, self.runtime(), dict, lambda: None,
                                              tray_probe=probe)
                self.assertIsNone(tray)
                self.assertIn("tray unavailable", logs.output[0])
                self.assertIn(expect, logs.output[0])

    def test_t3_a_missing_tray_library_is_not_an_error(self):
        with mock.patch.dict(sys.modules, {"pystray": None}):
            with self.assertLogs("acp.launcher", "WARNING") as logs:
                tray = launcher.load_tray("win32", {}, self.runtime(), dict, lambda: None)
        self.assertIsNone(tray)
        self.assertIn("tray unavailable", logs.output[0])

    def test_t3_a_tray_that_raises_on_start_leaves_the_server_running(self):
        runtime = self.runtime()
        runtime.start()
        self.addCleanup(runtime.server.close)

        class Broken:
            def run(self):
                raise RuntimeError("no display server")

            def stop(self):
                pass
        runtime.tray = Broken()
        threading.Timer(0.3, runtime.shutdown, kwargs={"delay": 0}).start()
        with self.assertLogs("acp.launcher", "ERROR") as logs:
            launcher.run_tray_or_wait(runtime, runtime.tray)
        self.assertIn("tray unavailable", logs.output[0])
        self.assertIsNone(runtime.tray)
        self.assertTrue(runtime.stopped.is_set(), "the main thread waited on the server")

    @unittest.skipUnless(sys.platform == "linux", "Linux tray probe")
    def test_t3_the_probe_is_false_without_xlib_or_a_display(self):
        with mock.patch.dict(sys.modules, {"Xlib": None}):
            self.assertFalse(launcher.linux_tray_present())


# --------------------------------------------------------------------------
# Startup errors
# --------------------------------------------------------------------------

class TestReportError(unittest.TestCase):

    def test_t4_windows_uses_messageboxw(self):
        calls = []

        class User32:
            def MessageBoxW(self, *args):
                calls.append(args)

        class Ctypes:
            class windll:
                user32 = User32()
        route = launcher.report_error("Port 5555 is in use", "C:\\log", "win32", {}, Ctypes)
        self.assertEqual(route, "windows")
        self.assertIn("Port 5555 is in use", calls[0][1])
        self.assertIn("C:\\log", calls[0][1])

    def test_t4_mac_uses_osascript_and_passes_the_text_as_an_argument(self):
        calls = []
        route = launcher.report_error('say "hi" \\ now', "/log", "darwin", {},
                                      run=lambda argv, **kw: calls.append(argv))
        self.assertEqual(route, "mac")
        argv = calls[0]
        self.assertEqual(argv[0], "osascript")
        self.assertTrue(any("display alert" in part for part in argv))
        self.assertIn('say "hi" \\ now', argv[-1], "the text is an argument, not part of the script")
        self.assertNotIn("say", " ".join(argv[:-1]))

    def test_t4_linux_prefers_the_terminal_then_zenity_then_kdialog_then_the_log(self):
        out = []
        route = launcher.report_error("boom", "/log", "linux", {}, isatty=lambda: True,
                                      stream=__import__("io").StringIO(), which=lambda n: None)
        self.assertEqual(route, "terminal")

        def run(argv, **kw):
            out.append(argv)
        route = launcher.report_error("boom", "/log", "linux", {}, isatty=lambda: False, run=run,
                                      which=lambda n: "/usr/bin/" + n)
        self.assertEqual((route, out[-1][0]), ("zenity", "zenity"))
        route = launcher.report_error("boom", "/log", "linux", {}, isatty=lambda: False, run=run,
                                      which=lambda n: "/usr/bin/kdialog" if n == "kdialog" else None)
        self.assertEqual((route, out[-1][0]), ("kdialog", "kdialog"))
        route = launcher.report_error("boom", "/log", "linux", {}, isatty=lambda: False, run=run,
                                      which=lambda n: None)
        self.assertEqual(route, "log")

    def test_acp_no_dialog_skips_it(self):
        route = launcher.report_error("boom", "/log", "darwin", {"ACP_NO_DIALOG": "1"},
                                      run=lambda *a, **k: self.fail("opened a dialog"))
        self.assertEqual(route, "skipped")


# --------------------------------------------------------------------------
# The real launcher in a subprocess
# --------------------------------------------------------------------------

@unittest.skipIf(sys.platform == "win32", "the Windows process is checked by the CI smoke test")
class TestLauncherProcess(unittest.TestCase):
    """L1 and L2 against the real thing: app.py reads its environment at import,
    so this cannot run in the test process."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="acp-proc-"))
        cls.port = free_port()
        cls.env = {k: v for k, v in os.environ.items()
                   if not k.startswith("ACP_") and k not in ("FITS_ROOTS", "MANIFEST_PATH", "PORT")}
        cls.env.update({"ACP_HOME": str(cls.tmp / "home"), "PORT": str(cls.port),
                        "HOST": "0.0.0.0", "ACP_NO_BROWSER": "1", "ACP_NO_TRAY": "1",
                        "ACP_NO_DIALOG": "1", "ACP_SKIP_STARTUP_MIGRATION": "1",
                        "ACP_DISABLE_BUNDLED_EXTENSIONS": "1"})
        cls.proc = subprocess.Popen([sys.executable, "-X", "utf8", "-B", str(REPO / "launcher.py")],
                                    env=cls.env, cwd=str(cls.tmp), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        cls.up = cls.wait_up(cls.port, cls.proc, 60)

    @staticmethod
    def wait_up(port, proc, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1):
                    return True
            except OSError:
                time.sleep(0.3)
        return False

    @classmethod
    def tearDownClass(cls):
        if cls.proc.poll() is None:
            cls.proc.kill()
            cls.proc.wait(30)
        __import__("shutil").rmtree(cls.tmp, ignore_errors=True)

    def log(self):
        return (self.tmp / "home" / "logs" / "acp.log").read_text(encoding="utf-8")

    def test_a_server_runs_and_the_log_says_it_bound_loopback_despite_host(self):
        self.assertTrue(self.up, "ACP did not start")
        self.assertIn("listening on 127.0.0.1:", self.log())
        self.assertNotIn("0.0.0.0", self.log().split("listening on")[1].splitlines()[0])

    def test_l1_a_second_launch_during_the_first_starts_nothing(self):
        self.assertTrue(self.up)
        second = subprocess.run([sys.executable, "-X", "utf8", "-B", str(REPO / "launcher.py")],
                                env=self.env, cwd=str(self.tmp), capture_output=True, timeout=60)
        self.assertEqual(second.returncode, 0)
        self.assertIn(f"already running on port {self.port}", self.log())
        self.assertEqual(self.log().count("listening on"), 1)

    def test_zz_quit_stops_the_process_frees_the_port_and_releases_the_lock(self):
        self.assertTrue(self.up)
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/quit", data=b"{}",
                                         method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(self.proc.wait(30), 0)
        self.assertFalse(launcher.port_in_use(self.port))
        again = launcher.InstanceLock(self.tmp / "home" / "acp.lock")
        self.assertTrue(again.acquire(self.port))
        again.release()

    def test_l1_a_busy_port_exits_with_a_message_naming_it(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            busy = s.getsockname()[1]
            env = dict(self.env, ACP_HOME=str(self.tmp / "home2"), PORT=str(busy))
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(REPO / "launcher.py")],
                                    env=env, cwd=str(self.tmp), capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 1)
        log = (self.tmp / "home2" / "logs" / "acp.log").read_text(encoding="utf-8")
        self.assertIn(f"Port {busy} is already in use", log)


if __name__ == "__main__":
    unittest.main()
