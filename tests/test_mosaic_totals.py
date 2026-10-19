"""A mosaic's goal is per panel, so its totals are the row goal times the panel count.

The TS export gives every panel the full row goal, so a 2 x 1 mosaic with an
8 h L goal needs 16 h of L. Fixture: tests/fixtures/mosaic_2x1_plan.json, shaped
like the live plan bogey-m78-2x1.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import filter_rows as fr  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mosaic_2x1_plan.json"


def _plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestPanelCount(unittest.TestCase):

    def test_rows_times_cols(self):
        self.assertEqual(fr.panel_count(_plan()), 2)
        p = _plan()
        p["target"]["mosaic"] = {"rows": 2, "cols": 3}
        self.assertEqual(fr.panel_count(p), 6)

    def test_not_a_mosaic_is_one_panel(self):
        for plan in (None, {}, {"target": {}}, {"target": {"mosaic": None}},
                     {"target": {"mosaic": {"rows": 1, "cols": 1}}},
                     {"target": {"mosaic": "junk"}}):
            self.assertEqual(fr.panel_count(plan), 1)

    def test_junk_dimensions_clamp_to_one(self):
        p = {"target": {"mosaic": {"rows": 0, "cols": "x"}}}
        self.assertEqual(fr.panel_count(p), 1)
        p = {"target": {"mosaic": {"rows": True, "cols": 2.0}}}
        self.assertEqual(fr.panel_count(p), 2)


class TestMosaicHoursByFilter(unittest.TestCase):

    def test_goal_is_row_goal_times_panels(self):
        totals = fr.hours_by_filter(_plan())
        self.assertEqual(totals["L"]["target_hours"], 16.0)
        self.assertEqual(totals["Ha"]["target_hours"], 16.0)
        # R holds the 2 h row and the 15 frame star row: (2 + 15 x 15 / 3600) x 2.
        self.assertAlmostEqual(totals["R"]["target_hours"], (2.0 + 15 * 15 / 3600.0) * 2)

    def test_actual_is_the_plans_single_figure_not_scaled(self):
        self.assertEqual(fr.hours_by_filter(_plan())["L"]["actual_hours"], 3.0)

    def test_hours_left_for_the_example_plan(self):
        # 8 h L per panel, 3 h shot: 16 - 3 = 13 h left, not 8 - 3 = 5.
        t = fr.hours_by_filter(_plan())["L"]
        self.assertEqual(t["target_hours"] - t["actual_hours"], 13.0)

    def test_non_mosaic_plan_is_unchanged(self):
        p = _plan()
        p["target"]["mosaic"] = {"rows": 1, "cols": 1, "overlap_pct": 15}
        self.assertEqual(fr.hours_by_filter(p)["L"],
                         {"target_hours": 8.0, "actual_hours": 3.0, "credited_hours": 3.0})
        del p["target"]["mosaic"]
        self.assertEqual(fr.hours_by_filter(p)["L"],
                         {"target_hours": 8.0, "actual_hours": 3.0, "credited_hours": 3.0})

    def test_a_row_goal_is_still_per_panel(self):
        row = _plan()["filter_rows"][0]
        self.assertEqual(fr.goal_hours(row), 8.0)


class TestMosaicGoalReached(unittest.TestCase):

    def test_one_panels_worth_does_not_finish_a_two_panel_row(self):
        p = _plan()
        row = p["filter_rows"][0]
        row["actual_hours"] = 8.0
        self.assertFalse(app_module._row_at_goal(row, p))
        row["actual_hours"] = 16.0
        self.assertTrue(app_module._row_at_goal(row, p))

    def test_non_mosaic_row_unchanged(self):
        p = _plan()
        p["target"].pop("mosaic")
        row = p["filter_rows"][0]
        row["actual_hours"] = 8.0
        self.assertTrue(app_module._row_at_goal(row, p))


class TestTotalsAreReadSideOnly(unittest.TestCase):
    """The stored rows, and so the TS export's per-panel goal, are untouched."""

    def test_rows_not_mutated_by_totals(self):
        p = _plan()
        before = copy.deepcopy(p)
        fr.hours_by_filter(p)
        self.assertEqual(p, before)
        # 8 h at 180 s is 160 frames, which every panel still gets in the export.
        self.assertEqual(fr.desired(p["filter_rows"][0]), 160)


if __name__ == "__main__":
    unittest.main()
