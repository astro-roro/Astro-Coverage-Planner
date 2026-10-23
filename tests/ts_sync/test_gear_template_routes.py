"""The routes that carry the gear template writer's inputs and results
(docs/specs/gear-templates.md section 8, S21, S22 and S29), and the sub
refresh rule on the sync and links routes."""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from . import row_scenarios as sc
from .host import FakeHost
from .row_scenarios import GEAR_PROFILE


# A literal entry captured from ACP's own `_store_fingerprint`, so the
# nesting is ACP's and not a guess (tests/fixtures/acp-fingerprint-entry.json).
_ACP_ENTRY = json.loads((Path(__file__).parent / "fixtures" / "acp-fingerprint-entry.json").read_text())


def _acp_entry(profile_id=None, wheel_slots=None):
    e = copy.deepcopy(_ACP_ENTRY)
    if profile_id is not None:
        e["profile_id"] = e["fingerprint"]["profile_id"] = profile_id
    if wheel_slots is not None:
        e["fingerprint"]["wheel_slots"] = wheel_slots
    return e


@pytest.fixture
def host(tmp_path, monkeypatch):
    h = FakeHost(tmp_path, monkeypatch)
    h.write_gear(sc.gear_v3())
    return h


def _sync(host, db, **body):
    return host.client.post(
        "/api/ext/nina-ts-sync/sync",
        json={"profile_id": GEAR_PROFILE, "db_path": str(db), "profile_name": "Foo", **body})


def _gear_ha(host, hours=3.0):
    return sc.gear_plan("gear-ha", "M42", sc.CAMERA_6200, sc.gear_row(host.gear(), "row-ha", "Ha", hours))


def test_s21_the_sync_route_and_the_links_route_record_the_same_results(host, tmp_path):
    gear = host.gear()
    sc.gear_template(gear, "Ha")["offset"] = 25
    host.write_gear(gear)
    db = sc.make_template_db(tmp_path / "ts.sqlite")
    host.write_plans([_gear_ha(host)])

    r = _sync(host, db, wheel_slots=sc.WHEEL_SLOTS)
    assert r.status_code == 200, r.get_json()
    assert len(host.template_result_calls) == 1
    profile, results = host.template_result_calls[0]
    assert profile == GEAR_PROFILE and len(results) == 10
    assert r.get_json()["report"]["templates"] == results
    after_sync = sc.gear_template(host.gear(), "Ha")["ts"][GEAR_PROFILE]

    # The same results arrive through the links route on a fresh copy of gear.
    host.write_gear(sc.gear_v3())
    r = host.client.post("/api/ext/nina-ts-sync/links", json={
        "profile_id": GEAR_PROFILE, "machine": "VOYAGER", "states": [],
        "templates": results, "capabilities": ["templates", "repoint"]})
    assert r.status_code == 200, r.get_json()
    assert len(host.template_result_calls) == 2
    assert host.template_result_calls[1] == host.template_result_calls[0]
    assert sc.gear_template(host.gear(), "Ha")["ts"][GEAR_PROFILE] == after_sync
    assert after_sync["id"] == 12 and after_sync["base"]["offset"] == 25


def test_s21_a_links_post_without_templates_records_none(host):
    r = host.client.post("/api/ext/nina-ts-sync/links", json={"profile_id": GEAR_PROFILE, "states": []})
    assert r.status_code == 200
    assert host.template_result_calls == []


def test_s22_the_sync_route_reads_the_wheel_from_the_fingerprint_store(host, tmp_path):
    gear = host.gear()
    ha = sc.gear_template(gear, "Ha")
    sc.gear_templates_of(gear, ha).append({
        "id": sc.EXTRA_HA_600, "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": 25, "bin": 1,
        "starred": False, "aliases": [], "ts_names": [], "ts": {}})
    host.write_gear(gear)
    host.write_plans([_gear_ha(host)])

    host.fingerprints[GEAR_PROFILE] = _acp_entry()
    db = sc.make_template_db(tmp_path / "with.sqlite")
    assert _sync(host, db).status_code == 200
    r = sqlite3.connect(db).execute("SELECT filtername, defaultexposure FROM exposuretemplate WHERE name = 'Ha 600'").fetchone()
    assert r == ("H", 600.0)

    # No entry for the profile: an empty list, and S9's behaviour.
    host.fingerprints.clear()
    db2 = sc.make_template_db(tmp_path / "without.sqlite")
    r = _sync(host, db2)
    assert r.status_code == 200
    assert sqlite3.connect(db2).execute("SELECT COUNT(*) FROM exposuretemplate WHERE name = 'Ha 600'").fetchone()[0] == 0
    results = {t["template_id"]: t for t in r.get_json()["report"]["templates"]}
    assert results[sc.EXTRA_HA_600]["action"] == "skipped"
    assert results[sc.EXTRA_HA_600]["note"] == "the wheel of Foo is unknown"


def test_s22_a_body_wheel_beats_the_store(host, tmp_path):
    gear = host.gear()
    ha = sc.gear_template(gear, "Ha")
    sc.gear_templates_of(gear, ha).append({
        "id": sc.EXTRA_HA_600, "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": 25, "bin": 1,
        "starred": False, "aliases": [], "ts_names": [], "ts": {}})
    host.write_gear(gear)
    host.write_plans([_gear_ha(host)])
    host.fingerprints[GEAR_PROFILE] = _acp_entry(wheel_slots=["Q"])
    db = sc.make_template_db(tmp_path / "ts.sqlite")
    assert _sync(host, db, wheel_slots=["L", "R", "G", "B", "Ha", "O", "S"]).status_code == 200
    r = sqlite3.connect(db).execute("SELECT filtername FROM exposuretemplate WHERE name = 'Ha 600'").fetchone()
    assert r == ("Ha",)


