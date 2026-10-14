"""The Activity tab's storage and rules (docs/specs/activity-tab.md).

Events live in SQLite beside plans.json. ACP records its own events through
``record()``, the NINA plugin and AAC post theirs through ``ingest()``, and
the tab reads them through the query functions at the bottom. This module
knows nothing about Flask; the routes in app.py are thin wrappers.

Two promises matter more than the rest. ``record()`` can never fail the save
that called it, and a posted event is stored once however often it is sent.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("acp.activity")

SCHEMA_VERSION = 1

POSTABLE_SOURCES = ("plugin", "aac", "external")
SEVERITIES = ("info", "warn", "error")
MAX_KEY = 200
MAX_TITLE = 200
MAX_DETAIL = 2000
MAX_DATA_BYTES = 8 * 1024
MAX_BATCH = 100
MAX_BODY_BYTES = 2 * 1024 * 1024
NIGHT_PAGE = 200
OPEN_CAP = 50
DEFAULT_NIGHTS = 5
MAX_NIGHTS = 30
EXPIRY_DAYS = 7
MOVED_H = 0.01

REF_KEYS = ("plan_id", "project", "target", "bucket", "filter", "ra_deg", "dec_deg",
            "aac_job", "upload_id", "profile", "master")

# Chip id, label, and the event types it shows. Mirrored in static/activity.mjs.
CHIPS = (
    ("plans", "Plans", ("plan_created", "plan_edited", "plan_deleted", "project_settings",
                        "gear_changed", "counts_corrected", "other")),
    ("syncs", "Syncs", ("sync_for_tonight", "rig_synced", "ts_upload_received",
                        "ts_upload_applied", "ts_upload_discarded", "ts_push")),
    ("imaging", "Imaging", ("imaging_target", "goal_reached", "progress_report",
                            "progress_unknown", "session_start", "session_end")),
    ("site", "Site and focus", ("safety", "meridian_flip", "autofocus_summary", "park")),
    ("data", "Data landed", ("transfer_landed", "ingest")),
    ("processing", "Processing", ("calibration", "threshold_decision", "master_queued",
                                  "accepted_counts", "darks_built", "aac_alert",
                                  "aac_recovered")),
    ("masters", "Masters", ("master_built", "master_ready")),
    ("coverage", "Coverage", ("scan_finished", "scan_error", "coverage_changed")),
)
KNOWN_TYPES = frozenset(t for _, _, types in CHIPS for t in types)
DEFAULT_CHIPS = {cid: cid != "site" for cid, _, _ in CHIPS}

# Needs-you types that stop counting a week after they arrive.
EXPIRING_TYPES = frozenset({"ts_upload_received", "master_ready"})


# ---------------------------------------------------------------------------
# Settings, read fresh so a test (or an operator) can change them live
# ---------------------------------------------------------------------------

_default_db_path = lambda: Path("activity.sqlite")  # noqa: E731  (app.py replaces it)


def set_default_db_path(fn) -> None:
    """app.py points the default at ``activity.sqlite`` beside plans.json."""
    global _default_db_path
    _default_db_path = fn


def enabled() -> bool:
    return (os.environ.get("ACP_ACTIVITY") or "").strip().lower() != "off"


def db_path() -> Path:
    raw = (os.environ.get("ACP_ACTIVITY_DB") or "").strip()
    return Path(raw) if raw else Path(_default_db_path())


DEFAULT_TZ = "Australia/Sydney"


def tz() -> ZoneInfo:
    name = (os.environ.get("ACP_ACTIVITY_TZ") or "").strip() or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("ACP_ACTIVITY_TZ=%r is not a time zone; using %s", name, DEFAULT_TZ)
        return ZoneInfo(DEFAULT_TZ)


def keep_nights() -> int | None:
    raw = (os.environ.get("ACP_ACTIVITY_KEEP_NIGHTS") or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

_clock_lock = threading.Lock()
_last_now: datetime | None = None


def _now() -> datetime:
    """UTC now, strictly increasing within the process, so a change made
    straight after another always compares later."""
    global _last_now
    n = datetime.now(timezone.utc)
    with _clock_lock:
        if _last_now is not None and n <= _last_now:
            n = _last_now + timedelta(microseconds=1)
        _last_now = n
    return n


def _stamp(dt: datetime) -> str:
    """Fixed-width UTC text, so string order is time order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def parse_at(value) -> datetime:
    """An ISO 8601 time as an aware datetime. A time with no offset is read
    as local time in ACP_ACTIVITY_TZ."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("at must be an ISO 8601 time")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("at must be an ISO 8601 time") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return dt


def night_of(dt: datetime) -> str:
    """The date the evening started. The cut-over is local noon in
    ACP_ACTIVITY_TZ, so daylight saving never moves it."""
    local = dt.astimezone(tz())
    day = local.date() if local.hour >= 12 else local.date() - timedelta(days=1)
    return day.isoformat()


def current_night() -> str:
    return night_of(datetime.now(timezone.utc))


_NIGHT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_night(text) -> bool:
    if not isinstance(text, str) or not _NIGHT_RE.match(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Redaction and master paths
# ---------------------------------------------------------------------------

_REDACT = "[redacted]"
_SCRUBS = (
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]+"), r"\1" + _REDACT),
    (re.compile(r"(?i)\b(token\s*=\s*)[^\s&;,\"']+"), r"\1" + _REDACT),
    (re.compile(r"(?i)\b(password\s*=\s*)[^\s&;,\"']+"), r"\1" + _REDACT),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@"), r"\1" + _REDACT + "@"),
    (re.compile(r"(?i)\b[A-Za-z]:[\\/]Users[\\/][^\\/\s]+[\\/]"), "~\\\\"),
    (re.compile(r"/Users/[^/\s]+/"), "~/"),
)


def scrub(text: str) -> str:
    for pattern, repl in _SCRUBS:
        text = pattern.sub(repl, text)
    return text


def scrub_value(value):
    """Scrub every string inside a JSON value."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    return value


