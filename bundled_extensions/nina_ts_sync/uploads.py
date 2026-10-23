"""Uploaded Target Scheduler databases: storage, checks and the worker.

The NINA plugin sends a consistent copy of TS's database. The route checks it
and stores it; one background thread reads it and saves a merge preview; the
review page shows the preview; apply merges it and deletes it.

Each upload is two files in the upload directory, named by a random uuid4 hex
that ACP chose. Nothing the client sends is ever used as a file name or path.

    <id>.sqlite  the database copy
    <id>.json    state, times, size, SHA-256, meta, profiles, the preview

State is one of checking, ready, failed, superseded, applied. An upload lives
ACP_TS_UPLOAD_TTL_HOURS (24 by default). Expired uploads are swept when the
extension registers and on every new upload.

The file is only ever read by SQLite. It is never executed, imported,
unpacked, or passed to a shell.

TODO: this first version takes the whole database file.
The cleaner version has the plugin send the four tables' rows as JSON, under
ACP's normal 1 MB limit, so the temp files and the raised body limit go away.
See "Later: send rows as JSON instead of the file" in ACP's
docs/specs/ts-upload-import.md. The merge does not change.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SQLITE_MAGIC = b"SQLite format 3\x00"
ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_TABLES = ("project", "target", "exposureplan", "exposuretemplate")
PENDING = ("checking", "ready")

API_PREFIX = "/api/ext/nina-ts-sync"
PAGE_PREFIX = "/ext/nina-ts-sync"

_state_lock = threading.RLock()
_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


class UploadFailed(Exception):
    """The worker could not read the upload. The message is for the user."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def upload_dir() -> Path:
    raw = (os.environ.get("ACP_TS_UPLOAD_DIR") or "").strip()
    return Path(raw).expanduser() if raw else Path(tempfile.gettempdir()) / "acp-ts-uploads"


def max_bytes() -> int:
    try:
        return int(os.environ.get("ACP_TS_UPLOAD_MAX_BYTES") or 64 * 1024 * 1024)
    except ValueError:
        return 64 * 1024 * 1024


def ttl_seconds() -> float:
    try:
        return float(os.environ.get("ACP_TS_UPLOAD_TTL_HOURS") or 24) * 3600.0
    except ValueError:
        return 24 * 3600.0


def ensure_dir() -> Path:
    d = upload_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def status_url(upload_id: str) -> str:
    return f"{API_PREFIX}/import/uploads/{upload_id}"


def review_url(upload_id: str) -> str:
    return f"{PAGE_PREFIX}/import/uploads/{upload_id}"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# State files
# ---------------------------------------------------------------------------


def db_path(upload_id: str) -> Path:
    return upload_dir() / f"{upload_id}.sqlite"


def state_path(upload_id: str) -> Path:
    return upload_dir() / f"{upload_id}.json"


def part_path(upload_id: str) -> Path:
    return upload_dir() / f"{upload_id}.part"


def load_state(upload_id: str) -> dict | None:
    if not ID_RE.match(upload_id or ""):
        return None
    p = state_path(upload_id)
    with _state_lock:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if not isinstance(data, dict):
        return None
    if time.time() - float(data.get("created") or 0) > ttl_seconds():
        return None
    return data


def save_state(state: dict) -> None:
    p = state_path(state["upload_id"])
    tmp = p.with_suffix(".json.tmp")
    with _state_lock:
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(p)


def update_state(upload_id: str, **changes) -> dict | None:
    with _state_lock:
        state = load_state(upload_id)
        if state is None:
            return None
        state.update(changes)
        state["updated"] = time.time()
        save_state(state)
        return state


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logging.warning("[nina-ts-sync] could not delete %s: %s", p, e)


def delete_upload(upload_id: str) -> None:
    with _state_lock:
        for p in (db_path(upload_id), state_path(upload_id), part_path(upload_id)):
            _unlink(p)


def sweep() -> int:
    """Delete every upload older than the lifetime. Returns how many uploads
    went. An upload's age is the creation time in its state file; files with
    no state file (a part file from an interrupted upload) go by their own
    modification time."""
    d = upload_dir()
    if not d.is_dir():
        return 0
    cutoff = time.time() - ttl_seconds()
    removed = 0
    with _state_lock:
        by_id: dict[str, list[Path]] = {}
        for p in d.iterdir():
            stem = p.name.split(".", 1)[0]
            if ID_RE.match(stem):
                by_id.setdefault(stem, []).append(p)
        for uid, files in by_id.items():
            created = None
            sp = state_path(uid)
            if sp.exists():
                try:
                    created = float(json.loads(sp.read_text(encoding="utf-8")).get("created") or 0)
                except (OSError, ValueError, AttributeError):
                    created = None
            if created is None:
                try:
                    created = max(f.stat().st_mtime for f in files)
                except OSError:
                    continue
            if created < cutoff:
                for f in files:
                    _unlink(f)
                removed += 1
    return removed


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


