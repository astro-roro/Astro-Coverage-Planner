"""Live progress of Voyager's nightly upload to the NAS.

Voyager copies each night with rclone into ``<incoming>/<night_id>/``. At the
start of every run it writes ``upload_plan.json`` there, saying how many files
and bytes that run will send, and when the copy is complete it writes
``night_manifest.json``. ACP mounts the same folder read-only, so it can work
out progress by counting what has arrived so far against the plan.

Nothing here writes to the incoming folder. The watcher keeps its rate samples
in memory, so a restart only costs the five-minute rate until new samples
build up again.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PLAN_NAME = "upload_plan.json"
MANIFEST_NAME = "night_manifest.json"
# Files Voyager and AAC write beside the frames. None of them is a frame.
PROTOCOL_FILES = frozenset({PLAN_NAME, f"{PLAN_NAME}.tmp", MANIFEST_NAME,
                            f"{MANIFEST_NAME}.tmp", "ingest_result.json",
                            "ingest_result.json.tmp"})
# rclone writes an in-flight file as "<name>.<hash>.partial" and renames it
# when the transfer finishes. Voyager's own filters also drop ".tmp".
TEMP_SUFFIXES = (".partial", ".tmp")
SKIP_DIRS = frozenset({"_collisions"})

RECENT_WINDOW = timedelta(minutes=5)
SAMPLE_KEEP = timedelta(minutes=15)
# A plan whose cutoff passed this long ago with no manifest is a night that
# was given up on. Its card goes rather than saying "paused" forever.
PAUSED_HIDE_AFTER = timedelta(hours=24)
# A manifest older than this when first seen is history, not a finish to
# announce (for example, the first poll after ACP starts).
FINISH_ANNOUNCE_WITHIN = timedelta(hours=36)


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return 0
    return int(value)


def read_plan(path: Path) -> dict | None:
    """The plan as numbers and times, or None if it cannot be used."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    started = _parse_time(raw.get("started_at"))
    if started is None:
        return None
    night = raw.get("night_id") if isinstance(raw.get("night_id"), str) else path.parent.name
    path_kind = raw.get("path") if raw.get("path") in ("vpn", "tailscale") else None
    return {
        "night_id": night,
        "started_at": started,
        "window_end": _parse_time(raw.get("window_end")),
        "total_files": _count(raw.get("total_files")),
        "total_bytes": _count(raw.get("total_bytes")),
        "already_files": _count(raw.get("already_on_nas_files")),
        "already_bytes": _count(raw.get("already_on_nas_bytes")),
        "path": path_kind,
    }


def count_folder(folder: Path) -> dict:
    """Finished frames and their bytes, plus the bytes of files still in
    flight. Protocol files and anything under ``_collisions`` are ignored."""
    files = done_bytes = partial_bytes = 0
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name in PROTOCOL_FILES:
                continue
            try:
                size = os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue  # renamed between the listing and the stat
            if name.endswith(TEMP_SUFFIXES):
                partial_bytes += size
            else:
                files += 1
                done_bytes += size
    return {"files": files, "bytes": done_bytes, "partial_bytes": partial_bytes}


def _mbps(byte_count: float, seconds: float) -> float | None:
    if seconds <= 0:
        return None
    return round(byte_count * 8 / seconds / 1e6, 1)


def done(plan: dict, counts: dict) -> tuple[int, int]:
    """Files and bytes this run has sent. Frames already on the NAS before
    the run are not this run's work. In-flight bytes count, so a slow link
    still shows movement between finished files."""
    files_done = max(0, counts["files"] - plan["already_files"])
    bytes_done = max(0, counts["bytes"] - plan["already_bytes"]) + counts["partial_bytes"]
    if plan["total_files"]:
        files_done = min(files_done, plan["total_files"])
    if plan["total_bytes"]:
        bytes_done = min(bytes_done, plan["total_bytes"])
    return files_done, bytes_done


def progress(plan: dict, counts: dict, now: datetime, samples: list,
             zone=timezone.utc) -> dict:
    """What the card shows. ``samples`` is a list of ``(time, bytes_done)``
    for this run, oldest first, ending with the sample taken at ``now``.
    Times come back in ``zone``, so the page can print them as they are."""
    files_done, bytes_done = done(plan, counts)
    total_files, total_bytes = plan["total_files"], plan["total_bytes"]
    if total_bytes:
        fraction = bytes_done / total_bytes
    elif total_files:
        fraction = files_done / total_files
    else:
        fraction = 0.0
    elapsed = max(0.0, (now - plan["started_at"]).total_seconds())
    avg_bps = bytes_done / elapsed if elapsed > 0 else None

    recent_bps = None
    cutoff = now - RECENT_WINDOW
    base = next((s for s in reversed(samples) if s[0] <= cutoff), None)
    if base is None and samples and (now - samples[0][0]).total_seconds() >= 60:
        base = samples[0]  # under five minutes of history: use what there is
    if base is not None:
        recent_bps = max(0, bytes_done - base[1]) / (now - base[0]).total_seconds()

    eta = None
    rate = recent_bps or avg_bps
    remaining = total_bytes - bytes_done if total_bytes else None
    if rate and remaining is not None:
        eta = now + timedelta(seconds=round(remaining / rate))
    elif remaining == 0:
        eta = now
    window_end = plan["window_end"]
    paused = bool(window_end and now >= window_end)
    return {
        "night_id": plan["night_id"],
        "started_at": plan["started_at"].astimezone(zone).isoformat(),
        "window_end": window_end.astimezone(zone).isoformat() if window_end else None,
        "path": plan["path"],
        "files_done": files_done,
        "files_total": total_files,
        "bytes_done": bytes_done,
        "bytes_total": total_bytes,
        "percent": round(min(1.0, fraction) * 100, 1),
        "elapsed_s": int(elapsed),
        "avg_mbps": _mbps(avg_bps, 1) if avg_bps is not None else None,
        "recent_mbps": _mbps(recent_bps, 1) if recent_bps is not None else None,
        "eta": eta.astimezone(zone).isoformat() if eta and not paused else None,
        "eta_after_cutoff": bool(eta and window_end and eta > window_end and not paused),
        "state": "paused" if paused else "uploading",
    }


