"""Progress of file transfers that another tool reports (docs/api.md, "Upload
progress").

A tool moving files somewhere posts figures for a named transfer to
``POST /api/uploads/<transfer_id>/progress``. ACP keeps them in memory for a
short while and shows running and paused transfers on a card at the top of
the Activity tab. It never knows how or where the files move. When a transfer
reports ``finished`` or ``failed`` for the first time, app.py records one
Activity event for it.

Nothing is written to disk: a restart forgets every transfer, and the sender's
next post brings it back.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone

STATES = ("running", "paused", "finished", "failed")
ACTIVE = ("running", "paused")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
MAX_LABEL = 80
MAX_TRANSFERS = 50
#: A running or paused transfer with no post for this long is dropped.
STALE_AFTER = timedelta(minutes=30)
#: A finished or failed transfer is kept this long, then dropped.
KEEP_DONE = timedelta(minutes=10)

_lock = threading.Lock()
_transfers: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value, name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO 8601 time")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{name} must be an ISO 8601 time") from None
    if dt.tzinfo is None:
        raise ValueError(f"{name} must carry a time zone offset")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _count(body: dict, name: str) -> int | None:
    v = body.get(name)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"{name} must be a whole number of 0 or more")
    return v


def validate(transfer_id, body) -> dict:
    """A clean record from what the sender posted, or ValueError saying why not."""
    if not isinstance(transfer_id, str) or not ID_RE.match(transfer_id):
        raise ValueError("transfer id must be 1 to 80 letters, digits, '.', '_' or '-'")
    if not isinstance(body, dict):
        raise ValueError("the body must be a JSON object")
    state = body.get("state")
    if state not in STATES:
        raise ValueError("state must be one of " + ", ".join(STATES))
    rec = {"id": transfer_id, "state": state}
    for done, total in (("files_done", "files_total"), ("bytes_done", "bytes_total")):
        d, t = _count(body, done), _count(body, total)
        if d is not None and t is not None and d > t:
            raise ValueError(f"{done} is larger than {total}")
        rec[done], rec[total] = d, t
    rec["started_at"] = _parse_time(body.get("started_at"), "started_at")
    rec["eta"] = _parse_time(body.get("eta"), "eta")
    rec["cutoff"] = _parse_time(body.get("cutoff"), "cutoff")
    label = body.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
        raise ValueError(f"label must be text of {MAX_LABEL} characters or fewer")
    rec["label"] = " ".join(label.split()) if isinstance(label, str) and label.strip() else None
    return rec


def _prune(now: datetime) -> None:
    for tid in list(_transfers):
        rec = _transfers[tid]
        age = now - rec["_seen"]
        if age > (STALE_AFTER if rec["state"] in ACTIVE else KEEP_DONE):
            del _transfers[tid]


def report(transfer_id, body, now: datetime | None = None) -> tuple[dict, bool]:
    """Store a progress post. Returns (the stored record, whether this post
    is the first to say the transfer finished or failed). Raises ValueError
    for a bad post, and OverflowError when too many transfers are open."""
    rec = validate(transfer_id, body)
    now = now or _now()
    with _lock:
        _prune(now)
        old = _transfers.get(transfer_id)
        if old is None and len(_transfers) >= MAX_TRANSFERS:
            raise OverflowError(f"more than {MAX_TRANSFERS} transfers at once")
        # A new run of the same transfer id (a later started_at) starts fresh.
        same_run = old is not None and (rec["started_at"] in (None, old.get("started_at")))
        ended_now = rec["state"] not in ACTIVE and not (same_run and old["state"] not in ACTIVE)
        if same_run and rec["started_at"] is None:
            rec["started_at"] = old.get("started_at")
        rec["updated_at"] = now.isoformat(timespec="seconds")
        rec["_seen"] = now
        _transfers[transfer_id] = rec
        return public(rec), ended_now


def public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def listing(now: datetime | None = None) -> list[dict]:
    """Every transfer still kept, running and paused first, newest first."""
    now = now or _now()
    with _lock:
        _prune(now)
        recs = [public(r) for r in _transfers.values()]
    recs.sort(key=lambda r: r["updated_at"], reverse=True)
    recs.sort(key=lambda r: r["state"] not in ACTIVE)
    return recs


def clear() -> None:
    """Forget every transfer. For tests."""
    with _lock:
        _transfers.clear()


def _size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return str(n)


def event_title(rec: dict) -> str:
    """The Activity title for a transfer that ended."""
    name = rec.get("label") or rec["id"]
    files = (f"{rec['files_done']} of {rec['files_total']} files"
             if rec.get("files_total") is not None and rec.get("files_done") is not None else None)
    size = (f"{_size(rec.get('bytes_done'))} of {_size(rec.get('bytes_total'))}"
            if rec.get("bytes_total") is not None else None)
    detail = ", ".join(x for x in (files, size) if x)
    verb = "finished" if rec["state"] == "finished" else "failed"
    return f"Upload {verb}: {name}" + (f" ({detail})" if detail else "")
