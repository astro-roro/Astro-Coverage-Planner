"""The three ACP plans the push tests use.

The plan shape and the gear shape are taken from ACP's own
`tests/test_export_sync.py` helpers (`_plan` and `_save_gear`) so these tests
exercise the same dicts the live app hands the extension, rather than a shape
invented here. Values are ACP's defaults except where a test needs variety.
"""
from __future__ import annotations

PROFILE_ID = "profile-under-test"

GEAR = {
    "version": 2,
    "telescopes": [{
        "id": "tel-1", "name": "Test 600mm",
        "focal_length_mm": 600.0, "aperture_mm": 130,
    }],
    "cameras": [{
        "id": "cam-1", "name": "Test IMX571",
        "pixel_size_um": 3.76, "sensor_px": [6248, 4176],
        "filters": {
            "OIII": {"ts_template_name": "OIII 3nm", "default_sub_s": 300,
                     "gain": 100, "offset": 50, "bin": 1},
            "Ha": {"ts_template_name": "Ha 3nm", "default_sub_s": 600,
                   "gain": 100, "offset": 50, "bin": 1},
        },
    }],
}


def rows_from_goals(goals: dict, camera: dict | None = None) -> list[dict]:
    """Filter rows for a `filter_goals`-style dict, the way ACP's one-shot
    migration makes them (docs/specs/goal-panel-and-ts-order.md section 3).
    Each row's id is its goal key, so a test can name a row by its filter.
    Templates come from GEAR's camera unless *camera* is given."""
    from nina_ts_sync.rows import rows_of
    return rows_of({"filter_goals": goals}, camera if camera is not None else GEAR["cameras"][0])


def plan(plan_id, **kwargs):
    """ACP's `_plan` helper from tests/test_export_sync.py, with goals as
    filter rows. Pass `filter_goals` for rows made the migration's way, or
    `filter_rows` for rows as they are."""
    base = {
        "id": plan_id,
        "guid": f"guid-{plan_id}",
        "project_name": kwargs.pop("project_name", f"Project {plan_id}"),
        "target": {
            "name": kwargs.pop("target_name", f"T{plan_id}"),
            "center_ra_deg": kwargs.pop("ra", 100.0),
            "center_dec_deg": kwargs.pop("dec", -30.0),
            "rotation_deg": kwargs.pop("rot", 0.0),
            "mosaic": {
                "rows": kwargs.pop("rows", 1),
                "cols": kwargs.pop("cols", 1),
                "overlap_pct": kwargs.pop("overlap_pct", 15),
            },
        },
        "telescope_id": kwargs.pop("telescope_id", "tel-1"),
        "camera_id": kwargs.pop("camera_id", "cam-1"),
        "filter_rows": kwargs.pop("filter_rows") if "filter_rows" in kwargs else rows_from_goals(
            kwargs.pop("filter_goals", {"OIII": {"target_hours": 1.0, "sub_exposure_s": 300}})),
        "priority": kwargs.pop("priority", "normal"),
        "min_altitude_deg": kwargs.pop("min_altitude_deg", 30),
    }
    # "state" is left out by default: ACP's own convention is that an
    # absent plan reads as active. Pass state="draft", "inactive" or
    # "closed" to build a plan that isn't.
    if "state" in kwargs:
        base["state"] = kwargs.pop("state")
    if "minimum_time_min" in kwargs:
        base["minimum_time_min"] = kwargs.pop("minimum_time_min")
    if "destination_id" in kwargs:
        base["destination_id"] = kwargs.pop("destination_id")
    base.update(kwargs)
    return base


def three_plans():
    """One single target (active), one 2x2 mosaic (inactive, high priority),
    one with two filters (draft, minimum time 30). The spread of state,
    priority and minimum time docs/specs/ts-project-settings.md section 4
    asks the golden rows to cover."""
    return [
        plan("single", project_name="Single Target", target_name="NGC 253"),
        plan("mosaic", project_name="Mosaic", target_name="M31",
             rows=2, cols=2, overlap_pct=15, ra=10.68, dec=41.27,
             state="inactive", priority="high"),
        plan("twofilter", project_name="Two Filters", target_name="NGC 7000",
             ra=314.7, dec=44.5,
             filter_goals={
                 "Ha": {"target_hours": 4.0, "sub_exposure_s": 600},
                 "OIII": {"target_hours": 2.0, "sub_exposure_s": 300},
             },
             state="draft", minimum_time_min=30),
    ]


def row(plan: dict, filt: str, template: str | None = None) -> dict:
    """The one row of *plan* with filter *filt* (and *template*)."""
    hits = [r for r in plan["filter_rows"]
            if r["filter"] == filt and (template is None or r["template"] == template)]
    assert len(hits) == 1, plan["filter_rows"]
    return hits[0]
