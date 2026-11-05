"""The Activity plan diff and a frame grader's block under an old name.

A plan saved before the block was called `grader` holds it under another
name. Loading and migrating such a plan must not log a change, so the diff
leaves the block out whatever it is called: it finds the block by its
fields, as filter_rows does, or by an old name an extension registered. The
old names here are made up.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import filter_rows as fr  # noqa: E402

PLAN_BLOCK = {"filed_through_night": "2026-10-11", "min_total_frames": 10}
ROW_BLOCK = {"accepted_seconds": 3600.0, "accepted_frames": 12}


def _plan(**extra) -> dict:
    row = {"id": "r1", "filter": "Ha", "target_hours": 6.0, "unit": "h"}
    row.update(extra.pop("row", {}))
    return {"id": "p1", "project_name": "Sample", "min_altitude_deg": 30, "filter_rows": [row], **extra}


@pytest.fixture
def no_aliases(monkeypatch):
    monkeypatch.setattr(fr, "BLOCK_ALIASES", {})


def test_an_unmigrated_plan_and_its_migrated_copy_have_no_changes(no_aliases):
    before = _plan(oldblock=PLAN_BLOCK, row={"oldblock": ROW_BLOCK})
    after = _plan(grader=PLAN_BLOCK, row={"grader": ROW_BLOCK})
    assert activity.diff_plans(before, after) == {}


def test_new_figures_under_an_old_name_are_not_a_change(no_aliases):
    before = _plan(oldblock=PLAN_BLOCK, row={"oldblock": ROW_BLOCK})
    after = _plan(oldblock={**PLAN_BLOCK, "min_total_frames": 20},
                  row={"oldblock": {**ROW_BLOCK, "accepted_frames": 30}})
    assert activity.diff_plans(before, after) == {}


def test_a_registered_old_name_is_left_out_even_without_grader_fields(monkeypatch):
    monkeypatch.setattr(fr, "BLOCK_ALIASES", {"oldblock": fr.GRADER_KEY})
    before = _plan(oldblock={}, row={"oldblock": {}})
    after = _plan(grader=PLAN_BLOCK, row={"grader": ROW_BLOCK})
    assert activity.diff_plans(before, after) == {}


def test_a_real_edit_beside_an_old_block_is_still_reported(no_aliases):
    before = _plan(oldblock=PLAN_BLOCK, row={"oldblock": ROW_BLOCK})
    after = _plan(grader=PLAN_BLOCK, min_altitude_deg=40,
                  row={"grader": ROW_BLOCK, "target_hours": 8.0})
    assert activity.diff_plans(before, after) == {
        "min_altitude_deg": {"from": 30, "to": 40},
        "rows.Ha.target_hours": {"from": 6.0, "to": 8.0},
    }


def test_a_field_that_is_not_a_grader_block_is_still_compared(no_aliases):
    before = _plan(notes_meta={"colour": "red"})
    after = _plan(notes_meta={"colour": "blue"})
    assert activity.diff_plans(before, after) == {"notes_meta.colour": {"from": "red", "to": "blue"}}
