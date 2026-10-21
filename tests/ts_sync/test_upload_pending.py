"""GET /import/uploads/pending: every upload ACP core should mention on a
banner, because it is still waiting on a choice. Newest first; superseded,
applied and expired uploads never show up."""
from __future__ import annotations

import json
import time

import pytest

from nina_ts_sync import uploads
from .host import P, Q, FakeHost, new_db, push
from .plans import plan


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch)


def _db(tmp_path, name="ts.sqlite"):
    db = new_db(tmp_path / name)
    push(db, [plan("a", project_name="M31")], P)
    return db


def _pending(host):
    r = host.client.get("/api/ext/nina-ts-sync/import/uploads/pending")
    assert r.status_code == 200
    return r.get_json()["uploads"]


def test_nothing_waiting_is_an_empty_list(host):
    assert _pending(host) == []


def test_a_ready_upload_is_listed_with_its_summary_and_counts(host, tmp_path):
    status = host.upload_ready(_db(tmp_path))
    out = _pending(host)
    assert len(out) == 1
    row = out[0]
    assert row["upload_id"] == status["upload_id"]
    assert row["state"] == "ready"
    assert row["machine"] == status["machine"]
    assert row["profile_name"] == "Voyager main"
    assert row["summary"] == status["preview"]["summary"]
    assert row["counts"] == status["counts"]
    assert row["review_url"] == status["review_url"]
    assert row["created_iso"] == status["created_iso"]
    assert row["expires_iso"] == status["expires_iso"]


def test_a_checking_upload_is_listed_with_no_preview_yet(host, tmp_path):
    status = host.upload_ready(_db(tmp_path))
    uid = status["upload_id"]
    uploads.update_state(uid, state="checking", preview=None)
    out = _pending(host)
    assert len(out) == 1
    row = out[0]
    assert row["state"] == "checking"
    assert row["summary"] is None
    assert row["counts"] == {"new": 0, "updated": 0, "conflicts": 0}
    # meta.profiles carries the name even before the worker has run again.
    assert row["profile_name"] == "Voyager main"
    assert row["expires_iso"] == status["expires_iso"]


def test_a_superseded_upload_is_excluded_and_the_new_one_is_listed(host, tmp_path):
    db = _db(tmp_path)
    first = host.upload_ready(db)
    second = host.upload_ready(db)
    out = _pending(host)
    ids = {row["upload_id"] for row in out}
    assert first["upload_id"] not in ids
    assert second["upload_id"] in ids


def test_an_applied_upload_is_excluded(host, tmp_path):
    status = host.upload_ready(_db(tmp_path))
    r = host.apply(status)
    assert r.status_code == 200, r.get_json()
    assert _pending(host) == []


def test_an_expired_upload_is_excluded(host, tmp_path):
    status = host.upload_ready(_db(tmp_path))
    uid = status["upload_id"]
    state_file = uploads.state_path(uid)
    state = json.loads(state_file.read_text())
    state["created"] = time.time() - 25 * 3600
    state_file.write_text(json.dumps(state))
    assert _pending(host) == []


def test_newest_upload_comes_first(host, tmp_path):
    first = host.upload_ready(_db(tmp_path, "a.sqlite"), profile_id=P)
    second = host.upload_ready(_db(tmp_path, "b.sqlite"), profile_id=Q)
    out = _pending(host)
    assert [row["upload_id"] for row in out] == [second["upload_id"], first["upload_id"]]


def test_a_discarded_upload_is_excluded(host, tmp_path):
    status = host.upload_ready(_db(tmp_path))
    r = host.client.delete(f"/api/ext/nina-ts-sync/import/uploads/{status['upload_id']}")
    assert r.status_code == 200
    assert _pending(host) == []
