"""Adoption of the TS template list and the pull, in ACP's POST
/api/ts-templates (docs/specs/gear-templates.md sections 8 and 11): S28 and
M3 to M5, M10 to M12. The 20 templates are IMAGING-PC's, as the plugin's
ts-templates-20.sql builds them."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from test_gear_templates import (CAM, P1, P2, SLOTS, Base, camera, fingerprint, plan, read_gear,  # noqa: E402
                                 read_plans, row, tpl, v2_live, write_gear, write_plans)

# (id, name, filtername, sub, offset, plans, moon)
_T = [
    (1, "L", "L", 180, 25, 1), (2, "R", "R", 180, 25, 1), (3, "G", "G", 180, -1, 1), (4, "B", "B", 180, 25, 1),
    (5, "R Stars", "R", 15, 25, 1), (6, "G Stars", "G", 15, 25, 1), (7, "B Stars", "B", 15, 25, 1),
    (8, "H", "H", 300, 25, 1), (9, "O", "O", 300, 25, 0), (10, "S", "S", 300, 25, 0),
    (11, "OIII", "O", 300, -1, 55), (12, "Ha", "H", 300, -1, 23), (13, "SII", "S", 300, -1, 3),
    (14, "B", "B", 120, -1, 26), (15, "B Stars (ZWO ASI6200MM Pro)", "B Stars", 15, 25, 22),
    (16, "G (ZWO ASI6200MM Pro)", "G", 120, -1, 26), (17, "G Stars (ZWO ASI6200MM Pro)", "G Stars", 15, 25, 22),
    (18, "L", "L", 120, -1, 11), (19, "R (ZWO ASI6200MM Pro)", "R", 120, -1, 26),
    (20, "R Stars (ZWO ASI6200MM Pro)", "R Stars", 15, 25, 22),
]


def ts20(edits: dict | None = None) -> list[dict]:
    edits = edits or {}
    out = []
    for i, name, flt, sub, off, n in _T:
        t = {"id": i, "guid": f"guid-{i}", "name": name, "filter": flt, "default_exposure_s": float(sub),
             "gain": -1, "offset": off, "bin": 1, "moonavoidanceenabled": 0, "moonavoidanceseparation": 0.0,
             "moonavoidancewidth": 0, "twilightlevel": 0, "maximumhumidity": 0.0, "ditherevery": -1,
             "plans": [{"id": 1000 * i + k, "exposure": -1} for k in range(n)]}
        t.update(edits.get(i, {}))
        out.append(t)
    return out


class AdoptionBase(Base):
    def setUp(self):
        super().setUp()
        write_gear(v2_live())
        self.get_gear()  # migrate
        app_module.set_links_capable(P1, ["templates", "repoint"])

    def post_list(self, pid=P1, templates=None):
        r = self.client.post("/api/ts-templates", json={"profile_id": pid, "templates":
                                                        ts20() if templates is None else templates})
        self.assertEqual(r.status_code, 200, r.get_json())
        return r.get_json()

    def summary(self):
        cam = camera(read_gear())
        return {t["name"]: (f, t["sub_s"], t["offset"], t["ts"][P1]["id"], t["ts_names"])
                for f, v in cam["filters"].items() for t in v["templates"] if P1 in t["ts"]}


EXPECTED = {
    "L": ("L", 120, -1, 18, []),
    "R (ZWO ASI6200MM Pro)": ("R", 120, -1, 19, ["R"]),
    "R Stars": ("R", 15, 25, 5, ["R Stars (ZWO ASI6200MM Pro)"]),
    "G (ZWO ASI6200MM Pro)": ("G", 120, -1, 16, ["G"]),
    "G Stars": ("G", 15, 25, 6, ["G Stars (ZWO ASI6200MM Pro)"]),
    "B": ("B", 120, -1, 14, []),
    "B Stars": ("B", 15, 25, 7, ["B Stars (ZWO ASI6200MM Pro)"]),
    "Ha": ("Ha", 300, -1, 12, ["H"]),
    "OIII": ("OIII", 300, -1, 11, ["O"]),
    "SII": ("SII", 300, -1, 13, ["S"]),
}


class TestAdoption(AdoptionBase):
    def test_m3_the_list_and_the_fingerprint_give_ten_linked_templates(self):
        write_plans([plan("p1", [row("a", "B", "B Stars (ZWO ASI6200MM Pro)", None, 15),
                                 row("b", "R", "R", None, 120), row("c", "Ha", "H", None, 300)])])
        app_module.load_plans()  # the rows are unresolved until adoption
        self.assertTrue(read_plans()[0]["filter_rows"][0]["template_unresolved"])
        self.post_list()
        self.match(fingerprint())
        self.assertEqual(self.summary(), EXPECTED)
        stored = read_gear()
        ts = {t["name"]: t["ts"][P1] for _c, f, t in
              ((c, f, t) for c in stored["cameras"] if c["id"] == CAM
               for f, v in c["filters"].items() for t in v["templates"])}
        rows_by_id = {r["id"]: r for r in ts20()}
        for name, link in ts.items():
            ts_row = rows_by_id[link["id"]]
            self.assertEqual(link["base"], {"name": ts_row["name"], "filtername": ts_row["filter"],
                                            "defaultexposure": ts_row["default_exposure_s"],
                                            "gain": ts_row["gain"], "offset": ts_row["offset"],
                                            "bin": ts_row["bin"]})
            self.assertEqual(link["guid"], ts_row["guid"])
        self.assertEqual(len(camera(stored)["filters"]["R"]["templates"]), 2)
        self.assertTrue(tpl(stored, CAM, "R", "R (ZWO ASI6200MM Pro)")["starred"])
        self.assertFalse(tpl(stored, CAM, "R", "R Stars")["starred"])
        rows = {r["id"]: r for r in read_plans()[0]["filter_rows"]}
        self.assertEqual(rows["a"]["template_id"], tpl(stored, CAM, "B", "B Stars")["id"])
        self.assertEqual(rows["a"]["template"], "B Stars")
        self.assertEqual(rows["b"]["template_id"], tpl(stored, CAM, "R", "R (ZWO ASI6200MM Pro)")["id"])
        self.assertEqual(rows["b"]["template"], "R (ZWO ASI6200MM Pro)")
        self.assertEqual(rows["c"]["template"], "Ha")
        self.assertNotIn("template_unresolved", rows["a"])
        statuses = {t["status"] for f in camera(self.get_gear())["filters"].values() for t in f["templates"]}
        self.assertEqual(statuses, {"in_ts"})

    def test_m4_either_order_gives_the_same_result(self):
        self.post_list()
        self.match(fingerprint())
        first = self.summary()
        redo = self.__class__("test_m3_the_list_and_the_fingerprint_give_ten_linked_templates")
        redo.setUp()
        redo.match(fingerprint())
        redo.post_list()
        self.assertEqual(redo.summary(), first)

    def test_m5_a_repeat_changes_nothing(self):
        self.match(fingerprint())
        self.post_list()
        before = read_gear()
        notes = self.post_list()["notes"]
        self.assertEqual(read_gear(), before)
        self.assertEqual(notes, [])
        self.match(fingerprint())
        self.assertEqual(read_gear(), before)

    def test_adoption_needs_a_capable_plugin(self):
        app_module.set_links_capable(P1, None)
        self.match(fingerprint())
        self.post_list()
        self.assertEqual(self.summary(), {})
        app_module.set_links_capable(P1, ["templates", "repoint"])
        self.assertEqual(self.summary(), EXPECTED)

    def test_m10_a_list_without_ids_adopts_nothing(self):
        self.match(fingerprint())
        old = [{k: v for k, v in t.items() if k in ("name", "filter", "default_exposure_s", "gain", "offset", "bin")}
               for t in ts20()]
        self.post_list(templates=old)
        stored = read_gear()
        self.assertEqual(self.summary(), {})
        self.assertEqual(len(camera(stored)["filters"]["R"]["templates"]), 1)
        self.assertEqual(sum(len(v["templates"]) for v in camera(stored)["filters"].values()), 7)

    def test_m11_a_secondary_profile_links_with_the_gear_values_as_base(self):
        self.match(fingerprint())
        self.post_list()
        app_module.set_links_capable(P2, ["templates", "repoint"])
        self.match(fingerprint(profile_id=P2, profile_name="Bar"))  # newer, so the primary...
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))  # ...until this
        notes = self.post_list(P2, ts20({12: {"offset": 10}}))["notes"]
        ha = tpl(read_gear(), CAM, "Ha", "Ha")
        self.assertEqual(ha["offset"], -1)
        self.assertEqual(ha["ts"][P2]["base"]["offset"], -1)
        self.assertEqual(ha["ts"][P2]["id"], 12)
        self.assertEqual(ha["ts"][P1]["base"]["offset"], -1)
        self.assertTrue(any("Ha offset is 10 in TS and -1 in gear" in n for n in notes), notes)

    def test_a_secondary_profile_difference_after_linking_is_noted_and_not_pulled(self):
        self.match(fingerprint())
        self.post_list()
        app_module.set_links_capable(P2, ["templates", "repoint"])
        self.match(fingerprint(profile_id=P2, profile_name="Bar"))
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))
        self.post_list(P2)
        before = read_gear()
        notes = self.post_list(P2, ts20({12: {"offset": 11}}))["notes"]
        self.assertTrue(any("Ha offset is 11 in TS and -1 in gear" in n for n in notes), notes)
        self.assertEqual(read_gear(), before)

    def test_m12_a_secondary_waits_for_the_primary_to_create_a_template(self):
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))
        self.match(fingerprint(profile_id=P2, profile_name="Bar"))
        self.match(fingerprint(profile_id=P1, profile_name="Foo"))
        app_module.set_links_capable(P2, ["templates", "repoint"])
        notes = self.post_list(P2)["notes"]
        stored = read_gear()
        self.assertEqual([t["name"] for t in camera(stored)["filters"]["R"]["templates"]],
                         ["R (ZWO ASI6200MM Pro)"])
        self.assertTrue(any("waiting" in n and "R" in n for n in notes), notes)
        # Foo (primary) reports its list: it creates "R Stars", then Bar links it.
        self.post_list(P1)
        stars = tpl(read_gear(), CAM, "R", "R Stars")
        self.assertEqual(stars["ts"][P1]["id"], 5)
        self.assertEqual(stars["ts"][P2]["id"], 5)
        self.assertEqual(stars["ts"][P2]["base"]["defaultexposure"], 15)


class TestPull(AdoptionBase):
    def adopted(self):
        self.match(fingerprint())
        self.post_list()
        self.assertEqual(self.summary(), EXPECTED)

    def test_s4_a_ts_edit_gear_has_not_touched_is_taken(self):
        self.adopted()
        notes = self.post_list(templates=ts20({12: {"offset": 10}}))["notes"]
        ha = tpl(read_gear(), CAM, "Ha", "Ha")
        self.assertEqual((ha["offset"], ha["ts"][P1]["base"]["offset"]), (10, 10))
        self.assertIn("Changed in TS: Ha offset 10", notes)

    def test_a_gear_edit_is_left_for_the_push(self):
        self.adopted()
        doc = read_gear()
        tpl(doc, CAM, "Ha", "Ha")["offset"] = 25
        write_gear(doc)
        self.post_list(templates=ts20({12: {"offset": 10}}))
        ha = tpl(read_gear(), CAM, "Ha", "Ha")
        self.assertEqual((ha["offset"], ha["ts"][P1]["base"]["offset"]), (25, -1))

    def test_s28_a_sub_changed_in_ts_reaches_only_rows_on_the_default(self):
        self.adopted()
        name = "R (ZWO ASI6200MM Pro)"
        tid = tpl(read_gear(), CAM, "R", name)["id"]

        def with_pin(p, row_id, ep):
            p["ts_links"] = {P1: {"refs": {"exposure_plan_ids": {f"t_{row_id}": ep}}}}
            return p
        plans = [
            with_pin(plan("p258", [row("r258", "R", name, tid, 120)]), "r258", 19000),
            with_pin(plan("p265", [row("r265", "R", name, tid, 180)]), "r265", 19001),
            with_pin(plan("p408", [row("r408", "R", name, tid, 15, unit="fr", target_frames=20)]), "r408", 19002),
            with_pin(plan("pexp", [row("rexp", "R", name, tid, 120)]), "rexp", 19003),
        ]
        write_plans(plans)
        edits = {19: {"default_exposure_s": 150.0, "plans": [
            {"id": 19000, "exposure": -1}, {"id": 19001, "exposure": 180.0},
            {"id": 19002, "exposure": 15.0}, {"id": 19003, "exposure": 120.0}]}}
        notes = self.post_list(templates=ts20(edits))["notes"]
        r = read_gear()
        t = tpl(r, CAM, "R", name)
        self.assertEqual((t["sub_s"], t["ts"][P1]["base"]["defaultexposure"]), (150, 150))
        self.assertIn("Changed in TS: R (ZWO ASI6200MM Pro) sub 150", notes)
        subs = {p["id"]: p["filter_rows"][0]["template_sub_s"] for p in read_plans()}
        self.assertEqual(subs, {"p258": 150, "p265": 180, "p408": 15, "pexp": 120})


if __name__ == "__main__":
    unittest.main()
