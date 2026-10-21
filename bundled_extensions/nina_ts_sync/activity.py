"""Hooks into ACP's Activity tab (docs/specs/activity-tab.md in ACP).

The host exposes ``app.record_activity(**fields)``. An older ACP does not, and
then every call here does nothing, so a newer extension still works on it.
Recording runs after the route's work has succeeded and can never fail the
route: every error is logged and dropped.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app

logger = logging.getLogger("nina_ts_sync.activity")

DEFAULT_TZ = "Australia/Sydney"


def night_now() -> str:
    """The observing night right now, as ACP works it out: the date the
    evening started, cutting over at local noon in ACP_ACTIVITY_TZ."""
    name = (os.environ.get("ACP_ACTIVITY_TZ") or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        zone = ZoneInfo(name)
    except Exception:
        zone = ZoneInfo(DEFAULT_TZ)
    local = datetime.now(timezone.utc).astimezone(zone)
    day = local.date() if local.hour >= 12 else local.date().fromordinal(local.date().toordinal() - 1)
    return day.isoformat()


def record(**fields) -> None:
    try:
        fn = getattr(current_app, "record_activity", None)
        if fn is not None:
            fn(**fields)
    except Exception:
        logger.exception("could not record an activity event")


def upload_received(upload_id: str, meta: dict, profile_name: str) -> None:
    who = profile_name or meta.get("machine") or "a rig"
    record(
        type="ts_upload_received", key=f"ts_upload_received:{upload_id}",
        title=f"Target Scheduler database received from {who}",
        needs_you=True, resolve_key=f"upload:{upload_id}",
        refs={"upload_id": upload_id, "profile": meta.get("profile_id")},
    )


def upload_applied(upload_id: str, changed: int) -> None:
    record(
        type="ts_upload_applied", key=f"ts_upload_applied:{upload_id}",
        title=f"Upload applied: {changed} plan{'' if changed == 1 else 's'} changed",
        data={"plans_changed": changed}, resolves=[f"upload:{upload_id}"],
        refs={"upload_id": upload_id},
    )


def upload_discarded(upload_id: str) -> None:
    record(
        type="ts_upload_discarded", key=f"ts_upload_discarded:{upload_id}",
        title="Upload discarded", resolves=[f"upload:{upload_id}"],
        refs={"upload_id": upload_id},
    )


def rig_synced(profile_id: str, machine: str | None, updated: int) -> None:
    night = night_now()
    where = machine or profile_id
    record(
        type="rig_synced", key=f"rig_synced:{profile_id}:{night}", night=night,
        title=f"Rig synced: {where}", data={"plans_linked": updated},
        refs={"profile": profile_id},
    )
