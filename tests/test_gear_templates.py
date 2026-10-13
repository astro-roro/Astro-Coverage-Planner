"""Phase 1 of docs/specs/gear-templates.md: the gear v3 data, the migration,
the gear API, the wheel block, the fingerprint fields and the plan row
repair. Test ids (G1, W4, M1 and so on) are the spec's.

Each test builds its own gear, plans, fingerprints and TS list in a temp
directory, from tests/fixtures/gear-templates-v3.json (the gear after the
migration and adoption, shared with the plugin's tests) or
tests/fixtures/gear-v2-live.json (the live file before them).
"""
from __future__ import annotations

import copy
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import filter_rows as fr  # noqa: E402
from app import app  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
P1 = "9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7"
P2 = "093effd2-29a5-4ce6-ae38-d6eea265a912"
SLOTS = ["L", "R", "G", "B", "H", "O", "S"]
CAM = "zwo-asi6200mm-pro"


def v3() -> dict:
    return json.loads((FIXTURES / "gear-templates-v3.json").read_text(encoding="utf-8"))


def v2_live() -> dict:
    return json.loads((FIXTURES / "gear-v2-live.json").read_text(encoding="utf-8"))


def redirect_state() -> Path:
    td = Path(tempfile.mkdtemp())
    app_module.GEAR_PATH = td / "gear.json"
    app_module.PLANS_PATH = td / "plans.json"
    app_module.FINGERPRINTS_PATH = td / "fingerprints.json"
    app_module.TS_TEMPLATES_PATH = td / "ts_templates.json"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    app_module.MANIFEST_PATH = td / "manifest.json"
    for name in ("_gear_cache", "_plans_cache", "_fingerprints_cache", "_destinations_cache",
                 "_gear_cache_mtime", "_plans_cache_mtime", "_fingerprints_cache_mtime",
                 "_destinations_cache_mtime", "_ts_store_cache", "_gear_migration_failed",
                 "_manifest_cache", "_manifest_cache_mtime"):
        setattr(app_module, name, None)
    return td


def write_gear(doc: dict) -> None:
    app_module.GEAR_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    app_module._gear_cache = None
    app_module._gear_cache_mtime = None


def read_gear() -> dict:
    return json.loads(app_module.GEAR_PATH.read_text(encoding="utf-8"))


def write_plans(plans: list) -> None:
    # Both migration flags set, so a raw file is not rewritten under the test.
    app_module.PLANS_PATH.write_text(json.dumps({
        "version": 1, "settings_migrated": 1, "filter_rows_migrated": 1, "plans": plans}),
        encoding="utf-8")
    app_module._plans_cache = None
    app_module._plans_cache_mtime = None


def read_plans() -> list:
    return json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))["plans"]


def write_ts_list(profile_id: str, templates: list) -> None:
    path = app_module.TS_TEMPLATES_PATH
    existing = json.loads(path.read_text(encoding="utf-8"))["profiles"] if path.exists() else {}
    existing[profile_id] = {"received_at": "2026-10-13T00:00:00+00:00", "templates": templates}
    path.write_text(json.dumps({"profiles": existing}), encoding="utf-8")
    app_module._ts_store_cache = None


def tpl(doc: dict, camera_id: str, filt: str, name: str) -> dict:
    cam = next(c for c in doc["cameras"] if c["id"] == camera_id)
    return next(t for t in cam["filters"][filt]["templates"] if t["name"] == name)


def row(rid: str, filt: str, template: str, template_id: str | None, sub: float, **kw) -> dict:
    r = {"id": rid, "filter": filt, "template": template, "template_sub_s": sub,
         "unit": "h", "target_hours": 3}
    if template_id:
        r["template_id"] = template_id
    r.update(kw)
    return r


