"""Smoke test for a built ACP package (spec section 9: K1 to K4, K6, K8, W2).

Run it with the package's own Python, from outside the package, on an unzipped
copy that is in a different folder from the build:

    ACP\\python\\python.exe -X utf8 -B packaging\\smoke.py --package ACP --version 1.0.0

It starts the launcher with ACP_HOME in a temp folder whose name has
non-ASCII characters, drives the real HTTP routes, and exits 1 on the first
failed check. Standard library only. It also runs against a source checkout
(``--package`` pointing at the repo, ``--launcher`` at launcher.py), which is
how the unit tests exercise it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

START_BUDGET_SECONDS = 30
SCAN_BUDGET_SECONDS = 180
FIXTURE_FOLDER = "Andromède Études"


class Failure(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)
    print(f"ok: {message}")


# --------------------------------------------------------------------------
# A fixture archive of real FITS files, written by hand so the zip's Python
# needs nothing but the standard library.
# --------------------------------------------------------------------------

def _card(key: str, value, comment: str = "") -> bytes:
    if isinstance(value, str):
        body = f"{key:<8}= '{value:<8}'"
    elif isinstance(value, bool):
        body = f"{key:<8}= {'T' if value else 'F':>20}"
    else:
        body = f"{key:<8}= {value:>20}"
    return body.ljust(80)[:80].encode("ascii")


def write_fits(path: Path, **cards) -> None:
    side = 16
    header = [_card("SIMPLE", True), _card("BITPIX", 16), _card("NAXIS", 2),
              _card("NAXIS1", side), _card("NAXIS2", side)]
    header += [_card(key, value) for key, value in cards.items()]
    header.append(b"END".ljust(80))
    block = b"".join(header)
    block += b" " * (-len(block) % 2880)
    data = bytes(side * side * 2)
    data += b"\0" * (-len(data) % 2880)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(block + data)


def make_fixture(root: Path) -> Path:
    folder = root / FIXTURE_FOLDER
    for index, (date, filt) in enumerate((("2021-07-30T10:53:29", "L"),
                                          ("2021-07-30T11:00:29", "L"),
                                          ("2021-07-31T10:53:29", "R"))):
        write_fits(folder / "M 31" / f"frame_{index:03d}.fits", **{
            "IMAGETYP": "Light", "EXPTIME": 300.0, "FILTER": filt, "OBJECT": "M 31",
            "DATE-OBS": date, "OBJCTRA": "00 42 44.30", "OBJCTDEC": "+41 16 09.0",
            "FOCALLEN": 530.0, "XPIXSZ": 3.76})
    return folder


# --------------------------------------------------------------------------
# HTTP without following redirects
# --------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


# No proxy: a build machine's proxy settings must not catch loopback calls.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def call(port: int, method: str, path: str, body=None, accept: str | None = None):
    """(status, headers, parsed body) for one request; HTTP errors do not raise."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if accept:
        request.add_header("Accept", accept)
    try:
        with _OPENER.open(request, timeout=20) as resp:
            raw, status, headers = resp.read(), resp.status, resp.headers
    except urllib.error.HTTPError as err:
        raw, status, headers = err.read(), err.code, err.headers
    except OSError:
        # Nothing answering yet (or any more). Callers poll on the status.
        return 0, {}, None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    return status, headers, parsed


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_is_free(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return False
    except OSError:
        return True


def wait_for(predicate, seconds: float, interval: float = 0.5) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# --------------------------------------------------------------------------
# The package under test
# --------------------------------------------------------------------------

def snapshot(folder: Path) -> set[str]:
    found = set()
    for current, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            found.add(os.path.relpath(os.path.join(current, name), folder))
    return found


def interpreter_flags(python: Path) -> dict:
    """What sys.flags says when the interpreter gets the same command line
    Start ACP.bat uses. The ._pth file makes the embeddable Python ignore
    PYTHONUTF8 and PYTHONDONTWRITEBYTECODE, so the flags must come from here."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONDONTWRITEBYTECODE")}
    out = subprocess.run(
        [str(python), "-X", "utf8", "-B", "-c",
         "import sys, json; print(json.dumps({'utf8': sys.flags.utf8_mode, "
         "'nobytecode': sys.flags.dont_write_bytecode}))"],
        capture_output=True, text=True, env=env, timeout=60, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def start_launcher(python: Path, launcher: Path, home: Path, port: int, tray: bool):
    env = dict(os.environ)
    env.update({"ACP_HOME": str(home), "PORT": str(port), "HOST": "0.0.0.0",
                "ACP_NO_BROWSER": "1", "ACP_NO_DIALOG": "1"})
    if tray:
        env.pop("ACP_NO_TRAY", None)
    else:
        env["ACP_NO_TRAY"] = "1"
    for name in ("FITS_ROOTS", "MANIFEST_PATH", "ACP_DATA_DIR", "ACP_API_TOKEN",
                 "PYTHONUTF8", "PYTHONDONTWRITEBYTECODE"):
        env.pop(name, None)
    return subprocess.Popen([str(python), "-X", "utf8", "-B", str(launcher)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=30)


def log_text(home: Path) -> str:
    try:
        return (home / "logs" / "acp.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def windows_child_names(pid: int) -> list[str]:
    """Names of the processes whose parent is ``pid``. Windows only."""
    script = (f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}' "
              "| ForEach-Object { $_.Name }")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60)
    return [line.strip().lower() for line in out.stdout.splitlines() if line.strip()]


def run(args) -> None:
    package = Path(args.package).resolve()
    python = Path(args.python) if args.python else package / "python" / (
        "python.exe" if sys.platform == "win32" else "bin/python3")
    launcher = Path(args.launcher) if args.launcher else package / "app" / "launcher.py"
    work = Path(tempfile.mkdtemp(prefix="acp smoke é "))
    home = work / "ACP Hôme"
    try:
        # K6
        flags = interpreter_flags(python)
        check(flags == {"utf8": 1, "nobytecode": 1},
              f"the package's Python takes -X utf8 and -B from the command line ({flags})")

        fixture = make_fixture(work / "archive")
        before = snapshot(package)
        port = free_port()
        # On Windows the launcher runs as pythonw.exe, as Start ACP.bat starts it.
        gui = python.with_name("pythonw.exe") if args.windows_checks else python
        proc = start_launcher(gui, launcher, home, port, tray=False)
        try:
            # K1
            started = wait_for(lambda: call(port, "GET", "/api/version")[0] == 200
                               if proc.poll() is None else False, START_BUDGET_SECONDS)
            check(started, f"/api/version answers within {START_BUDGET_SECONDS} seconds")
            _, _, version = call(port, "GET", "/api/version")
            if args.version:
                check(version["version"] == args.version, f"the version is {args.version}")

            # K2
            status, headers, _ = call(port, "GET", "/", accept="text/html")
            check(status == 302 and headers.get("Location", "").endswith("/setup"),
                  "GET / redirects to /setup on first run")
            status, _, body = call(port, "PUT", "/api/settings", {"fits_roots": [str(fixture)]})
            check(status == 200, f"saving the image folder works (HTTP {status})")
            status, _, _ = call(port, "POST", "/api/scan/start", {})
            check(status == 202, "the scan starts")

            def finished():
                _, _, s = call(port, "GET", "/api/scan/status")
                return bool(s) and not s["running"]
            check(wait_for(finished, SCAN_BUDGET_SECONDS, 1.0), "the scan finishes")
            _, _, scan = call(port, "GET", "/api/scan/status")
            check(scan["last_exit_code"] == 0, "the scan exits with code 0")
            _, _, settings = call(port, "GET", "/api/settings")
            check(settings["targets"] >= 1, f"the scan found targets ({settings['targets']})")
            _, _, manifest = call(port, "GET", "/api/extensions/manifest")
            check("nina_ts_sync" in json.dumps(manifest).replace("-", "_") or args.no_extension_check,
                  "the Target Scheduler sync extension is listed")
            check("-X utf8 -B" in log_text(home), "the scan subprocess ran with -X utf8 and -B")

            # K3
            after = snapshot(package)
            check(after == before, f"the package folder has no new files ({sorted(after - before)[:3]})")
            check((home / "settings.json").exists() and (home / "data").is_dir(),
                  "settings.json and data/ exist under ACP_HOME")

            # W2
            if sys.platform == "win32" and args.windows_checks:
                children = windows_child_names(proc.pid)
                check("conhost.exe" not in children, f"no console window child ({children})")

            # K4
            status, _, _ = call(port, "POST", "/api/quit", {})
            check(status == 200, "POST /api/quit answers 200")
            check(wait_for(lambda: proc.poll() is not None, 20), "the process exits after quit")
            check(wait_for(lambda: port_is_free(port), 10), "the port is free after quit")
        finally:
            stop(proc)

        # K8
        if args.tray:
            port = free_port()
            proc = start_launcher(gui, launcher, home, port, tray=True)
            try:
                started = wait_for(lambda: call(port, "GET", "/api/version")[0] == 200
                                   if proc.poll() is None else False, START_BUDGET_SECONDS)
                check(started, "/api/version answers with the tray enabled")
                text = log_text(home)
                print("note: the tray " + ("was not available on this machine (logged)"
                                           if "tray unavailable" in text else "started"))
                call(port, "POST", "/api/quit", {})
                wait_for(lambda: proc.poll() is not None, 20)
            finally:
                stop(proc)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True, help="the unzipped ACP folder")
    parser.add_argument("--version", help="the version /api/version must report")
    parser.add_argument("--python", help="default: the package's own Python")
    parser.add_argument("--launcher", help="default: <package>/app/launcher.py")
    parser.add_argument("--tray", action="store_true", help="also start with the tray enabled (K8)")
    parser.add_argument("--windows-checks", action="store_true", help="run the Windows-only checks (W2)")
    parser.add_argument("--no-extension-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        run(args)
    except Failure as exc:
        print(f"FAIL: {exc}")
        return 1
    print("smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
