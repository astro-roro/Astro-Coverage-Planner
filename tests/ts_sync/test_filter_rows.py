"""Filter rows in the writer and the importer.

docs/specs/goal-panel-and-ts-order.md section 3 and the tests in section 7:
S1 to S3, S5, M2 to M4, R1 and R3, and the remove and re-add rules. The
scenarios themselves are in tests/row_scenarios.py, shared with the golden
rows the plugin compares itself to.
"""
from __future__ import annotations

import copy
import sqlite3

import pytest

from nina_ts_sync import import_merge
from nina_ts_sync.from_ts import import_to_acp_plans
from nina_ts_sync.reader import read_all
from nina_ts_sync.rows import desired, rows_of
from nina_ts_sync.schema import row_plan_guid
from . import row_scenarios as sc
from .host import MACHINE, P, FakeHost, connect, new_db
from .make_db import make_db
from .plans import PROFILE_ID, plan


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(make_db(28, tmp_path / "ts.sqlite"))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _plans(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM exposureplan ORDER BY Id")]


def _templates(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM exposuretemplate ORDER BY Id")]


def _override(conn, tid):
    return [(r["action"], r["referenceIdx"]) for r in conn.execute(
        'SELECT "action", referenceIdx FROM overrideexposureorderitem WHERE targetid = ? ORDER BY "order"',
        (tid,))]


def _cadence(conn, tid):
    return conn.execute("SELECT COUNT(*) FROM filtercadenceitem WHERE targetid = ?", (tid,)).fetchone()[0]


# -- S: star rows and templates -------------------------------------------


def test_s1_a_new_star_row_inserts_its_template_and_plan(conn):
    report = sc.s1(conn)
    (tpl,) = _templates(conn)
    assert (tpl["name"], tpl["filtername"], tpl["defaultexposure"]) == ("R Stars", "R", 15.0)
    (ep,) = _plans(conn)
    assert (ep["desired"], ep["exposure"], ep["exposureTemplateId"]) == (50, -1.0, tpl["Id"])
    assert ep["guid"] == row_plan_guid(PROFILE_ID, sc.m42_target_guid(), "R", "R Stars")
    assert report.exposuretemplate.inserted == 1 and report.exposureplan.inserted == 1


def test_s2_an_existing_template_is_used_as_it_is(conn):
    report = sc.s2(conn)
    (tpl,) = _templates(conn)
    assert (tpl["name"], tpl["defaultexposure"], tpl["guid"]) == ("R Stars", 10.0, "nina-guid-r-stars")
    assert report.exposuretemplate.inserted == 0 and report.exposuretemplate.updated == 0
    (ep,) = _plans(conn)
    assert ep["exposureTemplateId"] == tpl["Id"]


def test_s3_a_pushed_star_row_comes_back_with_its_id(conn):
    p = sc.m42([sc.row("row-rs", "R", "R Stars", 15, frames=50)])
    sc.push(conn, [p])
    snap = read_all(conn, PROFILE_ID)
    view = import_merge.build_view(snap, 28, [p], sc.ROW_GEAR, profile_id=PROFILE_ID,
                                   machine=None, strict=False)
    (m,) = view.matches
    (got,) = m.remote.plan["filter_rows"]
    assert (got["id"], got["filter"], got["template"]) == ("row-rs", "R", "R Stars")
    assert (got["unit"], got["target_frames"]) == ("fr", 50)
    assert not m.diff.auto_pull and not m.diff.conflict


def test_s5_stray_templates_are_listed_never_deleted(conn):
    for name in ("R Stars", "G Stars", "B Stars"):
        sc.add_template(conn, f"{name} (ZWO ASI6200MM Pro)", name, 10, f"old-{name}")
    conn.commit()
    report = sc.s1(conn)
    names = [t["name"] for t in _templates(conn)]
    assert len(names) == 4
    assert report.stray_templates == [
        "R Stars (ZWO ASI6200MM Pro)", "G Stars (ZWO ASI6200MM Pro)", "B Stars (ZWO ASI6200MM Pro)"]


# -- M: plans an older writer made ------------------------------------------


def test_m2_old_plans_are_restamped_not_duplicated(conn):
    tpl = sc.m2_old_writer_state(conn)
    before = {r["Id"]: r for r in _plans(conn)}
    report = sc.push(conn, [sc.m42(sc.m1_rows())])
    after = {r["Id"]: r for r in _plans(conn)}
    assert report.exposureplan.inserted == 0
    assert report.exposuretemplate.inserted == 0
    assert set(after) == set(before)
    tg = sc.m42_target_guid()
    stars = after[3]
    assert stars["guid"] == row_plan_guid(PROFILE_ID, tg, "R", "R Stars")
    # An existing plan keeps the template it has: the migration's naming is
    # not a change made in ACP. The spec's M2 had it repointed at "R Stars";
    # the rule since is that only an edit in ACP moves a plan.
    assert stars["exposureTemplateId"] == tpl["R Stars"]
    for pid, row in after.items():
        old = before[pid]
        assert (row["acquired"], row["accepted"], row["exposure"], row["desired"]) == \
            (old["acquired"], old["accepted"], old["exposure"], old["desired"]), pid
    assert after[2]["guid"] == row_plan_guid(PROFILE_ID, tg, "R", "R")
    assert after[2]["exposureTemplateId"] == tpl["R"]


def test_m3_an_explicit_exposure_stays_and_is_reported(conn):
    p, ep_id = sc.m3_state(conn)
    report = sc.push(conn, [p])
    (ep,) = _plans(conn)
    assert ep["Id"] == ep_id and ep["exposure"] == 300.0
    assert report.exposure_mismatches == ["M42 R: TS shoots 300 s, template R says 180 s"]
    sc.link(conn, [p])
    assert p["filter_rows"][0]["template_sub_s"] == 300


def test_m4_a_template_change_writes_the_default(conn):
    report = sc.m4(conn)
    (ep,) = _plans(conn)
    r300 = conn.execute("SELECT Id FROM exposuretemplate WHERE name = 'R 300'").fetchone()[0]
    assert (ep["exposure"], ep["exposureTemplateId"]) == (-1.0, r300)
    assert ep["guid"] == row_plan_guid(PROFILE_ID, sc.m42_target_guid(), "R", "R 300")
    assert (ep["acquired"], ep["accepted"]) == (4, 4)
    assert report.exposureplan.inserted == 0
    assert report.exposure_mismatches == []


def test_an_unchanged_template_leaves_exposure_alone_after_a_link(conn):
    p, _ = sc.m3_state(conn)
    sc.push(conn, [p])
    sc.link(conn, [p])
    conn.execute("UPDATE exposureplan SET exposure = 240")
    conn.commit()
    sc.push(conn, [p])
    assert _plans(conn)[0]["exposure"] == 240.0


# -- R: removing and adding a row back --------------------------------------


def test_r1_a_removed_row_is_disabled_and_the_override_rows_rebuilt(conn):
    report = sc.r1(conn)
    tid = sc.m42_target_id(conn)
    ha, oiii, sii = _plans(conn)
    assert (ha["enabled"], ha["acquired"], ha["accepted"]) == (0, 3, 2)
    assert (oiii["enabled"], sii["enabled"]) == (1, 1)
    assert report.disabled_plans == 1
    # The rows were the sync's, so they are built again from the new list.
    assert _override(conn, tid) == [(0, 1)] * 4 + [(0, 0)] * 4
    assert _cadence(conn, tid) == 0
    assert report.order_written == ["M42"]
    assert report.override_rebuilt == []

    plans, _, _ = import_to_acp_plans(read_all(conn, PROFILE_ID), sc.ROW_GEAR)
    (imported,) = plans
    # No Ha row, and the rows in the order TS shoots them.
    assert [r["filter"] for r in imported["filter_rows"]] == ["SII", "OIII"]


def test_r3_a_row_added_back_enables_its_old_plan(conn):
    report = sc.r3(conn)
    tid = sc.m42_target_id(conn)
    plans = _plans(conn)
    assert len(plans) == 3 and report.exposureplan.inserted == 0
    ha = plans[0]
    assert (ha["enabled"], ha["acquired"], ha["accepted"]) == (1, 3, 2)
    # Ha is enabled again ahead of OIII and SII in natural order, and the
    # rows are built again with Ha second, as the list has it.
    assert _override(conn, tid) == [(0, 2)] * 4 + [(0, 0)] * 4 + [(0, 1)] * 4
    assert report.order_written == ["M42"]
    assert _cadence(conn, tid) == 0

    p = sc.m42([sc.row("row-sii", "SII", "SII", 300, frames=4),
                sc.row("row-ha-again", "Ha", "Ha", 300, frames=4),
                sc.row("row-oiii", "OIII", "OIII", 300, frames=4)])
    sc.link(conn, [p])
    refs = p["ts_links"][PROFILE_ID]["refs"]["exposure_plan_ids"]
    assert refs[f"{tid}_row-ha-again"] == ha["Id"]


def test_a_removed_row_without_acps_stamp_is_left_alone(conn):
    p = sc.r1_state(conn)
    conn.execute("UPDATE exposureplan SET guid = 'nina-own-guid' WHERE Id = 1")
    conn.commit()
    p["filter_rows"] = [r for r in p["filter_rows"] if r["id"] != "row-ha"]
    report = sc.push(conn, [p])
    assert _plans(conn)[0]["enabled"] == 1
    assert report.disabled_plans == 0


def test_a_removed_row_with_no_link_is_left_alone(conn):
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4),
                sc.row("row-oiii", "OIII", "OIII", 300, frames=4)])
    sc.push(conn, [p])
    p["filter_rows"] = p["filter_rows"][1:]
    report = sc.push(conn, [p])
    assert [r["enabled"] for r in _plans(conn)] == [1, 1]
    assert report.disabled_plans == 0