def plan(plan_id: str, rows: list, camera_id: str = CAM, **kw) -> dict:
    p = {"id": plan_id, "project_name": "Test", "camera_id": camera_id,
         "target": {"name": plan_id, "center_ra_deg": 100.0, "center_dec_deg": -30.0,
                    "rotation_deg": 0, "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 10}},
         "filter_rows": rows}
    p.update(kw)
    return p


def fingerprint(camera: str = "ZWO ASI6200MM Pro", profile_id: str | None = P1,
                profile_name: str = "Voyager", slots=SLOTS, filters=None) -> dict:
    fp = {
        "profile_name": profile_name,
        "mode": "fit",
        "camera": {"name": camera, "sensor_px": [9576, 6388], "pixel_size_um": 3.76,
                   "colour": False, "bin": 1},
        "filters": list(slots) if filters is None else filters,
        "mount": {"name": "EQ6"},
        "focal_length_mm": {"profile": 550.0, "solved": 550.2, "source": "solved"},
        "wheel_slots": list(slots),
    }
    if profile_id is not None:
        fp["profile_id"] = profile_id
    return fp


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = redirect_state()
        self.client = app.test_client()

    def seed_v3(self, mutate=None, capable: bool = False) -> dict:
        doc = v3()
        if mutate:
            mutate(doc)
        write_gear(doc)
        if capable:
            app_module.set_links_capable(P1, ["templates", "repoint"])
        return read_gear()

    def match(self, fp: dict):
        r = self.client.post("/api/plans/match", json=fp)
        self.assertEqual(r.status_code, 200, r.get_json())
        return r

    def get_gear(self) -> dict:
        r = self.client.get("/api/gear")
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def post_gear(self, edit=None, **overrides):
        """POST what GET served, after `edit(body)`. The page sends the
        derived keys back and the server has to ignore them."""
        g = self.get_gear()
        body = {"gear_schema": 3, "rev": g["rev"], "telescopes": g["telescopes"], "cameras": g["cameras"]}
        if edit:
            edit(body)
        body.update(overrides)
        return self.client.post("/api/gear", json=body)


def camera(body: dict, camera_id: str = CAM) -> dict:
    return next(c for c in body["cameras"] if c["id"] == camera_id)


# ---------------------------------------------------------------------------
# W1, W2, W3: resolve_to_wheel
# ---------------------------------------------------------------------------


class TestResolveToWheel(unittest.TestCase):
    def test_w1_the_rigs_wheel(self):
        r = fr.resolve_to_wheel
        self.assertEqual(r("Ha", SLOTS), "H")
        self.assertEqual(r("OIII", SLOTS), "O")
        self.assertEqual(r("SII", SLOTS), "S")
        self.assertEqual(r("R", SLOTS), "R")
        self.assertIsNone(r("R Stars", SLOTS))
        self.assertIsNone(r("Antlia Ha", SLOTS))

    def test_w2_exact_then_case_then_alias(self):
        self.assertEqual(fr.resolve_to_wheel("Ha", ["Ha", "H"]), "Ha")
        self.assertEqual(fr.resolve_to_wheel("ha", ["Ha"]), "Ha")
        self.assertEqual(fr.resolve_to_wheel("L", ["Lum"]), "Lum")

    def test_w3_shared_case_file(self):
        cases = json.loads((FIXTURES / "wheel_resolve_cases.json").read_text(encoding="utf-8"))["cases"]
        self.assertGreaterEqual(len(cases), 20)
        for c in cases:
            with self.subTest(filter=c["filter"], slots=c["slots"]):
                self.assertEqual(fr.resolve_to_wheel(c["filter"], c["slots"]), c["expect"])


# ---------------------------------------------------------------------------
# G: the gear API
# ---------------------------------------------------------------------------


class TestGearGet(Base):
    def test_g1_filter_object_carries_the_starred_templates_scalars(self):
        def add(doc):
            ha = next(c for c in doc["cameras"] if c["id"] == CAM)["filters"]["Ha"]["templates"]
            ha.append({"id": "ha-600", "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": -1,
                       "bin": 1, "starred": False, "aliases": [], "ts_names": [], "ts": {}})
        self.seed_v3(add)
        ha = camera(self.get_gear())["filters"]["Ha"]
        self.assertEqual(ha["default_sub_s"], 300)
        self.assertEqual(ha["ts_template_name"], "Ha")
        self.assertEqual([t["name"] for t in ha["templates"]], ["Ha", "Ha 600"])
        self.assertEqual(ha["gain"], -1)
        self.assertEqual(ha["bin"], 1)

    def test_g20_load_adds_derived_keys_and_save_strips_them(self):
        self.seed_v3()
        loaded = app_module.load_gear()
        cam = next(c for c in loaded["cameras"] if c["id"] == CAM)
        ha = cam["filters"]["Ha"]
        self.assertIn("default_sub_s", ha)
        self.assertIn("status", ha["templates"][0])
        self.assertIn("used_by", ha["templates"][0])
        self.assertIn("wheel", cam)
        app_module.save_gear(loaded)
        stored = read_gear()
        self.assertEqual(stored, v3())
        text = app_module.GEAR_PATH.read_text(encoding="utf-8")
        for key in ("default_sub_s", "ts_template_name", '"status"', '"used_by"', '"wheel"',
                    "conflict_count", "wheel_unlinked"):
            self.assertNotIn(key, text)

    def test_ts_template_id_is_the_primary_profiles_id_as_a_string(self):
        self.seed_v3()
        self.match(fingerprint())
        self.assertEqual(camera(self.get_gear())["filters"]["Ha"]["ts_template_id"], "12")


class TestGearPost(Base):
    def test_g2_stale_rev_is_409_and_writes_nothing(self):
        self.seed_v3()
        before = app_module.GEAR_PATH.read_bytes()
        r = self.post_gear(rev=0)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["gear"]["rev"], 1)
        self.assertEqual(app_module.GEAR_PATH.read_bytes(), before)

    def test_g3_rename_reaches_the_rows_and_keeps_the_old_name(self):
        name = "R (ZWO ASI6200MM Pro)"
        doc = self.seed_v3(capable=True)
        self.match(fingerprint())
        t = tpl(doc, CAM, "R", name)
        write_plans([plan(f"p{i}", [row("r1", "R", name, t["id"], 120)]) for i in range(26)])
        rev = self.get_gear()["rev"]

        def rename(body):
            tpl(body, CAM, "R", name)["name"] = "R 120"
        r = self.post_gear(rename)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["rev"], rev + 1)
        self.assertEqual({p["filter_rows"][0]["template"] for p in read_plans()}, {"R 120"})
        stored = tpl(read_gear(), CAM, "R", "R 120")
        self.assertEqual(stored["aliases"][0], name)
        self.assertEqual(stored["ts"][P1]["id"], 19)
        self.assertEqual(read_gear()["rev"], rev + 1)

    def test_g4_removing_a_template_rows_use_is_refused(self):
        doc = self.seed_v3()
        t = tpl(doc, CAM, "R", "R Stars")
        write_plans([plan(f"p{i:02d}", [row("r1", "R", "R Stars", t["id"], 15, unit="fr", target_frames=20)])
                     for i in range(23)])
        before = app_module.GEAR_PATH.read_bytes()

        def drop(body):
            cfg = camera(body)["filters"]["R"]
            cfg["templates"] = [x for x in cfg["templates"] if x["name"] != "R Stars"]
        r = self.post_gear(drop)
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertEqual(body["rows"], 23)
        self.assertEqual(len(body["plans"]), 23)
        self.assertIn("p00", body["plans"])
        self.assertIn("23", body["error"])
        self.assertEqual(app_module.GEAR_PATH.read_bytes(), before)

    def test_g5_removing_an_unused_template_is_allowed(self):
        def add(doc):
            ha = next(c for c in doc["cameras"] if c["id"] == CAM)["filters"]["Ha"]["templates"]
            ha.append({"id": "ha-600", "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": -1,
                       "bin": 1, "starred": False, "aliases": [], "ts_names": [], "ts": {}})
        self.seed_v3(add)

        def drop(body):
            cfg = camera(body)["filters"]["Ha"]
            cfg["templates"] = [x for x in cfg["templates"] if x["name"] != "Ha 600"]
        r = self.post_gear(drop)
        self.assertEqual(r.status_code, 200, r.get_json())
        names = [t["name"] for t in read_gear()["cameras"][0]["filters"]["Ha"]["templates"]]
        self.assertEqual(names, ["Ha"])

    def test_removing_a_filter_or_camera_that_plans_use_is_refused(self):
        doc = self.seed_v3()
        t = tpl(doc, CAM, "SII", "SII")
        write_plans([plan("p1", [row("r1", "SII", "SII", t["id"], 300)])])

        def drop_filter(body):
            del camera(body)["filters"]["SII"]
        self.assertEqual(self.post_gear(drop_filter).status_code, 400)

        def drop_camera(body):
            body["cameras"] = [c for c in body["cameras"] if c["id"] != CAM]
        self.assertEqual(self.post_gear(drop_camera).status_code, 400)

    def test_g6_duplicate_names_ignoring_case(self):
        self.seed_v3()

        def dup(body):
            camera(body)["filters"]["Ha"]["templates"].append(
                {"name": "ha", "sub_s": 300, "gain": -1, "offset": -1, "bin": 1})
        r = self.post_gear(dup)
        self.assertEqual(r.status_code, 400)
        self.assertIn("'ha'", r.get_json()["error"])

    def test_range_checks_and_a_filter_with_no_template(self):
        self.seed_v3()
        for field, value in (("sub_s", 0), ("sub_s", 3601), ("gain", -2), ("offset", 1.5),
                             ("bin", 5), ("name", ""), ("name", "x" * 65), ("name", "a\nb")):
            with self.subTest(field=field, value=value):
                def bad(body, field=field, value=value):
                    tpl(body, CAM, "L", "L")[field] = value
                self.assertEqual(self.post_gear(bad).status_code, 400)

        def empty(body):
            camera(body)["filters"]["L"]["templates"] = []
        self.assertEqual(self.post_gear(empty).status_code, 400)

    def test_g7_a_sub_change_reaches_the_rows_that_follow_the_template(self):
        doc = self.seed_v3(capable=True)
        self.match(fingerprint())
        t = tpl(doc, CAM, "Ha", "Ha")
        write_plans([plan(f"p{i}", [row("r1", "Ha", "Ha", t["id"], 300, target_hours=3)])
                     for i in range(23)])
        self.assertEqual(fr.desired(read_plans()[0]["filter_rows"][0]), 36)

        def to_600(body):
            tpl(body, CAM, "Ha", "Ha")["sub_s"] = 600
        r = self.post_gear(to_600)
        self.assertEqual(r.status_code, 200, r.get_json())
        changes = r.get_json()["sub_changes"]
        self.assertEqual(len(changes), 23)
        self.assertEqual(changes[0]["old_sub_s"], 300)
        self.assertEqual(changes[0]["new_sub_s"], 600)
        for p in read_plans():
            rw = p["filter_rows"][0]
            self.assertEqual(rw["template_sub_s"], 600)
            self.assertEqual(rw["target_hours"], 3)
            self.assertEqual(fr.desired(rw), 18)

    def test_g8_a_row_that_crosses_sixty_seconds_changes_unit(self):
        doc = self.seed_v3(capable=True)
        self.match(fingerprint())
        t = tpl(doc, CAM, "R", "R Stars")
        write_plans([plan("p1", [row("r1", "R", "R Stars", t["id"], 15, unit="fr", target_frames=20)])])

        def to_90(body):
            tpl(body, CAM, "R", "R Stars")["sub_s"] = 90
        self.assertEqual(self.post_gear(to_90).status_code, 200)
        rw = read_plans()[0]["filter_rows"][0]
        self.assertEqual(rw["unit"], "h")
        self.assertEqual(rw["target_hours"], 0.0833)
        self.assertEqual(rw["template_sub_s"], 90)
        self.assertNotIn("target_frames", rw)

    def test_g22_deviating_rows_keep_their_own_sub(self):
        name = "R (ZWO ASI6200MM Pro)"
        doc = self.seed_v3(capable=True)
        self.match(fingerprint())
        t = tpl(doc, CAM, "R", name)
        rows_180 = [plan(f"a{i}", [row("r1", "R", name, t["id"], 180)]) for i in range(23)]
        rows_15 = [plan(f"b{i}", [row("r1", "R", name, t["id"], 15, unit="fr", target_frames=20)])
                   for i in range(3)]
        write_plans(rows_180 + rows_15)

        def to_150(body):
            tpl(body, CAM, "R", name)["sub_s"] = 150
        r = self.post_gear(to_150)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["sub_changes"], [])
        self.assertEqual({p["filter_rows"][0]["template_sub_s"] for p in read_plans()}, {180, 15})
        self.assertEqual(tpl(read_gear(), CAM, "R", name)["sub_s"], 150)
        self.assertEqual(tpl(self.get_gear(), CAM, "R", name)["used_by"], 26)

    def test_g9_an_old_page_cannot_revert_filters(self):
        self.seed_v3(capable=True)
        g = self.get_gear()
        body = {"telescopes": g["telescopes"], "cameras": [
            {"id": CAM, "name": "ZWO ASI6200MM Pro", "pixel_size_um": 3.76, "sensor_px": [9576, 6388],
             "filters": {"Ha": {"default_sub_s": 600}}}]}
        r = self.client.post("/api/gear", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["ignored"], ["filters"])
        stored = read_gear()
        ha = tpl(stored, CAM, "Ha", "Ha")
        self.assertEqual(ha["sub_s"], 300)
        self.assertEqual(ha["ts"][P1]["id"], 12)
        self.assertEqual(len(tpl(stored, CAM, "R", "R Stars")["ts"]), 1)

    def test_g10_an_old_page_never_deletes_a_camera(self):
        self.seed_v3()
        g = self.get_gear()
        before = len(g["cameras"])
        keep = [c for c in g["cameras"] if c["id"] == CAM]
        r = self.client.post("/api/gear", json={"telescopes": g["telescopes"], "cameras": keep})
        self.assertEqual(r.status_code, 200)
        stored = read_gear()
        self.assertEqual(len(stored["cameras"]), before)
        self.assertEqual(stored, {**v3(), "rev": 2})

    def test_an_old_page_adds_an_unknown_camera_with_default_templates(self):
        self.seed_v3()
        g = self.get_gear()
        new = {"id": "new-cam", "name": "New Cam", "pixel_size_um": 3.0, "sensor_px": [100, 100],
               "filters": {"Ha": {"default_sub_s": 240}, "L": {}}}
        r = self.client.post("/api/gear", json={"telescopes": g["telescopes"], "cameras": g["cameras"] + [new]})
        self.assertEqual(r.status_code, 200)
        cam = next(c for c in read_gear()["cameras"] if c["id"] == "new-cam")
        self.assertEqual([t["name"] for t in cam["filters"]["Ha"]["templates"]], ["Ha (New Cam)"])
        self.assertEqual(cam["filters"]["Ha"]["templates"][0]["sub_s"], 240)
        self.assertEqual(cam["filters"]["L"]["templates"][0]["sub_s"], 300)
        self.assertTrue(cam["filters"]["Ha"]["templates"][0]["starred"])

    def test_g11_filter_names_with_a_space_save(self):
        self.seed_v3()

        def add(body):
            for name in ("Antlia Ha", "IDAS + RED"):
                camera(body)["filters"][name] = {"templates": [
                    {"name": f"{name} (6200)", "sub_s": 120, "gain": -1, "offset": -1, "bin": 1}]}
        r = self.post_gear(add)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertIn("Antlia Ha", camera(read_gear())["filters"])

        def bad(body):
            camera(body)["filters"][" Ha"] = {"templates": [
                {"name": "x", "sub_s": 120, "gain": -1, "offset": -1, "bin": 1}]}
        self.assertEqual(self.post_gear(bad).status_code, 400)

        def script(body):
            camera(body)["filters"]["<b>"] = {"templates": []}
        self.assertEqual(self.post_gear(script).status_code, 400)

    def test_g12_server_owned_fields_come_from_the_store(self):
        self.seed_v3(capable=True)

        def forge(body):
            t = tpl(body, CAM, "Ha", "Ha")
            t["ts"] = {"nope": {"id": 99}}
            t["aliases"] = ["forged"]
            t["ts_names"] = ["forged"]
            tpl(body, CAM, "L", "L")["sub_s"] = 121  # a real change, so the save is not a no-op
        r = self.post_gear(forge)
        self.assertEqual(r.status_code, 200, r.get_json())
        ha = tpl(read_gear(), CAM, "Ha", "Ha")
        self.assertEqual(ha["aliases"], [])
        self.assertEqual(ha["ts_names"], ["H"])
        self.assertEqual(list(ha["ts"]), [P1])

    def test_g16_a_rename_onto_a_name_a_linked_ts_template_holds(self):
        self.seed_v3()
        self.match(fingerprint(camera="QHY268M", profile_id=P2, profile_name="Bar"))
        app_module.set_links_capable(P2, ["templates", "repoint"])
        write_ts_list(P2, [{"id": 11, "name": "OIII", "filter": "O", "default_exposure_s": 300,
                            "gain": -1, "offset": -1, "bin": 1, "plans": []}])

        def rename(body):
            tpl(body, "qhy268m", "Ha", "Ha (QHY268M)")["name"] = "OIII"
        r = self.post_gear(rename)
        self.assertEqual(r.status_code, 400)
        err = r.get_json()["error"]
        self.assertIn(P2, err)
        self.assertIn("11", err)
        self.assertNotIn("two templates", err)

    def test_g21_name_only_fingerprint_trips_the_plugin_guard(self):
        self.seed_v3()
        # An older plugin: no profile_id, no wheel_slots, and no links_capable.
        self.match(fingerprint(camera="ZWO ASI2600MM Pro", profile_id=None, slots=[]))
        name = "Ha (ZWO ASI2600MM Pro)"

        def rename_2600(body):
            tpl(body, "zwo-asi2600mm-pro", "Ha", name)["name"] = "Ha 2600"
        r = self.post_gear(rename_2600)
        self.assertEqual(r.status_code, 400)
        self.assertIn("has not reported", r.get_json()["error"])
        self.assertTrue(camera(self.get_gear(), "zwo-asi2600mm-pro")["template_edit_blocked"])

        def rename_duo(body):
            tpl(body, "zwo-asi2600mm-duo", "Ha", "Ha (ZWO ASI2600MM Duo)")["name"] = "Ha duo"
        r = self.post_gear(rename_duo)
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_u14_the_editor_flag_clears_after_a_capable_post_for_another_profile(self):
        """The server half of U14: the flag the editor reads drops once a profile that reports the camera is capable."""
        self.seed_v3()
        self.match(fingerprint(camera="ZWO ASI2600MM Pro", profile_id=None, slots=[]))
        self.assertTrue(camera(self.get_gear(), "zwo-asi2600mm-pro")["template_edit_blocked"])
        self.match(fingerprint(camera="ZWO ASI2600MM Pro", profile_id=P2, profile_name="Bar"))
        self.assertTrue(camera(self.get_gear(), "zwo-asi2600mm-pro")["template_edit_blocked"])
        app_module.set_links_capable(P2, ["templates", "repoint"])
        self.assertFalse(camera(self.get_gear(), "zwo-asi2600mm-pro")["template_edit_blocked"])

    def test_g26_a_links_post_without_capabilities_clears_the_entry(self):
        self.seed_v3(capable=True)
        self.assertIn(P1, read_gear()["links_capable"])
        app_module.set_links_capable(P1, None)
        self.assertNotIn(P1, read_gear()["links_capable"])

        def rename(body):
            tpl(body, CAM, "L", "L")["name"] = "L 120"
        self.match(fingerprint())
        r = self.post_gear(rename)
        self.assertEqual(r.status_code, 400)
        self.assertIn("has not reported", r.get_json()["error"])

    def test_a_capable_entry_needs_both_capabilities(self):
        self.seed_v3()
        app_module.set_links_capable(P1, ["templates"])
        self.assertNotIn(P1, read_gear()["links_capable"])
        app_module.set_links_capable(P1, ["templates", "repoint"])
        self.assertIn(P1, read_gear()["links_capable"])
        rev = read_gear()["rev"]
        self.assertFalse(app_module.set_links_capable(P1, ["repoint", "templates"]))
        self.assertEqual(read_gear()["rev"], rev)

    def test_a_template_cannot_move_to_another_filter(self):
        self.seed_v3()

        def move(body):
            t = tpl(body, CAM, "Ha", "Ha")
            camera(body)["filters"]["Ha"]["templates"] = [
                {"name": "Ha new", "sub_s": 300, "gain": -1, "offset": -1, "bin": 1}]
            camera(body)["filters"]["OIII"]["templates"].append(t)
        r = self.post_gear(move)
        self.assertEqual(r.status_code, 400)
        self.assertIn("cannot move", r.get_json()["error"])

    def test_stars_are_repaired(self):
        self.seed_v3()

        def unstar(body):
            for t in camera(body)["filters"]["R"]["templates"]:
                t["starred"] = True
            for t in camera(body)["filters"]["G"]["templates"]:
                t["starred"] = False
        self.assertEqual(self.post_gear(unstar).status_code, 200)
        stored = read_gear()
        self.assertEqual([t["starred"] for t in camera(stored)["filters"]["R"]["templates"]], [True, False])
        self.assertEqual([t["starred"] for t in camera(stored)["filters"]["G"]["templates"]], [True, False])


