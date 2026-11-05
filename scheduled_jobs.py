"""Scheduled jobs extensions register (docs/extensions.md, "Scheduled jobs").

An extension appends ``{"id", "cron", "run"}`` to ``app.scheduled_jobs`` in
its ``register(app)``. app.py starts one thread that runs each job when its
cron expression comes due, in local time, the way the archive rescan is
scheduled. ``ACP_DISABLE_JOBS=1`` keeps the thread from starting at all.

This module holds the timing and the bookkeeping, with no thread and no
Flask, so tests can drive it with a fixed clock.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from typing import Callable

logger = logging.getLogger("acp.jobs")

DISABLE_ENV = "ACP_DISABLE_JOBS"
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")


def disabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get(DISABLE_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def _next_after(expr: str, now: datetime) -> datetime:
    from croniter import croniter
    return croniter(expr, now).get_next(datetime)


def valid_jobs(raw) -> list[dict]:
    """The usable entries of *raw*, each checked once. A bad entry is logged
    and skipped; so is a second job with an id already taken."""
    try:
        from croniter import croniter
    except ImportError:
        logger.error("croniter is not installed; no extension job will run")
        return []
    out, seen = [], set()
    for job in raw if isinstance(raw, list) else []:
        jid = job.get("id") if isinstance(job, dict) else None
        reason = None
        if not isinstance(jid, str) or not _ID_RE.match(jid):
            reason = "id must be a short name"
        elif jid in seen:
            reason = "duplicate id"
        elif not isinstance(job.get("cron"), str) or not croniter.is_valid(job["cron"]):
            reason = "cron must be a valid cron expression"
        elif not callable(job.get("run")):
            reason = "run must be callable"
        if reason:
            logger.warning("scheduled job %r skipped: %s", jid, reason)
            continue
        seen.add(jid)
        out.append(job)
    return out


class Runner:
    """Works out which jobs are due and runs them, one at a time."""

    def __init__(self, jobs: list[dict], now: datetime):
        self.jobs = jobs
        self.state = {j["id"]: {"next_at": _next_after(j["cron"], now), "last_run": None,
                                "last_ok": None} for j in jobs}
        self._lock = threading.Lock()

    def run_due(self, now: datetime) -> list[str]:
        """Run every job whose time has come and return their ids. A job that
        raises is logged; the others still run."""
        ran = []
        for job in self.jobs:
            st = self.state[job["id"]]
            if now < st["next_at"]:
                continue
            ok = True
            try:
                job["run"]()
            except Exception:
                ok = False
                logger.exception("scheduled job %s raised", job["id"])
            with self._lock:
                st["last_run"] = now.isoformat(timespec="seconds")
                st["last_ok"] = ok
                st["next_at"] = _next_after(job["cron"], now)
            ran.append(job["id"])
        return ran

    def seconds_to_next(self, now: datetime) -> float:
        if not self.jobs:
            return 3600.0
        soonest = min(st["next_at"] for st in self.state.values())
        return max(0.0, (soonest - now).total_seconds())

    def status(self) -> list[dict]:
        """What /api/scan/status shows: no error text, only whether it worked."""
        with self._lock:
            return [{"id": j["id"], "cron": j["cron"],
                     "next_at": self.state[j["id"]]["next_at"].isoformat(timespec="seconds"),
                     "last_run": self.state[j["id"]]["last_run"],
                     "last_ok": self.state[j["id"]]["last_ok"]} for j in self.jobs]


def loop(runner: Runner, stop: threading.Event, clock: Callable[[], datetime] = datetime.now,
         poll_seconds: float = 30.0) -> None:
    while not stop.is_set():
        runner.run_due(clock())
        stop.wait(min(runner.seconds_to_next(clock()), poll_seconds))
