"""POST /sync-acquired reads every panel target of a mosaic.

A mosaic's panels are separate targets in Target Scheduler, named
"<target> Panel N (RxCy)". The route used to read panel 1 only and call it the
plan. Now a mosaic row gets a `panels` map keyed "row,col" (the key ACP uses
in `ts_links.refs.target_ids_by_panel`) and `actual_hours` is the sum.
"""
from __future__ import annotations

import pytest

from .host import P, FakeHost, connect, new_db, push
from .plans import plan


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch)


def ha(hours=8.0, sub=600):
    return {"Ha": {"target_hours": hours, "sub_exposure_s": sub}}


def _set_acquired(db, target_name, subs):
    conn = connect(db)
    try:
        conn.execute(
            "UPDATE exposureplan SET acquired = ?, accepted = ? WHERE targetid = "
            "(SELECT Id FROM target WHERE name = ?)", (subs, subs, target_name))
        conn.commit()
    finally:
        conn.close()


def _sync(host, db):
    r = host.client.post("/api/ext/nina-ts-sync/sync-acquired",
                         json={"profile_id": P, "db_path": str(db)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _ha_row(plan_dict):
    return next(r for r in plan_dict["filter_rows"] if r["filter"] == "Ha")


def test_a_mosaic_row_gets_every_panel_and_the_sum(host, tmp_path):
    m = plan("m", project_name="Mosaic", target_name="Bogey", rows=2, cols=1, filter_goals=ha())
    host.write_plans([m])
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [m], P)
    # 600 s subs: 12 subs is 2 h on panel 1, 6 subs is 1 h on panel 2.
    _set_acquired(db, "Bogey Panel 1 (R1C1)", 12)
    _set_acquired(db, "Bogey Panel 2 (R2C1)", 6)

    body = _sync(host, db)

    row = _ha_row(host.plan("m"))
    assert row["panels"] == {"1,1": 2.0, "2,1": 1.0}
    assert row["actual_hours"] == 3.0
    assert body["updated_rows"] == 1


def test_panel_keys_are_row_then_column(host, tmp_path):
    m = plan("m", project_name="Mosaic", target_name="Grid", rows=2, cols=2, filter_goals=ha())
    host.write_plans([m])
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [m], P)
    for n, subs in ((1, 6), (2, 12), (3, 18), (4, 24)):
        r, c = divmod(n - 1, 2)
        _set_acquired(db, f"Grid Panel {n} (R{r + 1}C{c + 1})", subs)

    _sync(host, db)

    assert _ha_row(host.plan("m"))["panels"] == {"1,1": 1.0, "1,2": 2.0, "2,1": 3.0, "2,2": 4.0}
    assert _ha_row(host.plan("m"))["actual_hours"] == 10.0


def test_a_panel_ts_has_no_plan_for_keeps_the_hours_acp_had(host, tmp_path):
    m = plan("m", project_name="Mosaic", target_name="Bogey", rows=2, cols=1, filter_goals=ha())
    row = _ha_row(m)
    row["panels"] = {"1,1": 0.5, "2,1": 0.75, "3,1": 9.0}
    host.write_plans([m])
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [m], P)
    conn = connect(db)
    conn.execute("DELETE FROM exposureplan WHERE targetid = "
                 "(SELECT Id FROM target WHERE name = 'Bogey Panel 2 (R2C1)')")
    conn.commit()
    conn.close()
    _set_acquired(db, "Bogey Panel 1 (R1C1)", 12)

    _sync(host, db)

    got = _ha_row(host.plan("m"))
    assert got["panels"]["1,1"] == 2.0
    assert got["panels"]["2,1"] == 0.75


def test_a_single_target_is_read_as_before_and_has_no_panels(host, tmp_path):
    s = plan("s", project_name="Single", target_name="M31", filter_goals=ha())
    host.write_plans([s])
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [s], P)
    _set_acquired(db, "M31", 12)

    _sync(host, db)

    row = _ha_row(host.plan("s"))
    assert row["actual_hours"] == 2.0
    assert "panels" not in row


def test_stale_panels_on_a_single_target_are_dropped(host, tmp_path):
    s = plan("s", project_name="Single", target_name="M31", filter_goals=ha())
    _ha_row(s)["panels"] = {"1,1": 5.0, "2,1": 5.0}
    host.write_plans([s])
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [s], P)
    _set_acquired(db, "M31", 12)

    _sync(host, db)

    row = _ha_row(host.plan("s"))
    assert row["actual_hours"] == 2.0
    assert "panels" not in row


def test_a_mosaic_pushed_as_one_target_is_read_by_its_plain_name(host, tmp_path):
    m = plan("m", project_name="Mosaic", target_name="Bogey", rows=2, cols=1, filter_goals=ha())
    _ha_row(m)["panels"] = {"1,1": 1.0}
    host.write_plans([m])
    db = new_db(tmp_path / "ts.sqlite")
    flat = plan("m", project_name="Mosaic", target_name="Bogey", filter_goals=ha())
    push(db, [flat], P)
    _set_acquired(db, "Bogey", 12)

    _sync(host, db)

    row = _ha_row(host.plan("m"))
    assert row["actual_hours"] == 2.0
    assert "panels" not in row