class TestGearRoutes(Base):
    def test_g14_adding_a_template_with_an_existing_name_is_refused(self):
        self.seed_v3()
        body = {"camera_id": CAM, "filter": "Ha", "name": "Ha", "sub_s": 600, "gain": -1, "offset": -1, "bin": 1}
        self.assertEqual(self.client.post("/api/gear/templates", json=body).status_code, 400)
        body["name"] = "HA"
        self.assertEqual(self.client.post("/api/gear/templates", json=body).status_code, 400)

    def test_adding_a_template_bumps_rev_and_is_not_starred(self):
        self.seed_v3()
        body = {"camera_id": CAM, "filter": "Ha", "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": 25, "bin": 1}
        r = self.client.post("/api/gear/templates", json=body)
        self.assertEqual(r.status_code, 201, r.get_json())
        self.assertEqual(r.get_json()["rev"], 2)
        new = tpl(read_gear(), CAM, "Ha", "Ha 600")
        self.assertFalse(new["starred"])
        self.assertEqual(new["offset"], 25)
        self.assertEqual(new["ts"], {})
        self.assertEqual(self.get_gear()["cameras"][0]["filters"]["Ha"]["templates"][1]["status"], "new")

    def test_star_moves_the_star(self):
        self.seed_v3()
        stars = tpl(v3(), CAM, "R", "R Stars")["id"]
        r = self.client.post(f"/api/gear/templates/{stars}/star")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([t["starred"] for t in camera(read_gear())["filters"]["R"]["templates"]], [False, True])
        self.assertEqual(self.client.post("/api/gear/templates/nope/star").status_code, 404)

    def test_g18_seed_adds_a_camera_in_v3_shape(self):
        self.seed_v3()
        app_module.MANIFEST_PATH.write_text(json.dumps({"targets": [{
            "target_id": 1, "telescopes": ["RASA 11"], "cameras": ["QHY600M"],
            "per_master_fov": [{"telescope": "RASA 11", "camera": "QHY600M", "filter": "Ha",
                                "pix_arcsec": 1.0, "fov_arcmin": [60.0, 40.0], "pixel_size_um": 3.76,
                                "sensor_px": [9600, 6422], "focal_length_mm": 620.0, "aperture_mm": 279.0}],
        }]}), encoding="utf-8")
        before = read_gear()
        r = self.client.post("/api/gear/seed")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["added_cameras"], ["QHY600M"])
        after = read_gear()
        self.assertEqual(after["rev"], before["rev"] + 1)
        new = next(c for c in after["cameras"] if c["name"] == "QHY600M")
        tpls = new["filters"]["Ha"]["templates"]
        self.assertEqual(len(tpls), 1)
        self.assertTrue(tpls[0]["starred"])
        self.assertEqual(tpls[0]["name"], "Ha (QHY600M)")
        self.assertNotIn("default_sub_s", new["filters"]["Ha"])
        old = [c for c in after["cameras"] if c["name"] != "QHY600M"]
        self.assertEqual(old, before["cameras"])

    def test_g19_telescope_colour_changes_only_rev(self):
        self.seed_v3()
        before = read_gear()
        r = self.client.post("/api/gear/telescope-colour", json={"name": "110GTX", "colour": "#ff8800"})
        self.assertEqual(r.status_code, 200)
        after = read_gear()
        self.assertEqual(after["rev"], before["rev"] + 1)
        self.assertEqual(after["telescope_colours"], {"110GTX": "#ff8800"})
        self.assertEqual(after["cameras"], before["cameras"])
        # The answer carries the new rev, so the editor's next save is not a 409.
        self.assertEqual(r.get_json()["rev"], after["rev"])

    def conflicted(self, ts_sub=240):
        """Ha at 600 s in gear, 300 s at the last push, and edited to *ts_sub* in TS."""
        def edit(doc):
            t = tpl(doc, CAM, "Ha", "Ha")
            t["sub_s"] = 600
        doc = self.seed_v3(edit, capable=True)
        self.match(fingerprint())
        write_ts_list(P1, [{"id": 12, "guid": tpl(doc, CAM, "Ha", "Ha")["ts"][P1]["guid"], "name": "Ha",
                            "filter": "H", "default_exposure_s": ts_sub, "gain": -1, "offset": -1, "bin": 1,
                            "plans": []}])
        t = tpl(self.get_gear(), CAM, "Ha", "Ha")
        self.assertEqual(t["status"], "conflict")
        self.assertEqual(t["conflicts"], [{"profile_id": P1, "field": "sub_s", "acp": 600, "ts": 240}])
        self.assertEqual(self.get_gear()["conflict_count"], 1)
        return doc, t["id"]

    def test_g23_take_ts_adopts_the_value_and_carries_the_sub(self):
        doc, tid = self.conflicted()
        write_plans([plan("p1", [row("r1", "Ha", "Ha", tid, 600, target_hours=3)]),
                     plan("p2", [row("r1", "Ha", "Ha", tid, 180, target_hours=3)])])
        r = self.client.post(f"/api/gear/templates/{tid}/resolve",
                             json={"profile_id": P1, "field": "sub_s", "take": "ts"})
        self.assertEqual(r.status_code, 200, r.get_json())
        t = tpl(read_gear(), CAM, "Ha", "Ha")
        self.assertEqual(t["sub_s"], 240)
        self.assertEqual(t["ts"][P1]["base"]["defaultexposure"], 240)
        subs = {p["id"]: p["filter_rows"][0]["template_sub_s"] for p in read_plans()}
        self.assertEqual(subs, {"p1": 240, "p2": 180})
        self.assertEqual(tpl(self.get_gear(), CAM, "Ha", "Ha")["status"], "in_ts")

    def test_g24_take_acp_moves_the_base_so_the_push_writes(self):
        doc, tid = self.conflicted()
        r = self.client.post(f"/api/gear/templates/{tid}/resolve",
                             json={"profile_id": P1, "field": "sub_s", "take": "acp"})
        self.assertEqual(r.status_code, 200, r.get_json())
        t = tpl(read_gear(), CAM, "Ha", "Ha")
        self.assertEqual(t["sub_s"], 600)
        self.assertEqual(t["ts"][P1]["base"]["defaultexposure"], 240)
        self.assertEqual(tpl(self.get_gear(), CAM, "Ha", "Ha")["status"], "changed")

    def test_resolve_refuses_bad_input(self):
        doc, tid = self.conflicted()
        url = f"/api/gear/templates/{tid}/resolve"
        for body in ({"profile_id": P1, "field": "sub_s", "take": "both"},
                     {"profile_id": P1, "field": "starred", "take": "ts"},
                     {"field": "sub_s", "take": "ts"}):
            self.assertEqual(self.client.post(url, json=body).status_code, 400)
        self.assertEqual(self.client.post(url, json={"profile_id": "other", "field": "sub_s", "take": "ts"}
                                          ).status_code, 404)

    def test_w8_nina_names_link_an_unlinked_camera(self):
        self.seed_v3()
        self.match(fingerprint(camera="ASI Camera (1)", profile_id=P2, profile_name="Bar"))
        g = self.get_gear()
        self.assertEqual(g["wheel_unlinked"], [
            {"profile_id": P2, "profile_name": "Bar", "camera_name": "ASI Camera (1)"}])
        r = self.client.post("/api/gear/cameras/asi-camera-1-as/nina-names", json={"name": "ASI Camera (1)"})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["rev"], g["rev"] + 1)
        self.assertEqual(camera(read_gear(), "asi-camera-1-as")["nina_names"], ["ASI Camera (1)"])
        g2 = self.get_gear()
        self.assertEqual(g2["wheel_unlinked"], [])
        self.assertEqual(camera(g2, "asi-camera-1-as")["wheel"]["profiles"][0]["profile_id"], P2)
        self.assertEqual(self.client.post("/api/gear/cameras/nope/nina-names", json={"name": "x"}).status_code, 404)


