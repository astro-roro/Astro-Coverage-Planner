"""Strictest-wins project fields still collapse the way they always did.

Plans that share a `project_name` become one Target Scheduler project, and
the project level constraints are reconciled: the highest minimum altitude,
the tightest non-zero meridian window, and the highest priority.
"""
from __future__ import annotations

import sqlite3

import pytest

from nina_ts_sync import convert, upsert
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, plan


def _group(**overrides_per_plan):
    """Several plans under one project name, each with its own constraints."""
    return [
        plan(pid, project_name="Shared Project", target_name=f"T{pid}", **kw)
        for pid, kw in overrides_per_plan.items()
    ]


def test_min_altitude_takes_the_highest():
    plans = _group(a={"min_altitude_deg": 20}, b={"min_altitude_deg": 45},
                   c={"min_altitude_deg": 30})
    payload = convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    assert len(payload.projects) == 1
    assert payload.projects[0].minimumaltitude == 45.0


def test_meridian_window_takes_the_tightest_non_zero():
    plans = _group(a={"meridian_window_min": 0}, b={"meridian_window_min": 90},
                   c={"meridian_window_min": 60})
    payload = convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    assert payload.projects[0].meridianwindow == 60


def test_meridian_window_stays_zero_when_no_plan_sets_one():
    plans = _group(a={"meridian_window_min": 0}, b={})
    payload = convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    assert payload.projects[0].meridianwindow == 0


def test_priority_takes_the_highest():
    plans = _group(a={"priority": "low"}, b={"priority": "high"},
                   c={"priority": "normal"})
    payload = convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    assert payload.projects[0].priority == convert.PRIORITY_RANK["high"]


@pytest.mark.parametrize("version", [23, 28])
def test_collapsed_values_reach_the_database_at_both_ends(tmp_path, version):
    plans = _group(
        a={"min_altitude_deg": 20, "meridian_window_min": 0, "priority": "low"},
        b={"min_altitude_deg": 45, "meridian_window_min": 90, "priority": "high"},
        c={"min_altitude_deg": 30, "meridian_window_min": 60, "priority": "normal"},
    )
    path = make_db(version, tmp_path / f"v{version}.sqlite")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        upsert.apply(conn, convert.build_payload(plans, GEAR, profile_id=PROFILE_ID))
        conn.commit()
        rows = conn.execute("SELECT * FROM project").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row["minimumaltitude"] == 45.0
        assert row["meridianwindow"] == 60
        assert row["priority"] == convert.PRIORITY_RANK["high"]
        # All three plans still land as their own targets under that project.
        assert conn.execute("SELECT COUNT(*) FROM target").fetchone()[0] == 3
    finally:
        conn.close()
