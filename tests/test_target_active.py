"""A plan's scheduling switch (`target_active`) and its per-panel switches
(`panel_active`): how they are stored, their defaults, the Activity entry a
change records, and the legacy TS export's `Enabled` flag."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import app as app_module  # noqa: E402
import filter_rows as fr  # noqa: E402
from app import app  # noqa: E402
from tests.test_plan_validation import _fresh_plans_path, _valid_plan_payload  # noqa: E402


class TestStorage(unittest.TestCase):
    def setUp(self):
        _fresh_plans_path()
        self.client = app.test_client()

    def _save(self, **fields):
        r = self.client.post("/api/plans", json=_valid_plan_payload(**fields))
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        return r.get_json()

    def test_absent_stays_absent(self):
        body = self._save()
        self.assertNotIn("target_active", body)
        self.assertNotIn("panel_active", body)
        self.assertTrue(fr.target_active(body))
        self.assertTrue(fr.target_active(body, "1,1"))

    def test_off_is_stored_and_on_is_dropped(self):
        body = self._save(target_active=False)
        self.assertIs(body["target_active"], False)
        body = self._save(target_active=True)
        self.assertNotIn("target_active", body)

    def test_panel_map_keeps_only_off_panels(self):
        target = {"name": "Wide Field", "center_ra_deg": 100.0, "center_dec_deg": -30.0,
                  "mosaic": {"rows": 2, "cols": 1, "overlap_pct": 15}}
        body = self._save(target=target, panel_active={"1,1": True, "2,1": False})
        self.assertEqual(body["panel_active"], {"2,1": False})
        self.assertTrue(fr.target_active(body, "1,1"))
        self.assertFalse(fr.target_active(body, "2,1"))
        body = self._save(target=target, panel_active={"1,1": True})
        self.assertNotIn("panel_active", body)

    def test_stored_in_plans_json_and_served_by_get(self):
        self._save(target_active=False, panel_active={"1,2": False})
        plan = self.client.get("/api/plans").get_json()["plans"][0]
        self.assertIs(plan["target_active"], False)
        self.assertEqual(plan["panel_active"], {"1,2": False})

    def test_bad_values_are_refused(self):
        for fields in ({"target_active": "no"}, {"target_active": 0},
                       {"panel_active": ["1,1"]}, {"panel_active": {"R1C1": False}},
                       {"panel_active": {"1,1": 0}}):
            r = self.client.post("/api/plans", json=_valid_plan_payload(**fields))
            self.assertEqual(r.status_code, 400, fields)


class TestActivity(unittest.TestCase):
    def test_diff_and_title(self):
        before = {"id": "p"}
        after = {"id": "p", "target_active": False, "panel_active": {"2,1": False}}
        changes = activity.diff_plans(before, after)
        self.assertEqual(set(changes), {"target_active", "panel_active.2,1"})
        title = activity.changes_title(changes)
        self.assertIn("scheduling on to off", title)
        self.assertIn("panel R2C1 scheduling on to off", title)
        self.assertEqual(activity.diff_plans(after, after), {})

    def test_a_save_that_turns_a_plan_off_records_an_edit(self):
        _fresh_plans_path()
        client = app.test_client()
        client.post("/api/plans", json=_valid_plan_payload())
        recorded = []
        with mock.patch.object(activity, "enabled", return_value=True), \
                mock.patch.object(app_module, "_activity_later", side_effect=lambda fn: fn()), \
                mock.patch.object(activity, "record_changes",
                                  side_effect=lambda **kw: recorded.append(kw)):
            r = client.post("/api/plans", json=_valid_plan_payload(target_active=False))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["etype"], "plan_edited")
        self.assertEqual(recorded[0]["changes"]["target_active"], {"from": None, "to": False})


class TestLegacyExport(unittest.TestCase):
    def test_enabled_follows_the_switches(self):
        gear = {"telescopes": [{"id": "tel-1", "focal_length_mm": 600.0}],
                "cameras": [{"id": "cam-1", "pixel_size_um": 3.76, "sensor_px": [6248, 4176],
                             "filters": {"OIII": {"ts_template_name": "OIII 3nm"}}}]}
        rows = [{"id": "r1", "filter": "OIII", "template": "OIII 3nm", "template_sub_s": 300,
                 "unit": "h", "target_hours": 1.0}]
        base = {"project_name": "Split", "telescope_id": "tel-1", "camera_id": "cam-1",
                "filter_rows": rows}
        plans = [
            {**base, "id": "a", "target": {"name": "A", "center_ra_deg": 10, "center_dec_deg": 0,
                                           "mosaic": {"rows": 2, "cols": 1}},
             "panel_active": {"2,1": False}},
            {**base, "id": "b", "target": {"name": "B", "center_ra_deg": 20, "center_dec_deg": 0},
             "target_active": False},
            {**base, "id": "c", "target": {"name": "C", "center_ra_deg": 30, "center_dec_deg": 0}},
        ]
        export, _ = app_module._build_ts_export(plans, gear)
        enabled = {t["Name"]: t["Enabled"]
                   for p in export["projects"] for t in p["Targets"]}
        self.assertEqual(enabled, {"A Panel 1 (R1C1)": True, "A Panel 2 (R2C1)": False,
                                   "B": False, "C": True})


if __name__ == "__main__":
    unittest.main()
