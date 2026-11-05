"""H2: plan editor sections an extension adds through the manifest."""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import extensions  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402

app = app_module.app


@pytest.fixture
def client(monkeypatch):
    td = Path(tempfile.mkdtemp())
    path = td / "plans.json"
    path.write_text(json.dumps({"version": 1, "plans": [{
        "id": "p1", "target": {"name": "p1", "center_ra_deg": 1.0, "center_dec_deg": 2.0},
        "filter_rows": []}]}), encoding="utf-8")
    monkeypatch.setattr(app_module, "PLANS_PATH", path)
    monkeypatch.setattr(app_module, "_plans_cache", None)
    monkeypatch.setattr(app_module, "_plans_cache_mtime", None)
    saved = list(app.extensions_manifest)
    yield app.test_client()
    app.extensions_manifest[:] = saved


def _host():
    """The dummy registers on a stand-in app (Flask refuses new routes on an
    app that has served a request); its manifest entry is copied to ACP's."""
    host = Flask("host")
    host.acp_extension_api = 1
    host.acp_extension_hooks = {"editor_sections", "plans_api"}
    host.extensions_manifest = []
    host.plans_api = app.plans_api
    load_dummy("plan_checklist").register(host)
    app.extensions_manifest.extend(host.extensions_manifest)
    return host


def test_hook_is_offered():
    assert "editor_sections" in app.acp_extension_hooks


def test_manifest_serves_the_section_and_its_page_and_route_work(client):
    ext = _host().test_client()
    entry = next(e for e in client.get("/api/extensions/manifest").get_json()
                 if e["extension"] == "plan_checklist")
    assert entry["editor_sections"] == [{"id": "checklist", "label": "Checklist",
                                         "page": "/api/ext/plan-checklist/section", "height": 120}]
    assert "acp:plan:reload" in ext.get("/api/ext/plan-checklist/section?plan_id=p1").get_data(as_text=True)
    assert ext.post("/api/ext/plan-checklist/tick", json={"plan_id": "p1", "item": "flats"}).status_code == 200
    assert client.get("/api/plans/p1").get_json()["checklist"] == {"flats": True}


@pytest.mark.parametrize("item,why", [
    ({"id": "x", "label": "X", "page": "https://elsewhere.example/page"}, "page"),
    ({"id": "x", "label": "X", "page": "/api/ext/other-ext/page"}, "page"),
    ({"id": "x", "label": "X", "page": "/api/ext/mine/../../secret"}, "page"),
    ({"id": "bad id", "label": "X", "page": "/api/ext/mine/p"}, "id"),
    ({"id": "x", "label": "", "page": "/api/ext/mine/p"}, "label"),
])
def test_bad_sections_are_dropped_with_a_warning(item, why, caplog):
    extensions._warned_pages.clear()
    with caplog.at_level(logging.WARNING, logger="acp.extensions"):
        out = extensions.sanitise_pages({"extension": "mine", "editor_sections": [item]},
                                        "editor_sections", needs_label=True)
    assert out["editor_sections"] == []
    assert why in caplog.text


def test_height_outside_the_range_is_dropped_and_the_section_kept():
    out = extensions.sanitise_pages(
        {"extension": "mine", "editor_sections": [
            {"id": "a", "label": "A", "page": "/api/ext/mine/a", "height": 5000},
            {"id": "b", "label": "B", "page": "/api/ext/mine/b?x=1", "height": 300}]},
        "editor_sections", needs_label=True)
    assert out["editor_sections"] == [
        {"id": "a", "label": "A", "page": "/api/ext/mine/a"},
        {"id": "b", "label": "B", "page": "/api/ext/mine/b?x=1", "height": 300}]


def test_an_entry_without_sections_is_returned_unchanged():
    entry = {"extension": "mine", "actions": []}
    assert extensions.sanitise_pages(entry, "editor_sections", needs_label=True) is entry
