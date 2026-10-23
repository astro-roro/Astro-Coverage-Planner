"""Reading acquired counts back out of a 28 database."""
from __future__ import annotations

import sqlite3

import pytest

from nina_ts_sync import convert, upsert
from nina_ts_sync.reader import read_acquired, read_all
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, three_plans


@pytest.fixture
def seeded_28(tmp_path):
    """A 28 database with the three plans pushed and exposure counts filled in.

    Counts are set straight on the rows the way Target Scheduler would after
    a few nights: `acquired` is everything shot, `accepted` is what survived
    grading.
    """
    path = make_db(28, tmp_path / "schedulerdb.sqlite")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    upsert.apply(conn, convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID))
    for i, row in enumerate(
        conn.execute("SELECT Id FROM exposureplan ORDER BY Id").fetchall()
    ):
        conn.execute(
            "UPDATE exposureplan SET acquired = ?, accepted = ? WHERE Id = ?",
            (10 + i, 8 + i, row["Id"]),
        )
    conn.commit()
    yield conn, path
    conn.close()


def test_read_acquired_returns_a_count_and_exposure_per_plan(seeded_28):
    conn, _ = seeded_28
    acquired = read_acquired(conn, PROFILE_ID)
    assert len(acquired) == 7

    # A pushed plan carries exposure -1 ("use the template's default"), so
    # the length comes from its template.
    expected = {}
    for i, row in enumerate(
        conn.execute(
            "SELECT e.guid, e.exposure, x.defaultexposure FROM exposureplan e "
            "JOIN exposuretemplate x ON x.Id = e.exposureTemplateId ORDER BY e.Id"
        ).fetchall()
    ):
        assert row["exposure"] == -1.0
        expected[row["guid"]] = (10 + i, float(row["defaultexposure"]))
    assert acquired == expected


def test_read_acquired_prefers_accepted_when_the_grader_is_on(seeded_28):
    conn, _ = seeded_28
    conn.execute("UPDATE project SET enablegrader = 1 WHERE name = 'Two Filters'")
    conn.commit()

    acquired = read_acquired(conn, PROFILE_ID)
    graded_guids = {
        r["guid"] for r in conn.execute(
            "SELECT ep.guid AS guid FROM exposureplan ep "
            "JOIN target t ON ep.targetid = t.Id "
            "JOIN project p ON t.projectid = p.Id WHERE p.name = 'Two Filters'"
        )
    }
    assert len(graded_guids) == 2
    for guid in graded_guids:
        row = conn.execute(
            "SELECT acquired, accepted FROM exposureplan WHERE guid = ?", (guid,)
        ).fetchone()
        assert acquired[guid][0] == row["accepted"]
        assert acquired[guid][0] != row["acquired"]


def test_read_acquired_skips_plans_with_no_guid(seeded_28):
    conn, _ = seeded_28
    conn.execute("UPDATE exposureplan SET guid = '' WHERE Id = (SELECT MIN(Id) FROM exposureplan)")
    conn.commit()
    assert len(read_acquired(conn, PROFILE_ID)) == 6


def test_read_acquired_is_scoped_to_the_profile(seeded_28):
    conn, _ = seeded_28
    assert read_acquired(conn, "some-other-profile") == {}


def test_read_all_round_trips_the_pushed_rows(seeded_28):
    conn, _ = seeded_28
    snap = read_all(conn, PROFILE_ID)
    assert len(snap.projects_by_id) == 3
    assert len(snap.targets_by_id) == 6
    assert len(snap.plans_by_id) == 7
    assert len(snap.templates_by_id) == 2
    # `target.priority` exists at 28 and we write the TS default.
    assert {t.priority for t in snap.targets_by_id.values()} == {-1}


def test_read_all_tolerates_a_23_database(tmp_path):
    """The column `target.priority` is absent at 23; the default stands in."""
    path = make_db(23, tmp_path / "v23.sqlite")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        upsert.apply(conn, convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID))
        conn.commit()
        snap = read_all(conn, PROFILE_ID)
        assert len(snap.targets_by_id) == 6
        assert {t.priority for t in snap.targets_by_id.values()} == {-1}
        assert len(read_acquired(conn, PROFILE_ID)) == 7
    finally:
        conn.close()
