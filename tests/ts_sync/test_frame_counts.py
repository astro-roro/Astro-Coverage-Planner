"""A push must not overwrite what the camera and the grader recorded.

`exposureplan.acquired` is how many subs were taken and `accepted` is the
grader's verdict on them. ACP has no business stating either on a row that
already exists, so both are written when a row is created and never on an
update. `project.createdate` goes the same way: a creation date that tracks
the last push is wrong on its face. Same for `project.flatsHandling`: it is
Target Scheduler's setting, so a change made there must survive the next
push.
"""
from __future__ import annotations

import sqlite3
import types

import pytest

from nina_ts_sync import convert, upsert
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, three_plans, row

FROZEN_NOW = 1_700_000_000
LATER = 1_800_000_000


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(convert, "time", types.SimpleNamespace(time=lambda: FROZEN_NOW))


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _push(conn):
    report = upsert.apply(
        conn, convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID))
    conn.commit()
    return report


def test_a_night_of_imaging_survives_the_next_push(tmp_path, frozen_clock):
    """The scenario the audit ran by hand: push, image, push the same plans."""
    conn = _connect(make_db(28, tmp_path / "counts.sqlite"))
    try:
        _push(conn)

        # A night happens. Target Scheduler records what the camera took and
        # what the grader kept.
        conn.execute("UPDATE exposureplan SET acquired = 137, accepted = 120 WHERE Id = 1")
        conn.execute("UPDATE exposureplan SET acquired = 40, accepted = 0 WHERE Id = 2")
        conn.commit()

        report = _push(conn)
        assert report.exposureplan.updated == 7
        assert report.exposureplan.inserted == 0

        rows = {r["Id"]: r for r in conn.execute("SELECT * FROM exposureplan")}
        assert (rows[1]["acquired"], rows[1]["accepted"]) == (137, 120)
        assert (rows[2]["acquired"], rows[2]["accepted"]) == (40, 0)
    finally:
        conn.close()


def test_a_brand_new_row_still_gets_its_initial_counts(tmp_path, frozen_clock):
    """Insert is the one place ACP does state the counts."""
    conn = _connect(make_db(28, tmp_path / "fresh.sqlite"))
    try:
        plans = three_plans()
        # Four hours already in the bag on the two-filter plan's Ha goal, at
        # 600 s a sub, so ACP computes 24 acquired on a row it is creating.
        row(plans[2], "Ha")["actual_hours"] = 4.0
        upsert.apply(conn, convert.build_payload(plans, GEAR, profile_id=PROFILE_ID))
        conn.commit()

        acquired = [r["acquired"] for r in conn.execute("SELECT * FROM exposureplan")]
        assert 24 in acquired
        assert sum(acquired) == 24
    finally:
        conn.close()


def test_the_push_still_updates_everything_it_is_meant_to(tmp_path, frozen_clock):
    """Only the insert-only columns are held back, not the whole row.

    `exposure` joined them with filter rows (goal-panel-and-ts-order.md
    section 3): a sub length set in TS stays, because the row's template
    did not change."""
    conn = _connect(make_db(28, tmp_path / "settings.sqlite"))
    try:
        _push(conn)
        conn.execute("UPDATE exposureplan SET desired = 1, exposure = 5 WHERE Id = 1")
        conn.execute("UPDATE project SET minimumaltitude = 0 WHERE Id = 1")
        conn.commit()

        _push(conn)
        plan_row = conn.execute("SELECT * FROM exposureplan WHERE Id = 1").fetchone()
        assert plan_row["desired"] == 12 and plan_row["exposure"] == 5.0
        project = conn.execute("SELECT * FROM project WHERE Id = 1").fetchone()
        assert project["minimumaltitude"] == 30.0
    finally:
        conn.close()


def test_createdate_is_the_creation_date_not_the_last_push(tmp_path, monkeypatch):
    conn = _connect(make_db(28, tmp_path / "created.sqlite"))
    try:
        monkeypatch.setattr(
            convert, "time", types.SimpleNamespace(time=lambda: FROZEN_NOW))
        _push(conn)
        assert {r["createdate"] for r in conn.execute("SELECT * FROM project")} == {
            FROZEN_NOW
        }

        monkeypatch.setattr(convert, "time", types.SimpleNamespace(time=lambda: LATER))
        _push(conn)
        assert {r["createdate"] for r in conn.execute("SELECT * FROM project")} == {
            FROZEN_NOW
        }
    finally:
        conn.close()


def test_flats_handling_changed_in_ts_survives_the_next_push(tmp_path, frozen_clock):
    """flatsHandling is a Target Scheduler setting, not ACP's to restate."""
    conn = _connect(make_db(28, tmp_path / "flats.sqlite"))
    try:
        _push(conn)
        assert {r["flatsHandling"] for r in conn.execute("SELECT * FROM project")} == {
            1
        }

        conn.execute("UPDATE project SET flatsHandling = 0 WHERE Id = 1")
        conn.commit()

        _push(conn)
        row = conn.execute("SELECT * FROM project WHERE Id = 1").fetchone()
        assert row["flatsHandling"] == 0
    finally:
        conn.close()


def test_claiming_a_hand_made_row_leaves_its_counts_alone(tmp_path, frozen_clock):
    """A claim is an update of a row that already exists, so the same rule."""
    conn = _connect(make_db(28, tmp_path / "claim.sqlite"))
    try:
        _push(conn)
        # Strip the guid off one plan row and give it a night's work, which is
        # the shape of a row Target Scheduler left unstamped.
        conn.execute(
            "UPDATE exposureplan SET guid = '', acquired = 55, accepted = 50 WHERE Id = 1")
        conn.commit()

        report = _push(conn)
        assert report.exposureplan.claimed == 1
        row = conn.execute("SELECT * FROM exposureplan WHERE Id = 1").fetchone()
        assert (row["acquired"], row["accepted"]) == (55, 50)
        assert row["guid"]
    finally:
        conn.close()
