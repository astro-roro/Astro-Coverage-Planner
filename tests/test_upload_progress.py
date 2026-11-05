"""Generic upload progress: POST /api/uploads/<id>/progress, GET /api/uploads,
and the single Activity event when a transfer ends."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import upload_progress as up  # noqa: E402
from app import app  # noqa: E402

TOKEN = "upload-test-token-0123456789"
H = {"Authorization": f"Bearer {TOKEN}"}
T0 = datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc)


def body(**kw):
    base = {"state": "running", "files_done": 3, "files_total": 10, "bytes_done": 300,
            "bytes_total": 1000, "started_at": "2026-03-01T20:00:00+00:00"}
    base.update(kw)
    return base


@pytest.fixture
def client():
    td = Path(tempfile.mkdtemp())
    env = {"ACP_API_TOKEN": TOKEN, "ACP_ACTIVITY_DB": str(td / "activity.sqlite")}
    with mock.patch.dict(os.environ, env):
        os.environ.pop("ACP_ACTIVITY", None)
        activity.close()
        up.clear()
        yield app.test_client()
        up.clear()
        activity.close()


def _events(c):
    nights = c.get("/api/activity?nights=30", headers=H).get_json()["nights"]
    return [e for n in nights for e in n["events"]]


def test_a_running_transfer_is_stored_and_listed(client):
    r = client.post("/api/uploads/night-1/progress", json=body(label="Lights to archive",
                    eta="2026-03-01T22:00:00+00:00", cutoff="2026-03-01T23:30:00+11:00"), headers=H)
    assert r.status_code == 200, r.get_json()
    t = r.get_json()["transfer"]
    assert t["id"] == "night-1" and t["state"] == "running" and t["label"] == "Lights to archive"
    assert t["cutoff"] == "2026-03-01T12:30:00+00:00"
    assert r.get_json()["event_recorded"] is False
    listed = client.get("/api/uploads", headers=H).get_json()["transfers"]
    assert [x["id"] for x in listed] == ["night-1"]
    assert _events(client) == []


def test_finishing_records_one_event_however_often_it_is_said(client):
    client.post("/api/uploads/night-1/progress", json=body(), headers=H)
    r = client.post("/api/uploads/night-1/progress",
                    json=body(state="finished", files_done=10, bytes_done=1000), headers=H)
    assert r.get_json()["event_recorded"] is True
    again = client.post("/api/uploads/night-1/progress",
                        json=body(state="finished", files_done=10, bytes_done=1000), headers=H)
    assert again.get_json()["event_recorded"] is False
    evs = [e for e in _events(client) if e["type"] == "upload_finished"]
    assert len(evs) == 1
    assert evs[0]["title"] == "Upload finished: night-1 (10 of 10 files, 1.0 KB of 1.0 KB)"
    assert evs[0]["refs"] == {}
    assert evs[0]["data"]["transfer_id"] == "night-1"


def test_a_failed_transfer_is_an_error_event(client):
    client.post("/api/uploads/x/progress", json=body(state="failed"), headers=H)
    ev = [e for e in _events(client) if e["type"] == "upload_failed"][0]
    assert ev["severity"] == "error" and ev["title"].startswith("Upload failed: x")


def test_a_new_run_of_the_same_id_can_finish_again(client):
    client.post("/api/uploads/nightly/progress", json=body(state="finished"), headers=H)
    r = client.post("/api/uploads/nightly/progress",
                    json=body(state="finished", started_at="2026-03-02T20:00:00+00:00"), headers=H)
    assert r.get_json()["event_recorded"] is True
    assert len([e for e in _events(client) if e["type"] == "upload_finished"]) == 2


@pytest.mark.parametrize("tid,payload,needle", [
    ("ok", {"files_done": 1}, "state"),
    ("ok", body(state="done"), "state"),
    ("ok", body(files_done=11), "files_done"),
    ("ok", body(bytes_done=-1), "bytes_done"),
    ("ok", body(files_total=True), "files_total"),
    ("ok", body(started_at="yesterday"), "started_at"),
    ("ok", body(eta="2026-03-01T22:00:00"), "time zone"),
    ("ok", body(label="x" * 81), "label"),
    ("-bad", body(), "transfer id"),
])
def test_bad_posts_are_refused(client, tid, payload, needle):
    r = client.post(f"/api/uploads/{tid}/progress", json=payload, headers=H)
    assert r.status_code == 400 and needle in r.get_json()["error"]


def test_a_non_json_body_is_refused(client):
    r = client.post("/api/uploads/ok/progress", data="state=running", headers=H,
                    content_type="text/plain")
    assert r.status_code == 400


def test_writes_are_gated_like_the_other_write_routes(client):
    r = client.post("/api/uploads/ok/progress", json=body())
    assert r.status_code == 401
    assert client.get("/api/uploads").status_code == 401


def test_stale_and_ended_transfers_are_dropped_after_a_while():
    up.clear()
    up.report("a", body(), now=T0)
    up.report("b", body(state="finished"), now=T0)
    assert {t["id"] for t in up.listing(now=T0 + timedelta(minutes=5))} == {"a", "b"}
    assert {t["id"] for t in up.listing(now=T0 + timedelta(minutes=11))} == {"a"}
    assert up.listing(now=T0 + timedelta(minutes=31)) == []


def test_too_many_open_transfers_are_refused():
    up.clear()
    for i in range(up.MAX_TRANSFERS):
        up.report(f"t{i}", body(), now=T0)
    with pytest.raises(OverflowError):
        up.report("one-more", body(), now=T0)
    up.report("t0", body(files_done=4), now=T0)  # an update to a known one still works
    up.clear()


def test_running_and_paused_come_first():
    up.clear()
    up.report("done", body(state="finished"), now=T0 + timedelta(minutes=2))
    up.report("run", body(), now=T0)
    up.report("pause", body(state="paused"), now=T0 + timedelta(minutes=1))
    assert [t["id"] for t in up.listing(now=T0 + timedelta(minutes=3))] == ["pause", "run", "done"]
    up.clear()