def _path_map() -> list[dict]:
    raw = (os.environ.get("ACP_ACTIVITY_PATH_MAP") or "").strip()
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except ValueError:
        logger.warning("ACP_ACTIVITY_PATH_MAP is not valid JSON; ignoring it")
        return []
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("from"), str)
            and e["from"] and isinstance(e.get("mac"), str) and isinstance(e.get("windows"), str)]


def map_master_path(path: str) -> dict:
    """The Mac folder, Windows folder and file name for a path as AAC sees it.

    The first map entry that matches on a whole path segment wins. A path no
    entry matches comes back as ``(unmapped) <file>`` in both folders.
    """
    name = re.split(r"[\\/]", path.rstrip("\\/"))[-1] if path else ""
    for entry in _path_map():
        prefix = entry["from"].rstrip("\\/")
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        if rest and rest[0] not in "/\\":
            continue
        segments = [s for s in re.split(r"[\\/]", rest) if s]
        if not segments:
            continue
        folders = segments[:-1]
        mac = entry["mac"].rstrip("/") + "/" + "".join(s + "/" for s in folders)
        win = entry["windows"].rstrip("\\") + "\\" + "".join(s + "\\" for s in folders)
        return {"mac_folder": mac, "windows_folder": win, "file_name": segments[-1]}
    unmapped = f"(unmapped) {name}"
    return {"mac_folder": unmapped, "windows_folder": unmapped, "file_name": name}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None

_SCHEMA_V1 = """
CREATE TABLE events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,
    key          TEXT NOT NULL,
    seq          INTEGER,
    type         TEXT NOT NULL,
    at           TEXT NOT NULL,
    at_ts        REAL NOT NULL,
    night        TEXT NOT NULL,
    title        TEXT NOT NULL,
    detail       TEXT,
    severity     TEXT NOT NULL DEFAULT 'info',
    refs         TEXT NOT NULL DEFAULT '{}',
    data         TEXT NOT NULL DEFAULT '{}',
    needs_you    INTEGER NOT NULL DEFAULT 0,
    resolve_key  TEXT,
    resolves     TEXT NOT NULL DEFAULT '[]',
    expires_at   TEXT,
    received_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  INTEGER,
    dismissed_at TEXT,
    content      TEXT NOT NULL,
    change_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX events_source_key ON events (source, key);
CREATE INDEX events_night_at ON events (night, at_ts, id);
CREATE INDEX events_resolve_key ON events (resolve_key);
CREATE TABLE resolutions (
    resolve_key TEXT NOT NULL,
    resolved_by INTEGER NOT NULL,
    at          TEXT NOT NULL,
    PRIMARY KEY (resolve_key, resolved_by)
);
CREATE TABLE tombstones (
    source TEXT NOT NULL,
    key    TEXT NOT NULL,
    PRIMARY KEY (source, key)
);
CREATE TABLE state (
    name  TEXT PRIMARY KEY,
    value TEXT
);
"""

