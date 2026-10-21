"""Review fixes for the AAC link, phase 6 (goal-panel-and-ts-order.md section 6).

AAC's floor never reaches ACP's goal, a row's `aac` block goes when its filter
or template changes, a goal of 0 disables the plan like a removed row, a push
says why it skipped AAC's counts, and the host's plans lock is shared.
"""
from __future__ import annotations

import copy
import sqlite3
import threading
from datetime import datetime

import pytest

from nina_ts_sync import convert, import_merge as im, merge, rows as fr, upsert
from nina_ts_sync.reader import read_all
from . import row_scenarios as sc
from .make_db import make_db
from .plans import PROFILE_ID

NOW = datetime.fromisoformat("2026-10-12T15:00:00+11:00")
BLOCK = {"accepted_seconds": 60.0, "accepted_frames": 4, "seen_seconds": 60.0, "seen_frames": 4,
         "complete": False, "estimated_hours": None, "measurement": None}


def _db(tmp_path):
    conn = sqlite3.connect(make_db(28, tmp_path / "ts.sqlite"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO profilepreference (profileId, delayGrading, enableGradeRMS, enableGradeStars, "
        "enableGradeHFR, enableGradeFWHM, enableGradeEccentricity) VALUES (?,0,0,0,0,0,0)", (PROFILE_ID,))
    return conn


def _push(conn, plans, now=NOW):
    rep = upsert.apply(conn, convert.build_payload(plans, sc.ROW_GEAR, profile_id=PROFILE_ID, now=now))
    conn.commit()
    sc.link(conn, plans, rep)
    return rep


def _floored(tmp_path):
    """One R Stars row with a goal of 8 frames, an AAC block, and a floor of
    10, pushed: TS holds desired 10, the base records the goal."""
    conn = _db(tmp_path)
    p = sc.m42([sc.row("rs", "R", "R Stars", 15, frames=8)])
    p["filter_rows"][0]["aac"] = copy.deepcopy(BLOCK)
    p["aac"] = {"filed_through_night": "2026-10-11", "min_total_frames": 10, "received_at": "x"}
    rep = _push(conn, [p])
    assert [r[0] for r in conn.execute("SELECT desired FROM exposureplan")] == [10]
    return conn, p, rep


def _view(conn, p):
    snap = read_all(conn, PROFILE_ID)
    return im.build_view(snap, 28, [p], sc.ROW_GEAR, profile_id=PROFILE_ID, machine=None, strict=False)


def _apply(view, decisions=None, require=False):
    im.apply_view(view, decisions=decisions or {}, add=None, new_plan_gear=None, require_decisions=require)
    return view.matches[0].plan


def _goal(p):
    return p["filter_rows"][0].get("target_frames")


# --- decision 1: the floor never reaches ACP's goal --------------------------


def test_the_base_records_the_goal_not_the_floored_desired(tmp_path):
    _, p, _ = _floored(tmp_path)
    base = next(iter(p["ts_links"].values()))["base_snapshot"]
    assert [e["desired"] for e in base["exposure_plans_by_panel_filter"].values()] == [8]


def test_no_edits_gives_no_diff_and_the_goal_stays(tmp_path):
    conn, p, _ = _floored(tmp_path)
    view = _view(conn, p)
    d = view.matches[0].diff
    assert (d.auto_pull, d.auto_keep, d.conflict) == ([], [], [])
    plan = _apply(view)
    assert _goal(plan) == 8
    assert "aac" in plan["filter_rows"][0]


def test_ts_raising_desired_past_the_floor_auto_pulls(tmp_path):
    conn, p, _ = _floored(tmp_path)
    conn.execute("UPDATE exposureplan SET desired = 14")
    conn.commit()
    view = _view(conn, p)
    d = view.matches[0].diff
    assert d.auto_pull == ["filter_rows.rs.target_frames"] and d.conflict == []
    plan = _apply(view)
    assert _goal(plan) == 14
    assert "aac" in plan["filter_rows"][0]


def test_take_ts_on_an_unrelated_conflict_leaves_the_goal(tmp_path):
    conn, p, _ = _floored(tmp_path)
    p["priority"] = "high"
    conn.execute("UPDATE project SET priority = 0")
    conn.commit()
    view = _view(conn, p)
    assert view.matches[0].diff.conflict == ["priority"]
    plan = _apply(view, {"m42": "take_ts"}, require=True)
    assert plan["priority"] == "low"
    assert _goal(plan) == 8 and plan["filter_rows"][0]["unit"] == "fr"
    assert "aac" in plan["filter_rows"][0]


def test_an_import_with_no_base_leaves_the_goal(tmp_path):
    conn, p, _ = _floored(tmp_path)
    for k in ("ts_links", "ts_refs", "ts_base_snapshot"):
        p.pop(k, None)
    view = _view(conn, p)
    assert view.matches
    assert _goal(_apply(view)) == 8


def test_an_hours_goal_below_the_floor_is_read_back_as_hours(tmp_path):
    conn = _db(tmp_path)
    p = sc.m42([sc.row("ha", "Ha", "Ha 300", 300, hours=0.5)])
    p["filter_rows"][0]["aac"] = copy.deepcopy(BLOCK)
    p["aac"] = {"filed_through_night": "2026-10-11", "min_total_frames": 10, "received_at": "x"}
    _push(conn, [p])
    assert [r[0] for r in conn.execute("SELECT desired FROM exposureplan")] == [10]
    view = _view(conn, p)
    d = view.matches[0].diff
    assert (d.auto_pull, d.auto_keep, d.conflict) == ([], [], [])
    row = _apply(view)["filter_rows"][0]
    assert row["target_hours"] == 0.5 and row["unit"] == "h"


# --- decision 2: a stale aac block goes --------------------------------------


def _plan_with_aac_row(**changes):
    row = {**sc.row("rs", "R", "R Stars", 15, frames=8), "aac": copy.deepcopy(BLOCK)}
    return sc.m42([row]), {**row, **changes}


def test_take_rows_keeps_the_block_while_filter_and_template_agree():
    p, remote_row = _plan_with_aac_row()
    remote_row.pop("aac")
    out = merge.take_rows(p, {"filter_rows": [remote_row]})
    assert out[0]["aac"] == BLOCK


@pytest.mark.parametrize("change", [{"template": "R Narrow"}, {"filter": "G"}])
def test_take_rows_drops_the_block_when_filter_or_template_differ(change):
    p, remote_row = _plan_with_aac_row(**change)
    remote_row.pop("aac")
    out = merge.take_rows(p, {"filter_rows": [remote_row]})
    assert "aac" not in out[0]


@pytest.mark.parametrize("field,value", [("template", "R Narrow"), ("filter", "G")])
def test_pulling_a_filter_or_template_drops_the_block(field, value):
    p, remote_row = _plan_with_aac_row(**{field: value})
    merge.apply_auto_pull(p, {"filter_rows": [remote_row]}, [f"filter_rows.rs.{field}"])
    assert p["filter_rows"][0][field] == value
    assert "aac" not in p["filter_rows"][0]


def test_pulling_the_same_template_keeps_the_block():
    p, remote_row = _plan_with_aac_row()
    merge.apply_auto_pull(p, {"filter_rows": [remote_row]}, ["filter_rows.rs.template"])
    assert "aac" in p["filter_rows"][0]


# --- decision 3: a goal of 0 is a removed row --------------------------------


def _two_rows(goal_oiii):
    return sc.m42([sc.row("ha", "Ha", "Ha 300", 300, frames=4), sc.row("o", "OIII", "OIII 300", 300, frames=goal_oiii)])


def _plans(conn):
    return {r["name"]: (r["enabled"], r["desired"]) for r in conn.execute(
        "SELECT x.name, e.enabled, e.desired FROM exposureplan e JOIN exposuretemplate x ON x.Id = e.exposureTemplateId")}


def test_goal_zero_disables_the_plan_and_a_rise_enables_it_again(tmp_path):
    conn = _db(tmp_path)
    p = _two_rows(4)
    _push(conn, [p])
    p["filter_rows"][1]["target_frames"] = 0
    rep = _push(conn, [p])
    assert _plans(conn)["OIII 300"][0] == 0 and _plans(conn)["Ha 300"][0] == 1
    assert rep.disabled_plans == 1
    p["filter_rows"][1]["target_frames"] = 6
    _push(conn, [p])
    assert _plans(conn)["OIII 300"] == (1, 6)


def test_a_disabled_zero_goal_plan_no_longer_blocks_aac(tmp_path):
    conn = _db(tmp_path)
    p = _two_rows(4)
    _push(conn, [p])
    p["filter_rows"][1]["target_frames"] = 0
    for r in p["filter_rows"]:
        r["aac"] = copy.deepcopy(BLOCK)
    p["aac"] = {"filed_through_night": "2026-10-11", "min_total_frames": 3, "received_at": "x"}
    rep = _push(conn, [p])
    assert rep.aac_written_projects == ["M42"]


def test_a_skipped_project_says_why(tmp_path):
    conn = _db(tmp_path)
    p = _two_rows(4)
    p["filter_rows"][0]["aac"] = copy.deepcopy(BLOCK)
    p["aac"] = {"filed_through_night": "2026-10-11", "min_total_frames": 3, "received_at": "x"}
    rep = _push(conn, [p])
    assert rep.aac_written_projects == []
    assert "M42: AAC counts not written, OIII 300 has no AAC figures yet." in rep.notes


def test_a_project_aac_never_touched_gets_no_note(tmp_path):
    conn = _db(tmp_path)
    rep = _push(conn, [_two_rows(4)])
    assert not any("AAC" in n for n in rep.notes)


def test_a_plan_ts_holds_that_acp_does_not_names_itself(tmp_path):
    conn = _db(tmp_path)
    p = _two_rows(4)
    _push(conn, [p])
    # Drop the OIII row in ACP but leave its plan enabled, as a hand edit in TS would.
    p["filter_rows"] = p["filter_rows"][:1]
    for r in p["filter_rows"]:
        r["aac"] = copy.deepcopy(BLOCK)
    p["aac"] = {"filed_through_night": "2026-10-11", "min_total_frames": 3, "received_at": "x"}
    conn.execute("UPDATE exposureplan SET guid = 'hand' WHERE Id = (SELECT MAX(Id) FROM exposureplan)")
    conn.commit()
    p.pop("ts_links", None)
    rep = _push(conn, [p])
    assert rep.aac_written_projects == []
    assert any("is enabled in Target Scheduler but is not a row in ACP" in n for n in rep.notes), rep.notes


# --- decision 4: complete is judged against the goal AAC saw -----------------


def test_complete_counts_only_while_the_goal_is_not_above_the_one_sent():
    row = {"unit": "h", "target_hours": 2.0, "aac": {"complete": True, "goal_at_send": 0.5}}
    assert not fr.complete_counts(row)
    row["target_hours"] = 0.5
    assert fr.complete_counts(row)
    row["target_hours"] = 0.25
    assert fr.complete_counts(row)
    fr_row = {"unit": "fr", "target_frames": 49, "aac": {"complete": True, "goal_at_send": 48}}
    assert not fr.complete_counts(fr_row)


def test_a_block_without_goal_at_send_keeps_its_complete_flag():
    assert fr.complete_counts({"unit": "h", "target_hours": 9.0, "aac": {"complete": True}})
    assert not fr.complete_counts({"unit": "h", "target_hours": 9.0, "aac": {"complete": False}})


# --- decision 5: the host's lock is the extension's lock ---------------------


def test_register_adopts_the_hosts_plans_lock():
    from flask import Flask
    import nina_ts_sync
    original = im.PLANS_LOCK
    host_lock = threading.RLock()
    app = Flask("lock-test")
    app.plans_lock = host_lock
    try:
        nina_ts_sync.register(app)
        assert im.PLANS_LOCK is host_lock
        from nina_ts_sync.api import _holds_plans_lock
        seen = []

        @_holds_plans_lock
        def route():
            seen.append(True)
            return host_lock.acquire(blocking=False)

        # An RLock lets the same thread in, so check from another thread that
        # the route really holds it while it runs.
        started, release, held = threading.Event(), threading.Event(), []

        @_holds_plans_lock
        def slow():
            started.set()
            release.wait(2)

        t = threading.Thread(target=slow)
        t.start()
        assert started.wait(2)
        probe = threading.Thread(target=lambda: held.append(host_lock.acquire(blocking=False)))
        probe.start()
        probe.join()
        release.set()
        t.join()
        assert held == [False]
    finally:
        im.PLANS_LOCK = original
