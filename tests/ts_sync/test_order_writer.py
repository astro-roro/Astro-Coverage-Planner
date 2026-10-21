"""The shooting order, written to Target Scheduler's override rows.

docs/specs/goal-panel-and-ts-order.md section 5 and section 7, tests O1 to
O11, R2 and the override half of F1. Each test checks the override rows and
cadence the sync writes. The fixture is the spec's: one plan "M42",
rows Ha then OIII at 300 s and 4 frames, pushed in that order so the natural
order is Ha, OIII, the grader on, switch frequency 0, smart order off and
ditherevery 0 (tests/row_scenarios.py, `o_state`).
"""
from __future__ import annotations

import sqlite3

import pytest

from nina_ts_sync import import_merge
from nina_ts_sync.reader import read_all
from . import row_scenarios as sc
from .host import P, FakeHost, new_db
from .make_db import make_db
from .plans import PROFILE_ID, plan

O, H, S, D = "OIII", "Ha", "SII", "D"
OIII_FIRST = [(0, 1)] * 4 + [(0, 0)] * 4


def _connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def conn(tmp_path):
    c = _connect(make_db(28, tmp_path / "ts.sqlite"))
    yield c
    c.close()


def _tid(conn) -> int:
    return sc.m42_target_id(conn)


def _override(conn) -> list[tuple[int, int]]:
    return [
        (int(r["action"]), int(r["referenceIdx"]))
        for r in conn.execute(
            'SELECT "action", referenceIdx FROM overrideexposureorderitem WHERE targetid = ? ORDER BY "order"',
            (_tid(conn),))
    ]


