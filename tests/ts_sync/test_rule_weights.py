"""Rule weights: the names Target Scheduler looks up, and healing the old ones.

On 2026-10-09 a sync seeded five internal names (PercentComplete and so on) on
every project it inserted. TS looks weights up by each rule's display name, so
planning threw "The given key 'Meridian Flip Penalty' was not present in the
dictionary" for every target until NINA restarted.
"""
from __future__ import annotations

import sqlite3

from nina_ts_sync import convert, upsert
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, three_plans

TS_RULE_NAMES = {
    "Percent Complete", "Project Priority", "Setting Soonest", "Meridian Window Priority",
    "Target Switch Penalty", "Mosaic Completion", "Smart Exposure Order", "Meridian Flip Penalty",
}


def _open(tmp_path, name="rules.sqlite"):
    conn = sqlite3.connect(make_db(28, tmp_path / name))
    conn.row_factory = sqlite3.Row
    return conn


def _push(conn):
    report = upsert.apply(conn, convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID))
    conn.commit()
    return report


def _names(conn, pid):
    return {r["name"]: r["weight"] for r in conn.execute(
        "SELECT name, weight FROM ruleweight WHERE projectid = ?", (pid,))}


def test_defaults_are_the_eight_ts_rules_at_ts_defaults():
    assert set(convert.DEFAULT_RULE_WEIGHTS) == TS_RULE_NAMES
    assert convert.DEFAULT_RULE_WEIGHTS["Meridian Window Priority"] == 75.0
    assert convert.DEFAULT_RULE_WEIGHTS["Target Switch Penalty"] == 67.0
    assert not (set(convert.DEFAULT_RULE_WEIGHTS) & convert.LEGACY_RULE_NAMES)


def test_new_project_gets_the_eight_ts_names(tmp_path):
    conn = _open(tmp_path)
    try:
        report = _push(conn)
        assert report.ruleweight_seeded_projects == 3
        for (pid,) in conn.execute("SELECT Id FROM project").fetchall():
            assert set(_names(conn, pid)) == TS_RULE_NAMES
    finally:
        conn.close()


def test_legacy_names_are_replaced_and_tuned_weights_survive(tmp_path):
    """Two broken shapes seen on Voyager: five legacy rows only (projects a bad
    sync inserted), and the eight real rows plus the five legacy ones (a bad
    sync followed by a NINA restart, which adds the real names)."""
    conn = _open(tmp_path)
    try:
        _push(conn)
        pids = [r[0] for r in conn.execute("SELECT Id FROM project ORDER BY Id")]
        legacy_only, mixed = pids[0], pids[1]
        conn.execute("DELETE FROM ruleweight WHERE projectid = ?", (legacy_only,))
        for name in convert.LEGACY_RULE_NAMES:
            conn.execute("INSERT INTO ruleweight (projectid, name, weight) VALUES (?, ?, 50)",
                         (legacy_only, name))
            conn.execute("INSERT INTO ruleweight (projectid, name, weight) VALUES (?, ?, 50)",
                         (mixed, name))
        conn.execute("UPDATE ruleweight SET weight = 99 WHERE projectid = ? AND name = ?",
                     (mixed, "Percent Complete"))
        conn.commit()

        report = _push(conn)

        assert _names(conn, legacy_only) == convert.DEFAULT_RULE_WEIGHTS
        healed = _names(conn, mixed)
        assert set(healed) == TS_RULE_NAMES
        assert healed["Percent Complete"] == 99
        assert sum("legacy rule weight" in n for n in report.notes) == 2
    finally:
        conn.close()


def test_hand_made_project_is_left_alone(tmp_path):
    """A project the sync claims by name but that has no legacy rows keeps
    exactly what it had, even an empty set."""
    conn = _open(tmp_path)
    try:
        payload = convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID)
        name = payload.projects[0].name
        conn.execute("INSERT INTO project (profileId, name, state, priority) VALUES (?, ?, 1, 1)",
                     (PROFILE_ID, name))
        conn.commit()
        _push(conn)
        pid = conn.execute("SELECT Id FROM project WHERE name = ?", (name,)).fetchone()[0]
        assert _names(conn, pid) == {}
    finally:
        conn.close()
