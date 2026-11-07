"""A plan's own scheduling switch and its per-panel switches reach Target
Scheduler as each target's `active` column, on insert and on update, and the
project state rule is unchanged. A plan with neither field pushes exactly as
it did before they existed."""
from __future__ import annotations

import sqlite3

from nina_ts_sync import convert, upsert
from .make_db import make_db
from .plans import GEAR, PROFILE_ID, plan


def _push(path, plans):
    payload = convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        report = upsert.apply(conn, payload)
        conn.commit()
        rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM target")}
        projects = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM project")}
        return report, rows, projects
    finally:
        conn.close()


def _halves(**north_south):
    """Two plans sharing one project, the shape of a target split in two."""
    return [
        plan("north", project_name="Split Nebula", target_name="Split Nebula North",
             **north_south.get("north", {})),
        plan("south", project_name="Split Nebula", target_name="Split Nebula South",
             dec=-32.0, **north_south.get("south", {})),
    ]


def test_plan_off_writes_target_inactive_and_leaves_the_project_active(tmp_path):
    path = make_db(28, tmp_path / "ts.sqlite")
    _, targets, projects = _push(path, _halves(south={"target_active": False}))
    assert targets["Split Nebula North"]["active"] == 1
    assert targets["Split Nebula South"]["active"] == 0
    assert projects["Split Nebula"]["state"] == 1


def test_mosaic_with_one_panel_off(tmp_path):
    path = make_db(28, tmp_path / "ts.sqlite")
    pl = plan("m", target_name="Wide Field", rows=2, cols=1, panel_active={"2,1": False})
    _, targets, _ = _push(path, [pl])
    assert targets["Wide Field Panel 1 (R1C1)"]["active"] == 1
    assert targets["Wide Field Panel 2 (R2C1)"]["active"] == 0


def test_plan_off_turns_every_panel_off(tmp_path):
    path = make_db(28, tmp_path / "ts.sqlite")
    pl = plan("m", target_name="Wide Field", rows=1, cols=2, target_active=False)
    _, targets, _ = _push(path, [pl])
    assert {t["active"] for t in targets.values()} == {0}


def test_switches_are_written_on_update_both_ways(tmp_path):
    path = make_db(28, tmp_path / "ts.sqlite")
    _push(path, _halves())
    _, targets, _ = _push(path, _halves(south={"target_active": False}))
    assert targets["Split Nebula South"]["active"] == 0
    report, targets, _ = _push(path, _halves())
    assert targets["Split Nebula South"]["active"] == 1
    assert report.target.updated == 2 and report.target.inserted == 0

    pl = plan("m", target_name="Wide Field", rows=2, cols=1)
    _push(path, [pl])
    pl["panel_active"] = {"1,1": False}
    _, targets, _ = _push(path, [pl])
    assert targets["Wide Field Panel 1 (R1C1)"]["active"] == 0
    assert targets["Wide Field Panel 2 (R2C1)"]["active"] == 1


def test_a_plan_without_the_fields_pushes_as_before(tmp_path):
    path = make_db(28, tmp_path / "ts.sqlite")
    _, targets, _ = _push(path, [plan("a"), plan("m", target_name="Wide Field", rows=2, cols=2)])
    assert len(targets) == 5
    assert {t["active"] for t in targets.values()} == {1}


def test_a_collapsed_mosaic_follows_the_plan_switch_only(tmp_path):
    """With no telescope the mosaic reaches TS as one target, so a panel
    switch has nothing to apply to."""
    path = make_db(28, tmp_path / "ts.sqlite")
    pl = plan("m", target_name="No Fov", rows=2, cols=1, telescope_id="", panel_active={"1,1": False})
    _, targets, _ = _push(path, [pl])
    assert targets["No Fov"]["active"] == 1


def test_a_target_with_a_foreign_guid_is_left_alone(tmp_path):
    """Only targets ACP made or claimed are written: a row of the same name
    stamped by another tool is not touched."""
    path = make_db(28, tmp_path / "ts.sqlite")
    _push(path, _halves())
    conn = sqlite3.connect(path)
    conn.execute("UPDATE target SET guid = 'someone-else', active = 1 WHERE name = 'Split Nebula South'")
    conn.commit()
    conn.close()
    _, _, _ = _push(path, _halves(south={"target_active": False}))
    conn = sqlite3.connect(path)
    try:
        foreign = conn.execute("SELECT active FROM target WHERE guid = 'someone-else'").fetchone()
    finally:
        conn.close()
    assert foreign[0] == 1


# -- import and merge from TS ---------------------------------------------------

MOSAIC_GEAR = {
    "telescopes": [{"id": "long", "name": "Long", "focal_length_mm": 1000.0}],
    "cameras": [{"id": "small", "name": "Small", "pixel_size_um": 2.9, "sensor_px": [3856, 2180]}],
}


def _acp_mosaic():
    return {
        "id": "m", "project_name": "Mosaic", "priority": "normal",
        "min_altitude_deg": 30, "meridian_window_min": 0,
        "telescope_id": "long", "camera_id": "small",
        "target": {"name": "Wide Field", "center_ra_deg": 270.9, "center_dec_deg": -24.38,
                   "rotation_deg": 0.0, "mosaic": {"rows": 2, "cols": 1, "overlap_pct": 20}},
        "filter_goals": {"Ha": {"target_hours": 2.0, "sub_exposure_s": 300}},
    }