class TestRecordTemplateResults(Base):
    def result(self, tid, action="updated", ts_id=12, **base):
        b = {"name": "Ha", "filtername": "H", "defaultexposure": 300.0, "gain": -1, "offset": -1, "bin": 1}
        b.update(base)
        return {"template_id": tid, "ts_id": ts_id, "guid": "g-" + str(ts_id), "base": b,
                "action": action, "fields_written": [], "conflicts": [], "note": None}

    def test_an_update_stores_the_link_and_a_repeat_changes_nothing(self):
        doc = self.seed_v3()
        tid = tpl(doc, CAM, "Ha", "Ha")["id"]
        res = self.result(tid, offset=25)
        self.assertEqual(app_module.record_template_results(P1, [res]), 1)
        link = tpl(read_gear(), CAM, "Ha", "Ha")["ts"][P1]
        self.assertEqual(link["base"]["offset"], 25)
        self.assertEqual(link["guid"], "g-12")
        rev = read_gear()["rev"]
        self.assertEqual(app_module.record_template_results(P1, [res]), 0)
        self.assertEqual(read_gear()["rev"], rev)

    def test_a_zero_in_the_base_stays_zero(self):
        doc = self.seed_v3()
        tid = tpl(doc, CAM, "Ha", "Ha")["id"]
        app_module.record_template_results(P1, [self.result(tid, gain=0, offset=0)])
        base = tpl(read_gear(), CAM, "Ha", "Ha")["ts"][P1]["base"]
        self.assertEqual((base["gain"], base["offset"]), (0, 0))

    def test_skipped_and_unknown_templates_record_nothing(self):
        doc = self.seed_v3()
        tid = tpl(doc, CAM, "Ha", "Ha")["id"]
        rev = read_gear()["rev"]
        self.assertEqual(app_module.record_template_results(
            P1, [self.result(tid, action="skipped"), self.result("nope"), {"junk": 1}, "x"]), 0)
        self.assertEqual(read_gear()["rev"], rev)

    def test_adopted_takes_ts_values_for_the_primary_profile_only(self):
        doc = self.seed_v3()
        self.match(fingerprint(camera="QHY268M", profile_id=P2, profile_name="Bar"))
        tid = tpl(doc, "qhy268m", "Ha", "Ha (QHY268M)")["id"]
        # Another profile reports the camera later, so it becomes the primary.
        self.match(fingerprint(camera="QHY268M", profile_id=P1, profile_name="Foo"))
        res = self.result(tid, action="adopted", ts_id=30, name="Ha TS", defaultexposure=240.0, offset=10)
        app_module.record_template_results(P2, [res])
        t = tpl(read_gear(), "qhy268m", "Ha", "Ha (QHY268M)")
        self.assertEqual(t["sub_s"], 300)
        self.assertEqual(t["offset"], -1)
        self.assertEqual(t["ts"][P2]["base"]["defaultexposure"], 300)
        self.assertEqual(t["ts"][P2]["base"]["offset"], -1)
        self.assertEqual(t["ts"][P2]["base"]["filtername"], "H")
        app_module.record_template_results(P1, [self.result(tid, action="adopted", ts_id=31,
                                                            name="Ha TS", defaultexposure=240.0, offset=10)])
        t = tpl(read_gear(), "qhy268m", "Ha", "Ha TS")
        self.assertEqual((t["sub_s"], t["offset"]), (240.0, 10))
        self.assertEqual(t["ts"][P1]["base"]["offset"], 10)

    def test_repoint_from_survives_and_is_cleared_on_request(self):
        doc = self.seed_v3(lambda d: tpl(d, CAM, "Ha", "Ha")["ts"][P1].update(repoint_from=[8]))
        tid = tpl(doc, CAM, "Ha", "Ha")["id"]
        app_module.record_template_results(P1, [self.result(tid, offset=3)])
        self.assertEqual(tpl(read_gear(), CAM, "Ha", "Ha")["ts"][P1]["repoint_from"], [8])
        self.assertTrue(app_module.clear_repoint(P1))
        self.assertEqual(tpl(read_gear(), CAM, "Ha", "Ha")["ts"][P1]["repoint_from"], [])


