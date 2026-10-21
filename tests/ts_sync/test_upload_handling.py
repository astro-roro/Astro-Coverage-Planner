"""Taking an upload from the NINA plugin: the route's checks, the worker,
storage and lifetime. The acceptance tests under "Extension: upload
handling" in ACP's docs/specs/ts-upload-import.md, plus the status fields the
plugin's dock reads."""
from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

from nina_ts_sync import uploads
from .host import MACHINE, P, Q, FakeHost, link, new_db, push
from .make_db import make_db
from .plans import plan


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch)


def _two_profile_db(tmp_path):
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [plan("a", project_name="M31"), plan("b", project_name="M33")], P)
    push(db, [plan("x", project_name="NGC 253")], Q)
    return db


def _files(host):
    if not host.upload_dir.exists():
        return []
    return sorted(p.name for p in host.upload_dir.iterdir())


def test_a_two_profile_database_uploads_and_previews_the_named_profile(host, tmp_path):
    db = _two_profile_db(tmp_path)
    r = host.upload(db)
    assert r.status_code == 202
    body = r.get_json()
    uid = body["upload_id"]
    assert len(uid) == 32
    assert body["status_url"] == f"/api/ext/nina-ts-sync/import/uploads/{uid}"
    assert body["review_url"] == f"/ext/nina-ts-sync/import/uploads/{uid}"

    status = host.ready(uid)
    assert status["state"] == "ready"
    preview = status["preview"]
    assert preview["profile_id"] == P
    assert preview["profile_name"] == "Voyager main"
    assert {p["profile_id"]: p["project_count"] for p in status["profiles"]} == {P: 2, Q: 1}
    names = {p["profile_id"]: p["name"] for p in status["profiles"]}
    assert names == {P: "Voyager main", Q: "Travel rig"}
    # Nothing in ACP yet, so both of P's plans are new and Q's is not listed.
    assert sorted(n["project_name"] for n in preview["new"]) == ["M31", "M33"]


def test_the_status_reply_keeps_the_field_names_the_dock_reads(host, tmp_path):
    """The plugin's dock reads state, error, review_url and counts {new,
    updated, conflicts}. Renaming any of them breaks the dock silently."""
    db = _two_profile_db(tmp_path)
    r = host.upload(db)
    uid = r.get_json()["upload_id"]
    status = host.ready(uid)
    assert status["state"] == "ready"
    assert status["error"] is None
    assert status["review_url"] == f"/ext/nina-ts-sync/import/uploads/{uid}"
    assert status["counts"] == {"new": 2, "updated": 0, "conflicts": 0}
    assert all(isinstance(v, int) for v in status["counts"].values())

    bad = make_db(28, tmp_path / "empty.sqlite")
    conn = sqlite3.connect(bad)
    conn.execute("DROP TABLE project")
    conn.commit()
    conn.close()
    failed = host.ready(host.upload(bad, profile_id=Q).get_json()["upload_id"])
    assert failed["state"] == "failed"
    assert isinstance(failed["error"], str) and failed["error"]
    assert failed["counts"] is None
    assert "review_url" in failed


def test_no_token_on_the_server_refuses_and_writes_nothing(tmp_path, monkeypatch):
    host = FakeHost(tmp_path, monkeypatch, token=None)
    db = _two_profile_db(tmp_path)
    r = host.upload(db, token=False)
    assert r.status_code == 403
    assert r.get_json()["error"] == "ACP needs an access token set before it accepts uploads"
    assert _files(host) == []


def test_a_text_file_is_refused_as_not_sqlite(host):
    r = host.upload(None, data=b"hello, this is not a database at all\n" * 10)
    assert r.status_code == 415
    assert r.get_json()["error"] == "not a SQLite database"
    assert _files(host) == []


def test_sqlite_with_no_project_table_fails_in_the_worker(host, tmp_path):
    db = make_db(28, tmp_path / "noproject.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE project")
    conn.commit()
    conn.close()
    status = host.ready(host.upload(db).get_json()["upload_id"])
    assert status["state"] == "failed"
    assert status["error"] == "a SQLite file, but not a Target Scheduler database"


def test_sqlite_where_project_is_a_view_fails(host, tmp_path):
    db = make_db(28, tmp_path / "view.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE project")
    conn.execute("CREATE VIEW project AS SELECT 1 AS Id, 'x' AS name, 'p' AS profileId")
    conn.commit()
    conn.close()
    status = host.ready(host.upload(db).get_json()["upload_id"])
    assert status["state"] == "failed"
    assert status["error"] == "a SQLite file, but not a Target Scheduler database"


