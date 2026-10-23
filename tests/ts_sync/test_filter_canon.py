"""TS filter names on import: canonicalised to ACP's spelling.

TS holds filter names however the wheel or Voyager spells them ("O", "H",
"S"); ACP's own plans key their goals by its canonical spelling ("OIII",
"Ha", "SII"). Before this fix the import kept TS's raw spelling as the goal
key, so a plan pushed to ACP as "OIII" came back from TS as a second, phantom
"O" goal on the next sync (the PNe Survey case: ACP has "OIII: 1.5 h", TS has
"O: 1.5 h, 300 s", and the review page showed both).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nina_ts_sync.filter_canon import FILTER_CANON, canonical_filter_name
from nina_ts_sync.from_ts import import_to_acp_plans
from nina_ts_sync.reader import read_all
from .make_db import make_db
from .plans import GEAR

PROFILE = "profile-canon"


# -- the table itself ---------------------------------------------------


@pytest.mark.parametrize("raw,expected", sorted(FILTER_CANON.items()))
def test_every_alias_canonicalises(raw, expected):
    assert canonical_filter_name(raw) == expected
    # Matched case-insensitively, same as the plugin and ACP's scanner.
    assert canonical_filter_name(raw.lower()) == expected


def test_unknown_name_passes_through():
    assert canonical_filter_name("R Stars") == "R Stars"
    assert canonical_filter_name("  Antlia Ha  ") == "Antlia Ha"


def test_empty_and_none_pass_through():
    assert canonical_filter_name("") == ""
    assert canonical_filter_name(None) == ""


# -- building a TS database directly, for full control over filtername --


def _make_ts_db(path: Path, *, project_name: str, target_name: str,
                 filters: list[tuple[str, str, int, float]]) -> None:
    """A TS database with one project, one target, and one exposure plan per
    (template name, filtername, desired, sub_s) tuple in *filters*."""
    make_db(28, path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO project (Id, profileId, name, description, state, priority, "
            "createdate, activedate, inactivedate, minimumtime, minimumaltitude, "
            "usecustomhorizon, horizonoffset, meridianwindow, filterswitchfrequency, "
            "ditherevery, enablegrader, isMosaic, flatsHandling, maximumAltitude, "
            "smartexposureorder, guid) VALUES "
            "(1, ?, ?, '', 1, 1, 0, NULL, NULL, 0, 20.0, 0, 0.0, 0, 0, 0, 0, 0, 0, 0.0, 0, ?)",
            (PROFILE, project_name, f"guid-{project_name}"))
        conn.execute(
            "INSERT INTO target (Id, name, active, ra, dec, epochcode, rotation, roi, "
            "projectid, unusedOEO, guid) VALUES (1, ?, 1, 10.0, -20.0, 2, 0.0, 100.0, 1, NULL, ?)",
            (target_name, f"guid-{target_name}"))
        for i, (tpl_name, filtername, desired, sub_s) in enumerate(filters, start=1):
            conn.execute(
                "INSERT INTO exposuretemplate (Id, profileId, name, filtername, gain, "
                "offset, bin, readoutmode, twilightlevel, moonavoidanceenabled, "
                "moonavoidanceseparation, moonavoidancewidth, maximumhumidity, "
                "defaultexposure, moonrelaxscale, moonrelaxmaxaltitude, "
                "moonrelaxminaltitude, moondownenabled, ditherevery, minutesOffset, guid) "
                "VALUES (?, ?, ?, ?, 0, 25, 1, -1, 0, 1, 120.0, 7, 0.0, ?, 2.0, 60.0, -15.0, "
                "0, 2, 0, ?)",
                (i, PROFILE, tpl_name, filtername, float(sub_s), f"guid-tpl-{i}"))
            conn.execute(
                "INSERT INTO exposureplan (Id, profileId, exposure, desired, acquired, "
                "accepted, targetid, exposureTemplateId, enabled, guid) VALUES "
                "(?, ?, ?, ?, 0, 0, 1, ?, 1, ?)",
                (i, PROFILE, float(sub_s), desired, i, f"guid-ep-{i}"))
        conn.commit()
    finally:
        conn.close()


def _plans_for(path: Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        snap = read_all(conn, PROFILE)
    finally:
        conn.close()
    return import_to_acp_plans(snap, GEAR)


def _pairs(plan) -> list[tuple[str, str]]:
    return [(r["filter"], r["template"]) for r in plan["filter_rows"]]


# -- a new plan arriving with short filter names -------------------------


def test_new_plan_o_h_s_arrive_as_oiii_ha_sii(tmp_path):
    path = tmp_path / "ts.sqlite"
    _make_ts_db(
        path, project_name="New Nebula", target_name="NGC X",
        filters=[("O", "O", 18, 300), ("H", "H", 18, 300), ("S", "S", 18, 300)])
    plans, notes, _ = _plans_for(path)
    (plan,) = plans
    assert _pairs(plan) == [("OIII", "O"), ("Ha", "H"), ("SII", "S")]
    assert [n for n in notes if n.level == "loss"] == []


def test_an_older_writers_filtername_reads_as_its_filter(tmp_path):
    """An older writer used a goal key such as "R Stars" as a filtername.
    The row's filter is R, through the vocabulary the writers share."""
    path = tmp_path / "ts.sqlite"
    _make_ts_db(
        path, project_name="Custom Filter", target_name="T1",
        filters=[("R Stars filter", "R Stars", 10, 60)])
    plans, notes, _ = _plans_for(path)
    (plan,) = plans
    assert _pairs(plan) == [("R", "R Stars filter")]