# ---------------------------------------------------------------------------
# F, W: fingerprints and the wheel
# ---------------------------------------------------------------------------


class TestFingerprintFields(Base):
    def test_f1_wheel_slots_and_profile_id_are_stored_under_the_profile_id(self):
        self.seed_v3()
        self.match(fingerprint())
        profiles = self.client.get("/api/fingerprints").get_json()["profiles"]
        self.assertEqual(list(profiles), [P1])
        fp = profiles[P1]["fingerprint"]
        self.assertEqual(fp["filters"], ["B", "G", "Ha", "L", "OIII", "R", "SII"])
        self.assertEqual(fp["wheel_slots"], SLOTS)
        self.assertEqual(fp["profile_id"], P1)
        self.assertEqual(profiles[P1]["profile_id"], P1)
        self.assertEqual(profiles[P1]["profile_name"], "Voyager")

    def test_f2_bad_wheel_slots_and_profile_ids_are_refused(self):
        self.seed_v3()
        self.match(fingerprint())
        for bad in ({"wheel_slots": ["x"] * 65}, {"wheel_slots": ["y" * 40]}, {"wheel_slots": "L"},
                    {"wheel_slots": [1]}, {"profile_id": "p" * 65}, {"profile_id": 7}):
            with self.subTest(bad=bad):
                r = self.client.post("/api/plans/match", json={**fingerprint(), **bad})
                self.assertEqual(r.status_code, 400)

    def test_f3_a_report_without_an_id_is_kept_apart_and_never_linked(self):
        self.seed_v3()
        self.match(fingerprint())
        before = self.client.get("/api/fingerprints").get_json()["profiles"][P1]
        self.match(fingerprint(profile_id=None, slots=["Z"]))
        profiles = self.client.get("/api/fingerprints").get_json()["profiles"]
        self.assertEqual(set(profiles), {P1, "Voyager"})
        self.assertEqual(profiles[P1], before)
        wheel = camera(self.get_gear())["wheel"]
        self.assertEqual([p["profile_id"] for p in wheel["profiles"]], [P1])
        self.assertEqual(wheel["filters"]["Ha"]["slots"], {P1: "H"})

    def test_f4_an_id_replaces_the_entry_an_older_plugin_left_under_the_name(self):
        self.seed_v3()
        self.match(fingerprint(profile_id=None, slots=[]))
        self.assertEqual(set(self.client.get("/api/fingerprints").get_json()["profiles"]), {"Voyager"})
        self.match(fingerprint())
        profiles = self.client.get("/api/fingerprints").get_json()["profiles"]
        self.assertEqual(set(profiles), {P1})

    def test_the_fingerprint_id_ignores_the_new_fields(self):
        self.seed_v3()
        a = self.match(fingerprint(profile_id=None, slots=[], filters=SLOTS)).get_json()["fingerprint_id"]
        b = self.match(fingerprint()).get_json()["fingerprint_id"]
        self.assertEqual(a, b)


