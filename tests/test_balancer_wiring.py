"""The balancer's routes, run and scheduler, scenarios 15, 16, 18 and 22 to 27.

docs/specs/imaging-balancer.md section 5, "Routes and wiring". The decision
itself is covered in test_balancer.py.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import app as app_module  # noqa: E402
import balancer  # noqa: E402
from app import app  # noqa: E402

SYD = ZoneInfo("Australia/Sydney")
NOV10 = datetime(2026, 11, 10, 14, 0, tzinfo=SYD)


def _rows(ha=0.0, oiii=0.0):
    return [
        {"id": "r-ha", "filter": "Ha", "template": "Ha", "template_sub_s": 300,
         "unit": "h", "target_hours": 10.0, "actual_hours": ha},
        {"id": "r-oiii", "filter": "OIII", "template": "OIII", "template_sub_s": 300,
         "unit": "h", "target_hours": 10.0, "actual_hours": oiii},
    ]


def _plan(pid, project, role, priority, **extra):
    p = {"id": pid, "project_name": project, "state": "active", "priority": priority,
         "balance_role": role, "minimum_time_min": 0, "filter_rows": _rows(),
         "target": {"name": project, "center_ra_deg": 10.0, "center_dec_deg": 41.0,
                    "rotation_deg": 0, "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 15}}}
    p.update(extra)
    return p


def fixture_plans(ha=1.5, oiii=0.0):
    return [
        _plan("g77", "G7.7-3.7", "science", "high"),
        _plan("pna", "PN-A", "survey", "normal"),
        _plan("pnb", "PN-B", "survey", "normal"),
        _plan("ngc300", "NGC 300", "pretty", "low", filter_rows=_rows(ha, oiii), commitments=[
            {"kind": "vote", "month": "2026-11", "goals": {"Ha": 3.0, "OIII": 3.0},
             "baseline": {"Ha": 0.0, "OIII": 0.0}, "set_at": "2026-11-01"}]),
        _plan("m42", "M42", "pretty", "low"),
    ]


class WiringCase(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        patcher = mock.patch.dict(os.environ, {
            "ACP_ACTIVITY_DB": str(self.td / "activity.sqlite"),
            "ACP_ACTIVITY_TZ": "Australia/Sydney",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("ACP_ACTIVITY", "ACP_ACTIVITY_KEEP_NIGHTS", "BALANCER_PATH"):
            os.environ.pop(name, None)
        activity.close()
        self.addCleanup(activity.close)
        self._saved = {n: getattr(app_module, n) for n in (
            "PLANS_PATH", "GEAR_PATH", "DESTINATIONS_PATH", "MANIFEST_PATH")}
        self.addCleanup(self._restore)
        app_module.PLANS_PATH = self.td / "plans.json"
        app_module.GEAR_PATH = self.td / "gear.json"
        app_module.DESTINATIONS_PATH = self.td / "destinations.json"
        app_module.MANIFEST_PATH = self.td / "manifest.json"
        self._reset_caches()
        app_module._visibility_cache.clear()
        self.addCleanup(app_module._visibility_cache.clear)
        # Every target is in season unless a test says otherwise, and no test
        # waits on astropy's Earth orientation download.
        self._in_season = self._visibility([6.0] * 12)
        self._in_season.start()
        self.addCleanup(self._in_season.stop)
        self.client = app.test_client()
        self.balancer_path = self.td / "balancer.json"

    def _restore(self):
        for n, v in self._saved.items():
            setattr(app_module, n, v)
        self._reset_caches()

    def _reset_caches(self):
        for name in ("_plans_cache", "_gear_cache", "_destinations_cache", "_manifest_cache",
                     "_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime",
                     "_manifest_cache_mtime"):
            setattr(app_module, name, None)

    # -- helpers -----------------------------------------------------------

    def _visibility(self, hours_by_month):
        """A stand-in for compute_year_visibility: the same hours every year."""
        def fake(targets, **kw):
            return {t["target_id"]: [{"month": m, "label": "fair", "peak_alt_deg": 50.0,
                                      "hours_above_min": hours_by_month[m - 1]} for m in range(1, 13)]
                    for t in targets}
        return mock.patch.object(app_module, "compute_year_visibility", side_effect=fake)

    def save(self, plans):
        app_module.save_plans({"version": 1, "settings_migrated": 1, "plans": plans})

    def stored(self):
        return {p["id"]: p for p in app_module.load_plans()["plans"]}

    def set_mode(self, mode, **more):
        state = balancer.load_state(self.balancer_path)
        state["settings"].update({"mode": mode, **more})
        balancer.save_state(self.balancer_path, state)

    def report(self, plan_id, night, hours):
        activity.record(type="progress_report", key=f"progress_report:{plan_id}:{night}",
                        night=night, title=f"{plan_id} progress", refs={"plan_id": plan_id},
                        data={"filters": {"Ha": {"hours": hours, "goal": 10, "delta_h": hours}}})

    def november_hours(self):
        for pid, h in (("g77", 6), ("pna", 4), ("pnb", 4), ("ngc300", 1.5)):
            self.report(pid, "2026-11-09", h)

    def events(self, etype):
        body = self.client.get("/api/activity?nights=30").get_json()
        return [e for n in body["nights"] for e in n["events"] if e["type"] == etype]


class RunAgainstFiles(WiringCase):
    def test_15_dry_run_leaves_plans_json_byte_identical(self):
        self.save(fixture_plans())
        self.november_hours()
        before = app_module.PLANS_PATH.read_bytes()
        out = app_module.run_balancer("manual", NOV10)
        self.assertTrue(out["ran"])
        self.assertEqual(app_module.PLANS_PATH.read_bytes(), before)
        ev = self.events("balancer_run")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["title"], "Balancer (dry run): would change 4 projects")
        self.assertEqual(len(ev[0]["data"]["changes"]), 4)

    def test_15b_dry_run_remembers_a_met_commitment_without_writing_met_at(self):
        self.save(fixture_plans(ha=3.0, oiii=3.2))
        self.november_hours()
        app_module.run_balancer("manual", NOV10)
        state = balancer.load_state(self.balancer_path)
        self.assertEqual(list(state["memory"]["met_seen"]), ["ngc300:2026-11"])
        self.assertNotIn("met_at", self.stored()["ngc300"]["commitments"][0])

    def test_26_live_run_saves_once_and_records_no_project_settings_events(self):
        self.save(fixture_plans())
        self.november_hours()
        self.set_mode("live")
        saves = []
        real = app_module.save_plans
        with mock.patch.object(app_module, "save_plans", side_effect=lambda d: (saves.append(1), real(d))[1]):
            app_module.run_balancer("manual", NOV10)
        self.assertEqual(len(saves), 1)
        self.assertEqual(self.events("project_settings"), [])
        stored = self.stored()
        self.assertEqual([stored[i]["priority"] for i in ("g77", "pna", "pnb", "ngc300", "m42")],
                         ["normal", "low", "low", "high", "low"])
        note = stored["ngc300"]["balancer_note"]
        self.assertEqual(note["rule"], 2)
        self.assertTrue(note["text"].startswith("Balancer set this to High on 10 Nov: "))
        payload, _ = app_module._build_ts_export(list(stored.values()), app_module.load_gear())
        priorities = {p["Name"]: p["Priority"] for p in payload["projects"]}
        self.assertEqual(priorities["NGC 300"], "High")
        self.assertEqual(priorities["G7.7-3.7"], "Normal")
        self.assertEqual(priorities["PN-A"], "Low")

    def test_live_run_lapses_an_out_of_season_winner_and_reports_it_once(self):
        self.save(fixture_plans())
        self.november_hours()
        self.set_mode("live")
        app_module._visibility_cache.clear()
        with self._visibility([6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 0.2, 0.2]):
            out = app_module.run_balancer("manual", NOV10)
            again = app_module.run_balancer("manual", datetime(2026, 11, 11, 14, 0, tzinfo=SYD))
        c = self.stored()["ngc300"]["commitments"][0]
        self.assertEqual((c["lapsed_at"], c["lapsed_reason"]), ("2026-11-10", "out of season"))
        self.assertEqual(len(out["lapsed_new"]), 1)
        self.assertEqual(again["lapsed_new"], [])
        ev = self.events("balancer_run")
        self.assertEqual(len(ev), 2)
        self.assertEqual(sum("lapsed" in e["detail"] for e in ev), 1)
        self.assertIn("NGC 300's November vote goal lapsed: out of season with 1.5 of 6 h done.",
                      "\n".join(e["detail"] for e in ev))
        self.assertEqual(self.stored()["ngc300"]["priority"], "normal")

    def test_dry_run_notes_the_lapse_in_memory_and_writes_no_plans(self):
        self.save(fixture_plans())
        self.november_hours()
        before = app_module.PLANS_PATH.read_bytes()
        app_module._visibility_cache.clear()
        with self._visibility([0.0] * 12):
            app_module.run_balancer("manual", NOV10)
        self.assertEqual(app_module.PLANS_PATH.read_bytes(), before)
        state = balancer.load_state(self.balancer_path)
        self.assertEqual(state["memory"]["lapsed_seen"], {"ngc300:2026-11": "2026-11-01"})

    def test_in_season_but_behind_does_not_lapse(self):
        self.save(fixture_plans())
        self.november_hours()
        self.set_mode("live")
        app_module._visibility_cache.clear()
        with self._visibility([6.0] * 12):
            out = app_module.run_balancer("manual", NOV10)
        self.assertEqual(out["lapsed_new"], [])
        self.assertNotIn("lapsed_at", self.stored()["ngc300"]["commitments"][0])

    def test_without_a_visibility_result_a_quiet_plan_lapses_after_21_nights(self):
        plans = fixture_plans()
        plans[3]["commitments"][0]["set_at"] = "2026-10-01"
        self.save(plans)
        self.report("g77", "2026-11-09", 6)
        self.set_mode("live")
        app_module._visibility_cache.clear()
        with mock.patch.object(app_module, "compute_year_visibility", side_effect=RuntimeError("no iers")):
            out = app_module.run_balancer("manual", datetime(2026, 11, 25, 14, 0, tzinfo=SYD))
        self.assertEqual(len(out["lapsed_new"]), 1)
        c = self.stored()["ngc300"]["commitments"][0]
        self.assertEqual(c["lapsed_reason"], "no hours in the last 21 nights")

    def test_fallback_leaves_a_held_plan_alone(self):
        plans = fixture_plans()
        plans[3]["commitments"][0]["set_at"] = "2026-10-01"
        plans[3]["balance_hold"] = True
        self.save(plans)
        self.set_mode("live")
        app_module._visibility_cache.clear()
        with mock.patch.object(app_module, "compute_year_visibility", side_effect=RuntimeError("no iers")):
            out = app_module.run_balancer("manual", datetime(2026, 11, 25, 14, 0, tzinfo=SYD))
        self.assertEqual(out["lapsed_new"], [])

    def test_adding_the_vote_win_again_replaces_a_lapsed_commitment(self):
        plans = fixture_plans()
        plans[3]["vote_goals"] = {"Ha": 3.0}
        plans[3]["commitments"][0].update({"lapsed_at": "2026-11-10", "lapsed_reason": "out of season"})
        self.save(plans)
        r = self.client.post("/api/plans/ngc300/commitments", json={"month": "2026-11"})
        self.assertEqual(r.status_code, 201)
        commitments = self.stored()["ngc300"]["commitments"]
        self.assertEqual(len(commitments), 1)
        self.assertNotIn("lapsed_at", commitments[0])
        r2 = self.client.post("/api/plans/ngc300/commitments", json={"month": "2026-11"})
        self.assertEqual(r2.status_code, 400)

    def test_16_two_runs_one_night_one_event(self):
        self.save(fixture_plans())
        self.november_hours()
        self.set_mode("live")
        app_module.run_balancer("manual", NOV10)
        out = app_module.run_balancer("manual", NOV10)
        self.assertEqual(out["changes"], [])
        ev = self.events("balancer_run")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["title"], "Balancer: no changes")

    def test_18_activity_off_stops_the_run(self):
        self.save(fixture_plans())
        self.set_mode("live")
        before = app_module.PLANS_PATH.read_bytes()
        with mock.patch.dict(os.environ, {"ACP_ACTIVITY": "off"}):
            out = app_module.run_balancer("manual", NOV10)
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], "activity_off")
        self.assertEqual(app_module.PLANS_PATH.read_bytes(), before)

    def test_a_failed_run_changes_nothing_and_counts_as_skipped(self):
        self.save(fixture_plans())
        self.set_mode("live")
        before = app_module.PLANS_PATH.read_bytes()
        with mock.patch.object(balancer, "decide", side_effect=RuntimeError("boom")):
            out = app_module.run_balancer("schedule", NOV10)
        self.assertEqual(out["reason"], "error")
        self.assertEqual(app_module.PLANS_PATH.read_bytes(), before)
        self.assertEqual(balancer.load_state(self.balancer_path)["memory"]["skipped_runs"], 1)

    def test_three_skipped_runs_open_the_blind_item_and_a_good_run_resolves_it(self):
        self.save(fixture_plans())
        self.set_mode("live")
        with mock.patch.object(balancer, "decide", side_effect=RuntimeError("boom")):
            for day in (10, 11, 12):
                app_module.run_balancer("schedule", datetime(2026, 11, day, 14, 0, tzinfo=SYD))
        open_items = self.client.get("/api/activity/summary").get_json()
        self.assertEqual(len(self.events("balancer_alert")), 1)
        self.assertEqual(open_items["needs_you"], 1)
        app_module.run_balancer("manual", datetime(2026, 11, 13, 14, 0, tzinfo=SYD))
        self.assertEqual(self.client.get("/api/activity/summary").get_json()["needs_you"], 0)

    def test_off_mode_refuses_run_now_but_preview_works(self):
        self.save(fixture_plans())
        self.set_mode("off")
        self.assertEqual(self.client.post("/api/balancer/run").status_code, 409)
        self.assertEqual(self.client.post("/api/balancer/preview").status_code, 200)

    def test_short_item_opens_and_resolves_in_activity(self):
        self.save(fixture_plans(ha=3.0, oiii=2.9))
        self.november_hours()
        night24 = datetime(2026, 11, 24, 14, 0, tzinfo=SYD)
        app_module.run_balancer("manual", night24)
        summary = self.client.get("/api/activity/summary").get_json()
        self.assertEqual(summary["needs_you"], 1)
        plans = app_module.load_plans()
        plans["plans"][3]["filter_rows"] = _rows(3.0, 3.0)
        app_module.save_plans(plans)
        app_module.run_balancer("manual", datetime(2026, 11, 25, 14, 0, tzinfo=SYD))
        self.assertEqual(self.client.get("/api/activity/summary").get_json()["needs_you"], 0)


class FollowUp1(WiringCase):
    def test_01_commitment_on_an_unnamed_plan_touches_only_that_plan(self):
        def unnamed(pid, target):
            p = _plan(pid, "", None, "low", public_section="vote",
                      vote_goals={"Ha": 3.0}, vote_baseline={"Ha": 0.0}, filter_rows=_rows(1.0, 0))
            del p["project_name"]
            p["target"] = {**p["target"], "name": target}
            return p
        self.save([unnamed("a", "Vela"), unnamed("b", "Carina"), _plan("c", "M42", None, "low")])
        self.assertEqual(self.client.post("/api/plans/a/commitments", json={"month": "2026-11"}).status_code, 201)
        stored = self.stored()
        self.assertEqual(stored["a"]["balance_role"], "pretty")
        self.assertIsNone(stored["b"].get("balance_role"))
        self.assertIsNone(stored["c"].get("balance_role"))

    def test_01_a_live_run_changes_unnamed_plans_one_at_a_time(self):
        def unnamed(pid, target, role, priority):
            p = _plan(pid, "", role, priority)
            del p["project_name"]
            p["target"] = {**p["target"], "name": target}
            return p
        self.save([unnamed("a", "Vela", "survey", "normal"), unnamed("b", "Carina", "science", "high")])
        self.report("a", "2026-11-09", 5.0)
        self.report("b", "2026-11-09", 1.0)
        self.set_mode("live")
        app_module.run_balancer("manual", NOV10)
        stored = self.stored()
        self.assertEqual((stored["a"]["priority"], stored["b"]["priority"]), ("low", "high"))
        self.assertIn("balancer_note", stored["a"])
        self.assertNotIn("balancer_note", stored["b"])

    def test_03_plan_list_carries_server_progress_with_canonical_names(self):
        plan = _plan("n", "NGC 300", "pretty", "low", filter_rows=[
            {"id": "r", "filter": "HA", "template": "Ha", "template_sub_s": 300, "unit": "h",
             "target_hours": 10.0, "actual_hours": 3.0}], commitments=[
            {"kind": "vote", "month": "2026-11", "goals": {"Ha": 3.0}, "baseline": {"Ha": 0.0}}])
        self.save([plan])
        got = self.client.get("/api/plans/n").get_json()
        prog = got["commitment_progress"]
        self.assertEqual([(p["month"], p["met"], p["goal_h"]) for p in prog], [("2026-11", True, 3.0)])
        listed = self.client.get("/api/plans").get_json()["plans"][0]
        self.assertTrue(listed["commitment_progress"][0]["met"])
        body = {k: v for k, v in got.items() if k != "filter_goals"}
        self.assertEqual(self.client.put("/api/plans/n", json=body).status_code, 200)
        self.assertNotIn("commitment_progress", self.stored()["n"])

    def test_04_run_after_1500_notes_the_next_night_sync(self):
        self.save(fixture_plans())
        self.november_hours()
        self.set_mode("live")
        app_module.run_balancer("manual", datetime(2026, 11, 10, 16, 0, tzinfo=SYD))
        detail = self.events("balancer_run")[0]["detail"]
        self.assertIn("after 15:00", detail)

    def test_05_manual_run_never_blocks_the_scheduled_run(self):
        self.save(fixture_plans())
        self.set_mode("dry_run")
        app_module.run_balancer("manual", datetime(2026, 11, 10, 13, 0, tzinfo=SYD))
        state = balancer.load_state(self.balancer_path)
        self.assertIsNone(state["memory"]["last_run_night"])
        self.assertIsNone(state["memory"]["last_attempt_night"])
        self.assertTrue(app_module.balancer_tick(datetime(2026, 11, 10, 14, 0, tzinfo=SYD)))

    def test_05_a_failed_manual_run_does_not_count_as_skipped(self):
        self.save(fixture_plans())
        self.set_mode("live")
        with mock.patch.object(balancer, "decide", side_effect=RuntimeError("boom")):
            out = app_module.run_balancer("manual", NOV10)
        self.assertEqual(out["reason"], "error")
        mem = balancer.load_state(self.balancer_path)["memory"]
        self.assertEqual((mem["skipped_runs"], mem["last_attempt_night"]), (0, None))
        self.assertTrue(app_module.balancer_tick(NOV10))

    def test_06_a_save_failure_does_not_500_or_retry_every_poll(self):
        self.save(fixture_plans())
        self.set_mode("dry_run")
        with mock.patch.object(balancer, "save_state", side_effect=OSError("disk full")):
            self.assertTrue(app_module.balancer_tick(NOV10))
            self.assertFalse(app_module.balancer_tick(NOV10))
            out = self.client.post("/api/balancer/run")
        self.assertEqual(out.status_code, 200)
        self.assertTrue(any("balancer.json" in w for w in out.get_json()["warnings"]))

    def test_06_an_activity_failure_does_not_fail_the_run(self):
        self.save(fixture_plans())
        self.set_mode("dry_run")
        with mock.patch.object(activity, "record", side_effect=RuntimeError("db locked")):
            out = app_module.run_balancer("manual", NOV10)
        self.assertTrue(out["ran"])
        self.assertTrue(any("Activity" in w for w in out["warnings"]))


class Routes(WiringCase):
    def test_22_project_settings_takes_role_and_hold(self):
        self.save(fixture_plans() + [_plan("pna2", "PN-A", None, "normal")])
        r = self.client.put("/api/projects/PN-A/settings", json={"balance_role": "survey", "balance_hold": True})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        stored = self.stored()
        for pid in ("pna", "pna2"):
            self.assertEqual((stored[pid]["balance_role"], stored[pid]["balance_hold"]), ("survey", True))
        self.assertEqual(self.client.put("/api/projects/PN-A/settings",
                                         json={"balance_role": "boss"}).status_code, 400)
        self.assertEqual(self.client.put("/api/projects/PN-A/settings",
                                         json={"balance_hold": "yes"}).status_code, 400)
        r = self.client.put("/api/projects/PN-A/settings", json={"balance_role": None})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.stored()["pna"]["balance_role"])

    def test_22b_a_project_with_a_commitment_must_stay_pretty(self):
        self.save(fixture_plans())
        r = self.client.put("/api/projects/NGC 300/settings", json={"balance_role": "survey"})
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/projects/NGC 300/settings", json={"balance_role": "pretty"})
        self.assertEqual(r.status_code, 200)

    def _vote_plan(self, pid, project, **extra):
        return _plan(pid, project, None, "low", public_section="vote",
                     vote_goals={"Ha": 3.0, "OIII": 3.0}, vote_baseline={"Ha": 1.0, "OIII": 0.0},
                     filter_rows=_rows(2.0, 0.5), **extra)

    def test_23b_vote_winner_role_reaches_unnamed_plans_sharing_a_target(self):
        a = self._vote_plan("a", "M42")
        b = _plan("b", "M42", "survey", "normal")
        for p in (a, b):
            p.pop("project_name")
        self.save([a, b])
        r = self.client.post("/api/plans/a/commitments", json={"month": "2026-11"})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        stored = self.stored()
        self.assertEqual((stored["a"]["balance_role"], stored["b"]["balance_role"]), ("pretty", "pretty"))

    def test_23_commitment_routes(self):
        self.save([self._vote_plan("a", "NGC 300"), self._vote_plan("b", "NGC 1365"),
                   _plan("c", "NGC 300", None, "low")])
        r = self.client.post("/api/plans/a/commitments", json={"month": "2026-11"})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        stored = self.stored()
        c = stored["a"]["commitments"][0]
        self.assertEqual((c["kind"], c["month"], c["goals"], c["baseline"]),
                         ("vote", "2026-11", {"Ha": 3.0, "OIII": 3.0}, {"Ha": 1.0, "OIII": 0.0}))
        self.assertTrue(c["set_at"])
        self.assertEqual(stored["a"]["balance_role"], "pretty")
        self.assertEqual(stored["c"]["balance_role"], "pretty")
        self.assertEqual(stored["b"]["public_section"], "vote")
        self.assertEqual(self.client.post("/api/plans/a/commitments", json={"month": "2026-11"}).status_code, 400)
        self.assertEqual(self.client.post("/api/plans/a/commitments", json={"month": "11/2026"}).status_code, 400)
        self.assertEqual(self.client.post("/api/plans/c/commitments", json={"month": "2026-11"}).status_code, 400)
        self.assertEqual(self.client.post("/api/plans/zzz/commitments", json={"month": "2026-11"}).status_code, 404)
        self.client.post("/api/plans/a/commitments", json={"month": "2026-12"})
        self.assertEqual(len(self.stored()["a"]["commitments"]), 2)
        r = self.client.delete("/api/plans/a/commitments/2026-11")
        self.assertEqual(r.status_code, 200)
        left = self.stored()["a"]["commitments"]
        self.assertEqual([x["month"] for x in left], ["2026-12"])
        self.assertEqual(self.client.delete("/api/plans/a/commitments/2026-11").status_code, 404)

    def test_23b_a_plan_with_no_vote_baseline_takes_the_hours_it_has_now(self):
        plan = self._vote_plan("a", "NGC 300")
        del plan["vote_baseline"]
        plan["public_section"] = "current"
        self.save([plan])
        self.client.post("/api/plans/a/commitments", json={"month": "2026-11"})
        self.assertEqual(self.stored()["a"]["commitments"][0]["baseline"], {"Ha": 2.0, "OIII": 0.5})

    def test_other_vote_plans_leave_the_vote_section_when_cleared(self):
        self.save([self._vote_plan("a", "NGC 300"), self._vote_plan("b", "NGC 1365"),
                   self._vote_plan("c", "M83"), _plan("d", "M42", None, "low")])
        r = self.client.post("/api/plans/a/commitments", json={"month": "2026-11", "clear_other_votes": True})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["_cleared_other_votes"], 2)
        stored = self.stored()
        self.assertEqual(stored["a"]["public_section"], "vote")
        for pid in ("b", "c"):
            self.assertEqual(stored[pid]["public_section"], "current")
            self.assertNotIn("vote_baseline", stored[pid])
            self.assertEqual(stored[pid]["vote_goals"], {"Ha": 3.0, "OIII": 3.0})
        self.assertEqual(stored["d"].get("public_section"), None)

    def test_24_editor_save_keeps_the_balancer_fields(self):
        self.save(fixture_plans())
        self.client.put("/api/projects/NGC 300/settings", json={"balance_hold": True})
        stored = self.stored()["ngc300"]
        stored["balancer_note"] = {"rule": 2, "at": "2026-11-10", "text": "kept"}
        plans = app_module.load_plans()
        plans["plans"][3] = stored
        app_module.save_plans(plans)
        body = {k: v for k, v in self.stored()["ngc300"].items()
                if k not in ("commitments", "balancer_note", "balance_role", "balance_hold")}
        body["priority"] = "normal"
        r = self.client.put("/api/plans/ngc300", json=body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        after = self.stored()["ngc300"]
        self.assertEqual(after["commitments"], stored["commitments"])
        self.assertEqual(after["balancer_note"], stored["balancer_note"])
        self.assertEqual((after["balance_role"], after["balance_hold"]), ("pretty", True))
        self.assertEqual(after["priority"], "normal")
        body["commitments"] = [{"kind": "vote", "month": "2027-01", "goals": {"Ha": 1}}]
        body["balancer_note"] = {"rule": 9, "at": "x", "text": "forged"}
        self.client.put("/api/plans/ngc300", json=body)
        after = self.stored()["ngc300"]
        self.assertEqual(after["commitments"], stored["commitments"])
        self.assertEqual(after["balancer_note"], stored["balancer_note"])

    def test_25_leaving_the_vote_clears_vote_baseline_not_the_commitments(self):
        self.save([self._vote_plan("a", "NGC 300")])
        self.client.post("/api/plans/a/commitments", json={"month": "2026-11"})
        body = dict(self.stored()["a"])
        body["public_section"] = "current"
        r = self.client.put("/api/plans/a", json=body)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        after = self.stored()["a"]
        self.assertNotIn("vote_baseline", after)
        self.assertEqual(after["commitments"][0]["baseline"], {"Ha": 1.0, "OIII": 0.0})

    def test_settings_route(self):
        r = self.client.put("/api/balancer/settings", json={"mode": "live", "tolerance": 5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/balancer").get_json()["settings"]["mode"], "live")
        self.assertEqual(self.client.put("/api/balancer/settings", json={"mode": "x"}).status_code, 400)
        self.assertEqual(balancer.load_state(self.balancer_path)["settings"]["tolerance"], 5)

    def test_tag_from_class_tags_survey_and_pretty_only(self):
        self.save([_plan("s", "PN-X", None, "normal", target_class="survey"),
                   _plan("p", "M42", None, "low", target_class="pretty"),
                   _plan("d", "Fesen", None, "high", target_class="deep"),
                   _plan("t", "Tagged", "science", "high", target_class="survey")])
        r = self.client.post("/api/balancer/tag-from-class")
        self.assertEqual(r.get_json()["tagged"], ["M42", "PN-X"])
        stored = self.stored()
        self.assertEqual((stored["s"]["balance_role"], stored["p"]["balance_role"]), ("survey", "pretty"))
        self.assertIsNone(stored["d"].get("balance_role"))
        self.assertEqual(stored["t"]["balance_role"], "science")


class Scheduler(WiringCase):
    def test_27_runs_at_1400_and_not_at_1359_and_not_twice(self):
        self.save(fixture_plans())
        self.set_mode("dry_run")
        self.assertFalse(app_module.balancer_tick(datetime(2026, 11, 10, 13, 59, tzinfo=SYD)))
        self.assertEqual(self.events("balancer_run"), [])
        self.assertTrue(app_module.balancer_tick(datetime(2026, 11, 10, 14, 0, tzinfo=SYD)))
        self.assertFalse(app_module.balancer_tick(datetime(2026, 11, 10, 14, 1, tzinfo=SYD)))
        self.assertEqual(len(self.events("balancer_run")), 1)
        self.assertTrue(app_module.balancer_tick(datetime(2026, 11, 11, 14, 0, tzinfo=SYD)))

    def test_runs_on_start_when_late_but_not_after_2300(self):
        self.save(fixture_plans())
        self.assertTrue(app_module.balancer_tick(datetime(2026, 11, 10, 19, 30, tzinfo=SYD)))
        self.set_mode("dry_run")
        state = balancer.load_state(self.balancer_path)
        state["memory"]["last_run_night"] = None
        state["memory"]["last_attempt_night"] = None
        balancer.save_state(self.balancer_path, state)
        self.assertFalse(app_module.balancer_tick(datetime(2026, 11, 10, 23, 5, tzinfo=SYD)))

    def test_off_mode_never_runs(self):
        self.save(fixture_plans())
        self.set_mode("off")
        self.assertFalse(app_module.balancer_tick(datetime(2026, 11, 10, 14, 0, tzinfo=SYD)))

    def test_the_thread_does_not_start_under_the_test_suite(self):
        self.assertFalse(app_module.start_balancer_scheduler())


if __name__ == "__main__":
    unittest.main()
