"""Tells the page when a newer ACP release is out.

The only network request is one GET to GitHub's public "latest release" API,
made at most once a day and only when the check is switched on. It carries no
version, no identifier and no cookie: just a fixed User-Agent (GitHub rejects
requests without one) and the Accept header. The answer is cached in memory and
in a small file in the data folder, so a restart does not ask again.

Every failure (no network, rate limit, bad JSON, a surprising shape) means "no
update known". Nothing here raises to the caller and nothing is shown to the
user. Standard library only; the fetch is injectable so tests never touch the
network.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Mapping

REPO = "astro-roro/Astro-Coverage-Planner"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL_PREFIX = f"https://github.com/{REPO}/"
CACHE_FILENAME = "update_check.json"

# One answer is good for a day. A failed attempt is retried after a shorter
# wait, so a laptop that started offline learns about a release the same day,
# yet a dead network or a rate limit is not hammered on every page load.
FRESH_SECONDS = 24 * 3600
RETRY_SECONDS = 6 * 3600
TIMEOUT_SECONDS = 4.0
MAX_BODY_BYTES = 256 * 1024

_VERSION_RE = re.compile(r"^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})$")
_FALSY = frozenset({"0", "false", "off", "no"})

_lock = threading.Lock()
_memory: dict | None = None


def parse_version(text) -> tuple[int, int, int] | None:
    """``v1.10.0`` or ``1.10.0`` as ``(1, 10, 0)``. Anything else, including
    ``dev``, ``1.2``, ``1.2.0-beta`` and non-text, is ``None``."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.match(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def jump_size(running, latest) -> str | None:
    """``major``, ``minor`` or ``patch`` when ``latest`` is newer than
    ``running``, else ``None`` (equal, older, or either unparseable)."""
    a, b = parse_version(running), parse_version(latest)
    if a is None or b is None or b <= a:
        return None
    if b[0] != a[0]:
        return "major"
    if b[1] != a[1]:
        return "minor"
    return "patch"


def enabled(env: Mapping[str, str], file_data: Mapping) -> dict:
    """``{"value": bool, "locked": bool}``. The environment variable
    ACP_UPDATE_CHECK wins and locks the setting (0, false, off or no turns it
    off, anything else leaves it on), then the settings file, then on."""
    raw = (env.get("ACP_UPDATE_CHECK") or "").strip().lower()
    if raw:
        return {"value": raw not in _FALSY, "locked": True}
    value = file_data.get("update_check")
    return {"value": value if isinstance(value, bool) else True, "locked": False}


def _fetch_latest() -> dict:
    """One GET to GitHub. Raises on any failure; ``check`` catches it."""
    request = urllib.request.Request(
        LATEST_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "acp-update-check"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read(MAX_BODY_BYTES).decode("utf-8"))


def _release_from(payload) -> dict | None:
    """``{"latest": "1.2.0", "url": ...}`` from GitHub's answer, or None when
    it is not a usable stable release. The link is kept only if it points into
    this project's releases; otherwise it is rebuilt from the tag."""
    if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
        return None
    tag = payload.get("tag_name")
    parsed = parse_version(tag)
    if parsed is None:
        return None
    url = payload.get("html_url")
    if not (isinstance(url, str) and url.startswith(RELEASES_URL_PREFIX) and len(url) < 300):
        url = f"{RELEASES_URL_PREFIX}releases/tag/{tag.strip()}"
    return {"latest": ".".join(str(n) for n in parsed), "url": url}


def _read_file(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("checked_at"), (int, float)) else None


def _write_file(path: Path | None, record: dict) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        pass


def reset_memory() -> None:
    """Forget the in-memory answer. Tests use this; the app never needs to."""
    global _memory
    with _lock:
        _memory = None


def check(cache_path: Path | None, now: Callable[[], float] = time.time,
          fetch: Callable[[], dict] | None = None) -> dict | None:
    """The latest stable release, ``{"latest": ..., "url": ...}``, or None when
    none is known. Asks GitHub only when the cached answer is older than a day
    (older than six hours after a failed attempt). Never raises."""
    global _memory
    fetch = fetch or _fetch_latest
    with _lock:
        record = _memory or _read_file(cache_path)
        current = now()
        if record is not None:
            age = current - record["checked_at"]
            wait = FRESH_SECONDS if record.get("ok") else RETRY_SECONDS
            if 0 <= age < wait:
                _memory = record
                return {"latest": record["latest"], "url": record["url"]} if record.get("latest") else None
        try:
            release = _release_from(fetch())
        except Exception:
            release = None
        record = {"checked_at": current, "ok": release is not None, "latest": None, "url": None}
        if release:
            record.update(release)
        elif _memory and _memory.get("latest"):
            # A failed refresh keeps the release already known.
            record.update(latest=_memory["latest"], url=_memory["url"])
        _memory = record
        _write_file(cache_path, record)
        return {"latest": record["latest"], "url": record["url"]} if record["latest"] else None


def status(running: str, is_enabled: bool, in_container: bool, cache_path: Path | None,
           now: Callable[[], float] = time.time, fetch: Callable[[], dict] | None = None) -> dict:
    """The body of GET /api/update. ``available`` is true only when the check is
    on, ``running`` parses, and a newer stable release is known."""
    out = {"available": False, "current": running, "latest": None, "url": None,
           "jump": None, "docker": bool(in_container)}
    if not is_enabled or parse_version(running) is None:
        return out
    release = check(cache_path, now=now, fetch=fetch)
    jump = jump_size(running, release["latest"]) if release else None
    if jump:
        out.update(available=True, latest=release["latest"], url=release["url"], jump=jump)
    return out