def test_a_removed_row_is_disabled_on_every_panel_of_a_mosaic(conn):
    p = plan("mos", project_name="Mosaic", target_name="M31", rows=1, cols=2,
             ra=10.68, dec=41.27,
             filter_rows=[sc.row("row-ha", "Ha", "Ha", 300, frames=4),
                          sc.row("row-oiii", "OIII", "OIII", 300, frames=4)])
    sc.push(conn, [p])
    sc.link(conn, [p])
    p["filter_rows"] = p["filter_rows"][1:]
    report = sc.push(conn, [p])
    assert report.disabled_plans == 2
    by_template = {}
    for r in conn.execute(
            "SELECT e.enabled, x.name FROM exposureplan e JOIN exposuretemplate x "
            "ON x.Id = e.exposureTemplateId"):
        by_template.setdefault(r["name"], []).append(r["enabled"])
    assert by_template == {"Ha": [0, 0], "OIII": [1, 1]}


def test_a_goal_of_zero_pushes_no_plan(conn):
    sc.push(conn, [sc.m42([sc.row("row-ha", "Ha", "Ha", 300, hours=0.0)])])
    assert _plans(conn) == []


# -- rows from an ACP that predates filter rows ------------------------------


def test_an_old_acp_plan_derives_rows_keyed_by_goal(conn):
    old = {"Ha": {"target_hours": 4.0, "sub_exposure_s": 300},
           "R Stars": {"target_hours": 0.0833, "sub_exposure_s": 15}}
    rows = rows_of({"filter_goals": old}, sc.ROW_GEAR["cameras"][0])
    assert [(r["id"], r["filter"], r["template"], r["unit"]) for r in rows] == [
        ("R Stars", "R", "R Stars", "fr"), ("Ha", "Ha", "Ha 3nm", "h")]
    assert [desired(r) for r in rows] == [20, 48]


