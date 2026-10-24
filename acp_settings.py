"""The settings file, where it lives, and which value wins.

Spec: docs/specs/windows-download-and-setup.md, sections 1.1 to 1.6.

Standard library only, so the scanner and the app can both import it. Every
function that depends on the platform or the environment takes both as
parameters, so each OS branch runs on any machine. Nothing here reads or writes
the disk until it is asked to, and resolving the config folder never raises: a
container whose user has no home folder means "no settings file".
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parent

SETTINGS_VERSION = 1
SETTINGS_FILENAME = "settings.json"

# key -> environment variable. HOST and ACP_DATA_DIR are environment-only on
# purpose: no file or page may open ACP to the network or say where the file is.
ENV_VARS = {
    "fits_roots": "FITS_ROOTS",
    "manifest_path": "MANIFEST_PATH",
    "port": "PORT",
    "scan_cron": "ACP_SCAN_CRON",
}
DEFAULT_PORT = 5555

MAX_ROOTS = 32
MAX_PATH_CHARS = 1000


# --------------------------------------------------------------------------
# Where the files live
# --------------------------------------------------------------------------

def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _set(env: Mapping[str, str], name: str) -> str | None:
    """The variable's value, or None when it is unset or empty. Compose files
    often declare blank variables, so empty counts as unset."""
    value = (env.get(name) or "").strip()
    return value or None


def config_dir(platform: str | None = None, env: Mapping[str, str] | None = None,
               home: Callable[[], PurePath] | None = None) -> PurePath | None:
    """The per-OS folder that holds settings.json, or None when it cannot be
    worked out. ACP_HOME replaces it on every OS."""
    platform = platform or sys.platform
    env = _env(env)
    override = _set(env, "ACP_HOME")
    if override:
        return Path(override)

    # A Windows path built on another OS needs the Windows flavour to keep its
    # backslashes. Only the tests do that; on Windows itself Path is it.
    windows_like = platform.startswith("win")
    flavour = PureWindowsPath if windows_like and os.name != "nt" else Path

    def home_dir() -> PurePath | None:
        try:
            return (home or Path.home)()
        except Exception:
            return None

    if windows_like:
        appdata = _set(env, "APPDATA")
        if appdata:
            return flavour(appdata) / "ACP"
        base = home_dir()
        return base / "AppData" / "Roaming" / "ACP" if base is not None else None
    base = home_dir()
    if base is None:
        return None
    if platform == "darwin":
        return base / "Library" / "Application Support" / "ACP"
    xdg = _set(env, "XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else base / ".config") / "acp"


def settings_file(platform: str | None = None, env: Mapping[str, str] | None = None,
                  home: Callable[[], PurePath] | None = None) -> PurePath | None:
    folder = config_dir(platform, env, home)
    return folder / SETTINGS_FILENAME if folder is not None else None


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """The folder for files the user creates. ACP_DATA_DIR moves it; the launcher
    sets that in packaged mode, so a source install keeps <repo>/data."""
    override = _set(_env(env), "ACP_DATA_DIR")
    return Path(override) if override else REPO_ROOT / "data"


def data_path(name: str, env: Mapping[str, str] | None = None) -> Path:
    return data_dir(env) / name


# Files a container runtime creates inside every container. Docker makes
# /.dockerenv and Podman makes /run/.containerenv. Checking them needs no change
# to the image, so images already published and running read as containers too.
CONTAINER_MARKERS = ("/.dockerenv", "/run/.containerenv")


def in_container() -> bool:
    """Whether ACP is running inside a container. A settings file written there
    lives in the container's own layer, which Watchtower or a compose pull
    throws away, so the setup page is read-only: a container is configured by
    environment variables."""
    return any(os.path.exists(marker) for marker in CONTAINER_MARKERS)


def is_writable(path: PurePath | None) -> bool:
    """Whether a file at ``path`` could be created or replaced, and whether
    keeping one there makes sense (never in a container). Checks the nearest
    folder that exists, so it never creates anything."""
    if path is None or in_container():
        return False
    folder = Path(path).parent
    while not folder.exists():
        if folder.parent == folder:
            return False
        folder = folder.parent
    return folder.is_dir() and os.access(folder, os.W_OK | os.X_OK)


# --------------------------------------------------------------------------
# Reading and writing the file
# --------------------------------------------------------------------------

def load_file(path: PurePath | None) -> tuple[dict, str | None]:
    """The file's contents and an error code. No file is ``({}, None)``.

    Reading never writes. A corrupt file gives ``({}, "corrupt")`` and ACP
    carries on with defaults; ``save`` takes the one backup before it replaces
    the bad file. A read that wrote would let any client that can call
    ``GET /api/settings`` put files on the disk.
    """
    if path is None:
        return {}, None
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError:
        return {}, "unreadable"
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return data, None
    return {}, "corrupt"


def _back_up_corrupt(path: Path) -> None:
    """Save the bad file once as ``settings.json.bak``. A second corrupt file
    does not replace the first copy, because the first is the one the user
    most likely still cares about. Raises OSError if the copy fails, so the
    caller never replaces a file it could not save."""
    backup = path.with_name(path.name + ".bak")
    if not backup.exists():
        shutil.copyfile(path, backup)


def save(path: PurePath, updates: Mapping) -> dict:
    """Merge ``updates`` into the file atomically and return what was written.
    Keys this version does not know are kept. A corrupt file is backed up
    first, and the write is refused (OSError) if the backup fails."""
    scripts = str(REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import target_ids

    path = Path(path)
    current, error = load_file(path)
    if error == "corrupt":
        _back_up_corrupt(path)
    merged = {**current, **updates, "version": SETTINGS_VERSION}
    target_ids.atomic_write_json(path, merged, indent=2)
    return merged


def shortcuts_value(data: Mapping) -> bool | None:
    """``None`` means the user has not been asked yet."""
    value = data.get("shortcuts")
    return value if isinstance(value, bool) else None


# --------------------------------------------------------------------------
# Which value wins
# --------------------------------------------------------------------------

def parse_roots(raw: str) -> list[str]:
    """Split the environment form of fits_roots: semicolons, trimmed, no blanks."""
    return [part.strip() for part in (raw or "").split(";") if part.strip()]


def _port(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if 1 <= value <= 65535 else None


def _text(raw) -> str | None:
    return raw.strip() or None if isinstance(raw, str) else None


def _file_roots(raw) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    roots = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return roots or None


_FROM_ENV = {
    "fits_roots": lambda raw: parse_roots(raw) or None,
    "manifest_path": _text,
    "port": _port,
    "scan_cron": _text,
}
_FROM_FILE = {
    "fits_roots": _file_roots,
    "manifest_path": _text,
    "port": _port,
    "scan_cron": _text,
}
_DEFAULTS = {"fits_roots": [], "manifest_path": None, "port": DEFAULT_PORT, "scan_cron": None}


def resolve_all(env: Mapping[str, str] | None = None,
                file_data: Mapping | None = None) -> dict[str, dict]:
    """Each key's effective value, where it came from, and whether the
    environment locks it.

    The environment wins if the variable is set and usable, then the file,
    then the default. A value that cannot be used (a port of "banana") is
    skipped rather than crashing, so the next source gets its turn.
    ``file_data=None`` reads the real settings file.
    """
    env = _env(env)
    if file_data is None:
        file_data, _ = load_file(settings_file(env=env))
    out = {}
    for key, var in ENV_VARS.items():
        raw = _set(env, var)
        value = _FROM_ENV[key](raw) if raw is not None else None
        if value is not None:
            out[key] = {"value": value, "source": "env", "locked": True}
            continue
        value = _FROM_FILE[key](file_data.get(key))
        if value is not None:
            out[key] = {"value": value, "source": "file", "locked": False}
            continue
        default = _DEFAULTS[key]
        out[key] = {"value": list(default) if isinstance(default, list) else default,
                    "source": "default", "locked": False}
    return out


def effective(key: str, env: Mapping[str, str] | None = None):
    return resolve_all(env)[key]["value"]


# --------------------------------------------------------------------------
# Checking a folder path without touching the disk
# --------------------------------------------------------------------------

def strip_path(raw: str) -> str:
    """Windows "Copy as path" wraps the path in quotes. Strip those and any
    whitespace around them, then expand a leading ~."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return os.path.expanduser(text)


