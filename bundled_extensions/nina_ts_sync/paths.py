"""Locate `schedulerdb.sqlite` on disk.

Default location on Windows (where NINA actually runs):
    %LOCALAPPDATA%\\NINA\\SchedulerPlugin\\schedulerdb.sqlite

For Linux dev / CI / Mac, there's no real install — caller must override
via the `ACP_TS_DB_PATH` env var.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

ENV_DB_PATH = "ACP_TS_DB_PATH"
DEFAULT_DB_FILENAME = "schedulerdb.sqlite"
ENV_PROFILES_DIR = "ACP_NINA_PROFILES_DIR"
_NINA_NS = "{http://schemas.datacontract.org/2004/07/NINA.Profile}"


def default_db_path() -> Path | None:
    """Return the conventional install path, or None if we can't guess."""
    override = os.environ.get(ENV_DB_PATH)
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "NINA" / "SchedulerPlugin" / DEFAULT_DB_FILENAME

    return None


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Pick the DB path, preferring `explicit` then env then OS default.

    Raises FileNotFoundError if nothing resolves to an existing file — keeps
    the failure mode tight so we never accidentally write to a fresh empty
    file in some unexpected place.
    """
    if explicit is not None:
        p = Path(explicit).expanduser()
    else:
        guess = default_db_path()
        if guess is None:
            raise FileNotFoundError(
                f"No DB path supplied and {ENV_DB_PATH} is unset. "
                f"On non-Windows systems you must set {ENV_DB_PATH} explicitly."
            )
        p = guess

    if not p.is_file():
        raise FileNotFoundError(
            f"schedulerdb.sqlite not found at {p}. "
            "Make sure NINA + the Target Scheduler plugin have been started "
            "at least once on this machine, or override with "
            f"{ENV_DB_PATH}=/path/to/schedulerdb.sqlite."
        )
    return p


def default_profiles_dir() -> Path | None:
    """Return NINA's Profiles dir, or None when we can't guess."""
    override = os.environ.get(ENV_PROFILES_DIR)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "NINA" / "Profiles"
    return None


def read_profile_names(profiles_dir: Path | None = None) -> dict[str, str]:
    """Map ``profile_id`` → human ``Name`` by scanning NINA's profile XML files.

    Returns ``{}`` if the directory doesn't exist or no *.profile files are
    readable. Callers should fall back to a short-id label when a given id
    isn't present in the returned map. Backup files (``*.profile.bkp``) are
    skipped — only the live ``*.profile`` is authoritative.
    """
    if profiles_dir is None:
        profiles_dir = default_profiles_dir()
    if profiles_dir is None or not profiles_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in profiles_dir.glob("*.profile"):
        try:
            root = ET.parse(p).getroot()
        except (ET.ParseError, OSError):
            continue
        pid_el = root.find(f"{_NINA_NS}Id")
        name_el = root.find(f"{_NINA_NS}Name")
        if pid_el is None or pid_el.text is None:
            continue
        pid = pid_el.text.strip().lower()
        name = (name_el.text or "").strip() if name_el is not None else ""
        if pid and name:
            out[pid] = name
    return out