# -- template_sub_s refresh through POST /links -----------------------------


def test_links_post_refreshes_each_rows_template_sub(tmp_path, monkeypatch):
    host = FakeHost(tmp_path, monkeypatch)
    p = sc.m42([sc.row("row-r", "R", "R", 180, hours=1.0)])
    host.write_plans([p])
    base = {"exposure_plans_by_panel_filter": {
        "1,1_row-r": {"desired": 20, "exposure": 300.0, "filter": "R", "template": "R",
                      "template_sub_s": 300.0}}}
    r = host.client.post("/api/ext/nina-ts-sync/links", json={
        "profile_id": P, "machine": MACHINE,
        "states": [{"plan_id": "m42", "refs": {"profile_id": P}, "base_snapshot": base}],
    })
    assert r.status_code == 200, r.get_json()
    assert host.plan("m42")["filter_rows"][0]["template_sub_s"] == 300


# -- a plan imported from TS keeps the user's own template -------------------


def test_an_existing_plan_keeps_its_template_when_acp_names_it_otherwise(conn):
    """A plan made in TS points at the user's template "Ha". ACP's migration
    names the row's template the way an older writer would have ("Ha 3nm",
    the camera's template name). The push must not insert "Ha 3nm" and move
    the plan onto it: the link pins the template, and the row's template has
    not changed since the base."""
    sc.push(conn, [sc.m42([])])
    tid = sc.m42_target_id(conn)
    mine = sc.add_template(conn, "Ha", "Ha", 600, "nina-guid-ha")
    ep = conn.execute(
        "INSERT INTO exposureplan (profileId, exposure, desired, acquired, accepted, targetid, "
        "exposureTemplateId, enabled, guid) VALUES (?, -1, 24, 5, 5, ?, ?, 1, 'nina-guid-plan')",
        (PROFILE_ID, tid, mine)).lastrowid
    conn.commit()
    p = sc.m42([sc.row("row-ha", "Ha", "Ha 3nm", 600, hours=4.0)])
    p["ts_links"] = {PROFILE_ID: {
        "machine": None,
        "refs": {"profile_id": PROFILE_ID, "project_id": 1, "target_ids_by_panel": {"1,1": tid},
                 "template_ids_by_filter": {"row-ha": mine},
                 "exposure_plan_ids": {f"{tid}_row-ha": ep}},
        "base_snapshot": {"exposure_plans_by_panel_filter": {
            "1,1_row-ha": {"desired": 24, "filter": "Ha", "template": "Ha 3nm"}}},
    }}
    report = sc.push(conn, [p])
    assert report.exposuretemplate.inserted == 0 and report.exposureplan.inserted == 0
    (row,) = _plans(conn)
    assert (row["Id"], row["exposureTemplateId"], row["exposure"]) == (ep, mine, -1.0)
    assert [t["name"] for t in _templates(conn)] == ["Ha"]

    # A template changed in ACP since the base is the row's own choice, so
    # the pin no longer stands in for it.
    p["filter_rows"][0]["template"] = "Ha 5nm"
    report = sc.push(conn, [p])
    assert report.exposuretemplate.inserted == 1
    assert _plans(conn)[0]["exposureTemplateId"] != mine


