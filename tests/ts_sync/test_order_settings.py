"""Switch frequency and smart order, written only when ACP changed them.

docs/specs/goal-panel-and-ts-order.md section 4, tests F1 and F2 (F3 is the
plugin replaying both against golden-rows.json). The override rows section 4
also rebuilds are the order writer's, which is phase 5, so these tests check
the column rule, the cadence rows and the base only.
"""
from __future__ import annotations

import copy
import sqlite3

import pytest

from nina_ts_sync import convert, import_merge
from nina_ts_sync.diff import base_snapshot_to_plan_shape, diff_plan
from nina_ts_sync.from_ts import import_to_acp_plans
from nina_ts_sync.reader import read_all
from nina_ts_sync.state import build_base_snapshot, carry_unwritten_project_fields
from . import row_scenarios as sc
from .host import P, FakeHost, new_db
from .make_db import make_db
from .plans import PROFILE_ID, plan

ORDER_COLS = {"filterswitchfrequency", "smartexposureorder"}


def _connect(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def conn(tmp_path):
    c = _connect(make_db(28, tmp_path / "ts.sqlite"))
    yield c
    c.close()


def _project(conn):
    return conn.execute(
        "SELECT filterswitchfrequency, smartexposureorder FROM project WHERE name = ?", (sc.M42,)
    ).fetchone()


def _cadence(conn, name):
    return conn.execute(
        "SELECT COUNT(*) FROM filtercadenceitem c JOIN target t ON t.Id = c.targetid "
        "WHERE t.name = ?", (name,)).fetchone()[0]


def _skip(plans):
    payload = convert.build_payload(plans, sc.ROW_GEAR, profile_id=PROFILE_ID)
    return payload.project_update_skip_cols_by_guid[payload.projects[0].guid]


def test_f1_a_changed_switch_frequency_is_written_and_every_cadence_deleted(conn):
    report = sc.f1(conn)
    assert _project(conn)["filterswitchfrequency"] == 3
    assert _cadence(conn, sc.M42) == 0
    assert _cadence(conn, sc.M43) == 0
    assert report.cadence_reset_projects == [sc.M42]


def test_f2_a_frequency_set_in_ts_survives_and_the_base_keeps_0(conn):
    plans = sc.f_state(conn, freq=0)
    old_project = copy.deepcopy(plans[0]["ts_base_snapshot"]["project"])
    conn.execute("UPDATE project SET filterswitchfrequency = 3 WHERE name = ?", (sc.M42,))
    conn.commit()
    assert ORDER_COLS <= _skip(plans)
    report = sc.push(conn, plans)
    assert _project(conn)["filterswitchfrequency"] == 3
    assert _cadence(conn, sc.M42) == 3
    assert report.cadence_reset_projects == []

    # The base after the push, as the /sync route stamps it: TS holds 3,
    # but the push did not write it, so the base keeps 0.
    new_base = build_base_snapshot(read_all(conn, PROFILE_ID), plans[0], profile_id=PROFILE_ID)
    assert new_base["project"]["filterswitchfrequency"] == 3
    carry_unwritten_project_fields(new_base, old_project, frozenset())
    assert new_base["project"]["filterswitchfrequency"] == 0


def test_the_sync_route_keeps_the_base_of_an_unwritten_setting(tmp_path, monkeypatch):
    host = FakeHost(tmp_path, monkeypatch)
    db = new_db(tmp_path / "ts.sqlite")
    host.write_plans([plan("a", project_name="M31", filter_switch_frequency=0)])
    url = "/api/ext/nina-ts-sync/sync"

    def sync():
        r = host.client.post(url, json={"profile_id": P, "db_path": str(db)})
        assert r.status_code == 200, r.get_json()
        return host.plan("a")["ts_links"][P]["base_snapshot"]["project"]["filterswitchfrequency"]

    def ts_freq():
        with sqlite3.connect(db) as c:
            return c.execute("SELECT filterswitchfrequency FROM project").fetchone()[0]

    assert sync() == 0
    with sqlite3.connect(db) as c:
        c.execute("UPDATE project SET filterswitchfrequency = 3")
    assert sync() == 0
    assert ts_freq() == 3

    # Now ACP changes it: written, and the base follows.
    got = host.plan("a")
    got["filter_switch_frequency"] = 5
    host.write_plans([got])
    assert sync() == 5
    assert ts_freq() == 5


def test_smart_order_is_written_alone_and_deletes_the_cadence(conn):
    report = sc.smart_order_on(conn)
    row = _project(conn)
    assert (row["smartexposureorder"], row["filterswitchfrequency"]) == (1, 0)
    assert _cadence(conn, sc.M42) == 0 and _cadence(conn, sc.M43) == 0
    assert report.cadence_reset_projects == [sc.M42]


def test_an_unknown_setting_is_never_written_on_update(conn):
    plans = sc.f_state(conn, freq=0)
    conn.execute("UPDATE project SET filterswitchfrequency = 7, smartexposureorder = 1")
    conn.commit()
    for p in plans:
        del p["filter_switch_frequency"]
    report = sc.push(conn, plans)
    row = _project(conn)
    assert (row["filterswitchfrequency"], row["smartexposureorder"]) == (7, 1)
    assert _cadence(conn, sc.M42) == 3
    assert report.cadence_reset_projects == []


def test_no_base_writes_neither_setting_on_update(conn):
    p = sc.m42([sc.row("row-ha", "Ha", "Ha", 300, frames=4)])
    sc.push(conn, [p])
    conn.execute("UPDATE project SET filterswitchfrequency = 2")
    conn.commit()
    p["filter_switch_frequency"] = 5
    sc.push(conn, [p])
    assert _project(conn)["filterswitchfrequency"] == 2


def test_insert_takes_acps_values_or_0_and_off(tmp_path):
    with _connect(make_db(28, tmp_path / "a.sqlite")) as a:
        sc.push(a, [sc.m42([])])
        assert tuple(_project(a)) == (0, 0)
    with _connect(make_db(28, tmp_path / "b.sqlite")) as b:
        p = sc.m42([])
        p.update(filter_switch_frequency=4, smart_exposure_order=True)
        sc.push(b, [p])
        assert tuple(_project(b)) == (4, 1)


def test_plans_that_disagree_take_the_first_and_say_so(conn):
    a = sc.m42([])
    b = plan("m43", project_name=sc.M42, target_name=sc.M43, filter_rows=[])
    a["filter_switch_frequency"], b["filter_switch_frequency"] = 2, 6
    payload = convert.build_payload([a, b], sc.ROW_GEAR, profile_id=PROFILE_ID)
    assert payload.projects[0].filterswitchfrequency == 2
    report = sc.push(conn, [a, b])
    assert any("disagree on the filter switch frequency" in n and "m42" in n for n in report.notes)


def test_a_base_from_before_the_fields_reads_as_0_and_off(conn):
    plans = sc.f_state(conn, freq=0)
    for p in plans:
        for key in ORDER_COLS:
            p["ts_base_snapshot"]["project"].pop(key)
    assert ORDER_COLS <= _skip(plans)
    plans[0]["filter_switch_frequency"] = 3
    assert "filterswitchfrequency" not in _skip(plans)


def test_the_base_records_the_three_project_fields(conn):
    plans = sc.f_state(conn, freq=2)
    conn.execute("UPDATE project SET ditherevery = 3, smartexposureorder = 1")
    conn.commit()
    base = build_base_snapshot(read_all(conn, PROFILE_ID), plans[0], profile_id=PROFILE_ID)
    proj = base["project"]
    assert (proj["filterswitchfrequency"], proj["smartexposureorder"], proj["ditherevery"]) == (2, 1, 3)
    shape = base_snapshot_to_plan_shape(base)
    assert (shape["filter_switch_frequency"], shape["smart_exposure_order"]) == (2, True)


def test_the_importer_maps_both_settings(conn):
    sc.f_state(conn, freq=3)
    conn.execute("UPDATE project SET smartexposureorder = 1")
    conn.commit()
    out = import_to_acp_plans(read_all(conn, PROFILE_ID), gear=sc.ROW_GEAR)
    plans = out[0] if isinstance(out, tuple) else out
    assert plans
    for p in plans:
        assert (p["filter_switch_frequency"], p["smart_exposure_order"]) == (3, True)


def test_an_upload_brings_a_ts_change_into_acp(conn):
    plans = sc.f_state(conn, freq=0)
    conn.execute("UPDATE project SET filterswitchfrequency = 3")
    conn.commit()
    view = import_merge.build_view(read_all(conn, PROFILE_ID), 28, plans, sc.ROW_GEAR,
                                   profile_id=PROFILE_ID, machine=None, strict=False)
    assert view.matches
    for m in view.matches:
        assert "filter_switch_frequency" in m.diff.auto_pull
        assert not m.diff.conflict


def test_an_unknown_plan_learns_a_ts_value_but_not_the_default():
    base = {"filter_switch_frequency": 3}
    remote = {"filter_switch_frequency": 3, "smart_exposure_order": False}
    d = diff_plan("p", base=base, local={}, remote=remote)
    assert d.auto_pull == ["filter_switch_frequency"]
    d = diff_plan("p", base=base, local={}, remote={"filter_switch_frequency": 0})
    assert d.auto_pull == []
    # ACP set it since a base from before the field: ACP's change, not TS's.
    d = diff_plan("p", base={}, local={"filter_switch_frequency": 3},
                  remote={"filter_switch_frequency": 0})
    assert d.auto_keep == ["filter_switch_frequency"]
