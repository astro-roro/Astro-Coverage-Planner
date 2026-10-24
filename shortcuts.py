"""Start menu, desktop and applications-menu shortcuts for a packaged ACP.

Spec: docs/specs/windows-download-and-setup.md, section 3.5.

Standard library only. Nothing here runs unless the user ticks the box on the
setup page (or presses Create on the settings page), and every file it writes
is one of the fixed names below, built from the app's own folder. No text from
a request ever reaches a path or a command.

Windows makes two ``.lnk`` files with PowerShell and the ``WScript.Shell`` COM
object, so there is no new dependency. The script text is fixed. Every path
reaches it through environment variables and is never interpolated, which
avoids the quoting trap with spaces and non-ASCII names. Linux writes one
``acp.desktop`` file. Mac has no shortcuts.

Every function that depends on the platform or the environment takes both as
parameters, and the Windows calls (``run``) are injectable, so the logic is
tested on any machine.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Callable, Mapping

LINK_NAME = "Astro Coverage Planner.lnk"
DESKTOP_ENTRY_NAME = "acp.desktop"

# subprocess.CREATE_NO_WINDOW exists only on Windows; the value is fixed.
CREATE_NO_WINDOW = 0x08000000

# One fixed script. Paths come in through the environment, never through the
# text. $env:ACP_LNK_FOLDERS holds the folders to write to, separated by a
# pipe, so the script never has to ask which desktop is in use; the caller
# does that with FOLDERS_SCRIPT first.
LINK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in ($env:ACP_LNK_FOLDERS -split '\|')) {
    if (-not $folder) { continue }
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $link = $shell.CreateShortcut((Join-Path $folder $env:ACP_LNK_NAME))
    $link.TargetPath = $env:ACP_LNK_TARGET
    $link.Arguments = $env:ACP_LNK_ARGS
    $link.WorkingDirectory = $env:ACP_LNK_WORKDIR
    $link.IconLocation = $env:ACP_LNK_ICON
    $link.Description = 'Astro Coverage Planner'
    $link.Save()
}
"""

# Prints the Start menu folder and the desktop folder, UTF-8, one per line. The
# desktop comes from GetFolderPath, which follows a OneDrive-redirected desktop.
FOLDERS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Output ([Environment]::GetFolderPath('StartMenu') + '\Programs')
Write-Output ([Environment]::GetFolderPath('Desktop'))
"""

# Reads a link back, for the tests that check what was written.
READ_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ACP_LNK_PATH)
Write-Output $link.TargetPath
Write-Output $link.Arguments
Write-Output $link.WorkingDirectory
Write-Output $link.IconLocation
"""


class ShortcutError(Exception):
    """Creating or removing a shortcut failed. The message is for the log."""


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def available(platform: str | None = None) -> bool:
    """Windows and Linux only. Mac has no shortcuts: the user drags ACP.app to
    Applications."""
    platform = platform or sys.platform
    return platform.startswith("win") or platform.startswith("linux")


def app_root(repo_root: Path | None = None) -> Path:
    """The packaged root (the folder that holds ``python`` and ``app``). The
    app folder is ``<root>/app``, so the root is its parent."""
    repo_root = repo_root or Path(__file__).resolve().parent
    return Path(repo_root).parent


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------

def _powershell(script: str, extra_env: Mapping[str, str], run: Callable | None = None,
                env: Mapping[str, str] | None = None) -> str:
    run = run or subprocess.run
    full_env = dict(_env(env))
    full_env.update(extra_env)
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   env=full_env, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=60, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(f"could not run PowerShell: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise ShortcutError("PowerShell failed: " + " | ".join(tail))
    return proc.stdout or ""


def windows_folders(run: Callable | None = None,
                    env: Mapping[str, str] | None = None) -> list[str]:
    """The Start menu Programs folder and the desktop folder."""
    out = _powershell(FOLDERS_SCRIPT, {}, run, env)
    folders = [line.strip() for line in out.splitlines() if line.strip()]
    if len(folders) != 2:
        raise ShortcutError("could not find the Start menu and desktop folders")
    return folders


def windows_link_spec(root: str | Path) -> dict[str, str]:
    """What the link points at, for the app folder ``root``. Windows separators
    throughout, because the link is read by Windows whichever OS built the
    string (the tests build it on any machine)."""
    base = PureWindowsPath(str(root))
    return {
        "target": str(base / "python" / "pythonw.exe"),
        "args": f'-X utf8 -B "{base / "app" / "launcher.py"}"',
        "workdir": str(base),
        "icon": str(base / "app" / "static" / "acp.ico"),
    }


def _windows_write(root, folders: list[str], run, env) -> list[str]:
    spec = windows_link_spec(root)
    _powershell(LINK_SCRIPT, {
        "ACP_LNK_FOLDERS": "|".join(folders),
        "ACP_LNK_NAME": LINK_NAME,
        "ACP_LNK_TARGET": spec["target"],
        "ACP_LNK_ARGS": spec["args"],
        "ACP_LNK_WORKDIR": spec["workdir"],
        "ACP_LNK_ICON": spec["icon"],
    }, run, env)
    return [os.path.join(folder, LINK_NAME) for folder in folders]


def read_windows_link(path: str, run: Callable | None = None,
                      env: Mapping[str, str] | None = None) -> dict[str, str]:
    out = _powershell(READ_SCRIPT, {"ACP_LNK_PATH": path}, run, env)
    lines = out.splitlines()
    lines += [""] * (4 - len(lines))
    return {"target": lines[0].strip(), "args": lines[1].strip(),
            "workdir": lines[2].strip(), "icon": lines[3].strip()}


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------

def _quote_exec(path: str) -> str:
    """A Desktop Entry ``Exec`` argument in double quotes. Inside quotes the
    spec says to backslash-escape the four characters below, and a literal
    percent must be doubled so it is not read as a field code."""
    escaped = (path.replace("\\", "\\\\").replace('"', '\\"')
               .replace("`", "\\`").replace("$", "\\$").replace("%", "%%"))
    return f'"{escaped}"'


def linux_entry_path(env: Mapping[str, str] | None = None,
                     home: Callable[[], Path] | None = None) -> Path:
    env = _env(env)
    data_home = (env.get("XDG_DATA_HOME") or "").strip()
    base = Path(data_home) if data_home else (home or Path.home)() / ".local" / "share"
    return base / "applications" / DESKTOP_ENTRY_NAME


def linux_entry_text(root: str | Path) -> str:
    root = str(root)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Astro Coverage Planner\n"
        "Comment=See which parts of the sky you have photographed\n"
        f"Exec={_quote_exec(root + '/start-acp')}\n"
        f"Icon={root}/app/static/acp.png\n"
        "Terminal=false\n"
        "Categories=Science;Astronomy;\n"
    )


