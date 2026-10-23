"""AAC's accepted counts reach Target Scheduler, tests A3 to A5.

docs/specs/goal-panel-and-ts-order.md section 6 and AAC's
incremental-master-builds.md section 7. Every case in
tests/fixtures/aac_accepted_cases.json runs here, in the plugin's C# suite
and (for the desired column) in ACP. A case seeds the exposure plans AAC
would have found, pushes the plan with its AAC figures, and checks `accepted`,
`desired`, `enablegrader` and the push result's notes. `acquired` must never
move.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from nina_ts_sync import convert, rows as fr, upsert
from . import row_scenarios as sc
from .make_db import make_db
from .plans import PROFILE_ID, plan

DOC = json.loads((Path(__file__).parent / "fixtures" / "aac_accepted_cases.json").read_text(encoding="utf-8"))
CASES = DOC["cases"]


def _connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _acp_rows(case: dict, *, with_aac: bool) -> list[dict]:
    out = []
    for i, r in enumerate(case["rows"]):
        row = {k: v for k, v in r.items() if k not in ("ts", "expected", "aac")}
        row["id"] = f"row{i}"
        if with_aac and r.get("aac") is not None:
            row["aac"] = copy.deepcopy(r["aac"])
        out.append(row)
    return out


def _plan(case: dict, *, with_aac: bool) -> dict:
    kw = {"cols": 2, "rows": 1} if case["plan"].get("mosaic") else {}
    p = plan("m42", project_name=sc.M42, target_name=sc.M42, filter_rows=_acp_rows(case, with_aac=with_aac), **kw)
    if with_aac:
        p["aac"] = {"filed_through_night": case["plan"]["filed_through_night"],
                    "min_total_frames": case["plan"]["min_total_frames"],
                    "received_at": "2026-10-12T10:05:00+00:00"}
    return p


def _run(case: dict, tmp_path):
    conn = _connect(make_db(28, tmp_path / "ts.sqlite"))
    prefs = {**DOC["default_profile"], **case["profile"]}
    cols = ", ".join(prefs)
    conn.execute(f"INSERT INTO profilepreference (profileId, {cols}) VALUES (?, {', '.join('?' for _ in prefs)})",
                 (PROFILE_ID, *prefs.values()))
    conn.commit()
    # What TS holds before: a push with no AAC figures, then the counts the case names.
    sc.push(conn, [_plan(case, with_aac=False)])
    for r in case["rows"]:
        conn.execute(
            "UPDATE exposureplan SET desired = ?, acquired = ?, accepted = ? WHERE exposureTemplateId IN "
            "(SELECT Id FROM exposuretemplate WHERE name = ?)",
            (r["ts"]["desired"], r["ts"]["acquired"], r["ts"]["accepted"], r["template"]))
    conn.execute("UPDATE project SET enablegrader = ?", (case["ts"]["enablegrader"],))
    project_id = conn.execute("SELECT Id FROM project").fetchone()[0]
    target_id = conn.execute("SELECT Id FROM target ORDER BY Id").fetchone()[0]
    for stamp in case["ts"]["acquired_images_utc"]:
        when = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
        conn.execute(
            "INSERT INTO acquiredimage (projectId, targetId, acquireddate, filtername, gradingStatus, metadata) "
            "VALUES (?, ?, ?, 'Ha', 1, '{}')", (project_id, target_id, when))
    conn.commit()
    acquired_before = {r["Id"]: r["acquired"] for r in conn.execute("SELECT Id, acquired FROM exposureplan")}

    payload = convert.build_payload([_plan(case, with_aac=True)], sc.ROW_GEAR, profile_id=PROFILE_ID,
                                    now=datetime.fromisoformat(case["now"]))
    report = upsert.apply(conn, payload)
    conn.commit()
    return conn, report, acquired_before


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_a3_to_a5_the_shared_cases(case, tmp_path):
    conn, report, acquired_before = _run(case, tmp_path)
    for r in case["rows"]:
        got = conn.execute(
            "SELECT e.desired, e.accepted FROM exposureplan e JOIN exposuretemplate x ON x.Id = e.exposureTemplateId "
            "WHERE x.name = ?", (r["template"],)).fetchall()
        assert got, r["template"]
        for g in got:  # a mosaic has one plan per panel
            assert (g["desired"], g["accepted"]) == (r["expected"]["desired"], r["expected"]["accepted"]), r["template"]
    assert conn.execute("SELECT enablegrader FROM project").fetchone()[0] == case["expected"]["enablegrader"]
    assert {r["Id"]: r["acquired"] for r in conn.execute("SELECT Id, acquired FROM exposureplan")} == acquired_before
    warning = case["expected"]["warning"]
    if warning:
        assert any(warning in n for n in report.notes), report.notes
    else:
        assert not any("AAC" in n for n in report.notes), report.notes
    assert bool(report.aac_written_projects) == case["expected"]["written"]


def test_a3_the_fixture_has_the_t8_cases():
    names = {c["name"] for c in CASES}
    assert {"ha_shortfall_grader_on", "dolphin_oiii_grader_switches_on", "mosaic_writes_nothing",
            "rerun_at_23_00_after_tonights_frames", "new_sii_row_with_zero_figures"} <= names


def test_a3_desired_follows_the_shared_floor_rule():
    for case in CASES:
        p = _plan(case, with_aac=True)
        for src, row in zip(case["rows"], fr.rows_of(p)):
            assert fr.desired_for(row, p) == src["expected"]["desired"], (case["name"], row["template"])


def test_a4_a_plan_without_figures_in_the_project_blocks_the_write(tmp_path):
    case = next(c for c in CASES if c["name"] == "ha_shortfall_grader_on")
    conn, _, _ = _run(case, tmp_path)
    conn.execute("UPDATE exposureplan SET accepted = 5")
    conn.execute("UPDATE project SET enablegrader = 0")
    conn.commit()
    # A second plan in the same project, with no AAC block, rules the project out.
    other = plan("m43", project_name=sc.M42, target_name="M43",
                 filter_rows=[sc.row("x", "Ha", "Ha 300", 300, frames=5)])
    payload = convert.build_payload([_plan(case, with_aac=True), other], sc.ROW_GEAR, profile_id=PROFILE_ID,
                                    now=datetime.fromisoformat(case["now"]))
    report = upsert.apply(conn, payload)
    assert report.aac_written_projects == []
    assert conn.execute("SELECT enablegrader FROM project").fetchone()[0] == 0
    assert [r[0] for r in conn.execute(
        "SELECT e.accepted FROM exposureplan e JOIN target t ON t.Id = e.targetid WHERE t.name = ?", (sc.M42,))] == [5]
