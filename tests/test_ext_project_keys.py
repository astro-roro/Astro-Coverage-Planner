"""H7: project-wide keys an extension declares on its plan_fields entry."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402

app = app_module.app


def _plan(pid, project):
    return {"id": pid, "project_name": project, "priority": "normal",
            "target": {"name": pid, "center_ra_deg": 10.0, "center_dec_deg": 20.0},
            "filter_rows": [], "updated_at": "2020-01-01T00:00:00+00:00"}


@pytest.fixture
def client(monkeypatch):
    td = Path(tempfile.mkdtemp())
    path = td / "plans.json"
    path.write_text(json.dumps({"version": 1, "plans": [
        _plan("a1", "Alpha"), _plan("a2", "Alpha"), _plan("b1", "Beta")]}), encoding="utf-8")
    monkeypatch.setattr(app_module, "PLANS_PATH", path)
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.setattr(app_module, "_plans_cache_mtime", None)
    saved = list(app.plan_fields)
    load_dummy("plan_rating").register(app)
    yield app.test_client()
    app.plan_fields[:] = saved


def _stored():
    doc = json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))
    return {p["id"]: p for p in doc["plans"]}


def test_the_hook_is_offered_and_the_dummy_declares_its_key():
    assert "project_keys" in app.acp_extension_hooks


def test_a_project_key_spreads_across_the_project_only(client):
    r = client.put("/api/projects/Alpha/settings", json={"rating_group": "a", "priority": "high"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["rating_group"] == "a" and body["priority"] == "high"
    s = _stored()
    assert s["a1"]["rating_group"] == s["a2"]["rating_group"] == "a"
    assert "rating_group" not in s["b1"]


def test_the_extension_validates_its_project_key(client):
    r = client.put("/api/projects/Alpha/settings", json={"rating_group": "zzz"})
    assert r.status_code == 400 and "rating_group" in r.get_json()["error"]
    assert "rating_group" not in _stored()["a1"]


def test_an_undeclared_key_is_ignored_by_the_settings_route(client):
    r = client.put("/api/projects/Alpha/settings", json={"rating": 5})
    assert r.status_code == 200
    assert "rating" not in _stored()["a1"]


def test_a_core_key_can_not_be_claimed_by_an_extension(client):
    app.plan_fields.append({"extension": "greedy", "project_keys": ["priority", "state"],
                            "validate": lambda v: "greedy saw " + ",".join(sorted(v))})
    r = client.put("/api/projects/Alpha/settings", json={"priority": "low"})
    assert r.status_code == 200
    assert _stored()["a1"]["priority"] == "low"