def _is_absolute(text: str) -> bool:
    return os.path.isabs(text) or PureWindowsPath(text).is_absolute()


def _compare_key(text: str) -> str:
    return os.path.normcase(os.path.normpath(text))


def validate_roots(raw: list) -> tuple[list[str], str | None]:
    """Clean a whole fits_roots list and return it with the first blocking
    code, or an empty code when it is fine. Syntax only: this never touches
    the disk, so a sleeping NAS cannot hold it up. The existence and
    readability checks are a separate step.

    Paths are stored as typed (after quote stripping and ~ expansion). The
    duplicate test normalises a copy for comparison only.
    """
    if len(raw) > MAX_ROOTS:
        return [], "too_many"
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = strip_path(item)
        if (not text or len(text) > MAX_PATH_CHARS
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)):
            return [], "invalid"
        if not _is_absolute(text):
            return [], "not_absolute"
        key = _compare_key(text)
        if key in seen:
            return [], "duplicate"
        seen.add(key)
        cleaned.append(text)
    return cleaned, None


# --------------------------------------------------------------------------
# Checking a folder path against the disk
# --------------------------------------------------------------------------

READ_TIMEOUT = 5.0
IMAGE_EXTENSIONS = (".fit", ".fits", ".fts", ".xisf")
IMAGE_WALK_ENTRIES = 5000
IMAGE_WALK_SECONDS = 2.0


