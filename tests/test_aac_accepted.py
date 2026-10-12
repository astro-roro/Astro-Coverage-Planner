"""What AAC needs from ACP: target_class and POST /api/plans/<id>/accepted.

docs/specs/goal-panel-and-ts-order.md section 6, tests A1 and A2, and the
ACP half of A3 (each row's desired in tests/fixtures/aac_accepted_cases.json).
The endpoint is checked against the request AAC's client sends
(auto_pix/acp_feedback.py in Astro-Auto-Calibration) and the response table
that client acts on.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import filter_rows as fr  # noqa: E402
from app import app  # noqa: E402

CASES = json.loads((Path(__file__).resolve().parent / "fixtures" / "aac_accepted_cases.json")
                   .read_text(encoding="utf-8"))["cases"]

HA_ID = "6f1c2a9e-4b7d-4e21-9a35-0c8d1e2f3a4b"
STARS_ID = "b2e0d7c4-1a5f-4c3e-8d6b-7f9a0e1c2d3e"
G_ID = "0a3e5c11-9a1c-4f0d-a0d4-6d3f6c0f6a10"


def _redirect_state():
    td = Path(tempfile.mkdtemp())
    app_module.PLANS_PATH = td / "plans.json"
    app_module.GEAR_PATH = td / "gear.json"
    app_module.DESTINATIONS_PATH = td / "destinations.json"
    for name in ("_plans_cache", "_gear_cache", "_destinations_cache",
                 "_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime"):
        setattr(app_module, name, None)
    return td


def _rows():
    return [
        {"id": HA_ID, "filter": "Ha", "template": "Ha", "template_sub_s": 300, "unit": "h",
         "target_hours": 6.0, "actual_hours": 5.0},
        {"id": STARS_ID, "filter": "R", "template": "R Stars", "template_sub_s": 15, "unit": "fr",
         "target_frames": 48, "actual_hours": 0.2},
        {"id": G_ID, "filter": "G", "template": "G Stars", "template_sub_s": 15, "unit": "fr",
         "target_frames": 48},
    ]


def _plan(plan_id="M42"):
    return {"id": plan_id, "project_name": "M42",
            "target": {"name": "M42", "center_ra_deg": 83.8, "center_dec_deg": -5.4},
            "filter_rows": _rows(), "updated_at": "2020-01-01T00:00:00+00:00"}


def _figures(row_id, filt, template, seconds, frames, complete=False):
    return {"id": row_id, "filter": filt, "template": template,
            "accepted_seconds": seconds, "accepted_frames": frames,
            "seen_seconds": seconds + 300, "seen_frames": frames + 1,
            "complete": complete, "estimated_hours": None, "measurement": None}


def _request(rows, night="2026-10-11", floor=10):
    return {"source": "aac", "at": "2026-10-12T10:05:00Z", "filed_through_night": night,
            "min_total_frames": floor, "rows": rows}


class TestAcceptedEndpoint(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.addCleanup(self._restore_token)
        self._prev = os.environ.pop("ACP_API_TOKEN", None)
        app_module.save_plans({"version": 1, "filter_rows_migrated": 1, "plans": [_plan()]})
        self.client = app.test_client()

    def _restore_token(self):
        if self._prev is None:
            os.environ.pop("ACP_API_TOKEN", None)
        else:
            os.environ["ACP_API_TOKEN"] = self._prev

    def _post(self, body, plan_id="M42", headers=None):
        return self.client.post(f"/api/plans/{plan_id}/accepted", json=body, headers=headers or {})

    def _stored(self):
        return app_module.load_plans()["plans"][0]

    def test_a2_stores_the_figures_on_the_row_and_the_watermark_on_the_plan(self):
        r = self._post(_request([_figures(HA_ID, "Antlia Ha", "Ha", 22320.0, 74),
                                 _figures(STARS_ID, "R", "R Stars", 705.0, 47, True)]))
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["unknown_rows"], [])
        plan = self._stored()
        ha, stars = plan["filter_rows"][0], plan["filter_rows"][1]
        self.assertEqual(ha["aac"]["accepted_seconds"], 22320.0)
        self.assertEqual(ha["aac"]["accepted_frames"], 74)
        self.assertEqual(ha["aac"]["seen_frames"], 75)
        self.assertFalse(ha["aac"]["complete"])
        self.assertTrue(stars["aac"]["complete"])
        self.assertEqual(plan["aac"]["filed_through_night"], "2026-10-11")
        self.assertEqual(plan["aac"]["min_total_frames"], 10)
        self.assertTrue(plan["aac"]["received_at"])
        self.assertEqual(ha["actual_hours"], 5.0)

    def test_a2_a_later_figure_may_be_lower(self):
        self._post(_request([_figures(HA_ID, "Ha", "Ha", 22320.0, 74)]))
        self._post(_request([_figures(HA_ID, "Ha", "Ha", 21240.0, 70)]))
        self.assertEqual(self._stored()["filter_rows"][0]["aac"]["accepted_seconds"], 21240.0)

    def test_a2_a_row_whose_template_changed_comes_back_unknown_with_its_reason(self):
        r = self._post(_request([_figures(HA_ID, "Ha", "Ha", 100.0, 1),
                                 _figures(G_ID, "G", "G Narrow", 10.0, 1),
                                 _figures("no-such-id", "Ha", "Ha", 1.0, 1)]))
        self.assertEqual(r.status_code, 200)
        unknown = {u["id"]: u["reason"] for u in r.get_json()["unknown_rows"]}
        self.assertEqual(unknown[G_ID], "row now has filter G and template G Stars")
        self.assertEqual(unknown["no-such-id"], "no such row")
        self.assertNotIn("aac", self._stored()["filter_rows"][2])
        self.assertIn("aac", self._stored()["filter_rows"][0])

    def test_a2_zeros_for_a_row_with_no_frames_are_stored_not_complete(self):
        r = self._post(_request([_figures(G_ID, "G", "G Stars", 0.0, 0)]))
        self.assertEqual(r.status_code, 200)
        aac = self._stored()["filter_rows"][2]["aac"]
        self.assertEqual((aac["accepted_frames"], aac["complete"]), (0, False))

    def test_a2_an_unknown_plan_is_404(self):
        self.assertEqual(self._post(_request([]), plan_id="nope").status_code, 404)

    def test_a2_a_malformed_request_is_400_and_stores_nothing(self):
        cases = [
            {**_request([]), "filed_through_night": None},
            {**_request([]), "filed_through_night": "yesterday"},
            {**_request([]), "min_total_frames": -1},
            {**_request([]), "rows": "all"},
            _request([{**_figures(HA_ID, "Ha", "Ha", 1.0, 1), "complete": "yes"}]),
            _request([{**_figures(HA_ID, "Ha", "Ha", 1.0, 1), "accepted_seconds": -5}]),
            _request([{k: v for k, v in _figures(HA_ID, "Ha", "Ha", 1.0, 1).items() if k != "template"}]),
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self._post(body).status_code, 400)
        self.assertNotIn("aac", self._stored())

    def test_a2_the_bearer_token_is_required_when_one_is_set(self):
        os.environ["ACP_API_TOKEN"] = "a-perfectly-ordinary-token"
        body = _request([_figures(HA_ID, "Ha", "Ha", 100.0, 1)])
        self.assertEqual(self._post(body).status_code, 401)
        self.assertEqual(self._post(body, headers={"Authorization": "Bearer wrong"}).status_code, 401)
        ok = self._post(body, headers={"Authorization": "Bearer a-perfectly-ordinary-token"})
        self.assertEqual(ok.status_code, 200)

    def test_a_save_from_the_editor_cannot_overwrite_aac_figures(self):
        self._post(_request([_figures(HA_ID, "Ha", "Ha", 22320.0, 74)]))
        edited = json.loads(json.dumps(self._stored()))
        edited["filter_rows"][0]["aac"] = {"accepted_seconds": 1.0, "accepted_frames": 0,
                                           "complete": True}
        edited["aac"] = {"filed_through_night": "1999-01-01", "min_total_frames": 0}
        r = self.client.put("/api/plans/M42", json=edited)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        stored = self._stored()
        self.assertEqual(stored["filter_rows"][0]["aac"]["accepted_seconds"], 22320.0)
        self.assertEqual(stored["aac"]["filed_through_night"], "2026-10-11")

    def test_the_progress_route_leaves_aac_figures_alone(self):
        self._post(_request([_figures(HA_ID, "Ha", "Ha", 22320.0, 74)]))
        r = self.client.post("/api/plans/M42/progress", json={
            "filters": {"Ha": {"acquired_hours": 9.0}}, "source": "ts"})
        self.assertEqual(r.status_code, 200)
        row = self._stored()["filter_rows"][0]
        self.assertEqual(row["actual_hours"], 9.0)
        self.assertEqual(row["aac"]["accepted_seconds"], 22320.0)


class TestReviewFixes(unittest.TestCase):
    """Section 6 after review: stale blocks go, goal_at_send is stored, and the
    routes that write plans.json hold PLANS_LOCK."""

    def setUp(self):
        _redirect_state()
        self._prev = os.environ.pop("ACP_API_TOKEN", None)
        self.addCleanup(self._restore_token)
        app_module.save_plans({"version": 1, "filter_rows_migrated": 1, "plans": [_plan()]})
        self.client = app.test_client()

    def _restore_token(self):
        if self._prev is None:
            os.environ.pop("ACP_API_TOKEN", None)
        else:
            os.environ["ACP_API_TOKEN"] = self._prev

    def _stored(self):
        return app_module.load_plans()["plans"][0]

    def _post(self, body):
        return self.client.post("/api/plans/M42/accepted", json=body)

    def test_the_goal_at_send_is_stored_in_the_rows_unit(self):
        self._post(_request([_figures(HA_ID, "Ha", "Ha", 100.0, 1, True),
                             _figures(STARS_ID, "R", "R Stars", 100.0, 1, True)]))
        rows = self._stored()["filter_rows"]
        self.assertEqual(rows[0]["aac"]["goal_at_send"], 6.0)
        self.assertEqual(rows[1]["aac"]["goal_at_send"], 48.0)

    def test_a_row_that_comes_back_unknown_loses_its_stored_block(self):
        self._post(_request([_figures(G_ID, "G", "G Stars", 10.0, 1)]))
        self.assertIn("aac", self._stored()["filter_rows"][2])
        r = self._post(_request([_figures(G_ID, "G", "G Narrow", 10.0, 1)]))
        self.assertEqual([u["id"] for u in r.get_json()["unknown_rows"]], [G_ID])
        self.assertNotIn("aac", self._stored()["filter_rows"][2])

    def _save_with(self, **changes):
        plan = self._stored()
        body = {k: v for k, v in plan.items()}
        body["filter_rows"] = [dict(r) for r in plan["filter_rows"]]
        body["filter_rows"][2].update(changes)
        return self.client.put("/api/plans/M42", json=body)

    def test_a_save_keeps_the_block_while_filter_and_template_agree(self):
        self._post(_request([_figures(G_ID, "G", "G Stars", 10.0, 1)]))
        self.assertEqual(self._save_with(target_frames=60).status_code, 200)
        self.assertIn("aac", self._stored()["filter_rows"][2])

    def test_a_save_drops_the_block_when_the_template_changes(self):
        self._post(_request([_figures(G_ID, "G", "G Stars", 10.0, 1)]))
        self.assertEqual(self._save_with(template="G Narrow").status_code, 200)
        self.assertNotIn("aac", self._stored()["filter_rows"][2])

    def test_a_save_drops_the_block_when_the_filter_changes(self):
        self._post(_request([_figures(G_ID, "G", "G Stars", 10.0, 1)]))
        self.assertEqual(self._save_with(filter="B").status_code, 200)
        self.assertNotIn("aac", self._stored()["filter_rows"][2])

    def test_the_routes_that_write_plans_hold_the_plans_lock(self):
        taken = []

        class Spy:
            def __enter__(self):
                taken.append("in")

            def __exit__(self, *a):
                taken.append("out")

        real = app_module.PLANS_LOCK
        app_module.PLANS_LOCK = Spy()
        self.addCleanup(setattr, app_module, "PLANS_LOCK", real)
        body = app_module.load_plans()["plans"][0]
        calls = [
            lambda: self.client.post("/api/plans/M42/accepted", json=_request([])),
            lambda: self.client.put("/api/plans/M42", json=body),
            lambda: self.client.post("/api/plans/M42/progress", json={"filters": {"Ha": {"acquired_hours": 1.0}}}),
        ]
        for call in calls:
            taken.clear()
            self.assertNotIn(call().status_code, (404, 500))
            self.assertEqual(taken, ["in", "out"])

    def test_an_accepted_write_waits_for_a_holder_of_the_lock(self):
        import threading
        done = threading.Event()
        result = []

        def post():
            result.append(self._post(_request([_figures(HA_ID, "Ha", "Ha", 100.0, 1)])).status_code)
            done.set()

        app_module.PLANS_LOCK.acquire()
        try:
            t = threading.Thread(target=post)
            t.start()
            self.assertFalse(done.wait(0.3), "the write ran while the lock was held")
        finally:
            app_module.PLANS_LOCK.release()
        self.assertTrue(done.wait(5))
        t.join()
        self.assertEqual(result, [200])
        self.assertIn("aac", self._stored()["filter_rows"][0])

    def test_the_host_exposes_its_lock_for_the_extension_to_share(self):
        self.assertIs(app.plans_lock, app_module.PLANS_LOCK)


class TestTargetClass(unittest.TestCase):
    def setUp(self):
        _redirect_state()
        self.client = app.test_client()

    def _save(self, **extra):
        return self.client.post("/api/plans", json={**{"id": "p1", "target": {"name": "M42"}}, **extra})

    def test_a1_deep_saves_and_giant_is_refused(self):
        self.assertEqual(self._save(target_class="deep").status_code, 201)
        self.assertEqual(self.client.get("/api/plans/p1").get_json()["target_class"], "deep")
        r = self._save(target_class="giant")
        self.assertEqual(r.status_code, 400)
        self.assertIn("target_class", r.get_json()["error"])

    def test_a1_each_known_class_saves(self):
        for cls in ("survey", "deep", "pretty"):
            self.assertEqual(self._save(target_class=cls).status_code, 201, cls)

    def test_a1_a_plan_with_none_reads_as_survey(self):
        self.assertEqual(self._save().status_code, 201)
        self.assertEqual(self.client.get("/api/plans/p1").get_json()["target_class"], "survey")
        listed = self.client.get("/api/plans").get_json()["plans"]
        self.assertEqual(listed[0]["target_class"], "survey")
        self.assertNotIn("target_class", app_module.load_plans()["plans"][0])


class TestSharedCases(unittest.TestCase):
    """The ACP half of A3: the desired the writers push, with AAC's floor."""

    def _plan(self, case):
        rows = []
        for i, r in enumerate(case["rows"]):
            row = {k: v for k, v in r.items() if k not in ("ts", "expected")}
            row["id"] = f"row{i}"
            if row.get("aac") is None:
                row.pop("aac", None)
            rows.append(row)
        plan = {"id": "m42", "filter_rows": rows}
        plan["aac"] = {"filed_through_night": case["plan"]["filed_through_night"],
                       "min_total_frames": case["plan"]["min_total_frames"]}
        return plan

    def test_a3_desired_for_matches_every_shared_case(self):
        for case in CASES:
            plan = self._plan(case)
            for src, row in zip(case["rows"], plan["filter_rows"]):
                with self.subTest(case=case["name"], template=row["template"]):
                    self.assertEqual(fr.desired_for(row, plan), src["expected"]["desired"])

    def test_a3_the_ts_export_pushes_the_floored_desired(self):
        _redirect_state()
        app_module.save_gear({"version": 2,
                              "telescopes": [{"id": "tel-1", "name": "T", "focal_length_mm": 600.0, "aperture_mm": 130}],
                              "cameras": [{"id": "cam-1", "name": "C", "pixel_size_um": 3.76, "sensor_px": [6248, 4176],
                                           "filters": {"R": {"default_sub_s": 15, "gain": 100, "offset": 50, "bin": 1}}}]})
        case = next(c for c in CASES if c["name"] == "frame_row_below_the_floor")
        plan = {**self._plan(case), "project_name": "P", "telescope_id": "tel-1", "camera_id": "cam-1",
                "target": {"name": "T", "center_ra_deg": 10.0, "center_dec_deg": 10.0,
                           "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 0}}}
        payload, _ = app_module._build_ts_export([plan], app_module.load_gear())
        desired = [ep["Desired"] for t in payload["projects"][0]["Targets"] for ep in t["ExposurePlans"]]
        self.assertEqual(desired, [10])

    def test_a_row_with_goal_zero_still_pushes_no_plan(self):
        plan = {"filter_rows": [{"id": "a", "filter": "Ha", "template": "Ha", "template_sub_s": 300,
                                 "unit": "fr", "target_frames": 0, "aac": {"accepted_seconds": 0}}],
                "aac": {"filed_through_night": "2026-10-11", "min_total_frames": 10}}
        self.assertEqual(fr.desired_for(plan["filter_rows"][0], plan), 0)

    def test_a_malformed_plan_block_gives_no_floor(self):
        row = {"id": "a", "filter": "Ha", "template": "Ha", "template_sub_s": 300, "unit": "fr",
               "target_frames": 8, "aac": {"accepted_seconds": 0}}
        for block in (None, {}, {"filed_through_night": "2026-10-11", "min_total_frames": 10.0},
                      {"filed_through_night": "soon", "min_total_frames": 10}):
            self.assertEqual(fr.desired_for(row, {"aac": block} if block is not None else {}), 8)


if __name__ == "__main__":
    unittest.main()
