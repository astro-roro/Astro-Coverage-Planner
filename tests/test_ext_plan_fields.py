"""H1: plan fields owned by an extension (validate, keep, view)."""
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


def _plan(pid="p1", **extra):
    return {"id": pid, "project_name": "Alpha",
            "target": {"name": pid, "center_ra_deg": 10.0, "center_dec_deg": 20.0},
            "filter_rows": [], **extra}


@pytest.fixture
def client(monkeypatch):
    td = Path(tempfile.mkdtemp())
    monkeypatch.setattr(app_module, "PLANS_PATH", td / "plans.json")
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.setattr(app_module, "_plans_cache_mtime", None)
    saved = list(app.plan_fields)
    load_dummy("plan_rating").register(app)
    yield app.test_client()
    app.plan_fields[:] = saved


def _stored(pid="p1"):
    doc = json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))
    return next(p for p in doc["plans"] if p["id"] == pid)


def test_the_app_offers_plan_fields():
    assert isinstance(app.plan_fields, list)
    assert "plan_fields" in app.acp_extension_hooks


def test_validate_rejects_a_bad_value_on_create_and_update(client):
    r = client.post("/api/plans", json=_plan(rating=9))
    assert r.status_code == 400 and "rating" in r.get_json()["error"]
    assert client.post("/api/plans", json=_plan(rating=4)).status_code == 201
    r = client.put("/api/plans/p1", json=_plan(rating="five"))
    assert r.status_code == 400
    assert _stored()["rating"] == 4


def test_keep_holds_a_server_field_and_drops_a_derived_one(client):
    client.post("/api/plans", json=_plan(rating=3, rated_at="client-made-this-up"))
    assert "rated_at" not in _stored()
    doc = json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))
    doc["plans"][0]["rated_at"] = "2026-01-01"
    app_module.save_plans(doc)
    client.put("/api/plans/p1", json=_plan(rating=5, rated_at="changed", rating_label="x"))
    stored = _stored()
    assert stored["rated_at"] == "2026-01-01"
    assert stored["rating"] == 5
    assert "rating_label" not in stored


def test_view_adds_a_derived_field_to_every_get(client):
    client.post("/api/plans", json=_plan(rating=5))
    assert client.get("/api/plans/p1").get_json()["rating_label"] == "excellent"
    listed = client.get("/api/plans").get_json()["plans"]
    assert listed[0]["rating_label"] == "excellent"
    assert "rating_label" not in _stored()


def test_unregistered_keys_pass_through_untouched(client):
    client.post("/api/plans", json=_plan(someone_elses_field={"a": [1, 2]}))
    assert _stored()["someone_elses_field"] == {"a": [1, 2]}
    assert client.get("/api/plans/p1").get_json()["someone_elses_field"] == {"a": [1, 2]}


def test_an_extension_that_raises_never_blocks_a_save_or_a_read(client):
    def boom(*_):
        raise RuntimeError("broken extension")
    app.plan_fields.append({"extension": "broken", "keys": ["x"],
                            "validate": boom, "keep": boom, "view": boom})
    assert client.post("/api/plans", json=_plan(rating=2)).status_code == 201
    assert client.get("/api/plans/p1").get_json()["rating_label"] == "fair"


def test_with_no_extension_the_fields_are_kept_as_sent(monkeypatch):
    td = Path(tempfile.mkdtemp())
    monkeypatch.setattr(app_module, "PLANS_PATH", td / "plans.json")
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.setattr(app_module, "_plans_cache_mtime", None)
    c = app.test_client()
    assert c.post("/api/plans", json=_plan(rating=99, rated_at="t")).status_code == 201
    assert _stored()["rating"] == 99 and _stored()["rated_at"] == "t"
