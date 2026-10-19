"""Per-panel acquired hours on a mosaic's filter rows.

A row may carry `panels`, a map from panel key ("row,col" from 1, the key
ts_links uses in `target_ids_by_panel`) to the hours acquired on that panel.
`actual_hours` is then the sum of the map. Plans without a map behave as they
did before, and the AAC /progress and /accepted payloads keep working.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import filter_rows as fr  # noqa: E402
from app import app  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mosaic_2x1_plan.json"


def _plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _redirect_state() -> None:
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.ZIP_OUTPUT_DIR = td / "exports"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for cache in ("_plans_cache", "_gear_cache", "_destinations_cache",
                  "_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime"):
        setattr(app_module, cache, None)


def _store(plans) -> None:
    app_module.PLANS_PATH.write_text(
        json.dumps({"version": 1, "settings_migrated": 1, "plans": plans}), encoding="utf-8")
    app_module._plans_cache = None
    app_module._plans_cache_mtime = None


def _row(plan: dict, row_id: str) -> dict:
    return next(r for r in plan["filter_rows"] if r["id"] == row_id)


class TestPanelKeys(unittest.TestCase):

    def test_valid_keys(self):
        for key in ("1,1", "2,1", "10,12"):
            self.assertTrue(fr.valid_panel_key(key), key)

    def test_invalid_keys(self):
        for key in ("0,1", "1", "1,1,1", "a,b", "1, 1", "-1,1", "", None, 11, "1000,1"):
            self.assertFalse(fr.valid_panel_key(key), key)

    def test_panel_key_counts_from_one(self):
        self.assertEqual(fr.panel_key(0, 0), "1,1")
        self.assertEqual(fr.panel_key(1, 0), "2,1")


class TestValidation(unittest.TestCase):

    def _validate(self, panels):
        row = {"id": "a", "filter": "L", "template": "L", "template_sub_s": 180,
               "unit": "h", "target_hours": 8.0, "panels": panels}
        return fr.validate_rows([row], None)

    def test_a_good_map_passes(self):
        self.assertIsNone(self._validate({"1,1": 3.0, "2,1": 0}))

    def test_no_map_and_null_pass(self):
        self.assertIsNone(self._validate(None))
        row = {"id": "a", "filter": "L", "template": "L", "template_sub_s": 180,
               "unit": "h", "target_hours": 8.0}
        self.assertIsNone(fr.validate_rows([row], None))

    def test_not_an_object(self):
        self.assertIn("panels", self._validate([1, 2]))

    def test_bad_key(self):
        self.assertIn("key", self._validate({"R1C1": 1.0}))

    def test_bad_values(self):
        for bad in (-1, "2", True, None, float("inf")):
            self.assertIn("at least 0", self._validate({"1,1": bad}), bad)

    def test_too_many_panels(self):
        many = {f"{r},{c}": 0.0 for r in range(1, 22) for c in range(1, 22)}
        self.assertIn("more than", self._validate(many))


class TestSumAndCredit(unittest.TestCase):

    def test_sync_actuals_makes_actual_the_sum(self):
        p = _plan()
        row = _row(p, "r-l")
        row["panels"] = {"1,1": 2.5, "2,1": 1.0}
        fr.sync_actuals(p)
        self.assertEqual(row["actual_hours"], 3.5)

    def test_rows_without_a_map_are_left_alone(self):
        p = _plan()
        fr.sync_actuals(p)
        self.assertEqual(_row(p, "r-l")["actual_hours"], 3.0)

    def test_keys_outside_the_grid_are_ignored(self):
        p = _plan()
        row = _row(p, "r-l")
        row["panels"] = {"1,1": 1.0, "2,1": 2.0, "3,1": 9.0, "1,2": 9.0}
        self.assertEqual(fr.row_panels(row, p), {"1,1": 1.0, "2,1": 2.0})
        self.assertEqual(fr.row_panels(row), {"1,1": 1.0, "2,1": 2.0, "3,1": 9.0, "1,2": 9.0})

    def test_sync_actuals_leaves_out_keys_outside_the_grid(self):
        p = _plan()
        row = _row(p, "r-l")
        row["panels"] = {"1,1": 1.0, "2,1": 2.0, "3,1": 9.0}
        fr.sync_actuals(p)
        self.assertEqual(row["actual_hours"], 3.0)

    def test_hours_by_filter_reports_shot_and_credited(self):
        p = _plan()
        row = _row(p, "r-l")
        row["panels"] = {"1,1": 16.0, "2,1": 0.0, "9,9": 5.0}
        totals = fr.hours_by_filter(p)["L"]
        # Goal 8 h x 2 panels; 16 h shot on panel 1 but only 8 h of it credited.
        self.assertEqual(totals["target_hours"], 16.0)
        self.assertEqual(totals["actual_hours"], 16.0)
        self.assertEqual(totals["credited_hours"], 8.0)

    def test_hours_by_filter_without_a_map_credits_actual_hours(self):
        totals = fr.hours_by_filter(_plan())["L"]
        self.assertEqual(totals["actual_hours"], 3.0)
        self.assertEqual(totals["credited_hours"], 3.0)

    def test_a_panel_counts_up_to_its_own_goal(self):
        p = _plan()
        row = _row(p, "r-l")
        row["panels"] = {"1,1": 12.0, "2,1": 1.0}
        # 8 + 1, not 12 + 1: panel 1's overshoot cannot pay for panel 2.
        self.assertEqual(fr.credited_hours(row, p), 9.0)

    def test_credit_without_a_map_is_actual_hours(self):
        p = _plan()
        self.assertEqual(fr.credited_hours(_row(p, "r-l"), p), 3.0)

    def test_row_at_goal_needs_every_panel(self):
        p = _plan()
        row = _row(p, "r-l")
        row["panels"] = {"1,1": 12.0, "2,1": 1.0}
        row["actual_hours"] = 13.0
        self.assertFalse(app_module._row_at_goal(row, p))
        row["panels"]["2,1"] = 8.0
        self.assertTrue(app_module._row_at_goal(row, p))


class TestProgressEndpoint(unittest.TestCase):

    def setUp(self):
        _redirect_state()
        self.client = app.test_client()
        _store([_plan()])

    def _post(self, body):
        return self.client.post("/api/plans/bogey-m78-2x1/progress", json=body)

    def _entry(self, panels, hours=0.0, template="L"):
        return {"entries": [{"filter": "L", "template": template, "acquired_hours": hours,
                             "panels": panels}]}

    def _stored(self):
        return next(p for p in app_module.load_plans()["plans"] if p["id"] == "bogey-m78-2x1")

    def test_panels_land_and_actual_becomes_the_sum(self):
        r = self._post(self._entry({"1,1": 2.0, "2,1": 1.5}, hours=99.0))
        self.assertEqual(r.status_code, 200, r.get_json())
        row = _row(self._stored(), "r-l")
        self.assertEqual(row["panels"], {"1,1": 2.0, "2,1": 1.5})
        self.assertEqual(row["actual_hours"], 3.5)
        self.assertEqual(r.get_json()["updated"], {"L": 3.5})

    def test_a_key_outside_the_grid_is_kept_but_not_totalled(self):
        r = self._post(self._entry({"1,1": 2.0, "2,1": 1.5, "3,1": 9.0}))
        self.assertEqual(r.status_code, 200, r.get_json())
        row = _row(self._stored(), "r-l")
        self.assertEqual(row["panels"], {"1,1": 2.0, "2,1": 1.5, "3,1": 9.0})
        self.assertEqual(row["actual_hours"], 3.5)

    def test_a_later_report_merges_and_panels_only_go_up(self):
        self._post(self._entry({"1,1": 2.0, "2,1": 1.5}))
        r = self._post(self._entry({"1,1": 1.0, "2,1": 2.5}))
        body = r.get_json()
        self.assertEqual(body["not_lowered"], ["L#1,1"])
        row = _row(self._stored(), "r-l")
        self.assertEqual(row["panels"], {"1,1": 2.0, "2,1": 2.5})
        self.assertEqual(row["actual_hours"], 4.5)

    def test_force_replaces_the_map(self):
        self._post(self._entry({"1,1": 2.0, "2,1": 1.5}))
        body = {**self._entry({"1,1": 0.5}), "force": True}
        self._post(body)
        row = _row(self._stored(), "r-l")
        self.assertEqual(row["panels"], {"1,1": 0.5})
        self.assertEqual(row["actual_hours"], 0.5)

    def test_a_report_without_panels_leaves_a_tracked_row_alone(self):
        self._post(self._entry({"1,1": 2.0, "2,1": 1.5}))
        r = self._post({"entries": [{"filter": "L", "template": "L", "acquired_hours": 50.0}]})
        body = r.get_json()
        self.assertEqual(body["updated"], {})
        self.assertEqual(body["panel_rows_kept"], ["L"])
        row = _row(self._stored(), "r-l")
        self.assertEqual(row["actual_hours"], 3.5)
        self.assertEqual(row["panels"], {"1,1": 2.0, "2,1": 1.5})

    def test_a_forced_report_without_panels_drops_the_map(self):
        self._post(self._entry({"1,1": 2.0, "2,1": 1.5}))
        self._post({"entries": [{"filter": "L", "template": "L", "acquired_hours": 1.0}], "force": True})
        row = _row(self._stored(), "r-l")
        self.assertEqual(row["actual_hours"], 1.0)
        self.assertNotIn("panels", row)

    def test_the_old_payload_shape_still_works_on_an_untracked_row(self):
        r = self._post({"filters": {"L": {"acquired_hours": 5.0}}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["updated"], {"L": 5.0})
        self.assertEqual(_row(self._stored(), "r-l")["actual_hours"], 5.0)
        self.assertNotIn("panels", _row(self._stored(), "r-l"))

    def test_bad_panel_payloads_are_refused(self):
        for panels in ({"R1C1": 1.0}, {"1,1": -1}, {"1,1": "2"}, [1], {"1,1": 1e9}):
            r = self._post(self._entry(panels))
            self.assertEqual(r.status_code, 400, panels)
        self.assertNotIn("panels", _row(self._stored(), "r-l"))

    def test_saving_a_plan_rederives_actual_from_the_map(self):
        p = self.client.get("/api/plans/bogey-m78-2x1").get_json()
        row = _row(p, "r-l")
        row["panels"] = {"1,1": 2.0, "2,1": 1.0}
        row["actual_hours"] = 99.0
        r = self.client.put("/api/plans/bogey-m78-2x1", json=p)
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(_row(self._stored(), "r-l")["actual_hours"], 3.0)

    def test_saving_refuses_a_bad_map(self):
        p = self.client.get("/api/plans/bogey-m78-2x1").get_json()
        _row(p, "r-l")["panels"] = {"nope": 1.0}
        r = self.client.put("/api/plans/bogey-m78-2x1", json=p)
        self.assertEqual(r.status_code, 400)


class TestExport(unittest.TestCase):

    def setUp(self):
        _redirect_state()
        app_module.save_gear({
            "version": 2,
            "telescopes": [{"id": "tel-1", "name": "Test 600mm", "focal_length_mm": 600.0,
                            "aperture_mm": 130}],
            "cameras": [{"id": "cam-1", "name": "Test IMX571", "pixel_size_um": 3.76,
                         "sensor_px": [6248, 4176],
                         "filters": {"L": {"ts_template_name": "L", "default_sub_s": 180,
                                           "gain": 100, "offset": 50, "bin": 1}}}],
        })

    def _plan(self, rows=2, cols=1, panels=None):
        p = {
            "id": "m", "guid": "g", "project_name": "Mosaic", "state": "active",
            "priority": "normal", "min_altitude_deg": 30,
            "telescope_id": "tel-1", "camera_id": "cam-1",
            "target": {"name": "Bogey", "center_ra_deg": 100.0, "center_dec_deg": -30.0,
                       "rotation_deg": 0.0, "mosaic": {"rows": rows, "cols": cols, "overlap_pct": 15}},
            "filter_rows": [{"id": "a", "filter": "L", "template": "L", "template_sub_s": 180,
                             "unit": "h", "target_hours": 8.0, "actual_hours": 3.0}],
        }
        if panels is not None:
            p["filter_rows"][0]["panels"] = panels
            fr.sync_actuals(p)
        return p

    def _acquired(self, plan):
        payload, _ = app_module._build_ts_export([plan], app_module.load_gear())
        return [t["ExposurePlans"][0]["Acquired"] for t in payload["projects"][0]["Targets"]]

    def test_without_a_map_every_panel_gets_the_plans_figure(self):
        # 3 h at 180 s is 60 frames, as after the totals change.
        self.assertEqual(self._acquired(self._plan()), [60, 60])

    def test_with_a_map_each_panel_gets_its_own_count(self):
        # 2 h is 40 frames and 1 h is 20.
        self.assertEqual(self._acquired(self._plan(panels={"1,1": 2.0, "2,1": 1.0})), [40, 20])

    def test_a_panel_missing_from_the_map_has_none(self):
        self.assertEqual(self._acquired(self._plan(panels={"2,1": 1.0})), [0, 20])

    def test_accepted_follows_acquired(self):
        payload, _ = app_module._build_ts_export(
            [self._plan(panels={"1,1": 2.0, "2,1": 1.0})], app_module.load_gear())
        eps = [t["ExposurePlans"][0] for t in payload["projects"][0]["Targets"]]
        self.assertEqual([e["Accepted"] for e in eps], [40, 20])

    def test_a_single_target_ignores_a_stale_map(self):
        plain = self._plan(rows=1, cols=1)
        stale = self._plan(rows=1, cols=1)
        stale["filter_rows"][0]["panels"] = {"1,1": 9.0}
        self.assertEqual(self._acquired(stale), self._acquired(plain))
        self.assertEqual(self._acquired(plain), [60])

    def test_non_mosaic_export_is_byte_identical_with_or_without_the_feature(self):
        def export(plan):
            payload, _ = app_module._build_ts_export([plan], app_module.load_gear())
            text = json.dumps(payload, sort_keys=True, default=str)
            # Guids and dates are random per export; blank them.
            import re
            text = re.sub(r'"(Guid|CreateDate)": "[^"]*"', r'"\1": ""', text)
            return text
        base = self._plan(rows=1, cols=1)
        self.assertEqual(export(copy.deepcopy(base)), export(copy.deepcopy(base)))
        with_map = copy.deepcopy(base)
        with_map["filter_rows"][0]["panels"] = {"1,1": 3.0}
        self.assertEqual(export(copy.deepcopy(base)), export(with_map))


if __name__ == "__main__":
    unittest.main()