def _cadence(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM filtercadenceitem WHERE targetid = ?", (_tid(conn),)).fetchone()[0]


def _view(conn, plans):
    return import_merge.build_view(read_all(conn, PROFILE_ID), 28, plans, sc.ROW_GEAR,
                                   profile_id=PROFILE_ID, machine=None, strict=False)


def _base_panel(p) -> dict:
    return p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"]["1,1"]


# -- O1 to O3: ACP's order, TS's order, and who wins ---------------------------


def test_o1_acps_order_is_written_and_imports_back_unchanged(conn):
    p, report = sc.o1_state(conn)
    assert _override(conn) == OIII_FIRST
    assert _cadence(conn) == 0
    assert report.order_written == ["M42"]
    assert _base_panel(p)["exposure_order"] == ["row-oiii", "row-ha"]
    assert _base_panel(p)["override_rows"] == [list(r) for r in OIII_FIRST]

    view = _view(conn, [p])
    (m,) = view.matches
    assert [r["id"] for r in m.remote.plan["filter_rows"]] == ["row-oiii", "row-ha"]
    assert not (m.diff.auto_pull or m.diff.auto_keep or m.diff.conflict)



def test_o2_an_order_set_in_ts_survives_a_push_and_comes_back_on_upload(conn):
    p, _ = sc.o1_state(conn)
    sc.set_override(conn, _tid(conn), [(0, 0), (0, 1)])
    conn.commit()
    report = sc.push(conn, [p])
    sc.link(conn, [p], report)
    assert _override(conn) == [(0, 0), (0, 1)]
    assert report.order_written == []
    assert _base_panel(p)["exposure_order"] == ["row-oiii", "row-ha"]

    view = _view(conn, [p])
    (m,) = view.matches
    assert m.diff.auto_pull == ["exposure_order"]
    assert m.diff.remote_values["exposure_order"] == ["row-ha", "row-oiii"]
    preview = import_merge.preview_dict(view, sc.ROW_GEAR)
    (field,) = preview["updated"][0]["fields"]
    assert (field["acp"], field["ts"]) == (["OIII", "Ha"], ["Ha", "OIII"])

    import_merge.apply_view(view, decisions={}, add=[], new_plan_gear=None, require_decisions=False)
    (pulled,) = view.plans
    assert [r["id"] for r in pulled["filter_rows"]] == ["row-ha", "row-oiii"]


def test_o3_acps_new_order_replaces_one_set_in_ts(conn):
    report = sc.o3(conn)
    # ACP's order Ha, OIII is the natural order, so it needs no override
    # rows at all: the ones set in TS go and TS shoots in natural order.
    assert _override(conn) == []
    assert report.order_written == ["M42"]
    assert any("replaced an order set in Target Scheduler" in n for n in report.notes)


# -- O4: block sizes ------------------------------------------------------------


def test_o4_switch_frequency_3_writes_blocks_of_3(conn):
    sc.o4_rotate(conn)
    assert _override(conn) == [(0, 1)] * 3 + [(0, 0)] * 3


@pytest.mark.parametrize("throttle, block", [(125, 5), (100, 4)])
def test_o4b_grader_off_sizes_blocks_by_the_throttle(conn, throttle, block):
    p = sc.o_state(conn)
    conn.execute("UPDATE project SET enablegrader = 0")
    conn.execute("INSERT INTO profilepreference (profileId, exposurethrottle) VALUES (?, ?)",
                 (PROFILE_ID, throttle))
    conn.commit()
    sc.reorder(p, ["row-oiii", "row-ha"])
    sc.push(conn, [p])
    assert _override(conn) == [(0, 1)] * block + [(0, 0)] * block
    # Past `desired` with the throttle at 125, as TS does.


def test_o4c_every_push_restarts_the_walk_at_row_1(conn):
    sc.o4c(conn)
    assert _override(conn) == OIII_FIRST
    assert _cadence(conn) == 0


def test_o4d_a_raised_goal_refreshes_the_rows_the_sync_wrote(conn):
    report = sc.o4d(conn)
    assert _override(conn) == [(0, 1)] * 6 + [(0, 0)] * 4
    assert report.order_written == ["M42"]


def test_unchanged_rows_are_not_rewritten(conn):
    p, _ = sc.o1_state(conn)
    report = sc.push(conn, [p])
    assert report.order_written == []
    assert _override(conn) == OIII_FIRST


# -- O5 to O8 -------------------------------------------------------------------


@pytest.mark.parametrize("freq", [0, 3])
def test_o5_the_natural_order_needs_no_rows(conn, freq):
    p = sc.o_state(conn, freq=freq)
    sc.push(conn, [p])
    assert _override(conn) == []


def test_o6_smart_order_writes_no_rows_and_deletes_the_cadence(conn):
    p = sc.o_state(conn)
    sc.add_cadence(conn, _tid(conn), 3)
    conn.commit()
    p["smart_exposure_order"] = True
    sc.reorder(p, ["row-oiii", "row-ha"])
    report = sc.push(conn, [p])
    assert conn.execute("SELECT smartexposureorder FROM project").fetchone()[0] == 1
    assert _override(conn) == []
    assert _cadence(conn) == 0
    assert report.order_written == []


def test_o7_dither_rows_follow_ts_rule(conn):
    sc.o7(conn)
    d = (1, -1)
    oiii, ha = (0, 1), (0, 0)
    assert _override(conn) == [oiii, oiii, d, oiii, oiii, ha, ha, d, ha, ha]


def test_o8_a_new_plan_goes_on_the_end_of_an_order_set_in_ts(conn):
    report = sc.o8(conn)
    assert report.exposureplan.inserted == 1
    assert _override(conn) == [(0, 0), (0, 1)] + [(0, 2)] * 4
    assert _cadence(conn) == 0
    assert report.override_rebuilt == ["M42"]
    assert report.order_written == []


# -- O10, O11 ---------------------------------------------------------------------


def test_o10_an_order_change_deletes_cadence_from_an_older_order(conn):
    p = sc.o_state(conn)
    sc.add_cadence(conn, _tid(conn), 5)
    conn.commit()
    sc.reorder(p, ["row-oiii", "row-ha"])
    sc.push(conn, [p])
    assert _cadence(conn) == 0


def test_o11_a_base_without_an_order_writes_none_and_the_upload_brings_ts_in(conn):
    report = sc.o11(conn)
    assert _override(conn) == []
    assert report.order_written == []
    p = sc.m42([sc.row("row-oiii", "OIII", "OIII", 300, frames=4), sc.row("row-ha", "Ha", "Ha", 300, frames=4)])
    sc.link(conn, [p])
    for entry in p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"].values():
        entry.pop("exposure_order")
        entry.pop("override_rows")
    sc.reorder(p, ["row-ha", "row-oiii"])
    (m,) = _view(conn, [p]).matches
    assert m.diff.auto_pull == ["exposure_order"]
    assert m.diff.remote_values["exposure_order"] == ["row-oiii", "row-ha"]


def test_hand_made_orders_stay_until_acp_changes_the_order(conn):
    """The Helix, Dolphin and G7.7-3.7: rows set in TS, a base from
    before orders were recorded. Push, upload, push: the rows stay. Only a
    change of order in ACP replaces them."""
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4), sc.row("row-oiii", "OIII", "OIII", 300, frames=4),
                sc.row("row-sii", "SII", "SII", 300, frames=4)])
    sc.link(conn, [p], sc.push(conn, [p]))
    for entry in p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"].values():
        entry.pop("exposure_order")
        entry.pop("override_rows")
    helix = [(0, 1), (0, 0), (0, 2)]
    sc.set_override(conn, _tid(conn), helix)
    conn.commit()
    sc.link(conn, [p], sc.push(conn, [p]))
    assert _override(conn) == helix

    view = _view(conn, [p])
    import_merge.apply_view(view, decisions={}, add=[], new_plan_gear=None, require_decisions=False)
    (p,) = view.plans
    assert [r["id"] for r in p["filter_rows"]] == ["row-oiii", "row-ha", "row-sii"]
    assert "override_rows" not in _base_panel(p)
    sc.link(conn, [p], sc.push(conn, [p]))
    assert _override(conn) == helix

    sc.reorder(p, ["row-sii", "row-oiii", "row-ha"])
    sc.push(conn, [p])
    assert _override(conn) == [(0, 2)] * 5 + [(0, 1)] * 5 + [(0, 0)] * 5