class TestWheelBlock(Base):
    def test_w4_filters_on_and_off_the_wheel(self):
        def add_v(doc):
            cam = next(c for c in doc["cameras"] if c["id"] == CAM)
            cam["filters"]["V"] = {"templates": [{"id": "v1", "name": "V", "sub_s": 120, "gain": -1,
                                                  "offset": -1, "bin": 1, "starred": True,
                                                  "aliases": [], "ts_names": [], "ts": {}}]}
        self.seed_v3(add_v)
        self.match(fingerprint())
        wheel = camera(self.get_gear())["wheel"]
        self.assertEqual(wheel["filters"]["Ha"], {"state": "on_wheel", "slots": {P1: "H"}})
        self.assertEqual(wheel["filters"]["V"]["state"], "not_on_wheel")
        self.assertEqual(wheel["filters"]["L"]["slots"], {P1: "L"})
        self.assertEqual(wheel["profiles"][0]["slots"], SLOTS)
        self.assertTrue(wheel["profiles"][0]["primary"])
        v = tpl(self.get_gear(), CAM, "V", "V")
        self.assertEqual(v["status"], "waiting")

    def test_w5_two_profiles_two_spellings(self):
        self.seed_v3()
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))
        self.match(fingerprint(profile_id=P2, profile_name="Bar", slots=["L", "R", "G", "B", "Ha", "OIII", "SII"]))
        ha = camera(self.get_gear())["wheel"]["filters"]["Ha"]
        self.assertEqual(ha["state"], "on_wheel")
        self.assertEqual(ha["slots"], {P1: "H", P2: "Ha"})

    def test_w6_a_slot_one_profile_lacks_is_partial(self):
        self.seed_v3()
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))
        self.match(fingerprint(profile_id=P2, profile_name="Bar", slots=["L", "R", "G", "B", "H", "O"]))
        self.assertEqual(camera(self.get_gear())["wheel"]["filters"]["SII"]["state"], "partial")

    def test_w7_no_fingerprint_means_unknown_and_nothing_waits(self):
        self.seed_v3()
        g = self.get_gear()
        cam = camera(g)
        self.assertEqual({v["state"] for v in cam["wheel"]["filters"].values()}, {"unknown"})
        self.assertEqual(cam["wheel"]["profiles"], [])
        statuses = {t["status"] for c in g["cameras"] for f in c["filters"].values() for t in f["templates"]}
        self.assertNotIn("waiting", statuses)

    def test_w9_a_fingerprint_matching_two_cameras_links_to_neither(self):
        def twin(doc):
            clone = copy.deepcopy(next(c for c in doc["cameras"] if c["id"] == "zwo-asi2600mm-pro"))
            clone["id"] = "asi2600mm-pro"
            clone["name"] = "ASI2600MM Pro"
            clone["filters"] = {}
            doc["cameras"].append(clone)
        self.seed_v3(twin)
        self.match(fingerprint(camera="ASI2600MM Pro", profile_id=P2, profile_name="Bar"))
        g = self.get_gear()
        self.assertEqual(g["wheel_ambiguous"], [{
            "profile_id": P2, "profile_name": "Bar", "camera_name": "ASI2600MM Pro",
            "camera_ids": ["zwo-asi2600mm-pro", "asi2600mm-pro"]}])
        self.assertEqual(camera(g, "zwo-asi2600mm-pro")["wheel"]["profiles"], [])
        self.assertEqual(camera(g, "asi2600mm-pro")["wheel"]["profiles"], [])

    def test_w10_a_filter_off_the_primary_wheel_waits_and_names_the_other(self):
        self.seed_v3()
        # Bar reports first and has the S slot; Foo reports later (the primary) and lacks it.
        self.match(fingerprint(profile_id=P2, profile_name="Bar"))
        self.match(fingerprint(profile_id=P1, profile_name="Foo", slots=["L", "R", "G", "B", "H", "O"]))
        g = self.get_gear()
        self.assertEqual(camera(g)["wheel"]["filters"]["SII"]["state"], "partial")
        sii = tpl(g, CAM, "SII", "SII")
        self.assertEqual(sii["status"], "waiting")
        self.assertIn("Bar", sii["note"])
        self.assertIn("Foo", sii["note"])

    def test_w11_the_newest_report_is_primary(self):
        self.seed_v3()
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))
        self.match(fingerprint(profile_id=P2, profile_name="Bar"))
        profiles = camera(self.get_gear())["wheel"]["profiles"]
        self.assertEqual([(p["profile_id"], p["primary"]) for p in profiles], [(P2, True), (P1, False)])
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))
        profiles = camera(self.get_gear())["wheel"]["profiles"]
        self.assertEqual([(p["profile_id"], p["primary"]) for p in profiles], [(P1, True), (P2, False)])


class TestStatuses(Base):
    def test_in_ts_new_and_changed(self):
        doc = self.seed_v3()
        self.match(fingerprint())
        g = self.get_gear()
        self.assertEqual(tpl(g, CAM, "Ha", "Ha")["status"], "in_ts")
        # A template with no link is new.
        self.assertEqual(tpl(g, "qhy268m", "Ha", "Ha (QHY268M)")["status"], "new")
        # An ACP-side edit against the base is changed.
        doc2 = read_gear()
        tpl(doc2, CAM, "Ha", "Ha")["offset"] = 25
        write_gear(doc2)
        self.assertEqual(tpl(self.get_gear(), CAM, "Ha", "Ha")["status"], "changed")

    def test_a_link_whose_ts_row_is_gone_is_new(self):
        self.seed_v3()
        self.match(fingerprint())
        write_ts_list(P1, [{"id": 99, "name": "Other", "filter": "L", "default_exposure_s": 1,
                            "gain": -1, "offset": -1, "bin": 1, "plans": []}])
        self.assertEqual(tpl(self.get_gear(), CAM, "Ha", "Ha")["status"], "new")


