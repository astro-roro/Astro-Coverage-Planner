"""Filter rows: the one-shot migration from filter_goals and the derived
filter_goals older plugins read.

docs/specs/goal-panel-and-ts-order.md section 3, tests M1 and S8.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import filter_rows as fr  # noqa: E402
from app import app  # noqa: E402

CAMERA = {
    "id": "cam-1", "name": "Test IMX571",
    "filters": {
        "L": {"default_sub_s": 120},
        "R": {"ts_template_name": "R", "default_sub_s": 180},
        "Ha": {"ts_template_name": "Ha 3nm", "default_sub_s": 300},
    },
}


def _redirect_state(plans_doc: dict) -> Path:
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for name in ("_plans_cache", "_gear_cache", "_destinations_cache",
                 "_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime"):
        setattr(app_module, name, None)
    app_module.GEAR_PATH.write_text(json.dumps({"version": 2, "cameras": [CAMERA]}), encoding="utf-8")
    app_module.PLANS_PATH.write_text(json.dumps(plans_doc), encoding="utf-8")
    return td


def _m1_plan() -> dict:
    return {
        "id": "m42",
        "project_name": "M42",
        "camera_id": "cam-1",
        "target": {"name": "M42", "center_ra_deg": 83.8, "center_dec_deg": -5.4},
        "filter_goals": {
            "Ha": {"target_hours": 4.0, "sub_exposure_s": 300, "actual_hours": 1.5},
            "R Stars": {"target_hours": 0.0833, "sub_exposure_s": 15},
            "R": {"target_hours": 3.0, "sub_exposure_s": 180},
            "L": {"target_hours": 2.0, "sub_exposure_s": 120},
        },
        "ts_links": {
            "profile-1": {
                "machine": "VOYAGER",
                "refs": {
                    "profile_id": "profile-1", "project_id": 5,
                    "target_ids_by_panel": {"1,1": 77},
                    "template_ids_by_filter": {"R Stars": 20, "R": 3},
                    "exposure_plan_ids": {"77_R Stars": 301, "77_R": 300},
                },
                "base_snapshot": {
                    "exposure_plans_by_panel_filter": {
                        "1,1_R Stars": {"desired": 20, "exposure": 15.0},
                    },
                    "templates_by_filter": {"R Stars": {"name": "R Stars (Test IMX571)"}},
                },
            },
        },
    }


class TestMigrationM1(unittest.TestCase):

    def setUp(self):
        self.td = _redirect_state({"version": 1, "settings_migrated": 1, "plans": [_m1_plan()]})
        self.client = app.test_client()

    def _plan(self):
        return app_module.load_plans()["plans"][0]

    def test_rows_in_filter_order_with_star_row_after_its_filter(self):
        rows = self._plan()["filter_rows"]
        self.assertEqual([(r["filter"], r["template"]) for r in rows], [
            ("L", "L (Test IMX571)"), ("R", "R"), ("R", "R Stars"), ("Ha", "Ha 3nm"),
        ])
        self.assertEqual(len({r["id"] for r in rows}), 4)
        self.assertNotIn("filter_goals", self._plan())

    def test_star_row_becomes_a_frame_row(self):
        stars = self._plan()["filter_rows"][2]
        self.assertEqual(stars["template_sub_s"], 15)
        self.assertEqual(stars["unit"], "fr")
        self.assertEqual(stars["target_frames"], 20)
        self.assertNotIn("target_hours", stars)

    def test_hour_rows_keep_hours_and_actuals(self):
        ha = self._plan()["filter_rows"][3]
        self.assertEqual((ha["unit"], ha["target_hours"], ha["template_sub_s"]), ("h", 4.0, 300))
        self.assertEqual(ha["actual_hours"], 1.5)

    def test_every_row_pushes_the_desired_the_old_writer_did(self):
        old = {"L": (2.0, 120), "R": (3.0, 180), "R Stars": (0.0833, 15), "Ha": (4.0, 300)}
        import math
        want = sorted(max(1, math.ceil(h * 3600 / s)) for h, s in old.values())
        got = sorted(fr.desired(r) for r in self._plan()["filter_rows"])
        self.assertEqual(got, want)

    def test_links_move_to_row_ids(self):
        plan = self._plan()
        stars_id = plan["filter_rows"][2]["id"]
        r_id = plan["filter_rows"][1]["id"]
        link = plan["ts_links"]["profile-1"]
        self.assertEqual(link["refs"]["exposure_plan_ids"], {f"77_{stars_id}": 301, f"77_{r_id}": 300})
        self.assertEqual(link["refs"]["template_ids_by_filter"], {stars_id: 20, r_id: 3})
        entry = link["base_snapshot"]["exposure_plans_by_panel_filter"][f"1,1_{stars_id}"]
        # The moved base entry names the row's filter and template, so the
        # writers see no template change and leave `exposure` alone.
        self.assertEqual((entry["filter"], entry["template"], entry["exposure"]), ("R", "R Stars", 15.0))
        self.assertIn(stars_id, link["base_snapshot"]["templates_by_filter"])

    def test_backup_flag_and_second_load(self):
        doc = app_module.load_plans()
        self.assertEqual(doc["filter_rows_migrated"], 1)
        self.assertTrue(list(self.td.glob("plans.json.bak-*")))
        before = app_module.PLANS_PATH.read_text(encoding="utf-8")
        app_module._plans_cache = None
        app_module._plans_cache_mtime = None
        app_module.load_plans()
        self.assertEqual(app_module.PLANS_PATH.read_text(encoding="utf-8"), before)

    def test_s8_get_serves_filter_goals_from_the_first_row_of_each_filter(self):
        plans = self.client.get("/api/plans").get_json()["plans"]
        goals = plans[0]["filter_goals"]
        self.assertEqual(sorted(goals), ["Ha", "L", "R"])
        self.assertEqual(goals["R"], {"target_hours": 3.0, "sub_exposure_s": 180})
        self.assertEqual(goals["Ha"]["actual_hours"], 1.5)
        single = self.client.get("/api/plans/m42").get_json()
        self.assertEqual(single["filter_goals"], goals)
        # Never stored.
        stored = json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("filter_goals", stored["plans"][0])


class TestMigrationCases(unittest.TestCase):
    """The four rows of the migration table."""

    def _migrate(self, goals, camera=CAMERA, **extra):
        plan = {"id": "p", "filter_goals": goals, **extra}
        ids = fr.migrate_plan(plan, camera)
        return plan, ids

    def test_canonical_key_without_a_template_name_uses_the_camera_name(self):
        plan, _ = self._migrate({"L": {"target_hours": 1, "sub_exposure_s": 60}})
        self.assertEqual(plan["filter_rows"][0]["template"], "L (Test IMX571)")

    def test_canonical_key_without_a_camera_uses_the_key(self):
        plan, _ = self._migrate({"SII": {"target_hours": 1}}, camera=None)
        row = plan["filter_rows"][0]
        self.assertEqual((row["filter"], row["template"], row["template_sub_s"]), ("SII", "SII", 300))

    def test_alias_key_keeps_its_name_as_the_template(self):
        plan, _ = self._migrate({"Lum": {"target_hours": 1, "sub_exposure_s": 300}})
        row = plan["filter_rows"][0]
        self.assertEqual((row["filter"], row["template"]), ("L", "Lum"))

    def test_a_brand_name_is_not_stripped(self):
        """The writers' vocabulary, not the archive scanner's: "Antlia Ha" is
        its own filter, so the migration and the writers agree on it."""
        with self.assertLogs(level="WARNING"):
            plan, _ = self._migrate({"Antlia Ha": {"target_hours": 1, "sub_exposure_s": 300}})
        row = plan["filter_rows"][0]
        self.assertEqual((row["filter"], row["template"]), ("Antlia Ha", "Antlia Ha"))

    def test_unknown_key_is_its_own_filter_and_logged(self):
        with self.assertLogs(level="WARNING") as cm:
            plan, _ = self._migrate({"Foo": {"target_hours": 1, "sub_exposure_s": 300}})
        self.assertEqual(plan["filter_rows"][0]["filter"], "Foo")
        self.assertIn("'p'", "\n".join(cm.output))

    def test_sub_rows_from_an_interim_build(self):
        plan, ids = self._migrate({"R": {
            "target_hours": 2, "sub_exposure_s": 180,
            "sub_rows": {"R Stars": {"target_hours": 0.25, "sub_exposure_s": 10}},
        }})
        self.assertEqual([(r["filter"], r["template"]) for r in plan["filter_rows"]],
                         [("R", "R"), ("R", "R Stars")])
        self.assertEqual(plan["filter_rows"][1]["target_frames"], 90)
        self.assertEqual(set(ids), {"R", "R Stars"})

    def test_zero_hour_frame_row_has_zero_frames(self):
        plan, _ = self._migrate({"R Stars": {"target_hours": 0, "sub_exposure_s": 15}})
        self.assertEqual(plan["filter_rows"][0]["target_frames"], 0)

    def test_others_come_last_by_name(self):
        plan, _ = self._migrate({"Zed": {"target_hours": 1}, "Ha": {"target_hours": 1},
                                 "Alpha": {"target_hours": 1}, "L": {"target_hours": 1}})
        self.assertEqual([r["filter"] for r in plan["filter_rows"]], ["L", "Ha", "Alpha", "Zed"])

    def test_the_base_takes_the_migrated_rows_filter_and_template(self):
        """A base the plugin wrote from rows it derived names the filter and
        template its own way. The migrated row's values replace them, so the
        next push sees no change of template."""
        plan = {"id": "p", "filter_goals": {"Ha": {"target_hours": 4, "sub_exposure_s": 300}},
                "ts_links": {"prof": {"refs": {}, "base_snapshot": {"exposure_plans_by_panel_filter": {
                    "1,1_Ha": {"desired": 48, "filter": "H", "template": "Ha (camera)"}}}}}}
        ids = fr.migrate_plan(plan, CAMERA)
        fr.rekey_links(plan, ids)
        entry = plan["ts_links"]["prof"]["base_snapshot"]["exposure_plans_by_panel_filter"][f"1,1_{ids['Ha']}"]
        self.assertEqual((entry["filter"], entry["template"], entry["desired"]), ("Ha", "Ha 3nm", 48))

    def test_plan_with_rows_is_left_alone(self):
        rows = [{"id": "x", "filter": "R"}]
        plan, ids = self._migrate({"Ha": {"target_hours": 1}}, filter_rows=rows)
        self.assertEqual(plan["filter_rows"], rows)
        self.assertEqual(ids, {})
        self.assertNotIn("filter_goals", plan)


class TestSharedRowFilter(unittest.TestCase):
    """tests/fixtures/row_filter_cases.json, the table the TS sync and the
    plugin test their own copies against."""

    def test_every_case(self):
        cases = json.loads((Path(__file__).resolve().parent / "fixtures" / "row_filter_cases.json")
                           .read_text(encoding="utf-8"))["cases"]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(fr.row_filter(raw), expected)

    def test_the_writers_alias_table_is_copied_exactly(self):
        """nina_ts_sync.filter_canon.FILTER_CANON, as that repo has it."""
        self.assertEqual(len(fr.ROW_FILTER_ALIASES), 35)
        self.assertEqual(fr.ROW_FILTER_ALIASES["O"], "OIII")

    def test_the_validator_accepts_an_imported_star_row(self):
        """The importer makes "R Stars (ZWO ASI6200MM Pro)" on filtername R
        into filter R, which a camera with R takes."""
        rows = [{"id": "a", "filter": "R", "template": "R Stars (ZWO ASI6200MM Pro)",
                 "template_sub_s": 10, "unit": "fr", "target_frames": 30}]
        self.assertIsNone(fr.validate_rows(rows, CAMERA))


class TestMigrationNeedsABackup(unittest.TestCase):

    def setUp(self):
        _redirect_state({"version": 1, "settings_migrated": 1, "plans": [_m1_plan()]})

    def test_a_failed_backup_leaves_the_goals_and_get_serves_them(self):
        from unittest import mock
        with mock.patch.object(app_module, "_backup_plans_json", return_value=False), \
                self.assertLogs(level="ERROR"):
            doc = app_module.load_plans()
        self.assertNotIn("filter_rows_migrated", doc)
        plan = doc["plans"][0]
        self.assertNotIn("filter_rows", plan)
        stored = json.loads(app_module.PLANS_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("filter_rows_migrated", stored)
        got = app.test_client().get("/api/plans").get_json()["plans"][0]
        self.assertEqual(got["filter_goals"]["R Stars"]["sub_exposure_s"], 15)

        # The next load with a working backup migrates.
        app_module._plans_cache = None
        app_module._plans_cache_mtime = None
        self.assertEqual(app_module.load_plans()["filter_rows_migrated"], 1)


class TestFilterGoalsView(unittest.TestCase):

    def test_a_filter_with_only_frame_rows_reads_in_hours(self):
        plan = {"filter_rows": [{"id": "a", "filter": "R", "template": "R Stars",
                                 "template_sub_s": 15, "unit": "fr", "target_frames": 240}]}
        self.assertEqual(fr.filter_goals_view(plan), {"R": {"target_hours": 1.0, "sub_exposure_s": 15}})

    def test_the_first_hour_row_wins_over_an_earlier_frame_row(self):
        plan = {"filter_rows": [
            {"id": "a", "filter": "R", "template": "R Stars", "template_sub_s": 15, "unit": "fr",
             "target_frames": 240},
            {"id": "b", "filter": "R", "template": "R", "template_sub_s": 180, "unit": "h",
             "target_hours": 3.0},
        ]}
        self.assertEqual(fr.filter_goals_view(plan)["R"]["sub_exposure_s"], 180)

    def test_hours_by_filter_sums_a_filters_rows(self):
        plan = {"filter_rows": [
            {"id": "a", "filter": "R", "template": "R Stars", "template_sub_s": 15, "unit": "fr",
             "target_frames": 240, "actual_hours": 0.5},
            {"id": "b", "filter": "R", "template": "R", "template_sub_s": 180, "unit": "h",
             "target_hours": 3.0, "actual_hours": 1.0},
        ]}
        self.assertEqual(fr.hours_by_filter(plan), {"R": {"target_hours": 4.0, "actual_hours": 1.5}})


if __name__ == "__main__":
    unittest.main()