def receive(stream, upload_id: str, expected_sha: str) -> tuple[int, str] | tuple[None, str]:
    """Stream the file part to ``<id>.part``, checking as it goes.

    Returns ``(size, "")`` once the bytes start with the SQLite header and
    hash to *expected_sha*, with the part renamed to ``<id>.sqlite``. Returns
    ``(None, reason)`` otherwise, with the part deleted; reason is
    "not_sqlite" or "sha_mismatch".
    """
    d = ensure_dir()
    part = d / f"{upload_id}.part"
    head = b""
    while len(head) < len(SQLITE_MAGIC):
        chunk = stream.read(len(SQLITE_MAGIC) - len(head))
        if not chunk:
            break
        head += chunk
    if head != SQLITE_MAGIC:
        return None, "not_sqlite"
    h = hashlib.sha256(head)
    size = len(head)
    try:
        with open(part, "wb") as out:
            os.chmod(part, 0o600)
            out.write(head)
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                out.write(chunk)
                size += len(chunk)
    except BaseException:
        _unlink(part)
        raise
    if h.hexdigest() != expected_sha:
        _unlink(part)
        return None, "sha_mismatch"
    part.replace(d / f"{upload_id}.sqlite")
    return size, ""


def new_state(upload_id: str, *, size: int, meta: dict) -> dict:
    now = time.time()
    return {
        "upload_id": upload_id,
        "state": "checking",
        "created": now,
        "updated": now,
        "created_iso": _iso(now),
        "expires_iso": _iso(now + ttl_seconds()),
        "size": size,
        "sha256": meta["sha256"],
        "meta": meta,
        "profile_id": meta["profile_id"],
        "profiles": [],
        "preview": None,
        "error": None,
        "superseded_by": None,
    }


def supersede_older(new_id: str, profile_id: str) -> list[str]:
    """Mark every pending upload for *profile_id* but *new_id* superseded and
    delete its database file. Its state file stays until the sweep, so its
    review page can point at the newer upload."""
    d = upload_dir()
    out = []
    if not d.is_dir():
        return out
    with _state_lock:
        for p in d.glob("*.json"):
            uid = p.stem
            if uid == new_id or not ID_RE.match(uid):
                continue
            state = load_state(uid)
            if not state or state.get("state") not in PENDING:
                continue
            if state.get("meta", {}).get("profile_id") != profile_id:
                continue
            _unlink(db_path(uid))
            update_state(uid, state="superseded", superseded_by=new_id, preview=None,
                         superseded_review_url=review_url(new_id))
            out.append(uid)
    return out


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


def _profile_names(meta: dict) -> dict[str, str]:
    out = {}
    for p in meta.get("profiles") or []:
        if isinstance(p, dict) and p.get("id"):
            out[str(p["id"]).lower()] = str(p.get("name") or "")
    return out


def check_file(path: Path) -> tuple[int, list[dict]]:
    """Open the upload the safe way and check it is a TS database we read.

    Returns the schema version and the profiles in the file with a project
    count each. Raises UploadFailed with a message for the user.
    """
    from .db import SchemaVersionError, assert_supported_version
    from .import_merge import open_readonly

    try:
        conn = open_readonly(path, immutable=True)
    except sqlite3.Error as e:
        raise UploadFailed(f"ACP could not open the upload as SQLite: {e}") from e
    try:
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as e:
            raise UploadFailed(f"The upload is damaged: {e}") from e
        if not row or row[0] != "ok":
            raise UploadFailed(f"The upload is damaged: {row[0] if row else 'no result'}")
        # sqlite_master is sqlite_schema's older name; it works on every
        # SQLite a Python install might carry.
        found: dict[str, str] = {}
        for r in conn.execute("SELECT name, type FROM sqlite_master"):
            name = (r[0] or "").lower()
            if name in REQUIRED_TABLES:
                found[name] = r[1]
        if any(found.get(t) != "table" for t in REQUIRED_TABLES):
            raise UploadFailed("a SQLite file, but not a Target Scheduler database")
        try:
            version = assert_supported_version(conn)
        except SchemaVersionError as e:
            raise UploadFailed(str(e)) from e

        counts: dict[str, int] = {}
        for r in conn.execute("SELECT profileId, COUNT(*) FROM project GROUP BY profileId"):
            if r[0]:
                counts[r[0]] = int(r[1])
        try:
            for r in conn.execute("SELECT DISTINCT profileId FROM profilepreference"):
                if r[0]:
                    counts.setdefault(r[0], 0)
        except sqlite3.Error:
            pass
    except sqlite3.DatabaseError as e:
        raise UploadFailed(f"ACP could not read the upload: {e}") from e
    finally:
        conn.close()
    return version, [{"profile_id": pid, "project_count": n} for pid, n in counts.items()]


def _fail(upload_id: str, profile_id: str, message: str) -> None:
    with _state_lock:
        current = load_state(upload_id)
        if (current is None or current.get("state") != "checking"
                or current.get("profile_id") != profile_id):
            return
        update_state(upload_id, state="failed", error=message, preview=None)


