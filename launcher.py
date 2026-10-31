"""Starts a packaged Astro Coverage Planner: settings folder, log, lock, server,
browser and tray icon.

Spec: docs/specs/windows-download-and-setup.md, sections 3.3 and 3.4.

Run it with the bundled Python: ``pythonw.exe -X utf8 -B launcher.py`` on
Windows. It serves all three platforms. A source install and Docker never use
it. The module imports only the standard library and ``acp_settings`` at the
top; ``app`` is imported inside ``main`` after the environment is set, because
``app.py`` reads it at import.

Test switches (documented here because the smoke tests and the unit tests use
them): ACP_NO_BROWSER=1 skips opening the browser, ACP_NO_TRAY=1 skips the tray,
ACP_NO_DIALOG=1 skips the native error dialog.
"""
from __future__ import annotations

import io
import logging
import logging.handlers
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Mapping

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import acp_settings  # noqa: E402

LOG = logging.getLogger("acp.launcher")

LOOPBACK = "127.0.0.1"
TITLE = "Astro Coverage Planner"
BROWSER_WAIT_SECONDS = 90
# A second launch only needs the first to be starting. If it is not answering
# after this long the first copy is probably stuck and the user should see why.
SECOND_LAUNCH_WAIT_SECONDS = 15
STOP_JOIN_SECONDS = 5
# Windows: the byte the lock holds, far past the end of the file. The file's
# first bytes (the port) stay readable by the second launch while it is held.
WINDOWS_LOCK_OFFSET = 1 << 20


class LauncherError(Exception):
    """Something stopped ACP starting. The message is written for the user."""


# --------------------------------------------------------------------------
# Environment and log
# --------------------------------------------------------------------------

def prepare_environment(env: dict | None = None, platform: str | None = None) -> Path:
    """Work out the config folder and set what ``app.py`` reads at import.

    ACP_DATA_DIR is only set if the user has not set it. HOST is left alone
    and ignored: ACP_BIND_HOST takes precedence in the app and carries what the
    launcher really binds.
    """
    env = os.environ if env is None else env
    folder = acp_settings.config_dir(platform, env)
    if folder is None:
        raise LauncherError("ACP could not work out where to keep its settings. "
                            "Set the ACP_HOME environment variable to a folder you can write to.")
    folder = Path(folder)
    try:
        (folder / "logs").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LauncherError(f"ACP could not create its settings folder, {folder}. "
                            f"Check that you can write there ({type(exc).__name__}).") from exc
    env["ACP_HOME"] = str(folder)
    if not (env.get("ACP_DATA_DIR") or "").strip():
        env["ACP_DATA_DIR"] = str(folder / "data")
    env["ACP_PACKAGED"] = "1"
    env["ACP_BIND_HOST"] = LOOPBACK
    return folder


class _LogWriter(io.TextIOBase):
    """Stands in for stdout and stderr. ``pythonw.exe`` has none at all, and
    ``print`` from app.py should land in the log either way."""

    def __init__(self, logger: logging.Logger, level: int):
        self._logger = logger
        self._level = level
        self._pending = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._pending.strip():
            self._logger.log(self._level, self._pending.rstrip())
        self._pending = ""


def setup_logging(config: Path, echo_to=None) -> Path:
    """Log to ``logs/acp.log`` (1 MB, three files), send stdout and stderr
    there, and optionally echo to a terminal."""
    log_path = Path(config) / "logs" / "acp.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    if echo_to is not None:
        echo = logging.StreamHandler(echo_to)
        echo.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(echo)
    sys.stdout = _LogWriter(logging.getLogger("acp.stdout"), logging.INFO)
    sys.stderr = _LogWriter(logging.getLogger("acp.stderr"), logging.ERROR)
    return log_path


# --------------------------------------------------------------------------
# One copy at a time
# --------------------------------------------------------------------------