def test_a_truncated_file_with_the_wrong_sha_is_refused_and_leaves_nothing(host, tmp_path):
    db = _two_profile_db(tmp_path)
    raw = db.read_bytes()
    import hashlib
    full_sha = hashlib.sha256(raw).hexdigest()
    r = host.upload(None, data=raw[: len(raw) // 2], sha=full_sha)
    assert r.status_code == 400
    assert r.get_json()["error"] == "upload incomplete or damaged"
    assert not [n for n in _files(host) if n.endswith((".part", ".sqlite"))]


def test_a_version_22_database_fails_with_the_version_message(host, tmp_path):
    db = make_db(22, tmp_path / "v22.sqlite")
    status = host.ready(host.upload(db).get_json()["upload_id"])
    assert status["state"] == "failed"
    assert "user_version=22" in status["error"]
    assert "[23, 24, 25, 26, 27, 28]" in status["error"]


def test_a_second_upload_for_the_same_profile_supersedes_the_first(host, tmp_path):
    db = _two_profile_db(tmp_path)
    first = host.upload_ready(db)
    second = host.upload_ready(db)

    old = host.status(first["upload_id"]).get_json()
    assert old["state"] == "superseded"
    assert old["superseded_by"] == second["upload_id"]
    assert old["superseded_review_url"] == second["review_url"]
    assert not uploads.db_path(first["upload_id"]).exists()
    assert uploads.db_path(second["upload_id"]).exists()

    r = host.apply(first)
    assert r.status_code == 404


def test_an_upload_for_another_profile_leaves_the_first_ready(host, tmp_path):
    db = _two_profile_db(tmp_path)
    first = host.upload_ready(db, profile_id=P)
    host.upload_ready(db, profile_id=Q)
    assert host.status(first["upload_id"]).get_json()["state"] == "ready"


def test_an_upload_past_its_lifetime_is_swept_on_the_next_upload(host, tmp_path, monkeypatch):
    db = _two_profile_db(tmp_path)
    old = host.upload_ready(db, profile_id=Q)
    uid = old["upload_id"]
    state_file = uploads.state_path(uid)
    state = json.loads(state_file.read_text())
    state["created"] = time.time() - 25 * 3600
    state_file.write_text(json.dumps(state))
    stray = host.upload_dir / ("f" * 32 + ".part")
    stray.write_bytes(b"x")
    past = time.time() - 25 * 3600
    os.utime(stray, (past, past))

    host.upload_ready(db, profile_id=P)
    assert not state_file.exists()
    assert not uploads.db_path(uid).exists()
    assert not stray.exists()
    assert host.status(uid).status_code == 404


def test_a_body_over_the_limit_is_refused_with_413_naming_the_limit(host, tmp_path, monkeypatch):
    monkeypatch.setenv("ACP_TS_UPLOAD_MAX_BYTES", "4096")
    db = _two_profile_db(tmp_path)
    r = host.upload(db)
    assert r.status_code == 413
    assert r.get_json()["max_bytes"] == 4096
    assert _files(host) == []


def test_an_upload_above_the_core_1mb_limit_is_accepted(host, tmp_path):
    """The route raises its own limit: every other route keeps 1 MB."""
    db = _two_profile_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE padding (b BLOB)")
    conn.execute("INSERT INTO padding VALUES (zeroblob(3 * 1024 * 1024))")
    conn.commit()
    conn.close()
    assert db.stat().st_size > 1024 * 1024
    status = host.upload_ready(db)
    assert status["counts"]["new"] == 2


def test_missing_parts_or_bad_meta_are_400(host, tmp_path):
    db = _two_profile_db(tmp_path)
    import io
    url = "/api/ext/nina-ts-sync/import/uploads"
    r = host.client.post(url, data={"meta": json.dumps({"profile_id": P, "sha256": "0" * 64})},
                         content_type="multipart/form-data")
    assert r.status_code == 400
    r = host.client.post(url, data={"file": (io.BytesIO(db.read_bytes()), "f.sqlite"),
                                     "meta": "{not json"},
                         content_type="multipart/form-data")
    assert r.status_code == 400
    assert _files(host) == []


def test_the_upload_directory_is_private(host, tmp_path):
    host.upload_ready(_two_profile_db(tmp_path))
    assert (host.upload_dir.stat().st_mode & 0o777) == 0o700


def test_preview_again_for_the_other_profile(host, tmp_path):
    db = _two_profile_db(tmp_path)
    first = host.upload_ready(db, profile_id=P)
    r = host.client.post(f"/api/ext/nina-ts-sync/import/uploads/{first['upload_id']}/preview",
                         json={"profile_id": Q})
    assert r.status_code == 202
    status = host.ready(first["upload_id"])
    assert status["profile_id"] == Q
    assert [n["project_name"] for n in status["preview"]["new"]] == ["NGC 253"]
    r = host.client.post(f"/api/ext/nina-ts-sync/import/uploads/{first['upload_id']}/preview",
                         json={"profile_id": "not-in-file"})
    assert r.status_code == 400


def test_a_profile_with_no_projects_gives_an_empty_preview(host, tmp_path):
    db = _two_profile_db(tmp_path)
    status = host.upload_ready(db, profile_id="profile-nobody")
    assert status["preview"]["empty"] is True
    assert status["counts"] == {"new": 0, "updated": 0, "conflicts": 0}


def test_discard_deletes_the_upload(host, tmp_path):
    status = host.upload_ready(_two_profile_db(tmp_path))
    uid = status["upload_id"]
    r = host.client.delete(f"/api/ext/nina-ts-sync/import/uploads/{uid}")
    assert r.status_code == 200
    assert _files(host) == []
    assert host.status(uid).status_code == 404


def test_an_id_that_is_not_ours_is_404_and_never_a_path(host):
    for bad in ("..%2F..%2Fetc", "abc", "g" * 32):
        assert host.status(bad).status_code == 404


def test_the_review_page_is_served(host, tmp_path):
    status = host.upload_ready(_two_profile_db(tmp_path))
    r = host.client.get(status["review_url"])
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert status["upload_id"] in html
    assert "__UPLOAD_ID__" not in html
    r = host.client.get("/ext/nina-ts-sync/import/uploads/<script>")
    assert "<script>\"" not in r.get_data(as_text=True)


def test_the_review_page_has_a_cancel_button(host, tmp_path):
    """The page's own JS wires the button to DELETE the upload and show an
    "Import cancelled" state. What the JS test setup can check from here is
    the button and its handler are in the served markup."""
    status = host.upload_ready(_two_profile_db(tmp_path))
    html = host.client.get(status["review_url"]).get_data(as_text=True)
    assert 'id="cancel"' in html
    assert "Cancel this import" in html
    assert "Nothing in ACP changes. Send again from NINA when TS is ready." in html
    assert '"cancel").addEventListener("click"' in html
    assert "Import cancelled" in html


def test_the_review_page_has_a_back_to_acp_link(host, tmp_path):
    status = host.upload_ready(_two_profile_db(tmp_path))
    html = host.client.get(status["review_url"]).get_data(as_text=True)
    assert '<a class="btn secondary" href="/">Back to ACP</a>' in html


def test_the_review_page_shows_when_a_pending_upload_expires(host, tmp_path):
    """The page carries created_iso and expires_iso so its own JS can render
    the relative expiry text; check the fields are in the served status
    payload the page's script reads, since the rest is beyond this test's
    reach."""
    status = host.upload_ready(_two_profile_db(tmp_path))
    r = host.client.get(f"/api/ext/nina-ts-sync/import/uploads/{status['upload_id']}")
    body = r.get_json()
    assert body["created_iso"] and body["expires_iso"]
    assert body["expires_iso"] > body["created_iso"]
    html = host.client.get(status["review_url"]).get_data(as_text=True)
    assert "fmtExpiry" in html
    assert "After that, send it again from NINA." in html


def test_the_worker_opens_the_copy_with_untrusted_schema_off(host, tmp_path, monkeypatch):
    """A view or trigger in a crafted file must not run functions with side
    effects. trusted_schema = OFF is how SQLite promises that."""
    seen = []
    from nina_ts_sync import import_merge
    real = import_merge.open_readonly

    def spy(path, *, immutable=False):
        conn = real(path, immutable=immutable)
        if immutable:
            seen.append(conn.execute("PRAGMA trusted_schema").fetchone()[0])
        return conn

    monkeypatch.setattr(import_merge, "open_readonly", spy)
    host.upload_ready(_two_profile_db(tmp_path))
    assert seen and all(v == 0 for v in seen)
