"""Tests for the public live-page document builder, publisher, endpoints and CLI.

Spec: docs/specs/shooting-page.md. The builder is pure, so most tests feed it
hand-built plans, a tiny manifest and gear, and check the exact output shape.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from shooting_publish import (  # noqa: E402
    PublishConfigError,
    build_shooting_document,
    push_document,
    resolve_dest,
    write_document,
)

NOW = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)

GEAR = {
    "telescopes": [{"id": "t1", "name": "RedCat 51", "focal_length_mm": 250}],
    "cameras": [{"id": "c1", "name": "ASI2600MM", "pixel_size_um": 3.76, "sensor_px": [6248, 4176]}],
}


def _plan(**over):
    p = {
        "id": "p1", "guid": "abc-123", "project_name": "Vela", "visibility": "public",
        "is_current": True, "public_blurb": "Big and faint.",
        "telescope_id": "t1", "camera_id": "c1",
        "target": {"name": "Vela SNR", "center_ra_deg": 128.5, "center_dec_deg": -45.0,
                   "rotation_deg": 0, "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 0}},
        "filter_goals": {"Ha": {"target_hours": 10.0, "sub_exposure_s": 300},
                         "OIII": {"target_hours": 8.0}},
    }
    p.update(over)
    return p


def _target(ra, dec, hours_ha=2.0, last="2026-09-01", fov=(190.0, 127.0)):
    return {
        "target_id": 7, "objects": ["Vela SNR"],
        "center_ra_deg": ra, "center_dec_deg": dec, "fov_arcmin": list(fov),
        "date_range": ["2026-08-01", last],
        "telescopes": ["RedCat 51"],
        # "HA" canonicalises to "Ha", so both keys must sum into one bar.
        "filters": {"Ha": {"total_hours": hours_ha, "files": 3},
                    "HA": {"total_hours": 0.5, "files": 1}},
        "master_files": ["/Users/rohan/astro/vela_Ha.fit"],
    }


def _manifest(*targets):
    return {"scan_date": "2026-09-03T06:10:00", "targets": list(targets)}


class BuildDocument(unittest.TestCase):
    def test_private_plan_excluded_even_if_current(self):
        doc = build_shooting_document(
            [_plan(visibility="private"), _plan(id="p2", visibility=None)],
            _manifest(), GEAR, now=NOW)
        self.assertEqual(doc["projects"], [])

    def test_public_plan_included_with_expected_keys(self):
        doc = build_shooting_document([_plan()], _manifest(), GEAR, now=NOW)
        self.assertEqual(len(doc["projects"]), 1)
        pr = doc["projects"][0]
        self.assertEqual(set(pr), {"project_name", "target_name", "blurb", "is_current",
                                   "center_ra_deg", "center_dec_deg", "fov_arcmin",
                                   "telescope", "filters", "last_imaged", "last_imaged_nights_ago"})
        self.assertEqual(doc["version"], 1)
        self.assertEqual(doc["data_as_of"], "2026-09-03")
        self.assertTrue(doc["generated_at"].startswith("2026-09-03"))

    def test_no_forbidden_keys_and_no_paths(self):
        doc = build_shooting_document([_plan()], _manifest(_target(128.5, -45.0)), GEAR, now=NOW)
        text = json.dumps(doc)
        for bad in ("guid", "abc-123", "telescope_id", "camera_id", "sub_exposure_s",
                    "master_files", "/Users/", "\"id\"", "RedCat", "ASI2600", "2023-", "T06:10"):
            self.assertNotIn(bad, text, bad)

    def test_matching_target_contributes_hours_and_date(self):
        doc = build_shooting_document([_plan()], _manifest(_target(128.6, -45.1)), GEAR, now=NOW)
        pr = doc["projects"][0]
        self.assertEqual(pr["filters"]["Ha"]["done_hours"], 2.5)
        self.assertEqual(pr["filters"]["OIII"]["done_hours"], 0.0)
        self.assertEqual(pr["last_imaged"], "2026-09-01")
        self.assertEqual(pr["last_imaged_nights_ago"], 2)

    def test_far_target_does_not_match(self):
        doc = build_shooting_document([_plan()], _manifest(_target(200.0, 10.0)), GEAR, now=NOW)
        pr = doc["projects"][0]
        self.assertEqual(pr["filters"]["Ha"]["done_hours"], 0.0)
        self.assertIsNone(pr["last_imaged"])
        self.assertIsNone(pr["last_imaged_nights_ago"])

    def test_actual_hours_wins_when_higher(self):
        plan = _plan(filter_goals={"Ha": {"target_hours": 10.0, "actual_hours": 4.0}})
        doc = build_shooting_document([plan], _manifest(_target(128.5, -45.0)), GEAR, now=NOW)
        self.assertEqual(doc["projects"][0]["filters"]["Ha"]["done_hours"], 4.0)

    def test_mosaic_extent_and_field_present(self):
        plan = _plan(target={"name": "Vela SNR", "center_ra_deg": 128.5, "center_dec_deg": -45.0,
                             "rotation_deg": 0, "mosaic": {"rows": 2, "cols": 2, "overlap_pct": 20}})
        doc = build_shooting_document([plan], _manifest(), GEAR, now=NOW)
        pr = doc["projects"][0]
        self.assertEqual(pr["mosaic"], {"rows": 2, "cols": 2})
        w, h = pr["fov_arcmin"]
        self.assertGreater(w, 300)  # a single panel is about 194 arcmin wide
        self.assertGreater(w, h)

    def test_telescope_is_aperture_class(self):
        doc = build_shooting_document([_plan()], _manifest(), GEAR, now=NOW)
        self.assertEqual(doc["projects"][0]["telescope"], "51mm refractor")

    def test_missing_gear_and_manifest_do_not_crash(self):
        doc = build_shooting_document([_plan(telescope_id="nope")], None, {}, now=NOW)
        pr = doc["projects"][0]
        self.assertEqual(pr["fov_arcmin"], [0.0, 0.0])
        self.assertEqual(pr["telescope"], "telescope")
        self.assertIsNone(doc["data_as_of"])

    def test_sort_recent_first_then_nulls_then_name(self):
        plans = [
            _plan(id="a", project_name="Bravo"),
            _plan(id="b", project_name="Alpha",
                  target={"name": "x", "center_ra_deg": 10.0, "center_dec_deg": 10.0}),
            _plan(id="c", project_name="Charlie",
                  target={"name": "y", "center_ra_deg": 50.0, "center_dec_deg": 50.0}),
        ]
        man = _manifest(_target(128.5, -45.0, last="2026-08-20"), _target(50.0, 50.0, last="2026-09-02"))
        doc = build_shooting_document(plans, man, GEAR, now=NOW)
        self.assertEqual([p["project_name"] for p in doc["projects"]], ["Charlie", "Bravo", "Alpha"])

    def test_path_anywhere_in_free_text_raises(self):
        cases = [
            _plan(public_blurb="the raw data is in /Users/rohan/astro/vela, huge"),
            _plan(public_blurb="master is vela_Ha.fit and it is big"),
            _plan(project_name="Vela (D:\\astro\\vela)"),
            _plan(target={"name": "see \\\\nas\\astro", "center_ra_deg": 1.0, "center_dec_deg": 1.0}),
        ]
        for plan in cases:
            with self.assertRaises(RuntimeError, msg=plan):
                build_shooting_document([plan], _manifest(), GEAR, now=NOW)

    def test_ordinary_free_text_passes(self):
        doc = build_shooting_document(
            [_plan(public_blurb="Shot at f/4.9 from Sydney. Ha and OIII, 3nm.")], _manifest(), GEAR, now=NOW)
        self.assertEqual(len(doc["projects"]), 1)

    def test_match_boundary_is_half_the_larger_diagonal(self):
        # Plan footprint is about 323 x 216 arcmin, diagonal about 389, so the
        # limit is about 3.24 deg. A tiny target just inside matches, one just
        # outside does not. If the rule were the full diagonal both would match.
        near = _target(128.5, -45.0 + 3.1, fov=(10.0, 10.0))
        far = _target(128.5, -45.0 + 3.4, fov=(10.0, 10.0))
        doc = build_shooting_document([_plan()], _manifest(near, far), GEAR, now=NOW)
        self.assertEqual(doc["projects"][0]["filters"]["Ha"]["done_hours"], 2.5)
        doc = build_shooting_document([_plan()], _manifest(far), GEAR, now=NOW)
        self.assertEqual(doc["projects"][0]["filters"]["Ha"]["done_hours"], 0.0)

    def test_sort_ties_on_date_break_by_name(self):
        plans = [_plan(id="a", project_name="Zulu"), _plan(id="b", project_name="alpha")]
        doc = build_shooting_document(plans, _manifest(_target(128.5, -45.0)), GEAR, now=NOW)
        self.assertEqual([p["project_name"] for p in doc["projects"]], ["alpha", "Zulu"])


class WriteAndPush(unittest.TestCase):
    def test_write_document_creates_file(self):
        td = Path(tempfile.mkdtemp())
        p = write_document({"version": 1, "projects": []}, td / "live")
        self.assertEqual(p.name, "shooting.json")
        self.assertEqual(json.loads(p.read_text())["version"], 1)

    def test_resolve_dest_requires_env(self):
        os.environ.pop("ACP_PUBLISH_DEST", None)
        with self.assertRaises(PublishConfigError):
            resolve_dest()
        os.environ["ACP_PUBLISH_DEST"] = "user@host:/srv/live/shooting.json"
        try:
            self.assertEqual(resolve_dest(), "user@host:/srv/live/shooting.json")
        finally:
            os.environ.pop("ACP_PUBLISH_DEST", None)

    def test_parse_dest(self):
        from shooting_publish import parse_dest
        self.assertEqual(parse_dest("u@h:/srv/live/shooting.json"), ("u", "h", 22, "/srv/live/shooting.json"))
        self.assertEqual(parse_dest("h:/srv/x.json"), (None, "h", 22, "/srv/x.json"))
        with self.assertRaises(PublishConfigError):
            parse_dest("just-a-host")

    def test_push_document_uploads_then_renames(self):
        import paramiko
        events = []

        class FakeSftp:
            def put(self, local, remote): events.append(("put", local, remote))
            def posix_rename(self, a, b): events.append(("rename", a, b))
            def close(self): events.append(("sftp-close",))

        class FakeClient:
            def __init__(self): events.append(("new",))
            def load_host_keys(self, f): events.append(("load", f))
            def set_missing_host_key_policy(self, p): events.append(("policy", type(p).__name__))
            def connect(self, host, **kw): events.append(("connect", host, kw["username"], kw["key_filename"], kw["look_for_keys"]))
            def save_host_keys(self, f): events.append(("save", f))
            def open_sftp(self): return FakeSftp()
            def close(self): events.append(("close",))

        td = Path(tempfile.mkdtemp())
        local = td / "shooting.json"
        local.write_text("{}")
        orig = paramiko.SSHClient
        paramiko.SSHClient = FakeClient
        try:
            r = push_document(local, "u@h:/srv/live/shooting.json", ssh_key="/k")
        finally:
            paramiko.SSHClient = orig
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(("connect", "h", "u", "/k", False), events)
        self.assertIn(("put", str(local), "/srv/live/shooting.json.tmp"), events)
        self.assertIn(("rename", "/srv/live/shooting.json.tmp", "/srv/live/shooting.json"), events)
        self.assertIn(("save", str(td / "known_hosts")), events)
        self.assertTrue((td / "known_hosts").exists())

    def test_push_document_reports_failure_without_raising(self):
        import paramiko

        class FailingClient:
            def load_host_keys(self, f): pass
            def set_missing_host_key_policy(self, p): pass
            def connect(self, host, **kw): raise OSError("connection refused")
            def close(self): pass

        td = Path(tempfile.mkdtemp())
        (td / "shooting.json").write_text("{}")
        orig = paramiko.SSHClient
        paramiko.SSHClient = FailingClient
        try:
            r = push_document(td / "shooting.json", "u@h:/srv/live/shooting.json", ssh_key="/k")
        finally:
            paramiko.SSHClient = orig
        self.assertEqual(r.returncode, 1)
        self.assertIn("connection refused", r.stderr)


class Endpoints(unittest.TestCase):
    def setUp(self):
        import app as app_module
        td = Path(tempfile.mkdtemp())
        app_module.PLANS_PATH = td / "plans.json"
        app_module._plans_cache = None
        app_module._plans_cache_mtime = None
        app_module.MANIFEST_PATH = td / "manifest.json"
        app_module._manifest_cache = None
        app_module._manifest_cache_mtime = None
        app_module.GEAR_PATH = td / "gear.json"
        app_module._gear_cache = None
        app_module._gear_cache_mtime = None
        app_module.DESTINATIONS_PATH = td / "destinations.json"
        app_module._destinations_cache = None
        app_module._destinations_cache_mtime = None
        app_module.LIVE_OUT_DIR = td / "live"
        (td / "plans.json").write_text(json.dumps(
            {"version": 1, "plans": [_plan(), _plan(id="p2", visibility="private")]}))
        (td / "manifest.json").write_text(json.dumps(_manifest(_target(128.5, -45.0))))
        (td / "gear.json").write_text(json.dumps(GEAR))
        self.client = app_module.app.test_client()

    def test_get_public_document(self):
        r = self.client.get("/api/public/shooting")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(len(body["projects"]), 1)
        self.assertEqual(body["projects"][0]["project_name"], "Vela")
        self.assertEqual(body["projects"][0]["filters"]["Ha"]["done_hours"], 2.5)

    def test_publish_config_reflects_env(self):
        os.environ.pop("ACP_PUBLISH_DEST", None)
        self.assertFalse(self.client.get("/api/publish/config").get_json()["live_page_enabled"])
        os.environ["ACP_PUBLISH_DEST"] = "u@h:/srv/live/shooting.json"
        try:
            self.assertTrue(self.client.get("/api/publish/config").get_json()["live_page_enabled"])
        finally:
            os.environ.pop("ACP_PUBLISH_DEST", None)

    def test_publish_without_dest_is_400(self):
        os.environ.pop("ACP_PUBLISH_DEST", None)
        r = self.client.post("/api/publish/shooting")
        self.assertEqual(r.status_code, 400)
        self.assertIn("ACP_PUBLISH_DEST", r.get_json()["error"])

    def test_publish_writes_and_pushes(self):
        import shooting_publish as sp
        os.environ["ACP_PUBLISH_DEST"] = "u@h:/srv/live/shooting.json"
        seen = {}

        def fake_push(path, dest, ssh_key=None):
            seen["path"], seen["dest"] = path, dest
            return sp.PushResult(0, "")

        orig = sp.push_document
        sp.push_document = fake_push
        try:
            r = self.client.post("/api/publish/shooting")
        finally:
            sp.push_document = orig
            os.environ.pop("ACP_PUBLISH_DEST", None)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(r.get_json()["projects"], 1)
        self.assertEqual(seen["dest"], "u@h:/srv/live/shooting.json")
        self.assertTrue(Path(seen["path"]).exists())

    def test_publish_reports_rsync_failure(self):
        import shooting_publish as sp
        os.environ["ACP_PUBLISH_DEST"] = "u@h:/srv/live/shooting.json"
        orig = sp.push_document
        sp.push_document = lambda path, dest, ssh_key=None: sp.PushResult(255, "connection refused")
        try:
            r = self.client.post("/api/publish/shooting")
        finally:
            sp.push_document = orig
            os.environ.pop("ACP_PUBLISH_DEST", None)
        self.assertEqual(r.status_code, 502)
        self.assertFalse(r.get_json()["ok"])
        self.assertIn("refused", r.get_json()["stderr"])


class Cli(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "publish_shooting.py"

    def _env(self, td: Path) -> dict:
        env = {**os.environ,
               "PLANS_PATH": str(td / "plans.json"),
               "MANIFEST_PATH": str(td / "manifest.json"),
               "GEAR_PATH": str(td / "gear.json"),
               "DESTINATIONS_PATH": str(td / "destinations.json"),
               "ACP_LIVE_OUT_DIR": str(td / "live")}
        env.pop("ACP_PUBLISH_DEST", None)
        return env

    def test_dry_run_writes_json_without_pushing(self):
        td = Path(tempfile.mkdtemp())
        (td / "plans.json").write_text(json.dumps({"version": 1, "plans": [_plan()]}))
        (td / "manifest.json").write_text(json.dumps(_manifest()))
        (td / "gear.json").write_text(json.dumps(GEAR))
        env = self._env(td)
        r = subprocess.run([sys.executable, str(self.SCRIPT), "--dry-run"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads((td / "live" / "shooting.json").read_text())
        self.assertEqual(out["projects"][0]["project_name"], "Vela")

    def test_missing_dest_exits_nonzero(self):
        td = Path(tempfile.mkdtemp())
        (td / "plans.json").write_text(json.dumps({"version": 1, "plans": []}))
        r = subprocess.run([sys.executable, str(self.SCRIPT)], env=self._env(td),
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ACP_PUBLISH_DEST", r.stderr)


if __name__ == "__main__":
    unittest.main()