class InstanceLock:
    """An exclusive lock on ``acp.lock``, held for the life of the process.

    The operating system drops it if the process dies, so there is no stale
    lock to clean up. The file holds the port, so a second launch knows where
    the first one is.
    """

    def __init__(self, path: Path, platform: str | None = None):
        self.path = Path(path)
        self.windows = (platform or sys.platform).startswith("win")
        self._fd: int | None = None

    def acquire(self, port: int) -> bool:
        # O_BINARY: without it Windows turns the newline in the port file into two bytes.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            if self.windows:
                import msvcrt
                os.lseek(fd, WINDOWS_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{port}\n".encode("ascii"))
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if self.windows:
                import msvcrt
                os.lseek(self._fd, WINDOWS_LOCK_OFFSET, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def recorded_port(self) -> int | None:
        try:
            value = int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        return value if 1 <= value <= 65535 else None


def port_in_use(port: int, host: str = LOOPBACK) -> bool:
    """Whether something already listens on the port. A connect test, not a
    bind test: Windows lets a second program bind a port that waitress's
    SO_REUSEADDR has taken, so a bind would not notice."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# The browser
# --------------------------------------------------------------------------

def url_for(port: int) -> str:
    return f"http://{LOOPBACK}:{port}/"


def _answers(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{LOOPBACK}:{port}/api/version", timeout=2) as resp:
            return resp.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def wait_for_server(port: int, timeout: float = BROWSER_WAIT_SECONDS, interval: float = 0.5,
                    probe: Callable[[int], bool] = _answers,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> bool:
    deadline = clock() + timeout
    while True:
        if probe(port):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def has_display(platform: str, env: Mapping[str, str]) -> bool:
    if not platform.startswith("linux"):
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def open_when_ready(port: int, timeout: float = BROWSER_WAIT_SECONDS,
                    opener: Callable[[str], object] = webbrowser.open,
                    env: Mapping[str, str] | None = None, platform: str | None = None,
                    probe: Callable[[int], bool] = _answers,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> None:
    """Open the browser once ``/api/version`` answers. After ``timeout`` the
    browser opens anyway and the log says so, because a slow disk is better
    served by a page that says "connection refused, retry" than by nothing."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    url = url_for(port)
    if (env.get("ACP_NO_BROWSER") or "").strip() == "1":
        LOG.info("ACP_NO_BROWSER is set; not opening %s", url)
        return
    if not wait_for_server(port, timeout, probe=probe, sleep=sleep, clock=clock):
        LOG.warning("ACP has not answered after %s seconds. Opening the browser anyway: %s",
                    int(timeout), url)
    if not has_display(platform, env):
        print(f"ACP is running. Open {url} in a browser.")
        print(f"From another computer: ssh -L {port}:{LOOPBACK}:{port} <user>@<this-host>")
        LOG.info("no display; printed the URL instead of opening a browser")
        return
    LOG.info("opening %s", url)
    try:
        opener(url)
    except Exception:
        LOG.exception("could not open the browser; the address is %s", url)


# --------------------------------------------------------------------------
# Startup errors
# --------------------------------------------------------------------------

def report_error(message: str, log_path: Path | str, platform: str | None = None,
                 env: Mapping[str, str] | None = None, ctypes_mod=None,
                 run: Callable = subprocess.run, which: Callable = shutil.which,
                 isatty: Callable[[], bool] | None = None, stream=None) -> str:
    """Show a startup failure where the user can see it. Returns which route
    was used: windows, mac, terminal, zenity, kdialog, skipped or log."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    text = f"{message}\n\nThe log is at:\n{log_path}"
    LOG.error("startup failed: %s", message)
    if (env.get("ACP_NO_DIALOG") or "").strip() == "1":
        return "skipped"
    try:
        if platform.startswith("win"):
            if ctypes_mod is None:
                import ctypes as ctypes_mod
            ctypes_mod.windll.user32.MessageBoxW(0, text, TITLE, 0x10)  # MB_ICONERROR
            return "windows"
        if platform == "darwin":
            # The text travels as an argument, so quotes in it cannot end the script.
            run(["osascript",
                 "-e", "on run argv",
                 "-e", f'display alert "{TITLE}" message (item 1 of argv) as critical',
                 "-e", "end run", text], timeout=600)
            return "mac"
        tty = isatty() if isatty is not None else sys.__stderr__ is not None and sys.__stderr__.isatty()
        if tty:
            print(text, file=stream or sys.__stderr__)
            return "terminal"
        if which("zenity"):
            run(["zenity", "--error", f"--title={TITLE}", f"--text={text}"], timeout=600)
            return "zenity"
        if which("kdialog"):
            run(["kdialog", "--title", TITLE, "--error", text], timeout=600)
            return "kdialog"
    except Exception:
        LOG.exception("could not show the error dialog")
    return "log"


# --------------------------------------------------------------------------
# The server and shutdown
# --------------------------------------------------------------------------

def make_server(wsgi_app, port: int):
    """A waitress server bound to loopback only, so a port clash fails here and
    not later in a thread. HOST is ignored."""
    import waitress
    try:
        return waitress.create_server(wsgi_app, host=LOOPBACK, port=port, threads=4)
    except OSError as exc:
        raise LauncherError(f"ACP could not use port {port}. Another program may already be "
                            f"using it. Close that program, or set a different port in the "
                            f"settings file.") from exc


def close_server(server) -> None:
    """Stop accepting, drop open browser connections and stop the worker
    threads. server.close() alone leaves keep-alive connections holding the
    loop open for up to two minutes."""
    server.close()
    try:
        server.asyncore.close_all(server._map)
        server.task_dispatcher.shutdown()
    except Exception:
        LOG.exception("could not finish closing the server")


class Runtime:
    """Everything shutdown() has to stop: the server thread, the tray and the
    lock. The tray's Quit item and POST /api/quit call the same ``shutdown``."""

    def __init__(self, server, lock: InstanceLock | None, scan_running: Callable[[], bool],
                 exit_fn: Callable[[int], None] = os._exit):
        self.server = server
        self.lock = lock
        self.scan_running = scan_running
        self.exit_fn = exit_fn
        self.tray = None
        self.thread: threading.Thread | None = None
        self.stopped = threading.Event()
        self._state = threading.Lock()
        self._stopping = False

    def start(self) -> None:
        self.thread = threading.Thread(target=self._serve, name="acp-server", daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            self.server.run()
        except Exception:
            # Closing the sockets under a running select() raises EBADF on some
            # platforms. During shutdown that is the loop ending, not a fault.
            if self._stopping:
                LOG.info("the server loop ended")
            else:
                LOG.exception("the server stopped with an error")

    def shutdown(self, delay: float = 0.3) -> bool:
        """False while a scan runs. Otherwise stop everything after ``delay``
        seconds, so the HTTP answer to /api/quit gets out first, and return True."""
        with self._state:
            if self._stopping:
                return True
            if self.scan_running():
                return False
            self._stopping = True
        threading.Thread(target=self._stop, args=(delay,), name="acp-shutdown",
                         daemon=True).start()
        return True

    def _stop(self, delay: float) -> None:
        time.sleep(delay)
        LOG.info("shutting down")
        try:
            if self.tray is not None:
                self.tray.stop()
            close_server(self.server)
            if self.thread is not None:
                self.thread.join(STOP_JOIN_SECONDS)
                if self.thread.is_alive():
                    LOG.warning("the server did not stop within %s seconds", STOP_JOIN_SECONDS)
        finally:
            if self.lock is not None:
                self.lock.release()
            self.stopped.set()
        self._exit_if_threads_linger()

    def _exit_if_threads_linger(self) -> None:
        """A thread that is not a daemon would keep the process alive after
        main returns. Name it in the log and end the process."""
        main = threading.main_thread()
        current = threading.current_thread()
        stragglers = [t for t in threading.enumerate()
                      if t not in (main, current) and not t.daemon and t.is_alive()]
        if (self.thread is not None and self.thread.is_alive()) or stragglers:
            LOG.warning("ending the process with threads still running: %s",
                        [t.name for t in stragglers] or [self.thread.name])
            logging.shutdown()
            self.exit_fn(0)

    def wait(self) -> None:
        """Block the main thread until shutdown has finished, or Ctrl+C."""
        try:
            while not self.stopped.wait(0.5):
                pass
        except KeyboardInterrupt:
            self.shutdown(delay=0)
            self.stopped.wait(STOP_JOIN_SECONDS + 5)


# --------------------------------------------------------------------------
# The tray icon
# --------------------------------------------------------------------------

def status_text(scan: Mapping) -> str:
    if scan.get("running"):
        progress = scan.get("progress")
        if progress:
            return f"Scanning: {progress['stage']}, {progress['files_seen']:,} files"
        return "Scanning"
    return "ACP is running"


def linux_tray_present() -> bool:
    """Whether something owns the ``_NET_SYSTEM_TRAY_S0`` selection, which is
    what a panel with a system tray does. Needs python-xlib."""
    try:
        from Xlib import X, display
        disp = display.Display()
        try:
            owner = disp.get_selection_owner(disp.get_atom("_NET_SYSTEM_TRAY_S0"))
            return owner != X.NONE
        finally:
            disp.close()
    except Exception:
        return False


def tray_unavailable_reason(platform: str, env: Mapping[str, str],
                            tray_probe: Callable[[], bool] = linux_tray_present) -> str | None:
    """Why there will be no tray, or None when one should be tried."""
    if (env.get("ACP_NO_TRAY") or "").strip() == "1":
        return "ACP_NO_TRAY is set"
    if platform.startswith("linux"):
        if not env.get("DISPLAY"):
            return "no DISPLAY"
        if not tray_probe():
            return "this desktop has no system tray"
    return None


def tray_image_name(platform: str) -> str:
    return "acp-menubar.png" if platform == "darwin" else "acp-tray.png"


class Tray:
    """The notification-area icon: Open ACP, a status line and Quit ACP.

    ``pystray`` and the image loader are passed in, so the tests run the menu
    logic with stand-ins.
    """

    def __init__(self, runtime: Runtime, scan_status: Callable[[], Mapping],
                 open_browser: Callable[[], None], pystray, image,
                 interval: float = 2.0):
        self.runtime = runtime
        self.scan_status = scan_status
        self.open_browser = open_browser
        self.interval = interval
        self._done = threading.Event()
        self._poller: threading.Thread | None = None
        self.icon = pystray.Icon("acp", image, TITLE, menu=pystray.Menu(
            pystray.MenuItem("Open ACP", self._on_open, default=True),
            pystray.MenuItem(lambda item: self.current_text(), lambda icon, item: None,
                             enabled=False),
            pystray.MenuItem("Quit ACP", self._on_quit,
                             enabled=lambda item: not self.scanning()),
        ))

    def current_text(self) -> str:
        return status_text(self.scan_status())

    def scanning(self) -> bool:
        return bool(self.scan_status().get("running"))

    def _on_open(self, icon=None, item=None) -> None:
        self.open_browser()

    def _on_quit(self, icon=None, item=None) -> None:
        self.runtime.shutdown()

    def _poll(self) -> None:
        last = None
        while not self._done.wait(self.interval):
            try:
                text = (self.current_text(), self.scanning())
                if text != last:
                    last = text
                    self.icon.update_menu()
            except Exception:
                LOG.exception("tray status poll failed")

    def run(self) -> None:
        """Blocks until stop(). Must be called on the main thread."""
        self._poller = threading.Thread(target=self._poll, name="acp-tray-poll", daemon=True)
        self._poller.start()
        self.icon.run()

    def stop(self) -> None:
        self._done.set()
        try:
            self.icon.stop()
        except Exception:
            LOG.exception("could not stop the tray icon")


def load_tray(platform: str, env: Mapping[str, str], runtime: Runtime,
              scan_status: Callable[[], Mapping], open_browser: Callable[[], None],
              tray_probe: Callable[[], bool] = linux_tray_present) -> Tray | None:
    """Build the tray, or return None with the reason logged. Never raises."""
    reason = tray_unavailable_reason(platform, env, tray_probe)
    if reason:
        LOG.info("tray unavailable: %s", reason)
        return None
    try:
        if platform.startswith("linux"):
            # The GTK and AppIndicator backends need the system gi module, which the
            # bundled Python cannot import.
            os.environ.setdefault("PYSTRAY_BACKEND", "xorg")
        import pystray
        from PIL import Image
        image = Image.open(APP_DIR / "static" / tray_image_name(platform))
        image.load()
        return Tray(runtime, scan_status, open_browser, pystray, image)
    except Exception as exc:
        LOG.warning("tray unavailable: %s: %s", type(exc).__name__, exc)
        return None


def run_tray_or_wait(runtime: Runtime, tray: Tray | None) -> None:
    """Run the tray on this (the main) thread, or wait for shutdown when there
    is none or it fails. The server never depends on the tray."""
    if tray is not None:
        runtime.tray = tray
        try:
            tray.run()
        except Exception:
            LOG.exception("tray unavailable: it stopped with an error")
            runtime.tray = None
    runtime.wait()


# --------------------------------------------------------------------------
# Shortcuts that point at a folder that has moved
# --------------------------------------------------------------------------

def refresh_shortcuts(env: Mapping[str, str], platform: str | None = None) -> None:
    """If the user asked for shortcuts and the app folder has moved, point the
    ones that still exist at the new folder. A shortcut the user deleted is not
    recreated."""
    import shortcuts
    if not shortcuts.available(platform):
        return
    path = acp_settings.settings_file(platform, env)
    data, _ = acp_settings.load_file(path)
    root = str(shortcuts.app_root(APP_DIR))
    if data.get("shortcuts") is not True or data.get("shortcuts_app_dir") == root:
        return
    try:
        shortcuts.rewrite(root, platform)
        acp_settings.save(path, {"shortcuts_app_dir": root})
        LOG.info("shortcuts now point at %s", root)
    except Exception:
        LOG.exception("could not update the shortcuts")


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------

def main(env: dict | None = None, platform: str | None = None,
         opener: Callable[[str], object] = webbrowser.open) -> int:
    env = os.environ if env is None else env
    platform = platform or sys.platform
    try:
        config = prepare_environment(env, platform)
    except LauncherError as exc:
        report_error(str(exc), "(no log yet)", platform, env)
        return 1
    echo = sys.__stderr__ if (platform.startswith("linux") and sys.__stderr__ is not None
                              and sys.__stderr__.isatty()) else None
    log_path = setup_logging(config, echo)
    try:
        return _run(env, platform, opener, config)
    except LauncherError as exc:
        report_error(str(exc), log_path, platform, env)
        return 1
    except Exception as exc:
        LOG.exception("ACP could not start")
        report_error(f"ACP could not start ({type(exc).__name__}).", log_path, platform, env)
        return 1


def _run(env: dict, platform: str, opener, config: Path) -> int:
    port = acp_settings.effective("port", env)
    lock = InstanceLock(config / "acp.lock", platform)
    if not lock.acquire(port):
        recorded = lock.recorded_port() or port
        LOG.info("ACP is already running on port %s; opening the browser", recorded)
        open_when_ready(recorded, SECOND_LAUNCH_WAIT_SECONDS, opener, env, platform)
        return 0
    runtime = None
    try:
        if port_in_use(port):
            raise LauncherError(f"Port {port} is already in use by another program, so ACP "
                                f"cannot start. Close that program, or set a different port "
                                f"in the settings file.")
        LOG.info("starting ACP; settings in %s, port %s", config, port)
        import app as acp_app
        server = make_server(acp_app.app, port)
        runtime = Runtime(server, lock, lambda: bool(acp_app.scan_status()["running"]))
        acp_app.set_quit_hook(runtime.shutdown)
        runtime.start()
        LOG.info("listening on %s:%s", server.effective_host, server.effective_port)

        threading.Thread(target=open_when_ready, args=(port, BROWSER_WAIT_SECONDS, opener, env,
                                                       platform),
                         name="acp-browser", daemon=True).start()
        threading.Thread(target=refresh_shortcuts, args=(env, platform),
                         name="acp-shortcuts", daemon=True).start()

        tray = load_tray(platform, env, runtime, acp_app.scan_status,
                         lambda: open_when_ready(port, 0, opener, env, platform,
                                                 probe=lambda p: True))
        run_tray_or_wait(runtime, tray)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