# -- override rows set in TS and a plan the push adds ------------------------


def _hand_override(conn):
    """M42 with Ha and OIII pushed and override rows set by hand in TS."""
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4),
                sc.row("row-oiii", "OIII", "OIII", 300, frames=4)])
    sc.push(conn, [p])
    sc.link(conn, [p])
    tid = sc.m42_target_id(conn)
    sc.set_override(conn, tid, [(0, 0), (0, 1)])
    sc.add_cadence(conn, tid, 2)
    conn.commit()
    return p, tid


def test_an_inserted_plan_is_appended_to_override_rows_set_in_ts(conn):
    p, tid = _hand_override(conn)
    p["filter_rows"].append(sc.row("row-sii", "SII", "SII", 300, frames=4))
    report = sc.push(conn, [p])
    assert report.exposureplan.inserted == 1
    assert _override(conn, tid) == [(0, 0), (0, 1)] + [(0, 2)] * 5
    assert _cadence(conn, tid) == 0
    assert report.override_rebuilt == ["M42"]


def test_the_block_follows_the_grader_throttle_and_switch_frequency(conn):
    p, tid = _hand_override(conn)
    conn.execute("INSERT INTO profilepreference (profileId, exposurethrottle) VALUES (?, 100)", (PROFILE_ID,))
    conn.commit()
    p["filter_rows"].append(sc.row("row-sii", "SII", "SII", 300, frames=4))
    sc.push(conn, [p])
    assert _override(conn, tid)[2:] == [(0, 2)] * 4

    conn.execute("UPDATE project SET enablegrader = 1, filterswitchfrequency = 3")
    conn.commit()
    p["filter_rows"].append(sc.row("row-l", "L", "L", 300, frames=7))
    sc.push(conn, [p])
    assert _override(conn, tid)[6:] == [(0, 3)] * 3


def test_a_target_without_override_rows_gets_none(conn):
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4)])
    sc.push(conn, [p])
    p["filter_rows"].append(sc.row("row-oiii", "OIII", "OIII", 300, frames=4))
    report = sc.push(conn, [p])
    assert _override(conn, sc.m42_target_id(conn)) == []
    assert report.override_rebuilt == []


def test_a_template_an_enabled_plan_uses_is_not_stray(conn):
    tid = None
    sc.push(conn, [sc.m42([])])
    tid = sc.m42_target_id(conn)
    used = sc.add_template(conn, "R Stars (ZWO ASI6200MM Pro)", "R Stars", 10, "old-r-stars")
    sc.add_template(conn, "G Stars (ZWO ASI6200MM Pro)", "G Stars", 10, "old-g-stars")
    conn.execute(
        "INSERT INTO exposureplan (profileId, exposure, desired, acquired, accepted, targetid, "
        "exposureTemplateId, enabled, guid) VALUES (?, 10, 5, 0, 0, ?, ?, 1, 'x')",
        (PROFILE_ID, tid, used))
    conn.commit()
    report = sc.s1(conn)
    assert report.stray_templates == ["G Stars (ZWO ASI6200MM Pro)"]


