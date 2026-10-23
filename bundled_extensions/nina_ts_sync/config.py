"""Persistent per-extension config (currently just `profile_id`).

Stored as JSON in a sibling-of-extensions directory so it survives
extension reinstalls/upgrades and stays per-user without polluting ACP's
``data/`` dir with extension-specific files.

Layout (Windows):
    %APPDATA%\\acp\\extensions-config\\nina-ts-sync.json

Layout (Linux/macOS):
    ~/.config/acp/extensions-config/nina-ts-sync.json

The location is overridable with the ``ACP_EXTENSIONS_CONFIG_DIR`` env var
for testing or non-default install layouts.

Schema (future-proofed for additional keys; only ``profile_id`` used today):
    {
      "profile_id": "<NINA profileId GUID>"
    }
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_FILENAME = "nina-ts-sync.json"


def default_config_dir() -> Path:
    """Per-OS sibling of ``%APPDATA%\\acp\\extensions``."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "acp" / "extensions-config"
    return Path.home() / ".config" / "acp" / "extensions-config"


def config_path() -> Path:
    raw = os.environ.get("ACP_EXTENSIONS_CONFIG_DIR", "").strip()
    base = Path(raw).expanduser() if raw else default_config_dir()
    return base / CONFIG_FILENAME


def load() -> dict:
    """Read the config file. Returns ``{}`` if missing or malformed."""
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(updates: dict) -> dict:
    """Merge *updates* into the on-disk config and return the merged dict.

    Creates the parent directory if needed. Atomic-ish: writes to a sibling
    ``.tmp`` and renames over so a partial write can't corrupt the file.
    """
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    current = load()
    current.update(updates)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    tmp.replace(p)
    return current


def get_profile_id() -> str | None:
    """Convenience: return the configured profile_id, or None if unset."""
    pid = load().get("profile_id")
    return pid if isinstance(pid, str) and pid.strip() else None