def _probe_directory(text: str) -> str:
    """The blocking code for one folder, or "ok". Every call here can hang on
    a sleeping NAS, so this only ever runs in a worker thread (see
    check_directory). The code names the failure; the operating system's own
    wording is dropped, because it repeats the path."""
    try:
        if not os.path.exists(text):
            return "not_found"
        if not os.path.isdir(text):
            return "not_a_directory"
        with os.scandir(text) as entries:
            next(entries, None)
    except PermissionError:
        return "not_readable"
    except FileNotFoundError:
        return "not_found"
    except NotADirectoryError:
        return "not_a_directory"
    except (OSError, ValueError):
        return "not_readable"
    return "ok"


def _within_deadline(work: Callable[[], object], timeout: float):
    """Run ``work`` in a daemon thread and wait up to ``timeout`` seconds.
    Returns ``(finished, result)``. A call that never returns is abandoned, not
    waited on, so it cannot hold up one of the web server's few threads."""
    box: list = []
    done = threading.Event()

    def run():
        try:
            box.append(work())
        except Exception:
            box.append(None)
        finally:
            done.set()

    threading.Thread(target=run, name="acp-path-check", daemon=True).start()
    if not done.wait(timeout):
        return False, None
    return True, box[0] if box else None


def check_directory(text: str, timeout: float | None = None) -> str:
    """``ok``, ``not_found``, ``not_a_directory``, ``not_readable`` or
    ``timed_out`` for an absolute path that already passed the syntax check."""
    finished, code = _within_deadline(lambda: _probe_directory(text),
                                      READ_TIMEOUT if timeout is None else timeout)
    if not finished:
        return "timed_out"
    return code or "not_readable"


def has_images(text: str, max_entries: int = IMAGE_WALK_ENTRIES,
               max_seconds: float = IMAGE_WALK_SECONDS) -> bool | None:
    """Whether a FITS or XISF file sits inside the folder. Breadth first, so a
    top-level frame is found before a deep subfolder is opened, and capped at
    ``max_entries`` entries or ``max_seconds`` so a huge archive costs a moment.
    None means the walk ran out of budget or could not read the folder, and the
    caller says nothing rather than warn on a guess."""
    deadline = time.monotonic() + max_seconds
    queue = [text]
    seen = 0
    while queue:
        folder = queue.pop(0)
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    seen += 1
                    if seen > max_entries or time.monotonic() > deadline:
                        return None
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            queue.append(entry.path)
                        elif entry.name.lower().endswith(IMAGE_EXTENSIONS):
                            return True
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def _split(text: str) -> list[str]:
    return [part for part in _compare_key(text).replace("\\", "/").split("/") if part]


def _is_drive_root(text: str) -> bool:
    """``C:\\`` or ``/``. A UNC share root is not one: a NAS share is a
    normal thing to scan."""
    win = PureWindowsPath(text)
    if win.drive and not win.drive.startswith("\\\\"):
        return len(win.parts) <= 1
    if text.startswith("/"):
        return len(PurePosixPath(text).parts) <= 1
    return False


def path_warnings(text: str, others: list[str]) -> list[str]:
    """Warnings that do not block: ``overlaps`` (inside, or containing, another
    saved folder) and ``very_large`` (a drive root). Syntax only."""
    out = []
    mine = _split(text)
    for other in others:
        theirs = _split(other)
        shorter = min(len(mine), len(theirs))
        if mine[:shorter] == theirs[:shorter]:
            out.append("overlaps")
            break
    if _is_drive_root(text):
        out.append("very_large")
    return out


def check_path(raw: str, saved: list[str], timeout: float | None = None,
               look_for_images: bool = True) -> dict:
    """Every check in section 2.4 for one path, against the saved list.

    Returns ``{"code": <code>, "warnings": [...]}``. The code is ``ok`` or the
    first blocking one. Syntax is judged first and never touches the disk, so
    a UNC path is refused or accepted before Windows could open a connection
    to it. Nothing here writes.
    """
    if len(saved) >= MAX_ROOTS:
        return {"code": "too_many", "warnings": []}
    cleaned, code = validate_roots(list(saved) + [raw])
    if code:
        return {"code": code, "warnings": []}
    text = cleaned[-1]
    code = check_directory(text, timeout)
    if code != "ok":
        return {"code": code, "warnings": []}
    warnings = path_warnings(text, list(saved))
    if look_for_images:
        finished, found = _within_deadline(lambda: has_images(text), IMAGE_WALK_SECONDS + 1)
        if finished and found is False:
            warnings.append("no_images_seen")
    return {"code": "ok", "warnings": warnings}