# Forward migrations, applied in order from PRAGMA user_version.
_MIGRATIONS = [_SCHEMA_V1]


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # journal_mode=DELETE because the data directory may sit on a network
    # share, where WAL is unsafe.
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=0.25,
                           isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 250")
        conn.execute("PRAGMA journal_mode = DELETE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > len(_MIGRATIONS):
            raise sqlite3.DatabaseError(
                f"{path} was written by a newer ACP (schema {version})")
        for i in range(version, len(_MIGRATIONS)):
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _MIGRATIONS[i].split(";\n"):
                    if stmt.strip():
                        conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {i + 1}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    except BaseException:
        conn.close()
        raise
    return conn


def _connect() -> sqlite3.Connection:
    """The open connection for the configured path. Caller holds ``_lock``."""
    global _conn, _conn_path
    path = db_path()
    if _conn is not None and _conn_path == str(path):
        return _conn
    close()
    _conn = _open(path)
    _conn_path = str(path)
    return _conn


def close() -> None:
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        _conn = None
        _conn_path = None


@contextmanager
def _tx():
    """One write transaction on the shared connection, under the module lock."""
    with _lock:
        conn = _connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _clean_master(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("refs.master must be an object")
    out = {}
    for k in ("path", "frames", "hours", "filter"):
        if k in raw:
            out[k] = raw[k]
    path = out.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("refs.master.path is required")
    out["path"] = scrub(path)
    out.update(map_master_path(out["path"]))
    return out


def validate(raw, *, allow_acp: bool, now: datetime) -> dict:
    """A clean event from what a sender gave, or ValueError saying why not."""
    if not isinstance(raw, dict):
        raise ValueError("an event must be an object")

    source = raw.get("source")
    if allow_acp:
        source = "acp"
    elif source not in POSTABLE_SOURCES:
        raise ValueError("source must be one of " + ", ".join(POSTABLE_SOURCES))

    key = raw.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("key is required")
    if len(key) > MAX_KEY:
        raise ValueError(f"key is longer than {MAX_KEY} characters")

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required")
    if len(title) > MAX_TITLE:
        raise ValueError(f"title is longer than {MAX_TITLE} characters")
    title = scrub(" ".join(title.split()))

    detail = raw.get("detail")
    if detail is not None:
        if not isinstance(detail, str):
            raise ValueError("detail must be text")
        detail = scrub(detail[:MAX_DETAIL])

    severity = raw.get("severity", "info")
    if severity is None:
        severity = "info"
    if severity not in SEVERITIES:
        raise ValueError("severity must be info, warn or error")

    etype = raw.get("type")
    if not isinstance(etype, str) or etype not in KNOWN_TYPES:
        etype = "other"

    seq = raw.get("seq")
    if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int)):
        raise ValueError("seq must be an integer")

    at = parse_at(raw["at"]) if raw.get("at") not in (None, "") else now
    night = raw.get("night")
    if night in (None, ""):
        night = night_of(at)
    elif not valid_night(night):
        raise ValueError("night must be YYYY-MM-DD")

    refs_in = raw.get("refs")
    if refs_in is None:
        refs_in = {}
    if not isinstance(refs_in, dict):
        raise ValueError("refs must be an object")
    refs = {}
    for k in REF_KEYS:
        if k not in refs_in:
            continue
        refs[k] = _clean_master(refs_in[k]) if k == "master" else scrub_value(refs_in[k])

    data = raw.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("data must be an object")
    try:
        size = len(json.dumps(data, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ValueError("data must be plain JSON") from None
    if size > MAX_DATA_BYTES:
        raise ValueError("data is larger than 8 KB")
    data = scrub_value(data)

    needs_you = raw.get("needs_you", False)
    if needs_you is None:
        needs_you = False
    if not isinstance(needs_you, bool):
        raise ValueError("needs_you must be true or false")
    resolve_key = raw.get("resolve_key")
    if resolve_key is not None and (not isinstance(resolve_key, str) or len(resolve_key) > MAX_KEY):
        raise ValueError("resolve_key must be text of 200 characters or fewer")
    if needs_you and not resolve_key:
        raise ValueError("resolve_key is required when needs_you is true")

    resolves = raw.get("resolves")
    if resolves is None:
        resolves = []
    if (not isinstance(resolves, list) or len(resolves) > 50
            or any(not isinstance(r, str) or not r or len(r) > MAX_KEY for r in resolves)):
        raise ValueError("resolves must be a list of up to 50 keys")

    return {
        "source": source, "key": key, "seq": seq, "type": etype, "at": at,
        "night": night, "title": title, "detail": detail, "severity": severity,
        "refs": refs, "data": data, "needs_you": needs_you,
        "resolve_key": resolve_key or None, "resolves": list(resolves),
    }


def _content(ev: dict) -> str:
    return _canonical({k: ev[k] for k in (
        "type", "title", "detail", "severity", "data", "refs",
        "needs_you", "resolve_key", "resolves")})


# ---------------------------------------------------------------------------
# Writing events
# ---------------------------------------------------------------------------

def _row_event(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "source": row["source"], "key": row["key"], "seq": row["seq"],
        "type": row["type"], "at": row["at"], "night": row["night"], "title": row["title"],
        "detail": row["detail"], "severity": row["severity"],
        "refs": json.loads(row["refs"]), "data": json.loads(row["data"]),
        "needs_you": bool(row["needs_you"]), "resolve_key": row["resolve_key"],
        "resolves": json.loads(row["resolves"]), "expires_at": row["expires_at"],
        "received_at": row["received_at"], "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"], "resolved_by": row["resolved_by"],
        "dismissed_at": row["dismissed_at"],
    }


def _expiry(etype: str, change_at: datetime) -> str | None:
    if etype in EXPIRING_TYPES:
        return _stamp(change_at + timedelta(days=EXPIRY_DAYS))
    return None


def _apply_resolutions(conn, event_id: int, ev: dict, change_at: datetime) -> None:
    """Write the keys this event resolves, and close the items they name."""
    for rkey in ev["resolves"]:
        conn.execute(
            "INSERT OR REPLACE INTO resolutions (resolve_key, resolved_by, at) VALUES (?,?,?)",
            (rkey, event_id, _stamp(change_at)))
        conn.execute(
            "UPDATE events SET resolved_at = ?, resolved_by = ? "
            "WHERE needs_you = 1 AND resolve_key = ? AND resolved_at IS NULL "
            "AND id != ? AND change_at <= ?",
            (_stamp(change_at), event_id, rkey, event_id, _stamp(change_at)))


def _close_if_already_resolved(conn, event_id: int, ev: dict, change_at: datetime) -> None:
    """A resolve that arrived before its item still closes it."""
    if not ev["needs_you"] or not ev["resolve_key"]:
        return
    hit = conn.execute(
        "SELECT resolved_by, at FROM resolutions WHERE resolve_key = ? AND at >= ? "
        "ORDER BY at DESC LIMIT 1", (ev["resolve_key"], _stamp(change_at))).fetchone()
    if hit is not None:
        conn.execute("UPDATE events SET resolved_at = ?, resolved_by = ? WHERE id = ?",
                     (hit["at"], hit["resolved_by"], event_id))


def _store(conn, ev: dict, now: datetime) -> tuple[str, int]:
    """Insert or update one clean event. Returns (outcome, id)."""
    if conn.execute("SELECT 1 FROM tombstones WHERE source = ? AND key = ?",
                    (ev["source"], ev["key"])).fetchone():
        return "stale", 0
    row = conn.execute("SELECT * FROM events WHERE source = ? AND key = ?",
                       (ev["source"], ev["key"])).fetchone()
    content = _content(ev)
    stamp_now = _stamp(now)

    if row is None:
        # The first posting counts from the event's own time, so a resolve
        # that was stamped earlier than its item arrived still closes it.
        change_at = ev["at"]
        cur = conn.execute(
            "INSERT INTO events (source,key,seq,type,at,at_ts,night,title,detail,severity,"
            "refs,data,needs_you,resolve_key,resolves,expires_at,received_at,updated_at,"
            "content,change_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev["source"], ev["key"], ev["seq"], ev["type"],
             ev["at"].astimezone(tz()).isoformat(), ev["at"].timestamp(), ev["night"],
             ev["title"], ev["detail"], ev["severity"], _canonical(ev["refs"]),
             _canonical(ev["data"]), int(ev["needs_you"]), ev["resolve_key"],
             _canonical(ev["resolves"]), _expiry(ev["type"], change_at),
             stamp_now, stamp_now, content, _stamp(change_at)))
        event_id = cur.lastrowid
        _apply_resolutions(conn, event_id, ev, change_at)
        _close_if_already_resolved(conn, event_id, ev, change_at)
        return "stored", event_id

    if ev["seq"] is not None and row["seq"] is not None and ev["seq"] < row["seq"]:
        return "stale", row["id"]
    seq = max(ev["seq"], row["seq"]) if ev["seq"] is not None and row["seq"] is not None \
        else (ev["seq"] if ev["seq"] is not None else row["seq"])
    if content == row["content"]:
        if seq != row["seq"]:
            conn.execute("UPDATE events SET seq = ? WHERE id = ?", (seq, row["id"]))
        return "unchanged", row["id"]

    change_at = max(ev["at"], now)
    conn.execute(
        "UPDATE events SET seq=?, type=?, title=?, detail=?, severity=?, refs=?, data=?, "
        "needs_you=?, resolve_key=?, resolves=?, expires_at=?, updated_at=?, content=?, "
        "change_at=?, resolved_at=NULL, resolved_by=NULL, dismissed_at=NULL WHERE id=?",
        (seq, ev["type"], ev["title"], ev["detail"], ev["severity"], _canonical(ev["refs"]),
         _canonical(ev["data"]), int(ev["needs_you"]), ev["resolve_key"],
         _canonical(ev["resolves"]), _expiry(ev["type"], change_at), stamp_now, content,
         _stamp(change_at), row["id"]))
    _apply_resolutions(conn, row["id"], ev, change_at)
    _close_if_already_resolved(conn, row["id"], ev, change_at)
    return "updated", row["id"]


