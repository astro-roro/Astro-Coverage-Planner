"""Rows written under the old identity recipe are restamped, not orphaned.

The fixture is built by pushing with the current code and then winding every
guid back to the pre-length-prefix recipe, which is spelled out here as a
literal f-string rather than imported, so the test states the old shape
rather than trusting the code under test to describe it.
"""
from __future__ import annotations

import sqlite3
import types
import uuid

import pytest

from nina_ts_sync import convert, upsert
from nina_ts_sync.schema import ACP_NS
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, three_plans

FROZEN_NOW = 1_700_000_000
CAMERA_ID = "cam-1"

TABLES = ("exposuretemplate", "project", "target", "exposureplan")


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(convert, "time", types.SimpleNamespace(time=lambda: FROZEN_NOW))


def _old(name: str) -> str:
    """The recipe as it stood before the length prefix arrived."""
    return str(uuid.uuid5(ACP_NS, name))


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _push(conn):
    report = upsert.apply(
        conn, convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID))
    conn.commit()
    return report


def _wind_back_to_the_old_recipe(conn, profile_id=PROFILE_ID):
    """Restamp every ACP row the way the pre-fix code would have stamped it."""
    for row in conn.execute("SELECT Id, filtername FROM exposuretemplate").fetchall():
        conn.execute(
            "UPDATE exposuretemplate SET guid = ? WHERE Id = ?",
            (_old(f"{profile_id}/template/{row['filtername']}/{CAMERA_ID}"), row["Id"]),
        )
    for row in conn.execute("SELECT Id, name FROM project").fetchall():
        conn.execute(
            "UPDATE project SET guid = ? WHERE Id = ?",
            (_old(f"{profile_id}/project/{row['name']}"), row["Id"]),
        )
    targets = conn.execute(
        "SELECT t.Id, t.name, p.name AS pname FROM target t "
        "JOIN project p ON p.Id = t.projectid"
    ).fetchall()
    old_target_guid_by_id = {}
    for row in targets:
        guid = _old(f"{profile_id}/target/{row['pname']}/{row['name']}")
        old_target_guid_by_id[row["Id"]] = guid
        conn.execute("UPDATE target SET guid = ? WHERE Id = ?", (guid, row["Id"]))
    plans = conn.execute(
        "SELECT e.Id, e.targetid, x.filtername FROM exposureplan e "
        "JOIN exposuretemplate x ON x.Id = e.exposureTemplateId"
    ).fetchall()
    for row in plans:
        parent = old_target_guid_by_id[row["targetid"]]
        conn.execute(
            "UPDATE exposureplan SET guid = ? WHERE Id = ?",
            (_old(f"{profile_id}/plan/{parent}/{row['filtername']}"), row["Id"]),
        )
    conn.commit()


def _guids(conn, table):
    return {r["Id"]: r["guid"] for r in conn.execute(f"SELECT Id, guid FROM {table}")}


def _counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def _old_recipe_db(tmp_path, name="legacy.sqlite"):
    """A database exactly as the pre-fix code would have left it."""
    conn = _connect(make_db(28, tmp_path / name))
    _push(conn)
    _wind_back_to_the_old_recipe(conn)
    return conn


def test_the_first_push_restamps_every_acp_row(tmp_path, frozen_clock):
    conn = _old_recipe_db(tmp_path)
    try:
        before = _counts(conn)
        expected = {t: dict(_guids(conn, t)) for t in TABLES}

        report = _push(conn)

        # 3 projects + 6 targets + 7 plans. Templates are found by name and
        # used as they are, so their guids are never rewritten
        # (goal-panel-and-ts-order.md section 3, "Templates").
        assert report.migrated_guids == 16
        assert report.notes == []
        # Nothing new was inserted, so nothing was orphaned beside the old tree.
        assert _counts(conn) == before
        for table in TABLES:
            assert report_table(report, table).inserted == 0
            now = _guids(conn, table)
            assert set(now) == set(expected[table]), f"{table}: rows appeared or went"
            for row_id, guid in now.items():
                if table == "exposuretemplate":
                    assert guid == expected[table][row_id], f"template {row_id} restamped"
                else:
                    assert guid != expected[table][row_id], f"{table} {row_id} not restamped"
    finally:
        conn.close()


