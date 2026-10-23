"""Per-rig links between an ACP plan and the TS rows it came from.

A rig is a NINA profile. Each plan carries one link per profile it has been
synced with::

    "ts_links": {
        "<profile_id>": {
            "machine": "IMAGING-PC",
            "refs": {...},            # the shape ts_refs has always had
            "base_snapshot": {...},   # the shape ts_base_snapshot has always had
            "updated_iso": "2026-09-24T10:31:00Z"
        }
    }

`ts_refs` and `ts_base_snapshot` stay on the plan as a copy of the most recent
link, so older readers keep working. A plan written before links existed has
only those two fields; its `ts_refs.profile_id` names the one profile it was
synced with, so it reads as a link for that profile with no machine recorded.
"""
from __future__ import annotations

import copy
import os
import platform
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_machine() -> str:
    """This machine's name the way NINA's Environment.MachineName gives it.

    Used by the routes that read a TS database on the ACP host itself.
    """
    name = os.environ.get("COMPUTERNAME") or platform.node().split(".")[0]
    return (name or "").upper()


def same_machine(a: str | None, b: str | None) -> bool:
    """Whether two machine names are the same. An unknown side is trusted,
    because a link written before machines were recorded has none."""
    if not a or not b:
        return True
    return a.strip().upper() == b.strip().upper()


def links_of(plan: dict) -> dict[str, dict]:
    """Every link on *plan*, keyed by profile id, including the legacy one."""
    out: dict[str, dict] = {}
    raw = plan.get("ts_links")
    if isinstance(raw, dict):
        for pid, link in raw.items():
            if isinstance(link, dict) and pid:
                out[pid] = link
    refs = plan.get("ts_refs")
    if isinstance(refs, dict):
        pid = refs.get("profile_id")
        if pid and pid not in out:
            out[pid] = {
                "machine": None,
                "refs": refs,
                "base_snapshot": plan.get("ts_base_snapshot"),
                "updated_iso": None,
            }
    return out


def link_for(plan: dict, profile_id: str) -> dict | None:
    return links_of(plan).get(profile_id)


def in_scope(plan: dict, profile_id: str) -> bool:
    """An upload for *profile_id* may read or change *plan*.

    True when the plan is linked to that profile, or linked to no profile at
    all. A plan linked only to other profiles belongs to other rigs.
    """
    links = links_of(plan)
    return not links or profile_id in links


def _materialise_legacy(plan: dict) -> None:
    """Move a legacy ts_refs link into ts_links before anything overwrites
    ts_refs, so writing one profile's link never erases another's."""
    refs = plan.get("ts_refs")
    if not isinstance(refs, dict) or not refs.get("profile_id"):
        return
    links = plan.get("ts_links")
    if not isinstance(links, dict):
        links = {}
    pid = refs["profile_id"]
    if pid not in links:
        links[pid] = {
            "machine": None,
            "refs": copy.deepcopy(refs),
            "base_snapshot": copy.deepcopy(plan.get("ts_base_snapshot")),
            "updated_iso": None,
        }
    plan["ts_links"] = links


def write_link(
    plan: dict,
    profile_id: str,
    *,
    machine: str | None,
    refs: dict,
    base_snapshot: dict | None,
) -> None:
    """Record *profile_id*'s link on *plan*, and copy it to the legacy fields."""
    _materialise_legacy(plan)
    links = plan.get("ts_links")
    if not isinstance(links, dict):
        links = {}
    links[profile_id] = {
        "machine": machine or None,
        "refs": copy.deepcopy(refs),
        "base_snapshot": copy.deepcopy(base_snapshot),
        "updated_iso": now_iso(),
    }
    plan["ts_links"] = links
    plan["ts_refs"] = copy.deepcopy(refs)
    plan["ts_base_snapshot"] = copy.deepcopy(base_snapshot)


def record_current_link(plan: dict, profile_id: str, machine: str | None) -> None:
    """Copy the plan's freshly stamped ts_refs and ts_base_snapshot into
    ts_links[profile_id]. For the stamping paths that write the legacy fields
    first (`stamp_plans` in /sync, /import/apply)."""
    refs = plan.get("ts_refs")
    if not isinstance(refs, dict):
        return
    links = plan.get("ts_links")
    if not isinstance(links, dict):
        links = {}
    links[profile_id] = {
        "machine": machine or None,
        "refs": copy.deepcopy(refs),
        "base_snapshot": copy.deepcopy(plan.get("ts_base_snapshot")),
        "updated_iso": now_iso(),
    }
    plan["ts_links"] = links


def preserve_legacy_links(plans: list[dict]) -> None:
    """Call before a path that overwrites ts_refs on many plans at once."""
    for plan in plans:
        _materialise_legacy(plan)


def set_link_only(
    plan: dict,
    profile_id: str,
    *,
    machine: str | None,
    refs: dict,
    base_snapshot: dict | None,
) -> None:
    """Write ts_links[profile_id] and nothing else. POST /links uses this:
    the plugin's push state touches no other field on the plan."""
    links = plan.get("ts_links")
    if not isinstance(links, dict):
        links = {}
    links[profile_id] = {
        "machine": machine or None,
        "refs": copy.deepcopy(refs),
        "base_snapshot": copy.deepcopy(base_snapshot),
        "updated_iso": now_iso(),
    }
    plan["ts_links"] = links