def _prune(conn) -> None:
    keep = keep_nights()
    if keep is None:
        return
    old = [r[0] for r in conn.execute(
        "SELECT DISTINCT night FROM events ORDER BY night DESC LIMIT -1 OFFSET ?", (keep,))]
    for night in old:
        conn.execute("INSERT OR IGNORE INTO tombstones (source, key) "
                     "SELECT source, key FROM events WHERE night = ?", (night,))
        conn.execute("DELETE FROM events WHERE night = ?", (night,))


def ingest(events: list, *, allow_acp: bool = False) -> dict:
    """Store a batch. A bad event is rejected alone and the rest are stored."""
    result = {"stored": 0, "updated": 0, "unchanged": 0, "stale": 0, "rejected": []}
    if not enabled():
        return result
    now = _now()
    with _tx() as conn:
        for raw in events:
            try:
                ev = validate(raw, allow_acp=allow_acp, now=now)
            except ValueError as exc:
                key = raw.get("key") if isinstance(raw, dict) else None
                result["rejected"].append(
                    {"key": key if isinstance(key, str) else None, "error": str(exc)})
                continue
            outcome, _ = _store(conn, ev, now)
            result[outcome] += 1
        _prune(conn)
    return result


def record(**fields) -> dict | None:
    """ACP's own events. Never raises: a failure is logged and dropped, so
    recording can not fail the save that called it."""
    try:
        if not enabled():
            return None
        out = ingest([fields], allow_acp=True)
        for bad in out["rejected"]:
            logger.warning("activity event %r rejected: %s", bad["key"], bad["error"])
        return out
    except Exception:
        logger.exception("could not record an activity event")
        return None


