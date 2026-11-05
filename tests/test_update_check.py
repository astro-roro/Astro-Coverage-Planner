"""The update check: version maths, the daily cache, failure paths, the off
switch, and the route. No test touches the network: every fetch is a stub."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import update_check as uc  # noqa: E402
from app import app  # noqa: E402

GOOD = {"tag_name": "v1.2.0", "html_url": "https://github.com/astro-roro/Astro-Coverage-Planner/releases/tag/v1.2.0"}


@pytest.fixture(autouse=True)
def fresh_memory():
    uc.reset_memory()
    yield
    uc.reset_memory()


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "update_check.json"


class Fetcher:
    def __init__(self, payload=GOOD, error=None):
        self.payload, self.error, self.calls = payload, error, 0

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


# --- version maths -----------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1.10.0", (1, 10, 0)), ("v1.2.3", (1, 2, 3)), (" v0.0.1 ", (0, 0, 1)),
    ("dev", None), ("1.2", None), ("1.2.0-beta", None), ("1.2.3.4", None),
    ("", None), (None, None), (12, None),
])
def test_parse_version(text, expected):
    assert uc.parse_version(text) == expected


@pytest.mark.parametrize("running,latest,expected", [
    ("1.9.0", "1.10.0", "minor"),
    ("1.10.0", "1.9.0", None),
    ("1.1.1", "1.1.2", "patch"),
    ("1.1.1", "1.2.0", "minor"),
    ("1.9.9", "2.0.0", "major"),
    ("1.1.1", "1.1.1", None),
    ("1.1.1", "v1.1.1", None),
    ("dev", "1.1.1", None),
    ("1.1.1", "nightly", None),
])
def test_jump_size(running, latest, expected):
    assert uc.jump_size(running, latest) == expected


# --- the cache ---------------------------------------------------------------

def test_one_fetch_per_day_from_memory(cache):
    fetch = Fetcher()
    clock = [1000.0]
    for _ in range(3):
        got = uc.check(cache, now=lambda: clock[0], fetch=fetch)
        clock[0] += 3600
    assert fetch.calls == 1
    assert got["latest"] == "1.2.0"


def test_a_new_day_fetches_again(cache):
    fetch = Fetcher()
    uc.check(cache, now=lambda: 1000.0, fetch=fetch)
    uc.check(cache, now=lambda: 1000.0 + uc.FRESH_SECONDS + 1, fetch=fetch)
    assert fetch.calls == 2


def test_the_file_survives_a_restart(cache):
    uc.check(cache, now=lambda: 1000.0, fetch=Fetcher())
    uc.reset_memory()
    again = Fetcher()
    got = uc.check(cache, now=lambda: 2000.0, fetch=again)
    assert again.calls == 0
    assert got["latest"] == "1.2.0"


def test_a_failure_is_retried_later_not_on_every_call(cache):
    fetch = Fetcher(error=OSError("offline"))
    assert uc.check(cache, now=lambda: 1000.0, fetch=fetch) is None
    assert uc.check(cache, now=lambda: 1100.0, fetch=fetch) is None
    assert fetch.calls == 1
    uc.check(cache, now=lambda: 1000.0 + uc.RETRY_SECONDS + 1, fetch=fetch)
    assert fetch.calls == 2


def test_a_failed_refresh_keeps_the_known_release(cache):
    uc.check(cache, now=lambda: 1000.0, fetch=Fetcher())
    later = 1000.0 + uc.FRESH_SECONDS + 1
    got = uc.check(cache, now=lambda: later, fetch=Fetcher(error=OSError("rate limited")))
    assert got["latest"] == "1.2.0"


# --- failure paths -----------------------------------------------------------

@pytest.mark.parametrize("payload", [
    None, [], "text", {}, {"tag_name": 5}, {"tag_name": "nightly"},
    {"tag_name": "v9.0.0", "draft": True}, {"tag_name": "v9.0.0", "prerelease": True},
])
def test_bad_answers_mean_nothing_known(cache, payload):
    assert uc.check(cache, now=lambda: 1.0, fetch=Fetcher(payload)) is None


@pytest.mark.parametrize("error", [OSError("no network"), TimeoutError(), ValueError("bad json"),
                                   RuntimeError("anything")])
def test_a_fetch_error_never_escapes(cache, error):
    assert uc.check(cache, now=lambda: 1.0, fetch=Fetcher(error=error)) is None


def test_a_corrupt_cache_file_is_ignored(cache):
    cache.write_text("{not json", encoding="utf-8")
    assert uc.check(cache, now=lambda: 1.0, fetch=Fetcher())["latest"] == "1.2.0"


def test_a_foreign_link_is_replaced_by_the_project_release_page(cache):
    payload = {"tag_name": "v1.2.0", "html_url": "https://example.com/other"}
    got = uc.check(cache, now=lambda: 1.0, fetch=Fetcher(payload))
    assert got["url"] == "https://github.com/astro-roro/Astro-Coverage-Planner/releases/tag/v1.2.0"


def test_the_real_fetch_sends_a_generic_request_only(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, n):
            return json.dumps(GOOD).encode()

    def fake_urlopen(request, timeout):
        seen["url"], seen["headers"], seen["timeout"] = request.full_url, dict(request.header_items()), timeout
        return Resp()

    monkeypatch.setattr(uc.urllib.request, "urlopen", fake_urlopen)
    assert uc._fetch_latest()["tag_name"] == "v1.2.0"
    assert seen["url"] == uc.LATEST_URL
    assert seen["timeout"] == uc.TIMEOUT_SECONDS
    assert {k.lower() for k in seen["headers"]} == {"accept", "user-agent"}


# --- status and the setting --------------------------------------------------

def test_status_shapes(cache):
    fetch = Fetcher()
    new = uc.status("1.1.1", True, False, cache, now=lambda: 1.0, fetch=fetch)
    assert new == {"available": True, "current": "1.1.1", "latest": "1.2.0", "url": GOOD["html_url"],
                   "jump": "minor", "docker": False}
    assert uc.status("1.2.0", True, True, cache, now=lambda: 1.0, fetch=fetch)["available"] is False
    assert uc.status("1.3.0", True, False, cache, now=lambda: 1.0, fetch=fetch)["available"] is False


def test_a_dev_or_unparseable_version_shows_nothing_and_asks_nothing(cache):
    fetch = Fetcher()
    for version in ("dev", "1.2.0-dev", ""):
        assert uc.status(version, True, False, cache, fetch=fetch)["available"] is False
    assert fetch.calls == 0


def test_switched_off_makes_no_request(cache):
    fetch = Fetcher()
    assert uc.status("1.1.1", False, False, cache, fetch=fetch)["available"] is False
    assert fetch.calls == 0
    assert not cache.exists()


def test_enabled_order():
    assert uc.enabled({}, {}) == {"value": True, "locked": False}
    assert uc.enabled({}, {"update_check": False}) == {"value": False, "locked": False}
    assert uc.enabled({}, {"update_check": "no"}) == {"value": True, "locked": False}
    assert uc.enabled({"ACP_UPDATE_CHECK": "0"}, {"update_check": True}) == {"value": False, "locked": True}
    assert uc.enabled({"ACP_UPDATE_CHECK": "Off"}, {}) == {"value": False, "locked": True}
    assert uc.enabled({"ACP_UPDATE_CHECK": "1"}, {"update_check": False}) == {"value": True, "locked": True}
    assert uc.enabled({"ACP_UPDATE_CHECK": "  "}, {"update_check": False}) == {"value": False, "locked": False}


# --- the route ---------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ACP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ACP_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("ACP_API_TOKEN", raising=False)
    app.config["TESTING"] = True
    return app.test_client()


def test_route_reports_a_newer_release(client):
    fetch = Fetcher({"tag_name": "v99.0.0", "html_url": GOOD["html_url"]})
    with mock.patch.object(uc, "_fetch_latest", fetch):
        body = client.get("/api/update").get_json()
        client.get("/api/update")
    assert body["available"] is True and body["jump"] == "major" and body["latest"] == "99.0.0"
    assert fetch.calls == 1


def test_route_failure_is_a_quiet_nothing_new(client):
    with mock.patch.object(uc, "_fetch_latest", Fetcher(error=OSError("offline"))):
        resp = client.get("/api/update")
    assert resp.status_code == 200
    assert resp.get_json()["available"] is False


def test_route_off_by_environment_makes_no_request(client, monkeypatch):
    monkeypatch.setenv("ACP_UPDATE_CHECK", "0")
    fetch = Fetcher()
    with mock.patch.object(uc, "_fetch_latest", fetch):
        assert client.get("/api/update").get_json()["available"] is False
    assert fetch.calls == 0


def test_route_off_by_settings_file_makes_no_request(client, tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "settings.json").write_text(json.dumps({"version": 1, "update_check": False}), encoding="utf-8")
    fetch = Fetcher()
    with mock.patch.object(uc, "_fetch_latest", fetch):
        assert client.get("/api/update").get_json()["available"] is False
    assert fetch.calls == 0


def test_docker_flag_follows_container_detection(client):
    with mock.patch.object(uc, "_fetch_latest", Fetcher({"tag_name": "v99.0.0"})), \
            mock.patch("acp_settings.in_container", return_value=True):
        assert client.get("/api/update").get_json()["docker"] is True


def test_settings_view_and_put_carry_update_check(client):
    view = client.get("/api/settings").get_json()
    assert view["update_check"] == {"value": True, "locked": False}
    put = client.put("/api/settings", json={"update_check": False}, headers={"Host": "localhost"})
    assert put.status_code == 200, put.get_json()
    assert put.get_json()["update_check"] == {"value": False, "locked": False}
    bad = client.put("/api/settings", json={"update_check": "no"}, headers={"Host": "localhost"})
    assert bad.status_code == 400


def test_put_is_refused_when_the_environment_locks_it(client, monkeypatch):
    monkeypatch.setenv("ACP_UPDATE_CHECK", "0")
    put = client.put("/api/settings", json={"update_check": True}, headers={"Host": "localhost"})
    assert put.status_code == 409 and put.get_json()["error"] == "locked_by_env"
