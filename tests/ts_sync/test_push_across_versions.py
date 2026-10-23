"""Push the same three plans into a 23 and a 28 database and compare.

Only the columns that genuinely differ between the two schemas may differ in
the result. Between 23 and 28 that is exactly one: `target.priority`, added by
Migrate/24.sql. Everything else must be byte-for-byte the same, which is the
real guard against a version-aware write path quietly changing behaviour on
the databases that already worked.
"""
from __future__ import annotations

import sqlite3
import types

import pytest

from nina_ts_sync import convert, upsert
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, three_plans

TABLES = ("exposuretemplate", "project", "target", "exposureplan", "ruleweight")

#: table -> columns allowed to be present in one schema and not the other.
#: Keep this list honest: anything added here is a documented schema delta.
SCHEMA_ONLY_COLUMNS = {"target": {"priority"}}

FROZEN_NOW = 1_700_000_000


@pytest.fixture
def frozen_clock(monkeypatch):
    """convert.build_payload stamps `createdate` from the wall clock."""
    monkeypatch.setattr(convert, "time", types.SimpleNamespace(time=lambda: FROZEN_NOW))


def _push(version, tmp_path):
    path = make_db(version, tmp_path / f"v{version}.sqlite")
    payload = convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        report = upsert.apply(conn, payload)
        conn.commit()
        return path, report, _dump(conn)
    finally:
        conn.close()


def _dump(conn):
    """Every row of every table we write, as dicts, ordered by Id."""
    out = {}
    for table in TABLES:
        out[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY Id")]
    return out


def test_push_writes_the_same_rows_at_23_and_28(tmp_path, frozen_clock):
    _, report23, rows23 = _push(23, tmp_path)
    _, report28, rows28 = _push(28, tmp_path)

    assert report23.to_dict() == report28.to_dict()

    for table in TABLES:
        a, b = rows23[table], rows28[table]
        assert len(a) == len(b), f"{table}: row count differs"
        for i, (ra, rb) in enumerate(zip(a, b)):
            allowed = SCHEMA_ONLY_COLUMNS.get(table, set())
            assert set(ra) - set(rb) == set(), f"{table}[{i}]: v23 has extra columns"
            assert set(rb) - set(ra) == allowed, f"{table}[{i}]: unexpected new columns at 28"
            shared = {k: v for k, v in ra.items() if k not in allowed}
            assert shared == {k: v for k, v in rb.items() if k not in allowed}, (
                f"{table}[{i}]: shared column values differ"
            )


def test_push_at_28_writes_the_ts_default_for_target_priority(tmp_path, frozen_clock):
    _, _, rows28 = _push(28, tmp_path)
    assert rows28["target"], "no targets written"
    # Migrate/24.sql declares `priority INTEGER DEFAULT -1`; ACP has no
    # target-level priority to map, so we write that default explicitly.
    assert {r["priority"] for r in rows28["target"]} == {-1}


def test_push_at_23_omits_target_priority_entirely(tmp_path, frozen_clock):
    path, _, _ = _push(23, tmp_path)
    conn = sqlite3.connect(path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(target)")]
    finally:
        conn.close()
    assert "priority" not in cols


def test_push_shape_matches_the_three_plans(tmp_path, frozen_clock):
    _, report, rows = _push(28, tmp_path)
    # Three project names, so three projects.
    assert {r["name"] for r in rows["project"]} == {"Single Target", "Mosaic", "Two Filters"}
    # Single target, 2x2 mosaic expanded to four panels, two-filter plan.
    assert len(rows["target"]) == 6
    assert sum(1 for r in rows["target"] if r["name"].startswith("M31 Panel ")) == 4
    # Two templates: OIII and Ha, deduped per (filter, camera).
    assert {r["filtername"] for r in rows["exposuretemplate"]} == {"OIII", "Ha"}
    assert len(rows["exposuretemplate"]) == 2
    # One plan per (target, filter): 1 + 4 + 2.
    assert len(rows["exposureplan"]) == 7
    assert report.exposureplan.inserted == 7


def test_push_is_idempotent_at_every_supported_version(tmp_path):
    """A second run updates in place rather than duplicating rows."""
    for version in (23, 24, 25, 26, 27, 28):
        path = make_db(version, tmp_path / f"idem{version}.sqlite")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            first = upsert.apply(
                conn, convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID))
            second = upsert.apply(
                conn, convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID))
            conn.commit()
            assert first.target.inserted == 6 and first.exposureplan.inserted == 7
            assert second.target.inserted == 0 and second.exposureplan.inserted == 0
            assert second.target.updated == 6 and second.exposureplan.updated == 7
            for table, expected in (("target", 6), ("exposureplan", 7)):
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert n == expected, f"v{version}: {table} duplicated"
        finally:
            conn.close()