def get_event(source: str, key: str) -> dict | None:
    with _lock:
        row = _connect().execute("SELECT * FROM events WHERE source = ? AND key = ?",
                                 (source, key)).fetchone()
    return _row_event(row) if row else None


def discard(source: str, key: str) -> None:
    """Remove an event outright (an edit that was reverted within its night)."""
    try:
        with _tx() as conn:
            conn.execute("DELETE FROM events WHERE source = ? AND key = ?", (source, key))
    except Exception:
        logger.exception("could not discard an activity event")


def record_merged(merge, **fields) -> dict | None:
    """``record()`` for an event whose content accumulates through its night.

    ``merge(previous_event_or_None)`` returns the fields to store (it may
    return None to store nothing). The read and the write share the module
    lock, so two saves can not both build from the same previous state.
    """
    try:
        if not enabled():
            return None
        with _lock:
            previous = get_event("acp", fields["key"])
            merged = merge(previous)
            if merged is None:
                if previous is not None:
                    discard("acp", fields["key"])
                return None
            return record(**{**fields, **merged})
    except Exception:
        logger.exception("could not record an activity event")
        return None


# ---------------------------------------------------------------------------
# What changed on a plan, a project or the gear
# ---------------------------------------------------------------------------

_PLAN_IGNORED = frozenset({
    "updated_at", "guid", "created_at", "last_synced_at",
    # Link bookkeeping the sync writes, and the derived views.
    "ts_links", "ts_refs", "ts_base_snapshot", "aac", "filter_goals",
})
_ROW_IGNORED = frozenset({"aac"})

_LABELS = {
    "min_altitude_deg": "min altitude",
    "minimum_time_min": "minimum time",
    "meridian_window_min": "meridian window",
    "filter_switch_frequency": "filter switch frequency",
    "smart_exposure_order": "smart exposure order",
    "target_class": "target class",
    "telescope_id": "telescope",
    "camera_id": "camera",
    "destination_id": "destination",
    "project_name": "project",
    "public_blurb": "public blurb",
    "is_current": "current target",
    "target.name": "target name",
    "target.center_ra_deg": "centre RA",
    "target.center_dec_deg": "centre Dec",
    "target.rotation_deg": "rotation",
}
_ROW_LABELS = {
    "target_hours": "goal", "target_frames": "goal frames", "actual_hours": "hours shot",
    "template": "template", "template_sub_s": "sub length", "unit": "unit", "filter": "filter",
}
_CAPITALISED = frozenset({"priority", "state", "target_class"})


def _blank(v) -> bool:
    return v is None


def _flatten(prefix: str, value, out: dict) -> None:
    if isinstance(value, dict) and value:
        for k in sorted(value):
            _flatten(f"{prefix}.{k}" if prefix else str(k), value[k], out)
    else:
        out[prefix] = value


def _row_label(row: dict, seen: dict) -> str:
    name = str(row.get("filter") or row.get("id") or "row")
    n = seen.get(name, 0) + 1
    seen[name] = n
    return name if n == 1 else f"{name}#{n}"


