"""Acceptance tests from docs/specs/ts-project-settings.md section 7 that
sit below the merge/import routes: the base snapshot's own priority
reading, and push idempotency at the convert/upsert layer directly.
"""
from __future__ import annotations

import sqlite3

from nina_ts_sync import convert, upsert
from nina_ts_sync.reader import read_all
from nina_ts_sync.state import build_base_snapshot
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, plan


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def test_a_priority_zero_project_snapshots_as_zero_not_one(tmp_path):
    """state.py's _project_snapshot used to read `proj.priority or 1`, which
    turns 0 (low) into 1 (normal) because 0 is falsy in Python. Only a
    genuinely unset column (None) should default."""
    p = plan("low-priority", project_name="Low Priority Project", priority="low")
    conn = _connect(make_db(28, tmp_path / "priority-zero.sqlite"))
    upsert.apply(conn, convert.build_payload([p], GEAR, profile_id=PROFILE_ID))
    conn.commit()

    snap = read_all(conn, PROFILE_ID)
    base = build_base_snapshot(snap, p, profile_id=PROFILE_ID)
    assert base["project"]["priority"] == 0


def test_a_second_push_with_no_acp_change_writes_no_state_priority_or_minimum_time(tmp_path):
    """Once ACP has a base snapshot and hasn't changed state, priority or
    minimum time since, a second push leaves all three untouched in TS,
    even when a hand edit changed one of them on the rig."""
    p = plan("steady", project_name="Steady Project", state="inactive",
              priority="high", minimum_time_min=45)
    conn = _connect(make_db(28, tmp_path / "steady.sqlite"))
    upsert.apply(conn, convert.build_payload([p], GEAR, profile_id=PROFILE_ID))
    conn.commit()

    snap = read_all(conn, PROFILE_ID)
    p["ts_base_snapshot"] = build_base_snapshot(snap, p, profile_id=PROFILE_ID)

    # An edit made on the rig since the base was captured.
    conn.execute("UPDATE project SET priority = 0 WHERE name = 'Steady Project'")
    conn.commit()

    upsert.apply(conn, convert.build_payload([p], GEAR, profile_id=PROFILE_ID))
    conn.commit()

    row = conn.execute(
        "SELECT state, priority, minimumtime FROM project WHERE name = 'Steady Project'"
    ).fetchone()
    assert row["state"] == 2  # ACP's inactive, unchanged since the base
    assert row["priority"] == 0  # the rig's edit survived the second push
    assert row["minimumtime"] == 45  # ACP's 45, unchanged since the base