def test_unknown_filter_name_passes_through(tmp_path):
    path = tmp_path / "ts.sqlite"
    _make_ts_db(
        path, project_name="Custom Filter", target_name="T1",
        filters=[("Antlia Ha 3nm", "Antlia Ha", 10, 60)])
    plans, notes, _ = _plans_for(path)
    (plan,) = plans
    assert _pairs(plan) == [("Antlia Ha", "Antlia Ha 3nm")]


def test_a_template_named_for_stars_on_filter_r_is_filter_r(tmp_path):
    """The row's filter comes from the template's filtername, never from its
    name."""
    path = tmp_path / "ts.sqlite"
    _make_ts_db(
        path, project_name="Stars", target_name="T1",
        filters=[("R Stars (ZWO ASI6200MM Pro)", "R", 10, 10)])
    plans, notes, _ = _plans_for(path)
    (plan,) = plans
    assert _pairs(plan) == [("R", "R Stars (ZWO ASI6200MM Pro)")]


@pytest.mark.parametrize("raw,expected", json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "row_filter_cases.json").read_text(encoding="utf-8"))["cases"])
def test_the_shared_row_filter_table(raw, expected):
    """The table ACP's migration and the plugin test against too."""
    from nina_ts_sync.rows import row_filter
    assert row_filter(raw) == expected


# -- the PNe Survey case: TS "O" matches ACP's "OIII" row ----------------


def test_pne_style_plan_keys_by_canonical_name_not_raw(tmp_path):
    """TS's exposuretemplate can be named 'OIII' (matching ACP's plan) while
    its filtername is 'O' (matching the wheel slot's own name), which is
    exactly the PNe Survey shape: the row's filter comes from the wheel-name
    column, canonicalised, and its template from the template's name."""
    path = tmp_path / "ts.sqlite"
    _make_ts_db(
        path, project_name="PNe Survey", target_name="OIII-T 266.25 -28.75",
        filters=[("OIII", "O", 18, 300)])
    plans, notes, _ = _plans_for(path)
    (plan,) = plans
    (row,) = plan["filter_rows"]
    assert (row["filter"], row["template"], row["unit"]) == ("OIII", "OIII", "h")
    assert row["target_hours"] == pytest.approx(18 * 300 / 3600)
    assert row["template_sub_s"] == 300
    assert [n for n in notes if n.level == "loss"] == []


# -- two plans on one target that share a canonical filter ---------------


def test_o_and_oiii_on_one_target_are_two_rows_of_one_filter(tmp_path):
    """A target carrying both a raw 'O' plan and a raw 'OIII' plan is two
    rows of filter OIII, one per template, so neither plan's frames are
    lost and there is nothing to report."""
    path = tmp_path / "ts.sqlite"
    _make_ts_db(
        path, project_name="Collision Test", target_name="T1",
        filters=[("O", "O", 18, 300), ("OIII", "OIII", 10, 60)])
    plans, notes, _ = _plans_for(path)
    (plan,) = plans
    assert _pairs(plan) == [("OIII", "O"), ("OIII", "OIII")]
    assert [r["template_sub_s"] for r in plan["filter_rows"]] == [300, 60]
    assert [n for n in notes if n.level == "loss"] == []


def test_r_and_r_stars_are_two_rows_of_filter_r(tmp_path):
    """Sculptor's R (60 s) and R Stars (10 s): two rows of filter R. The
    10 s row is a frame row (sub under 60 s)."""
    path = tmp_path / "ts.sqlite"
    _make_ts_db(
        path, project_name="Sculptor-style", target_name="T1",
        filters=[("R", "R", 18, 60), ("R Stars", "R", 10, 10)])
    plans, notes, _ = _plans_for(path)
    (plan,) = plans
    assert _pairs(plan) == [("R", "R"), ("R", "R Stars")]
    r, stars = plan["filter_rows"]
    assert (r["unit"], r["target_hours"]) == ("h", pytest.approx(0.3))
    assert (stars["unit"], stars["target_frames"]) == ("fr", 10)
    assert len({r["id"], stars["id"]}) == 2
    assert [n for n in notes if n.level == "loss"] == []
