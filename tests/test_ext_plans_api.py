"""H6: plan reads and writes for extensions, under the plans lock."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import plans_api  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402


def _plan(pid, project, priority="normal"):
    return {"id": pid, "project_name": project, "priority": priority,
            "target": {"name": pid, "center_ra_deg": 10.0, "center_dec_deg": 20.0},
            "filter_rows": [], "updated_at": "2020-01-01T00:00:00+00:00"}


@pytest.fixture
def plans_file(monkeypatch):
    td = Path(tempfile.mkdtemp())
    path = td / "plans.json"
    path.write_text(json.dumps({"version": 1, "plans": [
        _plan("a1", "Alpha"), _plan("a2", "Alpha"), _plan("b1", "Beta")]}), encoding="utf-8")
    monkeypatch.setattr(app_module, "PLANS_PATH", path)
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.setattr(app_module, "_plans_cache_mtime", None)
    return path


def _stored(path):
    return {p["id"]: p for p in json.loads(path.read_text(encoding="utf-8"))["plans"]}


def test_the_app_offers_plans_api():
    assert app_module.app.plans_api is plans_api
    assert "plans_api" in app_module.app.acp_extension_hooks


def test_read_returns_a_copy(plans_file):
    doc = plans_api.read()
    doc["plans"][0]["id"] = "changed"
    assert plans_api.read()["plans"][0]["id"] == "a1"


def test_dummy_writes_under_the_lock_and_spreads_a_project_value(plans_file):
    dummy = load_dummy("plan_stamp")
    host = Flask("host")
    host.acp_extension_api = 1
    host.acp_extension_hooks = {"plans_api"}
    host.plans_api = plans_api
    host.plans_lock = app_module.PLANS_LOCK
    dummy.register(host)
    r = host.test_client().post("/api/ext/plan-stamp/stamp", json={"project": "Alpha", "note": "hello"})
    assert r.status_code == 200
    assert r.get_json() == {"stamped": 2, "changes": ["priority"]}
    assert dummy.seen["lock_held"] is True
    stored = _stored(plans_file)
    assert stored["a1"]["stamp_note"] == stored["a2"]["stamp_note"] == "hello"
    assert stored["a1"]["priority"] == stored["a2"]["priority"] == "high"
    assert "stamp_note" not in stored["b1"] and stored["b1"]["priority"] == "normal"


def test_nothing_is_saved_when_the_function_raises(plans_file):
    plans_api.read()  # the first load heals the file and may write it
    before = plans_file.read_text(encoding="utf-8")

    def boom(plans):
        plans[0]["priority"] = "low"
        raise ValueError("no")

    with pytest.raises(ValueError):
        plans_api.transact(boom)
    assert plans_file.read_text(encoding="utf-8") == before


def test_nothing_is_written_when_nothing_changed(plans_file):
    plans_api.read()  # the first load heals the file and may write it
    mtime = plans_file.stat().st_mtime_ns
    assert plans_api.transact(lambda plans: len(plans)) == 3
    assert plans_file.stat().st_mtime_ns == mtime


def test_core_routes_see_what_the_extension_wrote(plans_file):
    plans_api.transact(lambda plans: plans.append(_plan("c1", "Gamma")))
    r = app_module.app.test_client().get("/api/plans/c1")
    assert r.status_code == 200 and r.get_json()["project_name"] == "Gamma"
