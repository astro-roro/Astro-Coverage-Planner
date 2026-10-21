"""A stand-in for the ACP host, for tests that drive the extension's routes.

The extension imports `load_plans`, `load_gear`, `save_plans` and
`PLANS_PATH` from ACP's `app` module at call time, and for gear templates
`load_fingerprints`, `record_template_results` and `set_links_capable`
(docs/specs/gear-templates.md sections 4 and 8). The last two are faked here
as recorders that also keep what ACP would: each gear template's `ts[P]`
link and `links_capable[P]`. `FakeHost` puts a module
of that name in `sys.modules` backed by files in a temp directory, and builds
a Flask app the way ACP does: a 1 MB body limit on every route, then the
extension's `register(app)`.

TS databases are built with the extension's own push, so their rows carry
the same guids a real push would, and then edited with SQL to stand for what
happened on the rig.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sqlite3
import sys
import threading
import types
from pathlib import Path

from flask import Flask

from nina_ts_sync import convert, upsert
from nina_ts_sync.links import write_link
from nina_ts_sync.reader import read_all
from nina_ts_sync.state import build_base_snapshot, build_ts_refs
from .make_db import make_db
from .plans import GEAR

TOKEN = "test-token-0123456789abcdef-0123456789"
P = "profile-P"
Q = "profile-Q"
MACHINE = "VOYAGER"

# ACP's gear list in these tests: the push tests' pair first, then a second
# pair, so "the first in the list" and "the most used" can differ.
HOST_GEAR = copy.deepcopy(GEAR)
HOST_GEAR["telescopes"].append({
    "id": "tel-2", "name": "Test 250mm", "focal_length_mm": 250.0, "aperture_mm": 50})
HOST_GEAR["cameras"].append({
    "id": "cam-2", "name": "Test IMX585", "pixel_size_um": 2.9, "sensor_px": [3840, 2160],
    "filters": copy.deepcopy(GEAR["cameras"][0]["filters"])})


class FakeHost:
    def __init__(self, root: Path, monkeypatch, *, token: str | None = TOKEN,
                 activity: bool = False):
        self.root = root
        self.plans_path = root / "data" / "plans.json"
        self.gear_path = root / "data" / "gear.json"
        self.plans_path.parent.mkdir(parents=True, exist_ok=True)
        self.gear_path.write_text(json.dumps(HOST_GEAR), encoding="utf-8")
        self.upload_dir = root / "uploads"
        #: ACP's fingerprint store entries, keyed by profile id, each shaped like
        #: tests/fixtures/acp-fingerprint-entry.json (rig under "fingerprint"). What
        #: `load_fingerprints` answers is ACP's real document shape,
        #: {"version": 1, "profiles": self.fingerprints}.
        self.fingerprints: dict = {}
        #: Stand-ins for ACP's GEAR_LOCK and the plans lock it shares with
        #: the extension. The fake gear functions take GEAR_LOCK and assert
        #: the plans lock is not held (gear-templates.md section 4).
        self.plans_lock = threading.RLock()
        self.gear_lock = threading.RLock()
        self.template_result_calls: list = []
        self.capability_calls: list = []
        self.links_capable: dict = {}

        mod = types.ModuleType("app")
        mod.PLANS_PATH = self.plans_path

        def load_plans():
            if not self.plans_path.exists():
                return {"version": 1, "plans": []}
            return json.loads(self.plans_path.read_text(encoding="utf-8"))

        def save_plans(data):
            self.plans_path.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")

        def load_gear():
            return json.loads(self.gear_path.read_text(encoding="utf-8"))

        def load_fingerprints():
            return {"version": 1, "profiles": copy.deepcopy(self.fingerprints)}

        def assert_plans_lock_free():
            # ACP: code that holds PLANS_LOCK never takes GEAR_LOCK.
            assert not self.plans_lock._is_owned(), (
                "gear function called while holding PLANS_LOCK (lock-order inversion)")

        def record_template_results(profile_id, results):
            assert_plans_lock_free()
            with self.gear_lock:
                self.template_result_calls.append((profile_id, copy.deepcopy(results)))
                gear = self.gear()
                by_id = {
                    t["id"]: t
                    for cam in gear.get("cameras", [])
                    for cfg in (cam.get("filters") or {}).values()
                    for t in cfg.get("templates", [])
                }
                for result in results:
                    tpl = by_id.get(result.get("template_id"))
                    if tpl is None or result.get("ts_id") is None:
                        continue
                    tpl.setdefault("ts", {})[profile_id] = {
                        "id": result["ts_id"], "guid": result.get("guid"), "base": result.get("base"),
                    }
                self.write_gear(gear)

        def set_links_capable(profile_id, capabilities):
            assert_plans_lock_free()
            with self.gear_lock:
                self.capability_calls.append((profile_id, list(capabilities)))
                if "templates" in capabilities and "repoint" in capabilities:
                    self.links_capable[profile_id] = {"templates": True, "repoint": True}
                else:
                    self.links_capable.pop(profile_id, None)

        mod.load_plans = load_plans
        mod.save_plans = save_plans
        mod.load_gear = load_gear
        mod.load_fingerprints = load_fingerprints
        mod.record_template_results = record_template_results
        mod.set_links_capable = set_links_capable
        monkeypatch.setitem(sys.modules, "app", mod)

        monkeypatch.setenv("ACP_TS_UPLOAD_DIR", str(self.upload_dir))
        monkeypatch.setenv("ACP_EXTENSIONS_CONFIG_DIR", str(root / "ext-config"))
        monkeypatch.delenv("ACP_TS_UPLOAD_TTL_HOURS", raising=False)
        monkeypatch.delenv("ACP_TS_UPLOAD_MAX_BYTES", raising=False)
        if token:
            monkeypatch.setenv("ACP_API_TOKEN", token)
        else:
            monkeypatch.delenv("ACP_API_TOKEN", raising=False)

        app = Flask("acp-test")
        app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
        app.config["TESTING"] = True
        app.plans_lock = self.plans_lock
        import nina_ts_sync
        nina_ts_sync.register(app)
        self.app = app
        self.client = app.test_client()
        # ACP's Activity tab hook. Off by default, like an ACP that predates it.
        self.activity: list[dict] = []
        if activity:
            app.record_activity = lambda **fields: self.activity.append(fields)

    # -- gear.json --------------------------------------------------------

    def gear(self) -> dict:
        return json.loads(self.gear_path.read_text(encoding="utf-8"))

    def write_gear(self, gear: dict) -> None:
        self.gear_path.write_text(json.dumps(gear, indent=2), encoding="utf-8")

    # -- plans.json -------------------------------------------------------

    def write_plans(self, plans: list[dict]) -> None:
        self.plans_path.write_text(json.dumps({"version": 1, "plans": plans}, indent=2),
                                   encoding="utf-8")

    def plans(self) -> list[dict]:
        return json.loads(self.plans_path.read_text(encoding="utf-8"))["plans"]

    def plan(self, plan_id: str) -> dict:
        return next(p for p in self.plans() if p["id"] == plan_id)

    def backups(self) -> list[Path]:
        return sorted(self.plans_path.parent.glob("plans-acpsync-*-backup.json"))

    # -- uploads ----------------------------------------------------------

    def upload(self, db: Path, *, profile_id: str = P, machine: str = MACHINE,
               profiles=None, sha: str | None = None, data: bytes | None = None,
               token: bool = True):
        raw = data if data is not None else db.read_bytes()
        meta = {
            "profile_id": profile_id,
            "profiles": profiles if profiles is not None else [
                {"id": P, "name": "Voyager main"}, {"id": Q, "name": "Travel rig"}],
            "sha256": sha or hashlib.sha256(raw).hexdigest(),
            "user_version": 28,
            "plugin_version": "3.3.0.0",
            "machine": machine,
        }
        headers = {"Authorization": f"Bearer {TOKEN}"} if token else {}
        return self.client.post(
            "/api/ext/nina-ts-sync/import/uploads",
            data={
                "file": (io.BytesIO(raw), "schedulerdb.sqlite", "application/vnd.sqlite3"),
                "meta": json.dumps(meta),
            },
            content_type="multipart/form-data",
            headers=headers,
        )

    def status(self, upload_id: str):
        return self.client.get(f"/api/ext/nina-ts-sync/import/uploads/{upload_id}")

    def ready(self, upload_id: str) -> dict:
        from nina_ts_sync import uploads
        assert uploads.wait_idle(20)
        r = self.status(upload_id)
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    def upload_ready(self, db: Path, **kw) -> dict:
        r = self.upload(db, **kw)
        assert r.status_code == 202, r.get_json()
        body = self.ready(r.get_json()["upload_id"])
        assert body["state"] == "ready", body
        return body

    def apply(self, status: dict, **body):
        preview = status["preview"]
        payload = {
            "profile_id": preview["profile_id"],
            "sha256": status["sha256"],
            "preview_hash": preview["preview_hash"],
            "decisions": {},
        }
        payload.update(body)
        return self.client.post(
            f"/api/ext/nina-ts-sync/import/uploads/{status['upload_id']}/apply",
            json=payload)


# -- TS databases -----------------------------------------------------------


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def push(db: Path, plans: list[dict], profile_id: str = P) -> None:
    """Write *plans* into the TS database the way the extension's push does."""
    conn = connect(db)
    try:
        upsert.apply(conn, convert.build_payload(copy.deepcopy(plans), HOST_GEAR, profile_id=profile_id))
        conn.commit()
    finally:
        conn.close()