# -- R2 and F1 ----------------------------------------------------------------------


def test_r2_rows_set_in_ts_are_remapped_not_regenerated(conn):
    report = sc.r2(conn)
    assert _override(conn) == [(0, 1), (0, 0)]
    assert report.order_written == []
    assert report.override_rebuilt == ["M42"]
    assert _cadence(conn) == 0


def test_f1_a_new_switch_frequency_rebuilds_the_rows_in_blocks_of_3(conn):
    report = sc.f1_override(conn)
    assert conn.execute("SELECT filterswitchfrequency FROM project").fetchone()[0] == 3
    assert _override(conn) == [(0, 1)] * 3 + [(0, 0)] * 3
    assert report.cadence_reset_projects == ["M42"]
    assert report.order_written == ["M42"]


# -- through the /sync route ------------------------------------------------------


def _route_sync(host, db):
    r = host.client.post("/api/ext/nina-ts-sync/sync", json={"profile_id": P, "db_path": str(db)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["report"]


def test_the_sync_route_writes_the_order_and_records_it(tmp_path, monkeypatch):
    host = FakeHost(tmp_path, monkeypatch)
    db = new_db(tmp_path / "ts.sqlite")
    rows = [sc.row("row-ha", "Ha", "Ha 3nm", 600, frames=4), sc.row("row-oiii", "OIII", "OIII 3nm", 300, frames=4)]
    host.write_plans([plan("a", project_name="M31", filter_rows=rows)])
    _route_sync(host, db)
    got = host.plan("a")
    assert got["ts_links"][P]["base_snapshot"]["targets_by_panel"]["1,1"]["exposure_order"] == ["row-ha", "row-oiii"]

    got["filter_rows"] = list(reversed(got["filter_rows"]))
    host.write_plans([got])
    report = _route_sync(host, db)
    assert report["order_written"] == ["Ta"]
    base = host.plan("a")["ts_links"][P]["base_snapshot"]["targets_by_panel"]["1,1"]
    assert base["exposure_order"] == ["row-oiii", "row-ha"]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM overrideexposureorderitem").fetchone()[0] == 10

    # TS's rows changed by hand: the next sync leaves them and the base.
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM overrideexposureorderitem WHERE \"order\" > 1")
    report = _route_sync(host, db)
    assert report["order_written"] == []
    base = host.plan("a")["ts_links"][P]["base_snapshot"]["targets_by_panel"]["1,1"]
    assert base["exposure_order"] == ["row-oiii", "row-ha"] and len(base["override_rows"]) == 10


def test_the_sync_route_keeps_the_base_of_state_priority_and_minimumtime(tmp_path, monkeypatch):
    """The fix phase 4 found: the route took state, priority and minimumtime
    for the next base straight from TS, so a pause made in TS looked agreed
    on and the push after that undid it. Like the plugin, a setting the push
    left alone keeps the old base's value."""
    host = FakeHost(tmp_path, monkeypatch)
    db = new_db(tmp_path / "ts.sqlite")
    host.write_plans([plan("a", project_name="M31")])

    def base_project():
        return host.plan("a")["ts_links"][P]["base_snapshot"]["project"]

    _route_sync(host, db)
    assert (base_project()["state"], base_project()["priority"], base_project()["minimumtime"]) == (1, 1, 0)
    with sqlite3.connect(db) as c:
        c.execute("UPDATE project SET state = 2, priority = 2, minimumtime = 30")
    _route_sync(host, db)
    assert (base_project()["state"], base_project()["priority"], base_project()["minimumtime"]) == (1, 1, 0)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT state, priority, minimumtime FROM project").fetchone() == (2, 2, 30)

    # ACP changes priority: written, and the base follows it alone.
    got = host.plan("a")
    got["priority"] = "low"
    host.write_plans([got])
    _route_sync(host, db)
    assert (base_project()["state"], base_project()["priority"]) == (1, 0)


def test_a_push_with_no_base_records_no_state_in_the_base(tmp_path, monkeypatch):
    """ts-project-settings.md section 5, "No base": the push writes none of
    the three on update, so its base leaves them out, as the plugin's does."""
    from nina_ts_sync.state import carry_unwritten_project_fields
    base = {"project": {"state": 2, "priority": 2, "minimumtime": 30, "filterswitchfrequency": 3}}
    carry_unwritten_project_fields(base, None, frozenset())
    assert base["project"] == {"filterswitchfrequency": 3}
    inserted = {"project": {"state": 1, "priority": 1, "minimumtime": 0}}
    carry_unwritten_project_fields(inserted, None, frozenset(), inserted=True)
    assert inserted["project"] == {"state": 1, "priority": 1, "minimumtime": 0}
    old = {"project": {"state": 2, "priority": 2, "minimumtime": 30}}
    carry_unwritten_project_fields(old, {"name": "x"}, frozenset({"priority"}))
    assert old["project"] == {"state": 1, "priority": 2, "minimumtime": 0}