def diff_plans(before: dict | None, after: dict | None) -> dict:
    """Field -> {from, to} for what a person changed between two stored plans.

    Bookkeeping fields and the derived ones are left out, and an absent field
    reads the same as null, so a plan that only differs in how it was loaded
    (a heal, a migration, a page echoing derived fields) has no changes.
    """
    before, after = before or {}, after or {}
    flat_b: dict = {}
    flat_a: dict = {}
    for src, dst in ((before, flat_b), (after, flat_a)):
        for k, v in src.items():
            if k in _PLAN_IGNORED or k == "filter_rows":
                continue
            if k == "target_class":
                continue
            _flatten(k, v, dst)
        tc = src.get("target_class")
        dst["target_class"] = tc if tc is not None else _default_target_class(src)
        rows = src.get("filter_rows")
        if isinstance(rows, list):
            seen: dict = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                label = _row_label(row, seen)
                dst[f"rows.{label}"] = "present"
                for fk, fv in row.items():
                    if fk in _ROW_IGNORED or fk in ("id",):
                        continue
                    dst[f"rows.{label}.{fk}"] = fv
    changes: dict = {}
    for key in sorted(set(flat_b) | set(flat_a)):
        b, a = flat_b.get(key), flat_a.get(key)
        if _same(b, a):
            continue
        changes[key] = {"from": b, "to": a}
    # A row that came or went reports itself, not each of its fields.
    for key in [k for k, v in changes.items() if k.startswith("rows.") and k.count(".") == 1]:
        prefix = key + "."
        for sub in [k for k in changes if k.startswith(prefix)]:
            del changes[sub]
    return changes


def diff_gear(before: dict | None, after: dict | None) -> dict:
    """Field -> {from, to} for telescopes and cameras added, removed or edited."""
    changes: dict = {}
    for kind, label in (("telescopes", "telescope"), ("cameras", "camera")):
        def index(doc):
            items = (doc or {}).get(kind) or []
            return {str(i.get("id")): i for i in items if isinstance(i, dict)}
        b, a = index(before), index(after)
        for gid in sorted(set(b) | set(a)):
            item = a.get(gid) or b.get(gid)
            name = str(item.get("name") or gid)
            if gid not in b:
                changes[f"{label}:{name}"] = {"from": None, "to": "present"}
                continue
            if gid not in a:
                changes[f"{label}:{name}"] = {"from": "present", "to": None}
                continue
            fb: dict = {}
            fa: dict = {}
            for src, dst in ((b[gid], fb), (a[gid], fa)):
                for k, v in src.items():
                    if k != "id":
                        _flatten(k, v, dst)
            for field in sorted(set(fb) | set(fa)):
                if not _same(fb.get(field), fa.get(field)):
                    changes[f"{label}:{name}.{field}"] = {"from": fb.get(field), "to": fa.get(field)}
    return changes


def _default_target_class(plan: dict) -> str:
    try:
        import filter_rows as fr
        return fr.target_class_of(plan)
    except Exception:
        return "survey"


def same_value(a, b) -> bool:
    return _same(a, b)


def _same(a, b) -> bool:
    if _blank(a) and _blank(b):
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b
    return _canonical(a) == _canonical(b)


def _fmt(key: str, v) -> str:
    if v is None:
        return "unset"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, float):
        if math.isfinite(v) and v == int(v):
            return str(int(v))
        return f"{v:.4g}" if math.isfinite(v) else str(v)
    if isinstance(v, (dict, list)):
        return "(changed)"
    text = str(v)
    if key in _CAPITALISED and text.islower():
        text = text.capitalize()
    return text


def _label(key: str) -> str:
    if key in _LABELS:
        return _LABELS[key]
    if key.startswith("rows."):
        parts = key.split(".")
        if len(parts) == 2:
            return f"{parts[1]} row"
        return f"{parts[1]} {_ROW_LABELS.get(parts[2], parts[2].replace('_', ' '))}"
    if ":" in key:
        kind, _, rest = key.partition(":")
        name, _, field = rest.partition(".")
        return f"{kind} {name}" + (f" {field.replace('_', ' ')}" if field else "")
    return key.replace("_", " ")


def _presence(ch: dict) -> str | None:
    if ch["from"] is None and ch["to"] == "present":
        return "added"
    if ch["from"] == "present" and ch["to"] is None:
        return "removed"
    return None


def changes_title(changes: dict, *, limit: int = 4) -> str:
    """"min altitude 20 to 30, priority Normal to High"."""
    parts = []
    for key, ch in changes.items():
        label = _label(key)
        presence = _presence(ch)
        if presence:
            parts.append(f"{presence} {label}")
            continue
        unit = " h" if key.endswith(".target_hours") or key.endswith(".actual_hours") else ""
        parts.append(f"{label} {_fmt(key, ch['from'])} to {_fmt(key, ch['to'])}{unit}")
    if len(parts) > limit:
        extra = len(parts) - limit
        parts = parts[:limit] + [f"{extra} more change{'s' if extra != 1 else ''}"]
    return ", ".join(parts)


