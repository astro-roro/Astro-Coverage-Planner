"""The neutral names for a frame grader and processing tools, and what happens
to data stored under older names (docs/api.md, "Accepted counts" and
"Activity"; docs/extensions.md, "Old names").

The old names here are made up: `oldblock` for the plan block, `oldtool`,
`old_alert` and `old_job` for Activity. Core knows none of them; a block is
found by its fields, and Activity names by what is already stored.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import app as app_module  # noqa: E402
import filter_rows as fr  # noqa: E402
from app import app  # noqa: E402

TOKEN = "grader-names-token-0123456789"
H = {"Authorization": f"Bearer {TOKEN}"}
ROW_ID = "6f1c2a9e-4b7d-4e21-9a35-0c8d1e2f3a4b"
BLOCK = {"accepted_seconds": 3600.0, "accepted_frames": 12, "seen_seconds": 3600.0, "seen_frames": 12,
         "complete": False, "estimated_hours": None, "measurement": None, "goal_at_send": 6.0}
PLAN_BLOCK = {"filed_through_night": "2026-10-11", "min_total_frames": 10, "received_at": "x"}
ACCEPTED = {"source": "my-grader", "at": "2026-10-12T10:05:00Z", "filed_through_night": "2026-10-12",
            "min_total_frames": 4, "rows": [{"id": ROW_ID, "filter": "Ha", "template": "Ha",
                                            "accepted_seconds": 7200.0, "accepted_frames": 24,
                                            "seen_seconds": 7200.0, "seen_frames": 24, "complete": False}]}


def _plan(row=None, **extra):
    return {"id": "p1", "project_name": "P1",
            "target": {"name": "P1", "center_ra_deg": 10.0, "center_dec_deg": 20.0},
            "filter_rows": [{"id": ROW_ID, "filter": "Ha", "template": "Ha", "template_sub_s": 300,
                             "unit": "h", "target_hours": 6.0, **(row or {})}],
            "updated_at": "2020-01-01T00:00:00+00:00", **extra}


@pytest.fixture
def env(monkeypatch):
    td = Path(tempfile.mkdtemp())
    monkeypatch.setattr(app_module, "PLANS_PATH", td / "plans.json")
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.setattr(app_module, "_plans_cache_mtime", None)
    monkeypatch.setattr(app_module, "_warned_grader_names", set())
    saved = dict(fr.BLOCK_ALIASES)
    with mock.patch.dict(os.environ, {"ACP_API_TOKEN": TOKEN,
                                      "ACP_ACTIVITY_DB": str(td / "activity.sqlite")}):
        os.environ.pop("ACP_ACTIVITY", None)
        activity.close()
        activity.reset_registrations()
        yield td
        activity.close()
        activity.reset_registrations()
    fr.BLOCK_ALIASES.clear()
    fr.BLOCK_ALIASES.update(saved)


def _write(td, *plans):
    (td / "plans.json").write_text(json.dumps({"version": 1, "plans": list(plans)}), encoding="utf-8")


def _stored(td):
    return json.loads((td / "plans.json").read_text(encoding="utf-8"))["plans"][0]


# -- a block under a name nobody registered --------------------------------------

def test_an_unregistered_old_block_is_read_kept_and_reported(env, caplog):
    _write(env, _plan(oldblock=PLAN_BLOCK, row={"oldblock": BLOCK}))
    with caplog.at_level(logging.WARNING):
        got = app.test_client().get("/api/plans/p1", headers=H).get_json()
    assert got["grader"] == PLAN_BLOCK and got["oldblock"] == PLAN_BLOCK
    assert got["filter_rows"][0]["grader"] == BLOCK and got["filter_rows"][0]["oldblock"] == BLOCK
    assert "'oldblock'" in caplog.text and "register_block_alias" in caplog.text


def test_an_unregistered_old_block_survives_a_save_and_is_kept_current(env):
    _write(env, _plan(oldblock=PLAN_BLOCK, row={"oldblock": BLOCK}))
    c = app.test_client()
    sent = c.get("/api/plans/p1", headers=H).get_json()
    sent["notes"] = "edited"
    assert c.put("/api/plans/p1", json=sent, headers=H).status_code == 200
    stored = _stored(env)
    assert stored["oldblock"] == PLAN_BLOCK and stored["grader"] == PLAN_BLOCK
    assert c.post("/api/plans/p1/accepted", json=ACCEPTED, headers=H).status_code == 200
    stored = _stored(env)
    assert stored["oldblock"]["filed_through_night"] == "2026-10-12" == stored["grader"]["filed_through_night"]
    row = stored["filter_rows"][0]
    assert row["oldblock"]["accepted_frames"] == 24 == row["grader"]["accepted_frames"]


# -- a block under a registered old name -----------------------------------------

def test_a_registered_old_block_moves_to_grader_and_is_still_served(env, caplog):
    fr.register_block_alias("oldblock", "grader")
    _write(env, _plan(oldblock=PLAN_BLOCK, row={"oldblock": BLOCK}))
    c = app.test_client()
    with caplog.at_level(logging.WARNING):
        got = c.get("/api/plans/p1", headers=H).get_json()
    assert "register_block_alias" not in caplog.text
    assert got["grader"] == PLAN_BLOCK and got["oldblock"] == PLAN_BLOCK
    assert got["filter_rows"][0]["oldblock"] == BLOCK
    got["notes"] = "edited"
    assert c.put("/api/plans/p1", json=got, headers=H).status_code == 200
    stored = _stored(env)
    assert stored["grader"] == PLAN_BLOCK and "oldblock" not in stored
    assert stored["filter_rows"][0]["grader"] == BLOCK and "oldblock" not in stored["filter_rows"][0]


def test_a_client_can_not_overwrite_the_block_under_any_name(env):
    _write(env, _plan(grader=PLAN_BLOCK, row={"grader": BLOCK}))
    sent = _plan(grader={"filed_through_night": "1999-01-01", "min_total_frames": 0},
                 oldblock={"filed_through_night": "1999-01-02", "min_total_frames": 0},
                 row={"grader": {"accepted_seconds": 1.0}, "oldblock": {"accepted_seconds": 2.0}})
    assert app.test_client().put("/api/plans/p1", json=sent, headers=H).status_code == 200
    stored = _stored(env)
    assert stored["grader"] == PLAN_BLOCK and "oldblock" not in stored
    assert stored["filter_rows"][0]["grader"] == BLOCK and "oldblock" not in stored["filter_rows"][0]


def test_accepted_writes_grader(env):
    _write(env, _plan())
    assert app.test_client().post("/api/plans/p1/accepted", json=ACCEPTED, headers=H).status_code == 200
    stored = _stored(env)
    assert stored["grader"]["min_total_frames"] == 4
    assert set(stored["filter_rows"][0]) >= {"grader"}


def test_desired_for_finds_a_block_by_its_fields():
    row = {"unit": "fr", "target_frames": 8}
    plan_block = {"filed_through_night": "2026-10-11", "min_total_frames": 10}
    assert fr.desired_for({**row, "oldblock": {"accepted_seconds": 0}}, {"oldblock": plan_block}) == 10
    assert fr.desired_for({**row, "grader": {"accepted_seconds": 0}}, {"grader": plan_block}) == 10
    assert fr.desired_for({**row, "notes_dict": {"x": 1}}, {"grader": plan_block}) == 8


def test_migrate_moves_registered_names_and_reports_the_rest():
    saved = dict(fr.BLOCK_ALIASES)
    try:
        fr.register_block_alias("oldblock", "grader")
        plan = {"grader": {"min_total_frames": 1}, "oldblock": {"min_total_frames": 2},
                "filter_rows": [{"oldblock": {"accepted_seconds": 1}}, {"otherblock": {"seen_frames": 3}}]}
        assert fr.migrate_grader_names(plan) == {"otherblock"}
        assert plan == {"grader": {"min_total_frames": 1},
                        "filter_rows": [{"grader": {"accepted_seconds": 1}},
                                        {"otherblock": {"seen_frames": 3}, "grader": {"seen_frames": 3}}]}
    finally:
        fr.BLOCK_ALIASES.clear()
        fr.BLOCK_ALIASES.update(saved)


# -- Activity: source processing, generic types, job ref --------------------------

def _post(c, **ev):
    base = {"key": "k1", "title": "t", "at": "2026-10-12T10:05:00Z"}
    return c.post("/api/activity/events", json={"events": [{**base, **ev}]}, headers=H).get_json()


def _all(c):
    nights = c.get("/api/activity?nights=30", headers=H).get_json()["nights"]
    return [e for n in nights for e in n["events"]]


def test_processing_is_a_source_with_generic_types(env):
    c = app.test_client()
    r = _post(c, source="processing", type="processing_alert", refs={"job": "j1"})
    assert r["stored"] == 1, r
    ev = _all(c)[0]
    assert (ev["source"], ev["type"], ev["refs"]) == ("processing", "processing_alert", {"job": "j1"})


def test_an_unknown_source_on_a_fresh_store_is_refused_not_dropped(env):
    r = _post(app.test_client(), source="oldtool")
    assert r["stored"] == 0 and "source" in r["rejected"][0]["error"]


def _seed_old_names(td):
    """A store holding an event a sender posted under old names, as an ACP
    from before the rename would have stored it."""
    c = app.test_client()
    _post(c, source="processing", type="processing_alert", refs={"job": "j3"}, key="old")
    activity.close()
    activity.reset_registrations()
    db = sqlite3.connect(str(td / "activity.sqlite"))
    db.execute("""UPDATE events SET source='oldtool', type='old_alert', refs='{"old_job": "j3"}'""")
    db.commit()
    db.close()
    return c


def test_stored_old_names_with_no_alias_keep_working_with_a_warning(env, caplog):
    c = _seed_old_names(env)
    with caplog.at_level(logging.WARNING, logger="acp.activity"):
        ev = _all(c)[0]
    assert (ev["source"], ev["type"], ev["refs"]) == ("oldtool", "old_alert", {"old_job": "j3"})
    for name in ("'oldtool'", "'old_alert'", "'old_job'"):
        assert name in caplog.text
    r = _post(c, source="oldtool", type="old_alert", refs={"old_job": "j4"}, key="old")
    assert r["updated"] == 1 and r["rejected"] == []
    assert _all(c)[0]["refs"] == {"old_job": "j4"}


def test_registered_aliases_rename_stored_old_names(env):
    c = _seed_old_names(env)
    activity.register_alias("source", "oldtool", "processing")
    activity.register_alias("type", "old_alert", "processing_alert")
    activity.register_alias("ref", "old_job", "job")
    ev = _all(c)[0]
    assert (ev["source"], ev["type"], ev["refs"]) == ("processing", "processing_alert", {"job": "j3"})
    again = _post(c, source="oldtool", type="old_alert", refs={"old_job": "j3"}, key="old")
    assert again["stored"] == 0 and again["unchanged"] == 1


def test_the_types_route_lists_processing(env):
    body = app.test_client().get("/api/activity/types", headers=H).get_json()
    assert "processing" in body["sources"]
    proc = next(ch for ch in body["chips"] if ch["id"] == "processing")
    assert {"processing_alert", "processing_recovered"} <= set(proc["types"])


def test_the_startup_check_reports_old_names_before_any_request(env, caplog, monkeypatch):
    _write(env, _plan(oldblock=PLAN_BLOCK))
    _seed_old_names(env)
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.delenv("ACP_SKIP_STARTUP_MIGRATION", raising=False)
    with caplog.at_level(logging.WARNING):
        app_module.check_old_names_at_startup()
    assert "'oldblock'" in caplog.text and "'oldtool'" in caplog.text


# -- the three existing senders, with the old names registered -------------------

def test_with_aliases_registered_old_senders_and_readers_keep_working(env):
    """A grader posting to /accepted, an Activity sender using old names, and
    a plugin reading the old block name all work unchanged once an extension
    registers the old names. The names are made up."""
    fr.register_block_alias("oldblock", "grader")
    activity.register_alias("source", "oldtool", "processing")
    activity.register_alias("type", "old_alert", "processing_alert")
    activity.register_alias("ref", "old_job", "job")
    _write(env, _plan())
    c = app.test_client()

    assert c.post("/api/plans/p1/accepted", json=ACCEPTED, headers=H).status_code == 200
    stored = _stored(env)
    assert stored["grader"]["filed_through_night"] == "2026-10-12" and "oldblock" not in stored

    listed = c.get("/api/plans", headers=H).get_json()["plans"][0]
    expanded = c.get("/api/plans?expand=site", headers=H).get_json()["plans"][0]
    one = c.get("/api/plans/p1", headers=H).get_json()
    for plan in (listed, expanded, one):
        assert plan["oldblock"] == plan["grader"]
        assert plan["oldblock"]["filed_through_night"] == "2026-10-12"
        row = plan["filter_rows"][0]
        assert row["oldblock"] == row["grader"] and row["oldblock"]["accepted_frames"] == 24

    r = _post(c, source="oldtool", type="old_alert", refs={"old_job": "j9"}, key="sent-old")
    assert r["stored"] == 1 and r["rejected"] == [], r
    ev = next(e for e in _all(c) if e["key"] == "sent-old")
    assert (ev["source"], ev["type"], ev["refs"]) == ("processing", "processing_alert", {"job": "j9"})