def test_s29_a_sync_leaves_links_capable_as_the_last_links_post_set_it(host, tmp_path):
    caps = {"profile_id": GEAR_PROFILE, "states": [], "capabilities": ["templates", "repoint"]}
    assert host.client.post("/api/ext/nina-ts-sync/links", json=caps).status_code == 200
    assert GEAR_PROFILE in host.links_capable
    host.write_plans([_gear_ha(host)])
    db = sc.make_template_db(tmp_path / "ts.sqlite")
    assert _sync(host, db, wheel_slots=sc.WHEEL_SLOTS).status_code == 200
    assert GEAR_PROFILE in host.links_capable
    assert all(c[0] != "sync" for c in host.capability_calls)
    assert host.client.post(
        "/api/ext/nina-ts-sync/links", json={"profile_id": GEAR_PROFILE, "states": []}).status_code == 200
    assert GEAR_PROFILE not in host.links_capable


def test_the_links_route_keeps_a_row_sub_acp_changed_since_the_last_push(host, tmp_path):
    gear = host.gear()
    p = sc.gear_plan("gear-ha", "M42", sc.CAMERA_6200, sc.gear_row(gear, "row-ha", "Ha", 3.0, 400.0))
    db = sc.make_template_db(tmp_path / "ts.sqlite")
    host.write_plans([p])
    assert _sync(host, db, wheel_slots=sc.WHEEL_SLOTS).status_code == 200
    plans = host.plans()
    assert plans[0]["filter_rows"][0]["template_sub_s"] == 400
    # ACP changes the row to 450; the plugin then reports a push that did not
    # write it (the base still says 400).
    base = plans[0]["ts_links"][GEAR_PROFILE]["base_snapshot"]
    plans[0]["filter_rows"][0]["template_sub_s"] = 450
    host.write_plans(plans)
    r = host.client.post("/api/ext/nina-ts-sync/links", json={
        "profile_id": GEAR_PROFILE, "states": [{"plan_id": "gear-ha", "refs": plans[0]["ts_links"][GEAR_PROFILE]["refs"],
                                                "base_snapshot": base}]})
    assert r.status_code == 200
    assert host.plans()[0]["filter_rows"][0]["template_sub_s"] == 450


def test_the_sync_route_records_template_results_after_releasing_the_plans_lock(host, tmp_path):
    # The fake host's record_template_results takes a GEAR_LOCK and asserts
    # this thread does not hold PLANS_LOCK (spec section 4: code holding
    # PLANS_LOCK never takes GEAR_LOCK). A regression is a 500 here.
    host.write_plans([_gear_ha(host)])
    db = sc.make_template_db(tmp_path / "ts.sqlite")
    r = _sync(host, db, wheel_slots=sc.WHEEL_SLOTS)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert host.template_result_calls, "the sync handed nothing to the host"


def test_the_fake_host_catches_a_gear_call_made_under_the_plans_lock(host):
    import app  # the fake host module
    with host.plans_lock:
        with pytest.raises(AssertionError, match="PLANS_LOCK"):
            app.record_template_results(GEAR_PROFILE, [])
        with pytest.raises(AssertionError, match="PLANS_LOCK"):
            app.set_links_capable(GEAR_PROFILE, [])


def test_wheel_slots_are_read_from_the_exact_structure_acp_produces(host, monkeypatch):
    import app
    from nina_ts_sync.api import wheel_slots_for
    slots = ["L", "R", "G", "B", "H", "O", "S"]
    store = {"version": 1, "profiles": {
        "other-id": _acp_entry(profile_id="other-id", wheel_slots=["X"]),
        GEAR_PROFILE: _acp_entry(),
        "Old Name": _acp_entry(profile_id="old-id", wheel_slots=["Y"]),
    }}
    monkeypatch.setattr(app, "load_fingerprints", lambda: store)
    assert wheel_slots_for({}, GEAR_PROFILE) == slots
    assert wheel_slots_for({}, "old-id") == ["Y"]
    assert wheel_slots_for({}, "unknown") == []
    monkeypatch.setattr(app, "load_fingerprints", lambda: {"version": 1, "profiles": {}})
    assert wheel_slots_for({}, GEAR_PROFILE) == []
    # ACP omits wheel_slots when the plugin sent none: that reads as unknown.
    bare = _acp_entry()
    del bare["fingerprint"]["wheel_slots"]
    monkeypatch.setattr(app, "load_fingerprints", lambda: {"version": 1, "profiles": {GEAR_PROFILE: bare}})
    assert wheel_slots_for({}, GEAR_PROFILE) == []


def test_a_plugin_sync_body_with_only_a_profile_id_gets_its_wheel_from_the_store(host, tmp_path):
    gear = host.gear()
    ha = sc.gear_template(gear, "Ha")
    sc.gear_templates_of(gear, ha).append({
        "id": sc.EXTRA_HA_600, "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": 25, "bin": 1,
        "starred": False, "aliases": [], "ts_names": [], "ts": {}})
    host.write_gear(gear)
    host.write_plans([_gear_ha(host)])
    host.fingerprints[GEAR_PROFILE] = _acp_entry()
    db = sc.make_template_db(tmp_path / "ts.sqlite")
    r = host.client.post("/api/ext/nina-ts-sync/sync", json={"profile_id": GEAR_PROFILE, "db_path": str(db)})
    assert r.status_code == 200, r.get_json()
    row = sqlite3.connect(db).execute(
        "SELECT filtername, defaultexposure FROM exposuretemplate WHERE name = 'Ha 600'").fetchone()
    assert row == ("H", 600.0)