def merge_changes(previous: dict, new: dict) -> dict:
    """Fold a save's changes into the night's set. ``from`` stays the first
    value of the night, and a field that came back to it drops out."""
    merged = {k: dict(v) for k, v in (previous or {}).items()}
    for key, ch in new.items():
        if key in merged:
            merged[key]["to"] = ch["to"]
            if _same(merged[key]["from"], merged[key]["to"]):
                del merged[key]
        else:
            merged[key] = {"from": ch["from"], "to": ch["to"]}
    return merged


def record_changes(*, etype: str, key: str, changes: dict, prefix: str = "",
                   refs: dict | None = None, extra_data: dict | None = None,
                   night: str | None = None) -> dict | None:
    """Record a plan, project or gear edit, coalesced into its night's event."""
    if not changes:
        return None

    def merge(previous):
        prior = (previous or {}).get("data", {}).get("changes") or {}
        merged = merge_changes(prior, changes)
        if not merged:
            return None
        title = changes_title(merged)
        return {"title": (prefix + title)[:MAX_TITLE],
                "data": {**(extra_data or {}), "changes": merged}}

    return record_merged(merge, type=etype, key=key, refs=refs or {},
                         **({"night": night} if night else {}), title="edit", data={})


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _is_open_sql(now: str) -> tuple[str, tuple]:
    return ("needs_you = 1 AND resolved_at IS NULL AND dismissed_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > ?)", (now,))


def get_state(name: str):
    with _lock:
        row = _connect().execute("SELECT value FROM state WHERE name = ?", (name,)).fetchone()
    return None if row is None else row[0]