def new_db(path: Path) -> Path:
    return make_db(28, path)


def link(db: Path, plans: list[dict], profile_id: str = P, machine: str = MACHINE) -> None:
    """Give each plan a link for *profile_id* from the database as it is
    now, the state a sync leaves behind."""
    conn = connect(db)
    try:
        snap = read_all(conn, profile_id)
    finally:
        conn.close()
    for plan in plans:
        refs = build_ts_refs(snap, plan, profile_id=profile_id, user_version=28, operation="push")
        base = build_base_snapshot(snap, plan, profile_id=profile_id)
        write_link(plan, profile_id, machine=machine, refs=refs, base_snapshot=base)


def set_desired(db: Path, project_name: str, filter_name: str, desired: int,
                profile_id: str = P) -> None:
    """Change an exposure plan's desired count in TS, as an edit on the rig."""
    conn = connect(db)
    try:
        n = conn.execute(
            "UPDATE exposureplan SET desired = ? WHERE Id IN ("
            " SELECT ep.Id FROM exposureplan ep"
            " JOIN target t ON ep.targetid = t.Id"
            " JOIN project p ON t.projectid = p.Id"
            " JOIN exposuretemplate et ON ep.exposureTemplateId = et.Id"
            " WHERE p.name = ? AND et.filtername = ? AND p.profileId = ?)",
            (desired, project_name, filter_name, profile_id)).rowcount
        assert n >= 1, f"no exposure plan for {project_name}/{filter_name}"
        conn.commit()
    finally:
        conn.close()


def set_project_state(db: Path, project_name: str, state: int, profile_id: str = P) -> None:
    """Change a project's state in TS (1 active, 2 inactive/parked), as an
    edit on the rig."""
    conn = connect(db)
    try:
        n = conn.execute(
            "UPDATE project SET state = ? WHERE name = ? AND profileId = ?",
            (state, project_name, profile_id)).rowcount
        assert n >= 1, f"no project named {project_name}"
        conn.commit()
    finally:
        conn.close()
