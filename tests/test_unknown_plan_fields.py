"""Plan fields core does not know survive a save and a reload untouched.

An extension can own plan fields (docs/extensions.md, "Plan fields owned by
an extension"). When that extension is not installed, core must neither
validate, drop nor rewrite those fields, so plans keep them until the
extension comes back. The fields here are made up and cover the shapes such
fields take: text, empty text, booleans, null, numbers, a mapping of filter
name to hours, and a list of nested records.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402

app = app_module.app

EXTRA = {
    "ext_role": "science",
    "ext_blank": "",
    "ext_flag": True,
    "ext_off": False,
    "ext_none": None,
    "ext_count": 7,
    "ext_ratio": 0.25,
    "ext_hours": {"Ha": 3.5, "OIII": 2},
    "ext_history": [
        {"kind": "promise", "month": "2026-01", "goals": {"Ha": 4.0}, "done_at": None},
        {"kind": "promise", "month": "2025-12", "goals": {}, "done_at": "2025-12-30"},
    ],
    "ext_note": {"rule": "r1", "text": "set by another tool", "night": "2026-01-02"},
}


def _plan(pid="p1", project="Alpha", **extra):
    return {"id": pid, "project_name": project,
            "target": {"name": pid, "center_ra_deg": 10.0, "center_dec_deg": 20.0},
            "filter_rows": [], **extra}


@pytest.fixture
def client(monkeypatch):
    td = Path(tempfile.mkdtemp())
    monkeypatch.setattr(app_module, "PLANS_PATH", td / "plans.json")
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.setattr(app_module, "_plans_cache_mtime", None)
    saved = list(app.plan_fields)
    app.plan_fields[:] = []  # no extension installed
    yield app.test_client()
    app.plan_fields[:] = saved


def _stored(pid="p1"):
    doc = json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))
    return next(p for p in doc["plans"] if p["id"] == pid)


def _extras(plan):
    return {k: plan[k] for k in EXTRA if k in plan}


def test_a_new_plan_keeps_unknown_fields_on_save_and_reload(client):
    r = client.post("/api/plans", json=_plan(**copy.deepcopy(EXTRA)))
    assert r.status_code == 201, r.get_json()
    assert _extras(r.get_json()) == EXTRA
    assert _extras(_stored()) == EXTRA
    assert _extras(client.get("/api/plans/p1").get_json()) == EXTRA
    listed = client.get("/api/plans").get_json()["plans"]
    assert _extras(listed[0]) == EXTRA


def test_saving_the_plan_as_loaded_changes_nothing(client):
    """The editor saves the plan it loaded, derived fields and all."""
    client.post("/api/plans", json=_plan(**copy.deepcopy(EXTRA)))
    loaded = client.get("/api/plans/p1").get_json()
    assert client.post("/api/plans", json=loaded).status_code == 201
    assert _extras(_stored()) == EXTRA
    assert client.put("/api/plans/p1", json=client.get("/api/plans/p1").get_json()).status_code == 200
    assert _extras(_stored()) == EXTRA


def test_an_update_stores_unknown_fields_as_sent(client):
    client.post("/api/plans", json=_plan(**copy.deepcopy(EXTRA)))
    changed = {**copy.deepcopy(EXTRA), "ext_role": "survey", "ext_hours": {"Ha": 1}}
    r = client.put("/api/plans/p1", json=_plan(**changed))
    assert r.status_code == 200, r.get_json()
    assert _extras(_stored()) == changed


def test_project_settings_leave_unknown_fields_alone(client):
    client.post("/api/plans", json=_plan("p1", **copy.deepcopy(EXTRA)))
    client.post("/api/plans", json=_plan("p2", **copy.deepcopy(EXTRA)))
    r = client.put("/api/projects/Alpha/settings", json={"priority": "high", "ext_role": "pretty"})
    assert r.status_code == 200, r.get_json()
    for pid in ("p1", "p2"):
        stored = _stored(pid)
        assert stored["priority"] == "high"
        assert _extras(stored) == EXTRA, "an unclaimed key is not spread across the project"