def finished_event(plan: dict, finished_at: datetime, night: str) -> dict:
    """The one Activity event for a completed upload. The plan's totals are
    used rather than a recount, because AAC may move the frames out of the
    folder before ACP's next poll."""
    seconds = (finished_at - plan["started_at"]).total_seconds()
    gb = plan["total_bytes"] / 1e9
    avg = _mbps(plan["total_bytes"], seconds)
    hours, rem = divmod(max(0, int(seconds)), 3600)
    took = f"{hours} h {rem // 60:02d} min" if hours else f"{rem // 60} min"
    title = f"Upload finished: {plan['total_files']} files, {gb:.1f} GB"
    detail = f"Took {took}" + (f" at an average of {avg:g} Mbps" if avg else "") + \
        (f" over {plan['path']}." if plan["path"] else ".")
    return {
        "type": "upload_finished", "key": f"upload_finished:{plan['night_id']}",
        "night": night, "at": finished_at.isoformat(), "title": title, "detail": detail,
        "data": {"files": plan["total_files"], "bytes": plan["total_bytes"],
                 "seconds": int(seconds), "avg_mbps": avg, "path": plan["path"],
                 "started_at": plan["started_at"].isoformat()},
    }


class UploadWatcher:
    """Polls the incoming folder. ``record`` stores an Activity event and
    ``already_recorded(key)`` says whether one exists, so a restart does not
    announce a finish twice."""

    def __init__(self, incoming: Path, record, already_recorded, night_of,
                 zone=timezone.utc):
        self.incoming = Path(incoming)
        self._record = record
        self._already = already_recorded
        self._night_of = night_of
        self._zone = zone
        self._lock = threading.Lock()
        self._samples: dict[tuple, list] = {}
        self._live: dict[str, dict] = {}
        self._announced: set[str] = set()

    def _night(self, plan: dict) -> str:
        nid = plan["night_id"]
        if isinstance(nid, str) and len(nid) == 10 and nid[4] == "-" and nid[7] == "-":
            return nid
        return self._night_of(plan["started_at"])

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        live: dict[str, dict] = {}
        try:
            folders = [p for p in self.incoming.iterdir() if p.is_dir()]
        except OSError:
            folders = []  # the share is not mounted, or the setting is wrong
        for folder in folders:
            plan_path = folder / PLAN_NAME
            if not plan_path.is_file():
                continue
            plan = read_plan(plan_path)
            if plan is None:
                continue
            manifest = folder / MANIFEST_NAME
            if manifest.is_file():
                self._finish(plan, manifest, now)
                continue
            if plan["window_end"] and now - plan["window_end"] > PAUSED_HIDE_AFTER:
                continue
            run = (plan["night_id"], plan["started_at"].astimezone(self._zone).isoformat())
            counts = count_folder(folder)
            samples = self._samples.setdefault(run, [])
            samples.append((now, done(plan, counts)[1]))
            while samples and now - samples[0][0] > SAMPLE_KEEP:
                samples.pop(0)
            live[plan["night_id"]] = progress(plan, counts, now, samples, self._zone)
        with self._lock:
            self._live = live
            alive = {(r["night_id"], r["started_at"]) for r in live.values()}
            self._samples = {k: v for k, v in self._samples.items() if k in alive}

    def _finish(self, plan: dict, manifest: Path, now: datetime) -> None:
        key = f"upload_finished:{plan['night_id']}"
        if key in self._announced:
            return
        self._announced.add(key)
        try:
            finished_at = datetime.fromtimestamp(manifest.stat().st_mtime, timezone.utc)
        except OSError:
            finished_at = now
        if now - finished_at > FINISH_ANNOUNCE_WITHIN or self._already(key):
            return
        self._record(**finished_event(plan, finished_at, self._night(plan)))

    def snapshot(self) -> list[dict]:
        with self._lock:
            return sorted(self._live.values(), key=lambda r: r["started_at"])
