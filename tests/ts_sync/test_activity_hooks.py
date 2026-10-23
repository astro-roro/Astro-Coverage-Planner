"""The extension's side of ACP's Activity tab (docs/specs/activity-tab.md,
acceptance tests 36 to 38), against a FakeHost whose record_activity appends
to a list."""
from __future__ import annotations

from nina_ts_sync import activity
from .host import MACHINE, P, FakeHost, TOKEN, link, new_db, push
from .plans import plan

import threading

import pytest


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch, activity=True)


def _ready_upload(host, tmp_path):
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [plan("a", project_name="M31")], P)
    return host.upload_ready(db)


def _types(host):
    return [e["type"] for e in host.activity]


def test_36_an_upload_records_received_with_its_resolve_key(host, tmp_path):
    status = _ready_upload(host, tmp_path)
    uid = status["upload_id"]
    [event] = host.activity
    assert event["type"] == "ts_upload_received"
    assert event["needs_you"] is True
    assert event["resolve_key"] == f"upload:{uid}"
    assert event["refs"]["upload_id"] == uid


def test_36_applying_records_applied_and_resolves_the_upload(host, tmp_path):
    status = _ready_upload(host, tmp_path)
    uid = status["upload_id"]
    r = host.apply(status)
    assert r.status_code == 200, r.get_json()
    applied = host.activity[-1]
    assert applied["type"] == "ts_upload_applied"
    assert applied["resolves"] == [f"upload:{uid}"]
    assert applied["data"]["plans_changed"] >= 1


def test_36_discarding_records_discarded_and_resolves_the_upload(host, tmp_path):
    status = _ready_upload(host, tmp_path)
    uid = status["upload_id"]
    r = host.client.delete(f"/api/ext/nina-ts-sync/import/uploads/{uid}")
    assert r.status_code == 200
    assert _types(host) == ["ts_upload_received", "ts_upload_discarded"]
    assert host.activity[-1]["resolves"] == [f"upload:{uid}"]


def _push_links(host, db, n=1):
    plans = [plan("a", project_name="M31")]
    host.write_plans(plans)
    push(db, plans, P)
    link(db, plans, P)
    states = [{"plan_id": "a", "refs": plans[0]["ts_links"][P]["refs"],
               "base_snapshot": plans[0]["ts_links"][P].get("base_snapshot")}]
    out = []
    for _ in range(n):
        out.append(host.client.post(
            "/api/ext/nina-ts-sync/links",
            json={"profile_id": P, "machine": MACHINE, "states": states},
            headers={"Authorization": f"Bearer {TOKEN}"}))
    return out


def test_37_a_links_push_records_rig_synced_once_per_rig_per_night(host, tmp_path):
    db = new_db(tmp_path / "ts.sqlite")
    replies = _push_links(host, db, n=3)
    assert [r.status_code for r in replies] == [200, 200, 200]
    events = [e for e in host.activity if e["type"] == "rig_synced"]
    assert len(events) == 3
    assert {e["key"] for e in events} == {f"rig_synced:{P}:{activity.night_now()}"}


def test_38_a_host_without_record_activity_still_answers_200(tmp_path, monkeypatch):
    bare = FakeHost(tmp_path, monkeypatch)
    assert not hasattr(bare.app, "record_activity")
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [plan("a", project_name="M31")], P)
    assert bare.upload(db).status_code == 202
    assert [r.status_code for r in _push_links(bare, db)] == [200]


def test_a_failing_record_activity_never_fails_the_route(host, tmp_path):
    def boom(**_):
        raise RuntimeError("down")
    host.app.record_activity = boom
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [plan("a", project_name="M31")], P)
    assert host.upload(db).status_code == 202


def _lock_is_free():
    """Whether another thread can take the plans lock right now."""
    from nina_ts_sync import import_merge
    got = []

    def probe():
        ok = import_merge.PLANS_LOCK.acquire(timeout=1)
        got.append(ok)
        if ok:
            import_merge.PLANS_LOCK.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return bool(got and got[0])


def test_apply_records_activity_after_the_plans_lock_is_released(host, tmp_path):
    status = _ready_upload(host, tmp_path)
    held = []
    inner = host.app.record_activity

    def spy(**fields):
        held.append(not _lock_is_free())
        inner(**fields)

    host.app.record_activity = spy
    assert host.apply(status).status_code == 200
    assert held == [False]


def test_38_apply_and_discard_still_answer_on_a_host_without_record_activity(tmp_path, monkeypatch):
    bare = FakeHost(tmp_path, monkeypatch)
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [plan("a", project_name="M31")], P)
    status = bare.upload_ready(db)
    assert bare.apply(status).status_code == 200
    db2 = new_db(tmp_path / "ts2.sqlite")
    push(db2, [plan("b", project_name="M33")], P)
    second = bare.upload_ready(db2)
    r = bare.client.delete(f"/api/ext/nina-ts-sync/import/uploads/{second['upload_id']}")
    assert r.status_code == 200
