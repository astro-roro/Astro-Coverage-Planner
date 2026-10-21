"""The TS import has to carry every row across, or say what it could not.

The fixture is the core of Voyager's Target Scheduler database on 2026-09-24:
16 projects made by hand in TS, 79 targets and 244 exposure plans. The import
turns it into ACP plans with ts_refs naming the rows each plan came from. The
plugin's writer then updates those rows by Id, so anything the import gets
wrong here is written back over the user's own TS data on the next push.

Each test runs the import the way `/import/apply` does, then feeds the plans
through the extension's own payload builder to see what a push would write.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nina_ts_sync import convert, upsert
from nina_ts_sync.from_ts import import_to_acp_plans
from nina_ts_sync.reader import read_all
from nina_ts_sync.schema import row_plan_guid, target_guid
from nina_ts_sync.state import build_base_snapshot_from_ids, build_ts_refs_from_ids
from .make_db import make_db

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROFILE = "9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7"
GEAR = json.loads((FIXTURES / "gear-voyager.json").read_text(encoding="utf-8"))

WITCH_HEAD = 1
SCULPTOR = 11
PARKED = 2


def _load_snapshot(tmp_path) -> sqlite3.Connection:
    path = make_db(23, tmp_path / "voyager.sqlite")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript((FIXTURES / "ts-snapshot-voyager.sql").read_text(encoding="utf-8"))
    conn.commit()
    return conn


def _import(conn, gear=GEAR, profile=PROFILE):
    """What /import/apply stores: the plans, with ts_refs and a base snapshot."""
    snap = read_all(conn, profile)
    plans, notes, id_maps = import_to_acp_plans(snap, gear)
    for plan, idmap in zip(plans, id_maps):
        plan["ts_refs"] = build_ts_refs_from_ids(
            profile_id=profile, user_version=23, operation="import", **idmap)
        plan["ts_base_snapshot"] = build_base_snapshot_from_ids(snap, plan=plan, **idmap)
    return plans, notes


@pytest.fixture
def voyager(tmp_path):
    conn = _load_snapshot(tmp_path)
    try:
        plans, notes = _import(conn)
        yield conn, plans, notes
    finally:
        conn.close()


def _rows(conn, table):
    return {r["Id"]: r for r in conn.execute(f"SELECT * FROM {table}")}


def _payload_targets(plans, gear=GEAR, profile=PROFILE):
    """Every target a push would write, as (project name, target) pairs."""
    payload = convert.build_payload(plans, gear, profile_id=profile)
    name_by_guid = {p.guid: p.name for p in payload.projects}
    return payload, [
        (name_by_guid[pg], t)
        for pg, targets in payload.targets_by_project_guid.items() for t in targets
    ]


# -- 1: two exposure plans with one filter on one target stay two rows -------


def _row_ids(plan) -> set[str]:
    return {r["id"] for r in plan["filter_rows"]}


def test_every_exposure_plan_is_named_by_exactly_one_row(voyager):
    conn, plans, _ = voyager
    named: dict[int, list[str]] = {}
    for plan in plans:
        refs = plan["ts_refs"]
        for key, ep_id in refs["exposure_plan_ids"].items():
            _, row_id = key.split("_", 1)
            assert row_id in _row_ids(plan), (plan["target"]["name"], key)
            named.setdefault(ep_id, []).append(plan["target"]["name"])

    enabled = {i for i, r in _rows(conn, "exposureplan").items() if r["enabled"] != 0}
    assert set(named) == enabled
    assert {i: n for i, n in named.items() if len(n) > 1} == {}


def test_sculptor_keeps_its_star_frames(voyager):
    """Sculptor's "R Stars", "G Stars" and "B Stars" rows hold 54 accepted
    frames between them. Keyed by filter, the empty R, G and B rows replaced
    them and ACP showed none. As filter rows they are rows of R, G and B."""
    _, plans, _ = voyager
    (sculptor,) = [p for p in plans if p["ts_refs"]["project_id"] == SCULPTOR]
    rows = {r["template"]: r for r in sculptor["filter_rows"]}

    assert {"R", "G", "B", "R Stars", "G Stars", "B Stars"} <= set(rows)
    for name in ("R", "G", "B"):
        assert rows[name]["template_sub_s"] == 60
        assert rows[name]["filter"] == name
    for name in ("R Stars", "G Stars", "B Stars"):
        row = rows[name]
        assert row["filter"] == name.split()[0]
        assert (row["template_sub_s"], row["unit"]) == (10, "fr")
        assert row["actual_hours"] == pytest.approx(18 * 10 / 3600)
    ids = sculptor["ts_refs"]["exposure_plan_ids"]
    assert {ids[f"21_{rows[n]['id']}"] for n in ("R Stars", "G Stars", "B Stars")} == {120, 121, 122}
    assert sculptor["ts_refs"]["template_ids_by_filter"][rows["R Stars"]["id"]] == 5


# -- 3: desired comes back exactly -----------------------------------------


def test_a_push_writes_back_the_desired_count_ts_has(voyager):
    conn, plans, _ = voyager
    eps = _rows(conn, "exposureplan")
    payload, targets = _payload_targets(plans)
    plan_by_target = {}
    for plan in plans:
        pname = plan["project_name"]
        plan_by_target[(pname, plan["target"]["name"])] = plan

    checked = 0
    drift = []
    for pname, tgt in targets:
        plan = plan_by_target[(pname, tgt.name)]
        tid = plan["ts_refs"]["target_ids_by_panel"]["1,1"]
        for row in plan["filter_rows"]:
            ep_id = plan["ts_refs"]["exposure_plan_ids"].get(f"{tid}_{row['id']}")
            written = next((
                ep for ep in payload.plans_by_target_guid.get(tgt.guid, [])
                if ep.guid == row_plan_guid(PROFILE, tgt.guid, row["filter"], row["template"])), None)
            ts = eps[ep_id]
            if ts["desired"] <= 0:
                assert written is None
                continue
            checked += 1
            # A push never writes `exposure` to a plan it found, so only
            # `desired` has to come back.
            if written.desired != ts["desired"]:
                drift.append((pname, tgt.name, row["template"], ts["desired"], written.desired))

    assert drift == []
    assert checked == sum(1 for r in eps.values() if r["desired"] > 0 and r["enabled"] != 0)


# -- 4: a mosaic ACP did not make keeps its panels ---------------------------


def test_witch_head_panels_keep_their_names_and_centres(voyager):
    conn, plans, notes = voyager
    ts_targets = {
        r["name"]: r for r in _rows(conn, "target").values() if r["projectid"] == WITCH_HEAD}
    _, targets = _payload_targets([p for p in plans if p["ts_refs"]["project_id"] == WITCH_HEAD])

    written = {t.name: t for _, t in targets}
    assert set(written) == set(ts_targets)
    for name, t in written.items():
        # The writer stores degrees / 15. Some hour values have no double in
        # degrees that divides back to them, so the last bit can differ.
        assert t.ra == pytest.approx(ts_targets[name]["ra"], rel=0, abs=1e-14), name
        assert t.dec == ts_targets[name]["dec"], name
        assert t.rotation == ts_targets[name]["rotation"], name

    witch = [n for n in notes if n.project == "Witch Head (Dark Skies)" and n.level == "loss"]
    assert any("mosaic" in n.message for n in witch)


def test_an_acp_mosaic_is_rebuilt_with_the_gear_that_made_it(tmp_path):
    """A 2x2 mosaic pushed by ACP with the second telescope and camera in the
    list comes back as that mosaic, on that gear, with panels that land where
    TS has them."""
    gear = {
        "telescopes": [
            {"id": "wide", "name": "Wide", "focal_length_mm": 250.0},
            {"id": "long", "name": "Long", "focal_length_mm": 1000.0},
        ],
        "cameras": [
            {"id": "big", "name": "Big", "pixel_size_um": 3.76, "sensor_px": [9576, 6388]},
            {"id": "small", "name": "Small", "pixel_size_um": 2.9, "sensor_px": [3856, 2180]},
        ],
    }
    acp_plan = {
        "id": "m", "project_name": "Mosaic", "priority": "normal",
        "min_altitude_deg": 30, "meridian_window_min": 0,
        "telescope_id": "long", "camera_id": "small",
        "target": {"name": "M8", "center_ra_deg": 270.9, "center_dec_deg": -24.38,
                   "rotation_deg": 30.0, "mosaic": {"rows": 2, "cols": 2, "overlap_pct": 20}},
        "filter_goals": {"Ha": {"target_hours": 2.0, "sub_exposure_s": 300}},
    }
    path = make_db(28, tmp_path / "acp.sqlite")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        upsert.apply(conn, convert.build_payload([acp_plan], gear, profile_id="p"))
        conn.commit()
        ts = {r["name"]: r for r in conn.execute("SELECT * FROM target")}
        plans, notes = _import(conn, gear=gear, profile="p")
    finally:
        conn.close()

    (plan,) = plans
    assert (plan["telescope_id"], plan["camera_id"]) == ("long", "small")
    assert plan["target"]["name"] == "M8"
    assert plan["target"]["mosaic"] == {"rows": 2, "cols": 2, "overlap_pct": 20}
    _, targets = _payload_targets(plans, gear=gear, profile="p")
    assert {t.name for _, t in targets} == set(ts)
    for _, t in targets:
        assert abs(t.ra - ts[t.name]["ra"]) * 15 * 3600 < 0.1
        assert abs(t.dec - ts[t.name]["dec"]) * 3600 < 0.1
    assert [n for n in notes if n.level == "loss"] == []


# -- 5: project state --------------------------------------------------------


def test_a_parked_project_arrives_as_an_inactive_plan(voyager):
    """A TS project that's inactive becomes an ACP plan with state:
    "inactive". State is always written explicitly, for every project,
    and none of the four states raises a loss note."""
    conn, plans, notes = voyager
    parked = {r["Id"]: r["name"] for r in _rows(conn, "project").values() if r["state"] == PARKED}
    assert SCULPTOR in parked

    for plan in plans:
        pid = plan["ts_refs"]["project_id"]
        assert plan["ts_base_snapshot"]["project"]["state"] == (PARKED if pid in parked else 1)
        assert plan["state"] == ("inactive" if pid in parked else "active")
    assert not [n for n in notes if n.level == "loss" and "state" in n.message]


def test_import_maps_all_four_states_priority_zero_and_minimum_time(tmp_path):
    """A round trip through a fresh push: four projects, one per state, one
    of them priority 0 (low) and one with a minimum time of 30. None of the
    four states raises a loss note, priority 0 comes back as "low" (not
    "normal": the same falsy-zero bug state.py had), and minimumtime 30
    comes back as minimum_time_min: 30."""
    from .plans import GEAR as PLANS_GEAR, PROFILE_ID as PLANS_PROFILE
    from .plans import plan as _plan

    plans_in = [
        _plan("draft-plan", project_name="Draft Project", state="draft"),
        _plan("active-plan", project_name="Active Project"),
        _plan("inactive-plan", project_name="Inactive Project", state="inactive", priority="low"),
        _plan("closed-plan", project_name="Closed Project", state="closed", minimum_time_min=30),
    ]
    conn = sqlite3.connect(make_db(28, tmp_path / "roundtrip.sqlite"))
    conn.row_factory = sqlite3.Row
    upsert.apply(conn, convert.build_payload(plans_in, PLANS_GEAR, profile_id=PLANS_PROFILE))
    conn.commit()

    snap = read_all(conn, PLANS_PROFILE)
    plans_out, notes, _ = import_to_acp_plans(snap, PLANS_GEAR)

    by_name = {p["project_name"]: p for p in plans_out}
    assert by_name["Draft Project"]["state"] == "draft"
    assert by_name["Active Project"]["state"] == "active"
    assert by_name["Inactive Project"]["state"] == "inactive"
    assert by_name["Inactive Project"]["priority"] == "low"
    assert by_name["Closed Project"]["state"] == "closed"
    assert by_name["Closed Project"]["minimum_time_min"] == 30
    assert not [n for n in notes if n.level == "loss" and "state" in n.message]


# -- 2: everything the import cannot carry exactly is reported ---------------


def test_loss_notes_name_the_project_and_target(voyager):
    _, _, notes = voyager
    losses = [n for n in notes if n.level == "loss"]
    assert losses
    for n in losses:
        assert n.project and n.project in n.message
        if n.target:
            assert n.target in n.message


def test_a_plan_on_its_template_default_takes_that_sub(voyager):
    """184 exposure plans use their template's default length (-1). Their
    rows carry the template's default as `template_sub_s`, the sub TS uses,
    and nothing is reported: ACP no longer writes a sub length."""
    conn, plans, notes = voyager
    templates = _rows(conn, "exposuretemplate")
    eps = _rows(conn, "exposureplan")
    checked = 0
    for plan in plans:
        tid = plan["ts_refs"]["target_ids_by_panel"]["1,1"]
        for row in plan["filter_rows"]:
            ep = eps[plan["ts_refs"]["exposure_plan_ids"][f"{tid}_{row['id']}"]]
            if ep["exposure"] > 0:
                continue
            checked += 1
            assert row["template_sub_s"] == templates[ep["exposureTemplateId"]]["defaultexposure"]
    assert checked > 0
    assert not [n for n in notes if "template default" in n.message]


def test_an_exposure_plan_without_its_template_is_reported(tmp_path):
    conn = _load_snapshot(tmp_path)
    try:
        conn.execute("DELETE FROM exposuretemplate WHERE Id = 5")  # R Stars
        conn.commit()
        plans, notes = _import(conn)
    finally:
        conn.close()
    (sculptor,) = [p for p in plans if p["ts_refs"]["project_id"] == SCULPTOR]
    assert "R Stars" not in {r["template"] for r in sculptor["filter_rows"]}
    assert any(
        n.level == "loss" and n.project == "Sculptor Galaxy (Any Skies)"
        and n.target == "Sculptor Galaxy" and "120" in n.message
        for n in notes)


def test_every_plan_is_imported(voyager):
    """No project with targets drops out: 76 plans from before, plus three
    more Witch Head panels now that its mosaic is kept as four targets."""
    _, plans, _ = voyager
    assert len(plans) == 79
    assert len({p["id"] for p in plans}) == len(plans)
    assert len({(p["project_name"], p["target"]["name"]) for p in plans}) == 79


def test_the_payload_builds_without_a_name_clash(voyager):
    _, plans, _ = voyager
    convert.build_payload(plans, GEAR, profile_id=PROFILE)