def _linux_write(root, env, home) -> list[str]:
    target = linux_entry_path(env, home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(linux_entry_text(root), encoding="utf-8")
    os.chmod(target, 0o644)
    return [str(target)]


# --------------------------------------------------------------------------
# What the app and the launcher call
# --------------------------------------------------------------------------

def create(root: str | Path, platform: str | None = None, env: Mapping[str, str] | None = None,
           run: Callable | None = None, home: Callable[[], Path] | None = None,
           start_menu_dir: str | None = None, desktop_dir: str | None = None) -> list[str]:
    """Make the shortcuts for the app folder ``root``. Returns the files written.

    ``start_menu_dir`` and ``desktop_dir`` replace the real folders; the tests
    use them to stay out of the user's profile. The app never passes them.
    """
    platform = platform or sys.platform
    if platform.startswith("win"):
        if start_menu_dir and desktop_dir:
            folders = [start_menu_dir, desktop_dir]
        else:
            folders = windows_folders(run, env)
            folders = [start_menu_dir or folders[0], desktop_dir or folders[1]]
        return _windows_write(root, folders, run, env)
    if platform.startswith("linux"):
        return _linux_write(root, env, home)
    raise ShortcutError("shortcuts are not supported on this platform")


def existing(platform: str | None = None, env: Mapping[str, str] | None = None,
             run: Callable | None = None, home: Callable[[], Path] | None = None,
             start_menu_dir: str | None = None, desktop_dir: str | None = None) -> list[str]:
    """The shortcut files that exist now."""
    platform = platform or sys.platform
    if platform.startswith("win"):
        if start_menu_dir and desktop_dir:
            folders = [start_menu_dir, desktop_dir]
        else:
            folders = windows_folders(run, env)
            folders = [start_menu_dir or folders[0], desktop_dir or folders[1]]
        paths = [os.path.join(folder, LINK_NAME) for folder in folders]
    elif platform.startswith("linux"):
        paths = [str(linux_entry_path(env, home))]
    else:
        return []
    return [p for p in paths if os.path.exists(p)]


def remove(platform: str | None = None, env: Mapping[str, str] | None = None,
           run: Callable | None = None, home: Callable[[], Path] | None = None,
           start_menu_dir: str | None = None, desktop_dir: str | None = None) -> list[str]:
    """Delete the shortcut files ACP made. Returns the ones deleted."""
    gone = []
    for path in existing(platform, env, run, home, start_menu_dir, desktop_dir):
        try:
            os.remove(path)
        except OSError as exc:
            raise ShortcutError(f"could not delete a shortcut: {type(exc).__name__}") from exc
        gone.append(path)
    return gone


def rewrite(root: str | Path, platform: str | None = None, env: Mapping[str, str] | None = None,
            run: Callable | None = None, home: Callable[[], Path] | None = None,
            start_menu_dir: str | None = None, desktop_dir: str | None = None) -> list[str]:
    """Point the shortcuts that still exist at ``root``. A shortcut the user
    deleted is not recreated. Returns the files rewritten."""
    platform = platform or sys.platform
    present = existing(platform, env, run, home, start_menu_dir, desktop_dir)
    if not present:
        return []
    if platform.startswith("win"):
        folders = [os.path.dirname(p) for p in present]
        return _windows_write(root, folders, run, env)
    return _linux_write(root, env, home)