def _set_state(conn, name: str, value: str) -> None:
    conn.execute("INSERT INTO state (name, value) VALUES (?, ?) "
                 "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, value))


def list_nights(*, nights: int, before: str | None) -> dict:
    with _lock:
        conn = _connect()
        if before:
            rows = conn.execute(
                "SELECT night FROM events WHERE night < ? GROUP BY night "
                "ORDER BY night DESC LIMIT ?", (before, nights + 1)).fetchall()
        else:
            rows = conn.execute(
                "SELECT night FROM events GROUP BY night ORDER BY night DESC LIMIT ?",
                (nights + 1,)).fetchall()
        names = [r[0] for r in rows]
        more_nights = len(names) > nights
        names = names[:nights]
        out = []
        for night in names:
            events, more = _night_events(conn, night, 0)
            out.append({"night": night, "events": events, "more": more})
        total = conn.execute("SELECT COUNT(DISTINCT night) FROM events").fetchone()[0]
    return {
        "nights": out,
        "next_before": names[-1] if names and more_nights else None,
        "total_nights": total,
        "last_seen": get_state("last_seen"),
    }


def _night_events(conn, night: str, offset: int) -> tuple[list, int]:
    rows = conn.execute(
        "SELECT * FROM events WHERE night = ? ORDER BY at_ts, id LIMIT ? OFFSET ?",
        (night, NIGHT_PAGE, offset)).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM events WHERE night = ?", (night,)).fetchone()[0]
    return [_row_event(r) for r in rows], max(0, total - offset - len(rows))


def night_page(night: str, offset: int) -> dict:
    with _lock:
        events, more = _night_events(_connect(), night, offset)
    return {"night": night, "events": events, "more": more}


def summary() -> dict:
    now = _stamp(_now())
    with _lock:
        conn = _connect()
        cond, args = _is_open_sql(now)
        total = conn.execute(f"SELECT COUNT(*) FROM events WHERE {cond}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM events WHERE {cond} ORDER BY at_ts, id LIMIT ?",
            args + (OPEN_CAP,)).fetchall()
        newest = conn.execute("SELECT MAX(updated_at) FROM events").fetchone()[0]
        seen = get_state("last_seen")
        last_night = _last_night(conn)
    return {
        "needs_you": total,
        "has_new": bool(newest) and (seen is None or newest > seen),
        "max_updated_at": newest,
        "last_night": last_night,
        "open": [_row_event(r) for r in rows],
        "open_total": total,
    }


def _hours(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else 0.0


def _last_night(conn) -> dict | None:
    row = conn.execute(
        "SELECT night FROM events WHERE type IN ('imaging_target','progress_report') "
        "ORDER BY night DESC LIMIT 1").fetchone()
    if row is None:
        return None
    night = row[0]
    events = [_row_event(r) for r in conn.execute(
        "SELECT * FROM events WHERE night = ? AND type IN "
        "('imaging_target','progress_report','goal_reached') ORDER BY at_ts, id", (night,))]
    finished = {e["refs"].get("plan_id") for e in events if e["type"] == "goal_reached"}
    targets: dict = {}
    for e in events:
        if e["type"] != "progress_report":
            continue
        filters = []
        for name, f in (e["data"].get("filters") or {}).items():
            if not isinstance(f, dict):
                continue
            delta = _hours(f.get("delta_h"))
            if delta <= 0:
                continue
            filters.append({"filter": name, "delta_h": round(delta, 2),
                            "hours": round(_hours(f.get("hours")), 2),
                            "goal": round(_hours(f.get("goal")), 2),
                            "done": _hours(f.get("goal")) > 0 and _hours(f.get("hours")) >= _hours(f.get("goal"))})
        if filters:
            targets[e["refs"].get("plan_id") or e["key"]] = {
                "plan_id": e["refs"].get("plan_id"),
                "target": e["refs"].get("target") or e["title"],
                "ra_deg": e["refs"].get("ra_deg"), "dec_deg": e["refs"].get("dec_deg"),
                "filters": filters, "hours": round(sum(f["delta_h"] for f in filters), 2),
                "finished": e["refs"].get("plan_id") in finished}
    # Before the plugin posts imaging_target events of its own, only the
    # progress reports carry hours. Once it does, frames fill in any target
    # a report has not covered.
    for e in events:
        if e["type"] != "imaging_target":
            continue
        pid = e["refs"].get("plan_id") or e["key"]
        if pid in targets:
            continue
        per: dict = {}
        for fr_ in e["data"].get("frames") or []:
            if isinstance(fr_, dict) and fr_.get("filter"):
                per[fr_["filter"]] = per.get(fr_["filter"], 0.0) + \
                    _hours(fr_.get("exposure_s")) * _hours(fr_.get("count")) / 3600.0
        filters = [{"filter": n, "delta_h": round(h, 2), "hours": None, "goal": None,
                    "done": False} for n, h in per.items() if h > 0]
        if filters:
            targets[pid] = {"plan_id": e["refs"].get("plan_id"),
                            "target": e["refs"].get("target") or e["title"],
                            "ra_deg": e["refs"].get("ra_deg"), "dec_deg": e["refs"].get("dec_deg"),
                            "filters": filters, "hours": round(sum(f["delta_h"] for f in filters), 2),
                            "finished": e["refs"].get("plan_id") in finished}
    return {"night": night, "targets": list(targets.values()),
            "total_h": round(sum(t["hours"] for t in targets.values()), 2)}


def mark_seen() -> str:
    """Move ``last_seen`` to the server's time. A client time is never taken,
    and the value never moves backwards."""
    now = _stamp(_now())
    with _tx() as conn:
        row = conn.execute("SELECT value FROM state WHERE name = 'last_seen'").fetchone()
        value = max(now, row[0]) if row and row[0] else now
        _set_state(conn, "last_seen", value)
    return value


def set_dismissed(event_id: int, dismissed: bool) -> bool:
    with _tx() as conn:
        stamp = _stamp(_now()) if dismissed else None
        cur = conn.execute("UPDATE events SET dismissed_at = ? WHERE id = ? AND needs_you = 1",
                           (stamp, event_id))
        return cur.rowcount > 0


def get_prefs() -> dict:
    raw = get_state("prefs")
    chips = dict(DEFAULT_CHIPS)
    if raw:
        try:
            stored = json.loads(raw).get("chips", {})
        except (ValueError, AttributeError):
            stored = {}
        for cid, on in stored.items():
            if cid in chips and isinstance(on, bool):
                chips[cid] = on
    return {"chips": chips}


def set_prefs(chips: dict) -> dict:
    clean = {cid: on for cid, on in chips.items() if cid in DEFAULT_CHIPS}
    with _tx() as conn:
        _set_state(conn, "prefs", json.dumps({"chips": clean}))
    return get_prefs()


# ---------------------------------------------------------------------------
# Small builders for ACP's own events
# ---------------------------------------------------------------------------

def plan_refs(plan: dict) -> dict:
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    refs = {"plan_id": plan.get("id")}
    if plan.get("project_name"):
        refs["project"] = plan["project_name"]
    if target.get("name"):
        refs["target"] = target["name"]
    for src, dst in (("center_ra_deg", "ra_deg"), ("center_dec_deg", "dec_deg")):
        if isinstance(target.get(src), (int, float)) and not isinstance(target.get(src), bool):
            refs[dst] = target[src]
    return refs


def plan_name(plan: dict) -> str:
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    return str(target.get("name") or plan.get("id") or "plan")


def copy_plan(plan):
    return copy.deepcopy(plan)
