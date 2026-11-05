"""H8: cards an extension adds to the top of the Activity tab."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import extensions  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402

app = app_module.app
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    saved = list(app.extensions_manifest)
    # The dummy registers on a stand-in app (Flask refuses new routes on an
    # app that has served a request); its manifest entry is copied to ACP's.
    host = Flask("host")
    host.acp_extension_api = 1
    host.acp_extension_hooks = {"activity_cards"}
    host.extensions_manifest = []
    load_dummy("disk_space_card").register(host)
    app.extensions_manifest.extend(host.extensions_manifest)
    yield app.test_client(), host.test_client()
    app.extensions_manifest[:] = saved


def test_hook_is_offered():
    assert "activity_cards" in app.acp_extension_hooks


def test_manifest_serves_the_card_and_its_page_answers(client):
    client, ext = client
    entry = next(e for e in client.get("/api/extensions/manifest").get_json()
                 if e["extension"] == "disk_space_card")
    assert entry["activity_cards"] == [{"id": "disk", "label": "Disk space",
                                        "page": "/api/ext/disk-space-card/card", "height": 60}]
    assert "Free space" in ext.get("/api/ext/disk-space-card/card").get_data(as_text=True)


def test_a_card_needs_no_label_but_a_page_inside_the_prefix():
    out = extensions.sanitise_pages({"extension": "mine", "activity_cards": [
        {"id": "a", "page": "/api/ext/mine/a"},
        {"id": "b", "page": "/api/other/b"}]}, "activity_cards", needs_label=False)
    assert out["activity_cards"] == [{"id": "a", "label": "", "page": "/api/ext/mine/a"}]


def test_the_activity_tab_has_a_card_box_above_the_events():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert html.index('id="activityCards"') < html.index('id="activityBody"')
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'pagesFrom(extensionsManifest, "activity_cards")' in js