def _push_then_import(tmp_path, plans, gear, inactive_names=()):
    """Push *plans*, turn the named targets inactive in TS by hand, then
    import the profile back the way /import/apply does."""
    from nina_ts_sync.from_ts import import_to_acp_plans
    from nina_ts_sync.reader import read_all
    from nina_ts_sync.state import build_base_snapshot_from_ids
    path = make_db(28, tmp_path / "ts.sqlite")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        upsert.apply(conn, convert.build_payload(plans, gear, profile_id="p"))
        for name in inactive_names:
            conn.execute("UPDATE target SET active = 0 WHERE name = ?", (name,))
        conn.commit()
        snap = read_all(conn, "p")
        imported, _, id_maps = import_to_acp_plans(snap, gear)
        bases = [build_base_snapshot_from_ids(snap, plan=pl, **idm) for pl, idm in zip(imported, id_maps)]
        return imported, bases
    finally:
        conn.close()


def test_an_inactive_single_target_imports_switched_off(tmp_path):
    plans, _ = _push_then_import(tmp_path, _halves(), GEAR, inactive_names=["Split Nebula South"])
    by_name = {p["target"]["name"]: p for p in plans}
    assert by_name["Split Nebula South"]["target_active"] is False
    assert "panel_active" not in by_name["Split Nebula South"]
    assert "target_active" not in by_name["Split Nebula North"]


def test_a_mosaic_with_one_inactive_panel_imports_with_that_panel_off(tmp_path):
    (plan,), _ = _push_then_import(tmp_path, [_acp_mosaic()], MOSAIC_GEAR,
                                   inactive_names=["Wide Field Panel 2 (R2C1)"])
    assert plan["target"]["mosaic"]["rows"] == 2
    assert plan["panel_active"] == {"2,1": False}
    assert "target_active" not in plan


def test_a_mosaic_with_every_panel_inactive_imports_with_the_plan_off(tmp_path):
    (plan,), _ = _push_then_import(
        tmp_path, [_acp_mosaic()], MOSAIC_GEAR,
        inactive_names=["Wide Field Panel 1 (R1C1)", "Wide Field Panel 2 (R2C1)"])
    assert plan["target_active"] is False
    assert "panel_active" not in plan


def test_an_all_active_import_adds_no_fields(tmp_path):
    plans, _ = _push_then_import(tmp_path, _halves() + [_acp_mosaic()], {
        "telescopes": GEAR["telescopes"] + MOSAIC_GEAR["telescopes"],
        "cameras": GEAR["cameras"] + MOSAIC_GEAR["cameras"],
    })
    assert len(plans) == 3
    for p in plans:
        assert "target_active" not in p and "panel_active" not in p


def test_the_base_snapshot_records_active(tmp_path):
    _, (base,) = _push_then_import(tmp_path, [_acp_mosaic()], MOSAIC_GEAR,
                                   inactive_names=["Wide Field Panel 2 (R2C1)"])
    assert {k: t["active"] for k, t in base["targets_by_panel"].items()} == {"1,1": 1, "2,1": 0}


def test_a_panel_turned_off_in_ts_is_pulled_into_a_linked_plan():
    """The merge path: a plan already linked, a panel made inactive in TS
    since the last sync. The diff pulls it, and the pull stores only off."""
    from nina_ts_sync.diff import SCHEDULING, base_snapshot_to_plan_shape, diff_plan
    from nina_ts_sync.merge import apply_auto_pull
    local = _acp_mosaic()
    base_snap = {"mosaic": {"rows": 2, "cols": 1, "overlap_pct": 20},
                 "targets_by_panel": {"1,1": {"name": "Wide Field Panel 1 (R1C1)", "active": 1},
                                      "2,1": {"name": "Wide Field Panel 2 (R2C1)", "active": 1}}}
    remote = {**_acp_mosaic(), "panel_active": {"2,1": False}}
    d = diff_plan("m", base_snapshot_to_plan_shape(base_snap), local, remote)
    assert SCHEDULING in d.auto_pull
    assert d.remote_values[SCHEDULING] == "off: R2C1"
    apply_auto_pull(local, remote, d.auto_pull)
    assert local["panel_active"] == {"2,1": False}
    assert "target_active" not in local and "scheduling" not in local["target"]

    # Turned back on in TS: the pull drops the field again.
    back_on = _acp_mosaic()
    base2 = {**base_snap, "targets_by_panel": {
        "1,1": {"name": "Wide Field Panel 1 (R1C1)", "active": 1},
        "2,1": {"name": "Wide Field Panel 2 (R2C1)", "active": 0}}}
    d = diff_plan("m", base_snapshot_to_plan_shape(base2), local, back_on)
    assert SCHEDULING in d.auto_pull
    apply_auto_pull(local, back_on, d.auto_pull)
    assert "panel_active" not in local


def test_a_switch_set_in_acp_is_kept_against_an_unchanged_ts():
    from nina_ts_sync.diff import SCHEDULING, base_snapshot_to_plan_shape, diff_plan
    local = {**_acp_mosaic(), "target_active": False}
    base_snap = {"mosaic": {"rows": 2, "cols": 1, "overlap_pct": 20},
                 "targets_by_panel": {"1,1": {"name": "Wide Field Panel 1 (R1C1)", "active": 1},
                                      "2,1": {"name": "Wide Field Panel 2 (R2C1)", "active": 1}}}
    d = diff_plan("m", base_snapshot_to_plan_shape(base_snap), local, _acp_mosaic())
    assert SCHEDULING in d.auto_keep


def test_target_active_helper():
    assert convert.target_active({}) is True
    assert convert.target_active({"target_active": False}) is False
    assert convert.target_active({"panel_active": {"1,2": False}}, "1,2") is False
    assert convert.target_active({"panel_active": {"1,2": False}}, "1,1") is True
    assert convert.target_active({"panel_active": {"1,2": False}}) is True