def test_the_restamped_guids_are_the_ones_the_push_would_write(tmp_path, frozen_clock):
    conn = _old_recipe_db(tmp_path, "match.sqlite")
    try:
        _push(conn)
        payload = convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID)
        wanted = (
            {p.guid for p in payload.projects}
            | {t.guid for ts in payload.targets_by_project_guid.values() for t in ts}
            | {p.guid for ps in payload.plans_by_target_guid.values() for p in ps}
        )
        on_disk = set()
        for table in TABLES:
            if table != "exposuretemplate":
                on_disk |= set(_guids(conn, table).values())
        assert on_disk == wanted
    finally:
        conn.close()


def test_no_row_is_left_pointing_at_a_parent_that_moved(tmp_path, frozen_clock):
    conn = _old_recipe_db(tmp_path, "orphans.sqlite")
    try:
        _push(conn)
        orphan_targets = conn.execute(
            "SELECT COUNT(*) FROM target t "
            "LEFT JOIN project p ON p.Id = t.projectid WHERE p.Id IS NULL"
        ).fetchone()[0]
        orphan_plans = conn.execute(
            "SELECT COUNT(*) FROM exposureplan e "
            "LEFT JOIN target t ON t.Id = e.targetid "
            "LEFT JOIN exposuretemplate x ON x.Id = e.exposureTemplateId "
            "WHERE t.Id IS NULL OR x.Id IS NULL"
        ).fetchone()[0]
        assert orphan_targets == 0 and orphan_plans == 0
        childless = conn.execute(
            "SELECT COUNT(*) FROM project p WHERE NOT EXISTS "
            "(SELECT 1 FROM target t WHERE t.projectid = p.Id)"
        ).fetchone()[0]
        assert childless == 0
    finally:
        conn.close()


def test_a_row_that_is_not_ours_is_left_alone(tmp_path, frozen_clock):
    conn = _old_recipe_db(tmp_path, "foreign.sqlite")
    try:
        stranger = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO project (profileId, name, guid, state) VALUES (?, ?, ?, 1)",
            (PROFILE_ID, "Made in Target Scheduler", stranger),
        )
        conn.commit()

        _push(conn)
        row = conn.execute(
            "SELECT * FROM project WHERE name = 'Made in Target Scheduler'"
        ).fetchone()
        assert row["guid"] == stranger
    finally:
        conn.close()


def test_a_second_push_migrates_nothing_and_changes_nothing(tmp_path, frozen_clock):
    conn = _old_recipe_db(tmp_path, "idem.sqlite")
    try:
        _push(conn)
        after_first = {t: dict(_guids(conn, t)) for t in TABLES}
        counts = _counts(conn)

        report = _push(conn)
        assert report.migrated_guids == 0
        assert _counts(conn) == counts
        assert {t: dict(_guids(conn, t)) for t in TABLES} == after_first
    finally:
        conn.close()


def test_migration_runs_inside_the_push_transaction(tmp_path, frozen_clock):
    """A failure after the restamping takes the restamping back with it."""
    conn = _old_recipe_db(tmp_path, "rollback.sqlite")
    try:
        before = {t: dict(_guids(conn, t)) for t in TABLES}
        payload = convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID)
        # A target group pointing at a project guid that is in the payload, but
        # with the project itself removed, is a note rather than a throw, so
        # break the write instead: a name column that cannot take a value.
        payload.projects[0].name = ExplodingValue()

        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(Exception):
            upsert.apply(conn, payload)
        conn.execute("ROLLBACK")

        assert {t: dict(_guids(conn, t)) for t in TABLES} == before
    finally:
        conn.close()


class ExplodingValue:
    """Not a type sqlite3 can bind, so the INSERT raises."""

    def __repr__(self) -> str:
        return "<unbindable>"


def report_table(report, table):
    return getattr(report, table)