class TestTsTemplateList(Base):
    def body(self, **extra):
        t = {"id": 12, "guid": "g", "name": "Ha", "filter": "H", "default_exposure_s": 300, "gain": -1,
             "offset": -1, "bin": 1, "moonavoidanceenabled": 1, "moonavoidanceseparation": 120.0,
             "moonavoidancewidth": 14, "twilightlevel": 0, "maximumhumidity": 100.0, "ditherevery": 2,
             "plans": [{"id": 1, "exposure": -1}, {"id": 2, "exposure": 15.0}]}
        t.update(extra)
        return {"profile_id": P1, "sent_at": "2026-10-13T00:00:00Z", "templates": [t]}

    def test_the_new_fields_are_kept(self):
        r = self.client.post("/api/ts-templates", json=self.body())
        self.assertEqual(r.status_code, 200, r.get_json())
        stored = json.loads(app_module.TS_TEMPLATES_PATH.read_text(encoding="utf-8"))["profiles"][P1]["templates"][0]
        self.assertEqual(stored["id"], 12)
        self.assertEqual(stored["ditherevery"], 2)
        self.assertEqual(stored["plans"], [{"id": 1, "exposure": -1}, {"id": 2, "exposure": 15.0}])

    def test_an_older_list_keeps_its_shape(self):
        body = {"profile_id": P1, "templates": [{"name": "Ha", "filter": "H", "default_exposure_s": 300}]}
        self.assertEqual(self.client.post("/api/ts-templates", json=body).status_code, 200)
        stored = json.loads(app_module.TS_TEMPLATES_PATH.read_text(encoding="utf-8"))["profiles"][P1]["templates"][0]
        self.assertNotIn("id", stored)
        self.assertNotIn("plans", stored)

    def test_bad_values_are_refused(self):
        for extra in ({"id": "12"}, {"plans": "x"}, {"plans": [{"id": 1}]}, {"ditherevery": "2"},
                      {"plans": [{"id": 1, "exposure": -1}] * 5001}, {"guid": 5}):
            with self.subTest(extra=list(extra)):
                self.assertEqual(self.client.post("/api/ts-templates", json=self.body(**extra)).status_code, 400)


# ---------------------------------------------------------------------------
# M: the migration
# ---------------------------------------------------------------------------


class TestMigration(Base):
    def backups(self):
        return sorted(self.dir.glob("gear.json.bak-*"))

    def test_m1_the_live_file_migrates_once_with_a_backup(self):
        write_gear(v2_live())
        original = app_module.GEAR_PATH.read_bytes()
        g = self.get_gear()
        self.assertEqual(g["version"], 3)
        self.assertEqual(g["rev"], 1)
        stored = read_gear()
        self.assertEqual((stored["version"], stored["rev"], stored["templates_migrated"]), (3, 1, 1))
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_bytes(), original)
        cam = camera(stored)
        names = {f: [t["name"] for t in v["templates"]] for f, v in cam["filters"].items()}
        self.assertEqual(names, {
            "B": ["B"], "G": ["G (ZWO ASI6200MM Pro)"], "Ha": ["Ha"], "L": ["L"], "OIII": ["OIII"],
            "R": ["R (ZWO ASI6200MM Pro)"], "SII": ["SII"]})
        subs = {f: v["templates"][0]["sub_s"] for f, v in cam["filters"].items()}
        self.assertEqual(subs, {"B": 120, "G": 120, "Ha": 300, "L": 120, "OIII": 300, "R": 120, "SII": 300})
        for v in cam["filters"].values():
            (t,) = v["templates"]
            self.assertTrue(t["starred"])
            self.assertEqual((t["aliases"], t["ts_names"], t["ts"]), ([], [], {}))
        self.assertNotIn("ts_template_id", json.dumps(stored))
        # A second load changes nothing.
        text = app_module.GEAR_PATH.read_text(encoding="utf-8")
        app_module._gear_cache = None
        app_module._gear_cache_mtime = None
        self.get_gear()
        self.assertEqual(app_module.GEAR_PATH.read_text(encoding="utf-8"), text)
        self.assertEqual(len(self.backups()), 1)
        # Every other camera and telescope came through.
        self.assertEqual([c["id"] for c in stored["cameras"]], [c["id"] for c in v2_live()["cameras"]])
        self.assertEqual(stored["telescopes"], v2_live()["telescopes"])

    def test_m2_no_backup_no_migration(self):
        write_gear(v2_live())
        original = app_module.GEAR_PATH.read_bytes()
        with mock.patch.object(app_module, "_backup_gear_json", return_value=False):
            r = self.client.get("/api/gear")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["version"], 2)
        self.assertEqual(camera(body)["filters"]["Ha"]["default_sub_s"], 300)
        self.assertEqual(app_module.GEAR_PATH.read_bytes(), original)

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs a directory the process cannot write to")
    def test_m2_an_unwritable_directory(self):
        write_gear(v2_live())
        original = app_module.GEAR_PATH.read_bytes()
        os.chmod(self.dir, stat.S_IRUSR | stat.S_IXUSR)
        try:
            r = self.client.get("/api/gear")
            posted = self.client.post("/api/gear", json={"gear_schema": 3, "rev": 0, "telescopes": [], "cameras": []})
        finally:
            os.chmod(self.dir, stat.S_IRWXU)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["version"], 2)
        self.assertEqual(posted.status_code, 503)
        self.assertEqual(app_module.GEAR_PATH.read_bytes(), original)

    def test_g17_two_threads_migrate_once(self):
        write_gear(v2_live())
        seen = []
        barrier = threading.Barrier(2)

        def go():
            barrier.wait()
            seen.append(json.dumps(app_module._load_gear_stored(), sort_keys=True))

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(read_gear()["rev"], 1)
        self.assertEqual(len(self.backups()), 1)

    def test_m7_a_camera_with_no_filters(self):
        write_gear(v2_live())
        cam = camera(self.get_gear(), "asi-camera-1-as")
        self.assertEqual(cam["filters"], {})
        self.assertEqual(camera(read_gear(), "asi-camera-1-as")["filters"], {})

    def test_m8_a_gain_of_zero_survives(self):
        doc = v2_live()
        camera(doc)["filters"]["Ha"].update(gain=0, offset=0)
        write_gear(doc)
        self.get_gear()
        t = tpl(read_gear(), CAM, "Ha", "Ha")
        self.assertEqual((t["gain"], t["offset"]), (0, 0))

    def test_two_filters_pinned_to_one_name_get_distinct_names(self):
        doc = v2_live()
        camera(doc)["filters"]["SII"]["ts_template_name"] = "Ha"
        write_gear(doc)
        self.get_gear()
        names = [t["name"] for _c, _f, t in
                 ((c, f, t) for c in read_gear()["cameras"] if c["id"] == CAM
                  for f, v in c["filters"].items() for t in v["templates"])]
        self.assertEqual(len(names), len({n.lower() for n in names}))

    def test_a_missing_file_is_not_created_by_a_read(self):
        self.assertEqual(self.client.get("/api/gear").status_code, 200)
        self.assertFalse(app_module.GEAR_PATH.exists())

    def test_a_fresh_install_saves_through_the_new_shape(self):
        r = self.client.post("/api/gear", json={"gear_schema": 3, "rev": 0, "telescopes": [{"id": "t", "name": "T"}],
                                                "cameras": [{"id": "c", "name": "C", "filters": {"Ha": {"templates": [
                                                    {"name": "Ha", "sub_s": 300, "gain": -1, "offset": -1, "bin": 1}]}}}]})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(read_gear()["rev"], 1)
        self.assertTrue(read_gear()["cameras"][0]["filters"]["Ha"]["templates"][0]["starred"])


# ---------------------------------------------------------------------------
# G13, G15, G25, M6, M9, P4, P5: plan rows
# ---------------------------------------------------------------------------


