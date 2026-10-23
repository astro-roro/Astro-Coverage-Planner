"""Timing cases around uploads: a double-clicked Apply, a worker result that
went stale while it ran, two applies in one second, and the gear default
not being frozen by an apply that made no choice."""
from __future__ import annotations

import threading

import pytest

from nina_ts_sync import config, uploads
from nina_ts_sync.import_merge import PLANS_LOCK
from .host import P, Q, FakeHost, link, new_db, push
from .plans import plan


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch)


def _db(host, tmp_path, *, profiles=(P,)):
    db = new_db(tmp_path / "ts.sqlite")
    a = plan("a", project_name="M31")
    for pid in profiles:
        push(db, [a], pid)
    link(db, [a], profiles[0])
    host.write_plans([a])
    # Each rig's new plan in its own patch of sky, so one rig's apply does
    # not make the other's a possible duplicate.
    for i, pid in enumerate(profiles):
        push(db, [plan(f"new-{pid}", project_name=f"Rosette {pid}", ra=98.0 + 40 * i, dec=5.0)], pid)
    return db


def test_a_second_apply_of_the_same_upload_is_404(host, tmp_path):
    status = host.upload_ready(_db(host, tmp_path))
    results = []

    def go():
        results.append(host.app.test_client().post(
            f"/api/ext/nina-ts-sync/import/uploads/{status['upload_id']}/apply",
            json={"profile_id": P, "sha256": status["sha256"],
                  "preview_hash": status["preview"]["preview_hash"], "decisions": {}}).status_code)

    with PLANS_LOCK:  # hold both requests at the door, then let them race
        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == [200, 404]
    assert len(host.plans()) == 2


def test_a_stale_worker_result_is_not_published(host, tmp_path):
    status = host.upload_ready(_db(host, tmp_path, profiles=(P, Q)))
    uid = status["upload_id"]
    # A re-preview for Q is queued while a job for P is still running.
    uploads.update_state(uid, state="checking", preview=None, profile_id=Q)
    uploads.run_preview(uid, P)
    state = uploads.load_state(uid)
    assert state["state"] == "checking" and state["preview"] is None
    uploads.run_preview(uid, Q)
    state = uploads.load_state(uid)
    assert state["state"] == "ready" and state["preview"]["profile_id"] == Q


def test_a_superseded_upload_is_not_flipped_back_to_ready(host, tmp_path):
    db = _db(host, tmp_path)
    first = host.upload_ready(db)
    uid = first["upload_id"]
    uploads.update_state(uid, state="checking")
    host.upload_ready(db)  # supersedes the first
    uploads.run_preview(uid, P)
    assert uploads.load_state(uid)["state"] == "superseded"


def test_two_applies_in_one_second_keep_two_backups(host, tmp_path):
    db = _db(host, tmp_path, profiles=(P, Q))
    s1 = host.upload_ready(db, profile_id=P)
    s2 = host.upload_ready(db, profile_id=Q)
    assert host.apply(s1).status_code == 200
    # Plan "a" and the plan P's apply adds are linked to P only, so Q's
    # scope did not move and its preview still stands.
    r = host.apply(s2)
    assert r.status_code == 200, r.get_json()
    assert len(host.backups()) == 2


def test_an_apply_without_a_gear_choice_saves_nothing(host, tmp_path):
    status = host.upload_ready(_db(host, tmp_path))
    assert host.apply(status).status_code == 200
    assert P not in (config.load().get("new_plan_gear") or {})
