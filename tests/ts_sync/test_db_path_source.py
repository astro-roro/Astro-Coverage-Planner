"""The extension opens the database the owner configured, never one a
request names, and checks it is a Target Scheduler database before it
copies anything beside it."""
from __future__ import annotations

import sqlite3

import pytest

from .host import P, FakeHost, new_db
from .plans import plan

ROUTES = ("/preview", "/sync", "/import/preview", "/import/apply",
          "/sync-acquired", "/import/diff", "/import/resolve")


@pytest.fixture
def host(tmp_path, monkeypatch):
    h = FakeHost(tmp_path, monkeypatch)
    h.write_plans([plan("a", project_name="M31")])
    return h


@pytest.mark.parametrize("route", ROUTES)
def test_body_db_path_is_ignored(host, tmp_path, route):
    real = new_db(tmp_path / "elsewhere.sqlite")
    host.use_db(tmp_path / "configured-but-missing.sqlite")
    r = host.client.post(f"/api/ext/nina-ts-sync{route}",
                         json={"profile_id": P, "db_path": str(real)})
    assert r.status_code == 400
    assert "elsewhere" not in r.get_data(as_text=True)


def test_missing_and_present_body_paths_get_the_same_reply(host, tmp_path):
    present = tmp_path / "present.txt"
    present.write_text("not a database")
    host.use_db(tmp_path / "configured-but-missing.sqlite")
    replies = []
    for path in (tmp_path / "absent.txt", present):
        r = host.client.post("/api/ext/nina-ts-sync/preview",
                             json={"profile_id": P, "db_path": str(path)})
        replies.append((r.status_code, r.get_data(as_text=True)))
    assert replies[0] == replies[1]


def test_configured_database_is_used(host, tmp_path):
    db = new_db(tmp_path / "ts.sqlite")
    host.use_db(db)
    r = host.client.post("/api/ext/nina-ts-sync/preview", json={"profile_id": P})
    assert r.status_code == 200, r.get_json()


def test_no_backup_of_an_unsupported_database(host, tmp_path):
    db = tmp_path / "other.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    host.use_db(db)
    r = host.client.post("/api/ext/nina-ts-sync/sync", json={"profile_id": P})
    assert r.status_code == 409
    assert list(tmp_path.glob("*acpsync*")) == []


def test_no_backup_of_a_file_that_is_not_a_database(host, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("any readable file\n")
    host.use_db(notes)
    try:
        r = host.client.post("/api/ext/nina-ts-sync/sync", json={"profile_id": P})
        assert r.status_code >= 400
    except sqlite3.DatabaseError:
        pass
    assert list(tmp_path.glob("*acpsync*")) == []
    assert notes.read_text() == "any readable file\n"