# -- canonicalisation and derivation are never an edit -----------------------


def _plan_state(conn):
    return [(r["Id"], r["exposureTemplateId"], r["exposure"], r["acquired"], r["accepted"], r["enabled"])
            for r in _plans(conn)]


def test_a_derived_row_keeps_the_existing_plans_template(conn):
    report = sc.derived_row_keeps_existing_template(conn)
    (ep,) = _plans(conn)
    g = conn.execute("SELECT Id FROM exposuretemplate WHERE name = 'G'").fetchone()[0]
    assert (ep["exposureTemplateId"], ep["exposure"], ep["acquired"]) == (g, 60.0, 7)
    assert report.exposuretemplate.inserted == 0 and report.exposureplan.inserted == 0
    assert ep["desired"] == 120


def test_acp_migration_moves_no_plan_and_makes_no_template(conn):
    """The probe seq.py: before the fix, "Antlia Ha" and "Ha" plans moved to
    new templates after ACP migrated."""
    sc.seq_old_writer_state(conn)
    before = _plan_state(conn)
    templates = len(_templates(conn))
    p = sc.old_acp(copy.deepcopy(sc.SEQ_GOALS))
    first = sc.push(conn, [p])
    assert _plan_state(conn) == before and first.exposuretemplate.inserted == 0
    sc.link(conn, [p])
    sc.migrate_like_acp(p)
    second = sc.push(conn, [p])
    assert _plan_state(conn) == before
    assert len(_templates(conn)) == templates
    assert second.exposuretemplate.inserted == 0 and second.exposureplan.inserted == 0
    # Every row still finds its plan, through its own guid now.
    tid = sc.m42_target_id(conn)
    sc.link(conn, [p])
    assert len(p["ts_links"][PROFILE_ID]["refs"]["exposure_plan_ids"]) == 4
    assert {k.split("_", 1)[0] for k in p["ts_links"][PROFILE_ID]["refs"]["exposure_plan_ids"]} == {str(tid)}


def test_an_older_acps_goals_become_rows_in_the_shared_vocabulary(conn):
    sc.older_acp_goals(conn)
    got = {(r["name"], r["filtername"]) for r in _templates(conn)}
    assert got == {("Ha 3nm", "Ha"), ("O", "OIII"), ("R Stars", "R"), ("Antlia Ha", "Antlia Ha"), ("Lum", "L")}


def test_a_plan_turned_off_in_ts_stays_off(conn):
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4),
                sc.row("row-oiii", "OIII", "OIII", 300, frames=4)])
    sc.push(conn, [p])
    sc.link(conn, [p])
    conn.execute("UPDATE exposureplan SET enabled = 0 WHERE Id = 1")
    conn.commit()
    report = sc.push(conn, [p])
    assert [r["enabled"] for r in _plans(conn)] == [0, 1]
    assert len(report.kept_disabled) == 1 and "Ha" in report.kept_disabled[0]
    assert report.to_dict()["kept_disabled"] == report.kept_disabled


def test_a_plan_off_in_ts_with_no_base_is_enabled(conn):
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4),
                sc.row("row-oiii", "OIII", "OIII", 300, frames=4)])
    sc.push(conn, [p])
    conn.execute("UPDATE exposureplan SET enabled = 0 WHERE Id = 1")
    conn.commit()
    report = sc.push(conn, [p])
    assert [r["enabled"] for r in _plans(conn)] == [1, 1]
    assert report.kept_disabled == []


def test_a_plan_turned_off_in_ts_stays_off_at_every_push(conn):
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4),
                sc.row("row-oiii", "OIII", "OIII", 300, frames=4)])
    sc.push(conn, [p])
    sc.link(conn, [p])
    conn.execute("UPDATE exposureplan SET enabled = 0 WHERE Id = 1")
    conn.commit()
    for _ in range(3):
        report = sc.push(conn, [p])
        sc.link(conn, [p], report)
        assert [r["enabled"] for r in _plans(conn)] == [0, 1]
        assert len(report.kept_disabled) == 1
    conn.execute("UPDATE exposureplan SET enabled = 1 WHERE Id = 1")
    conn.commit()
    report = sc.push(conn, [p])
    assert [r["enabled"] for r in _plans(conn)] == [1, 1]
    assert report.kept_disabled == []
