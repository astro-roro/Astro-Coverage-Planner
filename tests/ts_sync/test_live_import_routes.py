"""The routes that read the TS database on the ACP host, after they moved
onto the shared merge engine, and a round trip on Voyager's real rows.

There were no route tests for `/import/diff` and `/import/resolve` before
this change. What they did and still do:

- `/import/diff` answers auto_pull, auto_keep, conflicts, ts_only_new and
  notices. Plans are now matched by link and ACP guid instead of by project
  name, and a plan linked only to another profile is out of scope.
- `/import/resolve` applies decisions, leaves undecided conflicts alone,
  never deletes, and adds new plans only when asked. ACP's sync modal asks
  by project name, which still works; a ts_only_new entry's `key` works too.
- `/sync` and `/import/apply` now write `ts_links[profile]` as well as
  ts_refs and ts_base_snapshot.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from .host import P, Q, FakeHost, link, new_db, push, set_desired
from .make_db import make_db
from .plans import plan, row

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VOYAGER_PROFILE = "9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7"


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch)


def ha(hours):
    return {"Ha": {"target_hours": hours, "sub_exposure_s": 600}}


def _post(host, route, db, profile=P, **body):
    return host.client.post(f"/api/ext/nina-ts-sync{route}",
                            json={"profile_id": profile, "db_path": str(db), **body})


def test_import_diff_keeps_its_shape_and_scope(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    x = plan("x", project_name="M31", filter_goals=ha(10))
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a], P)
    push(db, [x], Q)
    link(db, [a], P)
    link(db, [x], Q)
    host.write_plans([a, x])
    set_desired(db, "M31", "Ha", 72, P)
    push(db, [plan("c", project_name="Rosette")], P)

    r = _post(host, "/import/diff", db)
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) >= {"db_path", "auto_pull", "auto_keep", "conflicts", "ts_only_new", "notices"}
    assert [d["plan_id"] for d in body["auto_pull"]] == ["a"]
    assert [n["project_name"] for n in body["ts_only_new"]] == ["Rosette"]
    assert body["ts_only_new"][0]["key"]
    assert "x" not in json.dumps(body)


def test_import_resolve_by_project_name_and_by_key(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a, plan("c", project_name="Rosette"), plan("d", project_name="Veil")])
    link(db, [a])
    host.write_plans([a])
    set_desired(db, "M31", "Ha", 72)

    key = next(n["key"] for n in _post(host, "/import/diff", db).get_json()["ts_only_new"]
               if n["project_name"] == "Veil")
    r = _post(host, "/import/resolve", db, import_ts_only_new=["Rosette", key])
    assert r.status_code == 200, r.get_json()
    plans = host.plans()
    assert sorted(p["project_name"] for p in plans) == ["M31", "Rosette", "Veil"]
    assert row(host.plan("a"), "Ha")["target_hours"] == pytest.approx(12.0)
    for p in plans:
        assert P in p["ts_links"]


def test_import_resolve_leaves_undecided_conflicts(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a])
    link(db, [a])
    row(a, "Ha")["target_hours"] = 14
    host.write_plans([a])
    set_desired(db, "M31", "Ha", 72)
    before = host.plan("a")
    r = _post(host, "/import/resolve", db)
    assert r.status_code == 200
    assert host.plan("a") == before
    r = _post(host, "/import/resolve", db, decisions={"a": "take_ts"})
    assert row(host.plan("a"), "Ha")["target_hours"] == pytest.approx(12.0)


def test_sync_writes_ts_links(host, tmp_path):
    a = plan("a", project_name="M31")
    db = new_db(tmp_path / "ts.sqlite")
    host.write_plans([a])
    r = _post(host, "/sync", db)
    assert r.status_code == 200, r.get_json()
    got = host.plan("a")
    assert got["ts_links"][P]["refs"] == got["ts_refs"]
    assert got["ts_links"][P]["base_snapshot"] == got["ts_base_snapshot"]


def test_import_apply_writes_ts_links(host, tmp_path):
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [plan("a", project_name="M31")])
    host.write_plans([])
    r = _post(host, "/import/apply", db)
    assert r.status_code == 200, r.get_json()
    [got] = host.plans()
    assert got["ts_links"][P]["refs"] == got["ts_refs"]


# ---------------------------------------------------------------------------
# Voyager round trip
# ---------------------------------------------------------------------------


def _voyager_db(tmp_path) -> Path:
    path = make_db(23, tmp_path / "voyager.sqlite")
    conn = sqlite3.connect(path)
    conn.executescript((FIXTURES / "ts-snapshot-voyager.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


def test_voyager_upload_applies_and_a_second_upload_changes_nothing(host, tmp_path):
    """The last step of the live run order: upload, apply, upload again, and
    the preview shows nothing new, nothing updated and no conflicts."""
    host.gear_path.write_text((FIXTURES / "gear-voyager.json").read_text(encoding="utf-8"),
                              encoding="utf-8")
    host.write_plans([])
    db = _voyager_db(tmp_path)
    profiles = [{"id": VOYAGER_PROFILE, "name": "Voyager main"}]

    first = host.upload_ready(db, profile_id=VOYAGER_PROFILE, profiles=profiles)
    preview = first["preview"]
    assert preview["counts"]["new"] > 50
    assert preview["notes"] and preview["notes"][0]["losses"] > 0
    r = host.apply(first)
    assert r.status_code == 200, r.get_json()
    added = len(host.plans())
    assert added == preview["counts"]["new"]

    second = host.upload_ready(db, profile_id=VOYAGER_PROFILE, profiles=profiles)
    assert second["counts"] == {"new": 0, "updated": 0, "conflicts": 0}
    assert second["preview"]["proposals"] == []
    assert second["preview"]["counts"]["unchanged"] == added
    assert all(m["matched_by"] == "link" for m in second["preview"]["unchanged"])


# ---------------------------------------------------------------------------
# plans.json keeps every top-level key it didn't set, e.g. ACP core's
# settings_migrated flag (the one-shot draft-to-active migration guard).
# ---------------------------------------------------------------------------


def _seed_unknown_top_level_key(host, plans):
    host.plans_path.write_text(
        json.dumps({"version": 1, "settings_migrated": 1, "plans": plans}),
        encoding="utf-8")


def _top_level_doc(host) -> dict:
    return json.loads(host.plans_path.read_text(encoding="utf-8"))


def test_import_apply_preserves_unknown_top_level_keys(host, tmp_path):
    _seed_unknown_top_level_key(host, [])
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [plan("a", project_name="M31")])
    r = _post(host, "/import/apply", db)
    assert r.status_code == 200, r.get_json()
    assert _top_level_doc(host)["settings_migrated"] == 1


def test_links_route_preserves_unknown_top_level_keys(host, tmp_path):
    a = plan("a", project_name="M31")
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a])
    _seed_unknown_top_level_key(host, [a])

    refs = {"profile_id": P, "project_id": 1}
    r = host.client.post(
        "/api/ext/nina-ts-sync/links",
        json={"profile_id": P, "machine": "VOYAGER",
              "states": [{"plan_id": "a", "refs": refs, "base_snapshot": None}]})
    assert r.status_code == 200, r.get_json()
    assert _top_level_doc(host)["settings_migrated"] == 1