def run_preview(upload_id: str, profile_id: str) -> None:
    """The worker's job for one upload and profile. Never raises."""
    from .import_merge import diff_from, preview_dict

    state = load_state(upload_id)
    if state is None or state.get("state") not in PENDING:
        return
    path = db_path(upload_id)
    try:
        if not path.exists():
            raise UploadFailed("The upload's file is gone. Send it again from NINA.")
        version, profiles = check_file(path)
        names = _profile_names(state.get("meta") or {})
        for p in profiles:
            p["name"] = names.get(p["profile_id"].lower(), "")
        label = names.get(profile_id.lower()) or None
        view, gear = diff_from(path, profile_id, (state.get("meta") or {}).get("machine"),
                               strict=True, immutable=True, profile_label=label)
        preview = preview_dict(view, gear, profile_name=label)
        preview["loss_count"] = sum(g["losses"] for g in preview["notes"])
    except UploadFailed as e:
        _fail(upload_id, profile_id, str(e))
        return
    except Exception as e:  # noqa: BLE001  a bug must not kill the worker
        logging.exception("[nina-ts-sync] preview of upload %s failed", upload_id)
        _fail(upload_id, profile_id, f"ACP could not read the upload: {e}")
        return
    # Check and write under one lock: a supersede, a discard or a re-preview
    # for another profile may have landed while this ran, and then this
    # result is stale and must not be published.
    with _state_lock:
        current = load_state(upload_id)
        if (current is None or current.get("state") != "checking"
                or current.get("profile_id") != profile_id):
            return
        update_state(upload_id, state="ready", error=None, user_version=version,
                     profiles=profiles, profile_id=profile_id, preview=preview)


def _worker_loop() -> None:
    while True:
        upload_id, profile_id = _queue.get()
        try:
            run_preview(upload_id, profile_id)
        finally:
            _queue.task_done()


def start_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, name="nina-ts-sync-uploads", daemon=True)
        _worker.start()


def enqueue(upload_id: str, profile_id: str) -> None:
    start_worker()
    _queue.put((upload_id, profile_id))


def wait_idle(timeout: float = 10.0) -> bool:
    """Block until the queue is empty. For tests."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _queue.unfinished_tasks == 0:
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Listing pending uploads, for ACP core's waiting-upload banner
# ---------------------------------------------------------------------------


def list_pending() -> list[dict]:
    """Every upload in state checking or ready, newest first.

    Superseded, applied and failed uploads are excluded, and so are expired
    ones: ``load_state`` already returns ``None`` past the upload's TTL.
    """
    d = upload_dir()
    if not d.is_dir():
        return []
    out = []
    with _state_lock:
        for p in d.glob("*.json"):
            uid = p.stem
            if not ID_RE.match(uid):
                continue
            state = load_state(uid)
            if state is None or state.get("state") not in PENDING:
                continue
            out.append(state)
    out.sort(key=lambda s: s.get("created") or 0, reverse=True)
    return out


def profile_name_for(state: dict) -> str:
    """The human name of *state*'s current profile_id, if the plugin sent
    one. Checks the upload's own meta.profiles (present from the first
    request) before the worker's profiles list (only set once ready)."""
    profile_id = (state.get("profile_id") or "").strip().lower()
    if not profile_id:
        return ""
    meta = state.get("meta") or {}
    for p in meta.get("profiles") or []:
        if isinstance(p, dict) and str(p.get("id") or "").strip().lower() == profile_id:
            return str(p.get("name") or "")
    for p in state.get("profiles") or []:
        if isinstance(p, dict) and str(p.get("profile_id") or "").strip().lower() == profile_id:
            return str(p.get("name") or "")
    return ""


# ---------------------------------------------------------------------------
# What the status endpoint returns
# ---------------------------------------------------------------------------


def public_status(state: dict) -> dict:
    """The status reply. The plugin's dock reads `state`, `error`,
    `review_url` and `counts` {new, updated, conflicts}; keep those names."""
    preview = state.get("preview")
    counts = None
    if state.get("state") == "ready" and isinstance(preview, dict):
        c = preview.get("counts") or {}
        counts = {
            "new": int(c.get("new") or 0),
            "updated": int(c.get("updated") or 0),
            "conflicts": int(c.get("conflicts") or 0),
        }
    uid = state["upload_id"]
    return {
        "upload_id": uid,
        "state": state.get("state"),
        "error": state.get("error") if state.get("state") == "failed" else None,
        "status_url": status_url(uid),
        "review_url": review_url(uid),
        "counts": counts,
        "summary": (preview or {}).get("summary") if isinstance(preview, dict) else None,
        "created_iso": state.get("created_iso"),
        "expires_iso": state.get("expires_iso"),
        "size": state.get("size"),
        "sha256": state.get("sha256"),
        "machine": (state.get("meta") or {}).get("machine"),
        "plugin_version": (state.get("meta") or {}).get("plugin_version"),
        "profile_id": state.get("profile_id"),
        "profiles": state.get("profiles") or [],
        "superseded_by": state.get("superseded_by"),
        "superseded_review_url": state.get("superseded_review_url"),
        "preview": preview,
    }