class TestPlanRows(Base):
    def test_g13_a_template_id_of_another_camera_is_refused(self):
        doc = self.seed_v3()
        other = tpl(doc, "qhy268m", "Ha", "Ha (QHY268M)")["id"]
        write_plans([plan("p1", [])])
        body = plan("p1", [row("r1", "Ha", "Ha (QHY268M)", other, 300)])
        r = self.client.put("/api/plans/p1", json=body)
        self.assertEqual(r.status_code, 400)
        self.assertIn("template_id", r.get_json()["error"])
        r = self.client.post("/api/plans", json=body)
        self.assertEqual(r.status_code, 400)

    def test_a_template_id_with_a_different_name_is_refused(self):
        doc = self.seed_v3()
        write_plans([plan("p1", [])])
        ha = tpl(doc, CAM, "Ha", "Ha")["id"]
        r = self.client.put("/api/plans/p1", json=plan("p1", [row("r1", "Ha", "Old name", ha, 300)]))
        self.assertEqual(r.status_code, 400)

    def test_g15_rows_get_their_template_id_on_load_and_on_save(self):
        doc = self.seed_v3()
        ha, oiii = tpl(doc, CAM, "Ha", "Ha"), tpl(doc, CAM, "OIII", "OIII")
        write_plans([plan("p1", [row("r1", "Ha", "Ha", None, 300), row("r2", "OIII", "O", None, 300)])])
        loaded = app_module.load_plans()["plans"][0]["filter_rows"]
        self.assertEqual(loaded[0]["template_id"], ha["id"])
        self.assertEqual(loaded[1]["template_id"], oiii["id"])
        self.assertEqual(loaded[1]["template"], "OIII")
        stored = read_plans()[0]["filter_rows"]
        self.assertEqual(stored[0]["template_id"], ha["id"])
        r = self.client.put("/api/plans/p1", json=plan("p1", [row("r1", "Ha", "Ha", None, 300),
                                                              row("r2", "OIII", "O", None, 300)]))
        self.assertEqual(r.status_code, 200, r.get_json())
        out = r.get_json()["filter_rows"]
        self.assertEqual([x["template_id"] for x in out], [ha["id"], oiii["id"]])
        self.assertEqual(out[1]["template"], "OIII")

    def _plan_backups(self):
        return sorted(app_module.PLANS_PATH.parent.glob("plans.json.bak-*"))

    def test_a_row_rewrite_takes_a_plans_backup_with_the_old_rows(self):
        doc = self.seed_v3()
        ha = tpl(doc, CAM, "Ha", "Ha")
        write_plans([plan("p1", [row("r1", "Ha", "Ha", None, 300)])])
        app_module.load_plans()
        self.assertEqual(read_plans()[0]["filter_rows"][0]["template_id"], ha["id"])
        backups = self._plan_backups()
        self.assertTrue(backups)
        held = [json.loads(b.read_text(encoding="utf-8")) for b in backups]
        self.assertTrue(all("template_id" not in d["plans"][0]["filter_rows"][0] for d in held))

    def test_a_load_with_nothing_to_rewrite_writes_no_plans_backup(self):
        doc = self.seed_v3()
        write_plans([plan("p1", [row("r1", "Ha", "Ha", tpl(doc, CAM, "Ha", "Ha")["id"], 300)])])
        app_module.load_plans()
        before = self._plan_backups()
        app_module._plans_cache = None
        app_module._plans_cache_mtime = None
        app_module.load_plans()
        self.assertEqual(self._plan_backups(), before)
        self.assertEqual(app_module.repair_plan_templates(), False)
        self.assertEqual(self._plan_backups(), before)

    def test_g25_a_row_nothing_matches_is_flagged_not_guessed(self):
        self.seed_v3()
        write_plans([plan("p1", [row("r1", "R", "Q Stars", None, 15)])])
        with self.assertLogs(level="WARNING") as logs:
            rw = app_module.load_plans()["plans"][0]["filter_rows"][0]
        self.assertTrue(rw["template_unresolved"])
        self.assertEqual(rw["template"], "Q Stars")
        self.assertNotIn("template_id", rw)
        self.assertTrue(any("p1" in line and "Q Stars" in line for line in logs.output))
        # Choosing a template clears the flag.
        stars = tpl(v3(), CAM, "R", "R Stars")["id"]
        body = plan("p1", [row("r1", "R", "R Stars", stars, 15, template_unresolved=True)])
        r = self.client.put("/api/plans/p1", json=body)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertNotIn("template_unresolved", r.get_json()["filter_rows"][0])

    def test_m6_a_row_naming_a_ts_name_gets_the_gear_templates_name(self):
        doc = self.seed_v3()
        stars = tpl(doc, CAM, "R", "R Stars")
        write_plans([plan("p1", [row("r1", "R", "R Stars (ZWO ASI6200MM Pro)", None, 15)])])
        rw = app_module.load_plans()["plans"][0]["filter_rows"][0]
        self.assertEqual(rw["template_id"], stars["id"])
        self.assertEqual(rw["template"], "R Stars")

    def test_m9_a_row_on_a_filter_with_no_template(self):
        write_gear(v2_live())
        write_plans([plan("p-orphan", [row("r1", "Ha", "Ha", None, 300)], camera_id="asi-camera-1-as")])
        with self.assertLogs(level="WARNING") as logs:
            rw = app_module.load_plans()["plans"][0]["filter_rows"][0]
        self.assertTrue(rw["template_unresolved"])
        self.assertNotIn("template_id", rw)
        self.assertTrue(any("p-orphan" in line for line in logs.output))

    def test_p4_progress_finds_a_renamed_row_through_the_alias(self):
        def renamed(doc):
            t = tpl(doc, CAM, "Ha", "Ha")
            t["name"], t["aliases"] = "Ha 300", ["Ha"]
        doc = self.seed_v3(renamed)
        ha = tpl(doc, CAM, "Ha", "Ha 300")["id"]
        write_plans([plan("p1", [row("r1", "Ha", "Ha 300", ha, 300)])])
        r = self.client.post("/api/plans/p1/progress", json={
            "entries": [{"filter": "Ha", "template": "Ha", "acquired_hours": 1.5}]})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["unknown_filters"], [])
        self.assertEqual(read_plans()[0]["filter_rows"][0]["actual_hours"], 1.5)

    def test_p5_accepted_finds_a_renamed_row_through_the_alias(self):
        def renamed(doc):
            t = tpl(doc, CAM, "Ha", "Ha")
            t["name"], t["aliases"] = "Ha 300", ["Ha"]
        doc = self.seed_v3(renamed)
        ha = tpl(doc, CAM, "Ha", "Ha 300")["id"]
        write_plans([plan("p1", [row("r1", "Ha", "Ha 300", ha, 300)])])
        r = self.client.post("/api/plans/p1/accepted", json={
            "filed_through_night": "2026-10-12", "min_total_frames": 0,
            "rows": [{"id": "r1", "filter": "Ha", "template": "Ha", "accepted_seconds": 3600,
                      "seen_seconds": 3600, "accepted_frames": 12, "seen_frames": 12, "complete": False}]})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(r.get_json()["unknown_rows"], [])
        self.assertEqual(read_plans()[0]["filter_rows"][0]["aac"]["accepted_frames"], 12)

    def test_a_name_that_is_not_an_alias_is_still_unknown(self):
        doc = self.seed_v3()
        ha = tpl(doc, CAM, "Ha", "Ha")["id"]
        write_plans([plan("p1", [row("r1", "Ha", "Ha", ha, 300)])])
        r = self.client.post("/api/plans/p1/progress", json={
            "entries": [{"filter": "Ha", "template": "Nope", "acquired_hours": 1.5}]})
        self.assertEqual(r.get_json()["unknown_filters"], ["Ha (Nope)"])


if __name__ == "__main__":
    unittest.main()
