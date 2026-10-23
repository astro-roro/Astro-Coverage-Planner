"""The imaging balancer's decision function, scenarios 1 to 21.

docs/specs/imaging-balancer.md section 5. Every scenario starts from the
November 2026 fixture and calls the pure `balancer.decide`, so these run as
plain tables with no Flask and no files.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import balancer  # noqa: E402

PROJECTS = ("G7.7-3.7", "PN-A", "PN-B", "NGC 300", "M42")


def _plan(pid, project, role, priority, **extra):
    p = {"id": pid, "project_name": project, "state": "active", "priority": priority,
         "balance_role": role}
    p.update(extra)
    return p


def fixture_plans():
    return [
        _plan("g77", "G7.7-3.7", "science", "high"),
        _plan("pna", "PN-A", "survey", "normal"),
        _plan("pnb", "PN-B", "survey", "normal"),
        _plan("ngc300", "NGC 300", "pretty", "low", commitments=[
            {"kind": "vote", "month": "2026-11", "goals": {"Ha": 3.0, "OIII": 3.0},
             "baseline": {"Ha": 0.0, "OIII": 0.0}, "set_at": "2026-11-01"}]),
        _plan("m42", "M42", "pretty", "low"),
    ]


def hours(science=0.0, survey=0.0, pretty=0.0):
    """Month hours by role, spread over the fixture's projects."""
    return {"G7.7-3.7": science, "PN-A": survey / 2, "PN-B": survey / 2, "NGC 300": pretty}


def done(ha=0.0, oiii=0.0):
    return {"ngc300": {"Ha": ha, "OIII": oiii}}


class Sim:
    """Carries plans and memory from one run to the next, as the app would."""

    def __init__(self, mode="live", plans=None, **settings):
        self.plans = plans if plans is not None else fixture_plans()
        self.settings = {**balancer.DEFAULT_SETTINGS, "mode": mode, **settings}
        self.memory = copy.deepcopy(balancer.DEFAULT_MEMORY)
        self.last = None

    def run(self, night, role_hours=None, done_h=None, **kw):
        res = balancer.decide(
            self.plans, role_hours if role_hours is not None else hours(),
            done_h if done_h is not None else done(), self.settings, self.memory, night, **kw)
        self.memory = res["memory"]
        if self.settings["mode"] == "live":
            for ch in res["changes"]:
                for p in self.plans:
                    if p["project_name"] == ch["project"]:
                        p["priority"] = ch["to"]
            for m in res["met_new"]:
                for p in self.plans:
                    if p["id"] == m["plan_id"]:
                        for c in p["commitments"]:
                            if c["month"] == m["month"]:
                                c["met_at"] = night
        self.last = res
        return res

    def prio(self, project):
        return next(p["priority"] for p in self.plans if p["project_name"] == project)

    def priorities(self):
        return {name: self.prio(name) for name in PROJECTS}

    def changed(self, res=None):
        res = res or self.last
        return {c["project"]: (c["from"], c["to"]) for c in res["changes"]}


class Scenarios(unittest.TestCase):
    def test_01_start_of_month(self):
        s = Sim()
        res = s.run("2026-11-01")
        self.assertEqual(s.changed(), {
            "NGC 300": ("low", "high"), "G7.7-3.7": ("high", "normal"),
            "PN-A": ("normal", "low"), "PN-B": ("normal", "low")})
        self.assertEqual(s.prio("M42"), "low")
        self.assertTrue(res["urgent"])
        self.assertIn("on pace", res["detail"])
        self.assertNotIn("behind", res["detail"])
        self.assertEqual(set(res["bands"].values()), {"in"})

    def test_02_behind_pace(self):
        s = Sim()
        res = s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        self.assertEqual(s.changed(), {
            "NGC 300": ("low", "high"), "G7.7-3.7": ("high", "normal"),
            "PN-A": ("normal", "low"), "PN-B": ("normal", "low")})
        self.assertEqual(s.prio("M42"), "low")
        self.assertEqual(res["bands"], {"science": "in", "survey": "over", "pretty": "under"})
        first = res["detail"].splitlines()[0]
        self.assertIn("NGC 300", first)
        self.assertIn("1.5 of 6 h", first)
        self.assertIn("behind the pace of 2.4 h", first)

    def test_03_cloudy_week(self):
        s = Sim()
        s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0), newest_report_night="2026-11-09")
        res = s.run("2026-11-17", hours(6, 8, 1.5), done(1.5, 0), newest_report_night="2026-11-09")
        self.assertEqual(res["changes"], [])
        self.assertEqual(res["title"], "Balancer: no changes")
        self.assertTrue(any("8 nights old" in w for w in res["warnings"]))
        self.assertEqual(res["severity"], "warn")

    def test_04_vote_goal_met(self):
        s = Sim()
        s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        s.run("2026-11-17", hours(6, 8, 1.5), done(1.5, 0))
        res = s.run("2026-11-20", hours(10, 9, 9), done(3.0, 3.2))
        self.assertEqual(res["bands"], {"science": "in", "survey": "in", "pretty": "in"})
        self.assertEqual(s.changed(), {
            "NGC 300": ("high", "low"), "G7.7-3.7": ("normal", "high"),
            "PN-A": ("low", "normal"), "PN-B": ("low", "normal")})
        self.assertEqual(s.prio("M42"), "low")
        self.assertEqual(res["met_new"], [{"plan_id": "ngc300", "month": "2026-11", "project": "NGC 300"}])
        self.assertEqual(s.plans[3]["commitments"][0]["met_at"], "2026-11-20")
        again = s.run("2026-11-21", hours(10, 9, 9), done(3.0, 3.2))
        self.assertEqual(again["changes"], [])
        self.assertEqual(s.prio("NGC 300"), "low")

    def _met_on_8th(self):
        s = Sim()
        s.plans[3]["commitments"][0]["met_at"] = "2026-11-08"
        s.plans[3]["priority"] = "low"
        return s

    def test_05_fesen_ahead(self):
        s = self._met_on_8th()
        res = s.run("2026-11-12", hours(14, 4, 7), done(3, 3))
        self.assertEqual(res["bands"], {"science": "over", "survey": "under", "pretty": "in"})
        self.assertEqual(s.changed(), {"G7.7-3.7": ("high", "normal")})
        self.assertEqual(s.prio("PN-A"), "normal")
        self.assertEqual(s.prio("M42"), "low")
        self.assertEqual(s.prio("NGC 300"), "low")
        # Same hours, goal still open: urgent instead.
        o = Sim()
        o.run("2026-11-12", hours(14, 4, 7), done(0, 0))
        self.assertEqual(o.changed(), {
            "G7.7-3.7": ("high", "low"), "PN-A": ("normal", "low"),
            "PN-B": ("normal", "low"), "NGC 300": ("low", "high")})

    def test_06_fesen_ahead_hysteresis(self):
        s = self._met_on_8th()
        s.run("2026-11-12", hours(14, 4, 7), done(3, 3))
        res = s.run("2026-11-13", hours(10, 7.5, 7.5), done(3, 3))
        self.assertEqual(res["bands"]["science"], "over")
        self.assertEqual(s.prio("G7.7-3.7"), "normal")
        res = s.run("2026-11-14", hours(8.5, 8.25, 8.25), done(3, 3))
        self.assertEqual(res["bands"]["science"], "in")
        self.assertEqual(s.prio("G7.7-3.7"), "high")

    def test_07_fesen_behind_calm(self):
        s = self._met_on_8th()
        res = s.run("2026-11-15", hours(2, 9, 9), done(3, 3))
        self.assertEqual(res["bands"]["science"], "under")
        self.assertEqual(s.prio("G7.7-3.7"), "high")
        self.assertNotIn("G7.7-3.7", s.changed())

    def test_08_fesen_behind_vote_on_pace(self):
        s = Sim()
        res = s.run("2026-11-15", hours(2, 9, 9), done(2.0, 2.0))
        self.assertEqual(s.changed(), {
            "G7.7-3.7": ("high", "normal"), "NGC 300": ("low", "high"),
            "PN-A": ("normal", "low"), "PN-B": ("normal", "low")})
        self.assertIn("on the pace", res["detail"].splitlines()[0])

    def test_09_end_of_month(self):
        s = Sim()
        res = s.run("2026-11-30", hours(30, 40, 10), done(2.0, 2.0))
        self.assertEqual(res["bands"], {"science": "in", "survey": "over", "pretty": "under"})
        self.assertEqual(s.changed(), {
            "NGC 300": ("low", "high"), "G7.7-3.7": ("high", "normal"),
            "PN-A": ("normal", "low"), "PN-B": ("normal", "low")})
        self.assertEqual(s.prio("M42"), "low")
        self.assertEqual([i["key"] for i in res["items_open"]],
                         ["balancer_short:ngc300:2026-11"])
        self.assertEqual(res["items_open"][0]["title"],
                         "NGC 300 is short of its November vote goal with a week to go: 4.0 of 6 h")
        self.assertIn("behind", res["detail"])

    def test_10_last_week(self):
        s = Sim()
        res = s.run("2026-11-23", hours(20, 20, 18), done(3.0, 2.9))
        self.assertEqual(res["bands"], {"science": "in", "survey": "in", "pretty": "in"})
        self.assertEqual(s.changed(), {
            "NGC 300": ("low", "high"), "G7.7-3.7": ("high", "normal"),
            "PN-A": ("normal", "low"), "PN-B": ("normal", "low")})
        self.assertEqual(res["items_open"], [])
        res = s.run("2026-11-24", hours(20, 20, 18), done(3.0, 2.9))
        self.assertEqual(res["changes"], [])
        self.assertEqual([i["title"] for i in res["items_open"]], [
            "NGC 300 is short of its November vote goal with a week to go: 5.9 of 6 h"])
        res = s.run("2026-11-25", hours(20, 20, 18), done(3.0, 3.0))
        self.assertEqual(res["items_resolve"], ["balancer_short:ngc300:2026-11"])
        self.assertEqual(s.prio("NGC 300"), "low")

    def _rolled_over(self):
        s = Sim()
        s.run("2026-11-30", hours(30, 40, 10), done(2.0, 2.0))
        return s

    def test_11_month_rollover(self):
        s = self._rolled_over()
        res = s.run("2026-12-01", hours(), done(2.0, 2.0))
        self.assertEqual(res["changes"], [])
        self.assertEqual(s.priorities(), {"G7.7-3.7": "normal", "PN-A": "low", "PN-B": "low",
                                           "NGC 300": "high", "M42": "low"})
        self.assertEqual([i["title"] for i in res["items_open"]],
                         ["NGC 300 missed its November vote goal: 4.0 of 6 h"])
        self.assertEqual(set(res["bands"].values()), {"in"})
        res = s.run("2026-12-06", hours(), done(3.0, 3.0))
        self.assertEqual(sorted(res["items_resolve"]),
                         ["balancer_missed:ngc300:2026-11", "balancer_short:ngc300:2026-11"])
        self.assertEqual(s.prio("NGC 300"), "low")

    def test_12_state_never_moves(self):
        s = Sim()
        for n, h, d in (("2026-11-01", hours(), done()),
                        ("2026-11-10", hours(6, 8, 1.5), done(1.5, 0)),
                        ("2026-11-20", hours(10, 9, 9), done(3, 3.2))):
            s.run(n, h, d)
        self.assertTrue(all(p["state"] == "active" for p in s.plans))
        for res in (s.last,):
            self.assertTrue(all(c["field"] == "priority" for c in res["changes"]))
        t = Sim()
        t.plans[2]["state"] = "inactive"
        t.run("2026-11-23", hours(20, 20, 18), done(3.0, 2.9))
        self.assertEqual(t.changed()["PN-B"], ("normal", "low"))
        self.assertEqual(t.plans[2]["state"], "inactive")

    def test_13_hold(self):
        s = Sim()
        s.plans[4]["balance_hold"] = True
        s.plans[4]["priority"] = "high"
        for night, h, d in (("2026-11-01", hours(), done()),
                            ("2026-11-10", hours(6, 8, 1.5), done(1.5, 0)),
                            ("2026-11-20", hours(10, 9, 9), done(3, 3.2))):
            s.run(night, h, d)
            self.assertEqual(s.prio("M42"), "high")
        # Its hours still count toward pretty.
        res = s.run("2026-11-21", {"G7.7-3.7": 5, "PN-A": 5, "PN-B": 5, "M42": 5}, done(3, 3.2))
        self.assertEqual(res["shares"]["pretty"]["hours"], 5.0)

    def test_14_skips(self):
        for tweak in ("closed", "draft"):
            s = Sim()
            s.plans[1]["state"] = tweak
            s.run("2026-11-01")
            self.assertEqual(s.prio("PN-A"), "normal", tweak)
        s = Sim()
        s.plans.append(_plan("pnc", "PN-C", "survey", "normal"))
        s.plans.append(_plan("pnc2", "PN-C", "science", "normal"))
        res = s.run("2026-11-01")
        self.assertNotIn("PN-C", s.changed())
        self.assertTrue(any("PN-C" in w and "disagree" in w for w in res["warnings"]))
        # NGC 300 on hold with its goal open still makes the month urgent.
        h = Sim()
        h.plans[3]["balance_hold"] = True
        h.run("2026-11-01")
        self.assertEqual(h.prio("NGC 300"), "low")
        self.assertEqual(h.prio("G7.7-3.7"), "normal")
        self.assertTrue(h.last["urgent"])

    def test_15_dry_run_reports_the_same_changes(self):
        live = Sim().run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        s = Sim(mode="dry_run")
        res = s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        self.assertEqual(res["title"], "Balancer (dry run): would change 4 projects")
        self.assertEqual(res["data"]["changes"], live["data"]["changes"])
        self.assertEqual(s.priorities()["NGC 300"], "low")
        s = Sim(mode="dry_run")
        res = s.run("2026-11-20", hours(10, 9, 9), done(3.0, 3.2))
        self.assertEqual(res["memory"]["met_seen"], {"ngc300:2026-11": "2026-11-20"})
        self.assertNotIn("met_at", s.plans[3]["commitments"][0])
        self.assertEqual(len(res["met_new"]), 1)
        again = s.run("2026-11-21", hours(10, 9, 9), done(3.0, 3.2))
        self.assertEqual(again["met_new"], [])

    def test_16_second_run_changes_nothing(self):
        s = Sim()
        s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        res = s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        self.assertEqual(res["changes"], [])
        self.assertEqual(res["title"], "Balancer: no changes")

    def test_17_future_commitment(self):
        s = Sim()
        s.plans[4]["commitments"] = [
            {"kind": "vote", "month": "2026-12", "goals": {"Ha": 4.0}, "baseline": {"Ha": 0.0}}]
        s.plans[4]["done"] = None
        s.run("2026-11-10", hours(6, 8, 1.5), {**done(1.5, 0), "m42": {"Ha": 0.0}})
        self.assertEqual(s.prio("M42"), "low")
        self.assertNotIn("M42", s.changed())

    def test_18_missing_data(self):
        s = Sim()
        s.plans[3]["commitments"][0]["goals"] = {"SII": 3.0}
        res = s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        self.assertFalse(res["urgent"])
        self.assertTrue(any("NGC 300" in w for w in res["warnings"]))
        self.assertEqual(s.prio("G7.7-3.7"), "high")
        self.assertEqual(s.changed(), {
            "PN-A": ("normal", "low"), "PN-B": ("normal", "low"),
            "NGC 300": ("low", "normal"), "M42": ("low", "normal")})

    def test_19_stale_age(self):
        s = Sim()
        res = s.run("2026-11-12", hours(6, 8, 1.5), done(1.5, 0), newest_report_night="2026-11-09")
        self.assertEqual(res["warnings"], [])
        res = s.run("2026-11-13", hours(6, 8, 1.5), done(1.5, 0), newest_report_night="2026-11-09")
        self.assertEqual(res["warnings"], ["last progress report 4 nights old, so hours may be stale."])

    def test_20_two_commitments_on_one_plan(self):
        s = self._rolled_over()
        s.run("2026-12-01", hours(), done(2.0, 2.0))
        s.plans[3]["commitments"].append(
            {"kind": "vote", "month": "2026-12", "goals": {"SII": 4.0}, "baseline": {"SII": 0.0}})
        d = lambda ha, oiii, sii: {"ngc300": {"Ha": ha, "OIII": oiii, "SII": sii}}  # noqa: E731
        s.run("2026-12-02", hours(), d(2, 2, 0))
        self.assertEqual(s.prio("NGC 300"), "high")
        res = s.run("2026-12-06", hours(), d(3, 3, 1))
        self.assertEqual(sorted(res["items_resolve"]),
                         ["balancer_missed:ngc300:2026-11", "balancer_short:ngc300:2026-11"])
        self.assertEqual(s.plans[3]["commitments"][0]["met_at"], "2026-12-06")
        self.assertEqual(s.prio("NGC 300"), "high")
        self.assertTrue(res["urgent"])
        res = s.run("2026-12-15", hours(10, 10, 10), d(3, 3, 4))
        self.assertEqual(s.prio("NGC 300"), "low")
        self.assertFalse(res["urgent"])
        self.assertEqual(s.prio("G7.7-3.7"), "high")
        self.assertEqual(s.prio("PN-A"), "normal")
        self.assertEqual(s.prio("PN-B"), "normal")

    def test_21_activity_retention(self):
        s = Sim()
        res = s.run("2026-11-10", keep_nights=20)
        self.assertIn("Activity keeps only 20 nights, so this month's hours may be undercounted.",
                      res["warnings"])
        self.assertEqual(res["severity"], "warn")
        self.assertEqual(Sim().run("2026-11-10", keep_nights=31)["warnings"], [])
        self.assertEqual(Sim().run("2026-11-10", keep_nights=None)["warnings"], [])


class Review1(unittest.TestCase):
    """Follow-up 1: findings 1, 4 and 6 at the decision level."""

    @staticmethod
    def _unnamed(pid, target, role, priority):
        return {"id": pid, "state": "active", "priority": priority, "balance_role": role,
                "target": {"name": target}}

    def test_project_key_follows_the_ts_export_rule(self):
        self.assertEqual(balancer.project_key({"id": "a", "project_name": " M31 ", "target": {"name": "x"}}), "M31")
        self.assertEqual(balancer.project_key({"id": "a", "target": {"name": " Vela "}}), "Vela")
        self.assertEqual(balancer.project_key({"id": "a", "project_name": "", "target": {}}), "a")

    def test_01_unnamed_plans_are_separate_projects(self):
        plans = [self._unnamed("u1", "Vela", "survey", "normal"),
                 self._unnamed("u2", "Carina", "survey", "normal"),
                 self._unnamed("u3", "Orion", "science", "high")]
        plans.append({"id": "n", "project_name": "NGC 300", "state": "active", "priority": "low",
                      "balance_role": "pretty", "commitments": [
                          {"kind": "vote", "month": "2026-11", "goals": {"Ha": 3.0}, "baseline": {"Ha": 0.0}}]})
        res = balancer.decide(plans, {"Vela": 1, "Carina": 1, "Orion": 1}, {"n": {"Ha": 0.0}},
                              balancer.DEFAULT_SETTINGS, {}, "2026-11-01")
        self.assertEqual({c["project"] for c in res["changes"]}, {"Vela", "Carina", "Orion", "NGC 300"})
        self.assertEqual(res["warnings"], [])

    def test_04_late_run_says_changes_reach_ts_next_night(self):
        late = Sim().run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0), late=True)
        self.assertIn("This run was after 15:00, so its changes reach TS at the next night's sync.",
                      late["detail"].splitlines())
        early = Sim().run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        self.assertNotIn("after 15:00", early["detail"])
        quiet = Sim()
        quiet.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        self.assertNotIn("after 15:00", quiet.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0), late=True)["detail"])

    def test_06_a_commitment_with_a_bad_month_is_skipped_with_a_note(self):
        for bad in (None, 202611, "Nov", "2026-13"):
            s = Sim()
            s.plans[4]["commitments"] = [{"kind": "vote", "month": bad, "goals": {"Ha": 1.0}}]
            res = s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
            self.assertTrue(any("M42" in w and "no usable month" in w for w in res["warnings"]), bad)
            self.assertEqual(s.changed()["NGC 300"], ("low", "high"))

    def test_06_malformed_commitment_containers_do_not_fail_the_run(self):
        s = Sim()
        s.plans[4]["commitments"] = "oops"
        s.plans[3]["commitments"][0]["goals"] = ["Ha"]
        res = s.run("2026-11-10", hours(6, 8, 1.5), done(1.5, 0))
        self.assertTrue(res["title"].startswith("Balancer"))


class Settings(unittest.TestCase):
    def test_validate(self):
        base = balancer.DEFAULT_SETTINGS
        ok, err = balancer.validate_settings({"mode": "live", "tolerance": 5}, base)
        self.assertIsNone(err)
        self.assertEqual((ok["mode"], ok["tolerance"]), ("live", 5))
        for bad in ({"mode": "on"}, {"run_at": "25:00"}, {"finish_by": 0},
                    {"shares": {"science": 50, "survey": 50, "pretty": 50}},
                    {"min_month_hours": -1}, {"nope": 1}):
            self.assertIsNotNone(balancer.validate_settings(bad, base)[1], bad)


class Holes(unittest.TestCase):
    """A role with nothing up gives its spare share to the others, evenly."""

    NIGHT = "2026-10-23"
    OCT = {"G7.7-3.7": 4.5, "PN-A": 9.9, "Dolphin Nebula": 17.2}

    def plans(self):
        return [_plan("g77", "G7.7-3.7", "science", "high"),
                _plan("pna", "PN-A", "survey", "normal"),
                _plan("dolphin", "Dolphin Nebula", "pretty", "high")]

    def run_it(self, month, recent):
        s = {**balancer.DEFAULT_SETTINGS, "mode": "live"}
        return balancer.decide(self.plans(), month, {}, s, copy.deepcopy(balancer.DEFAULT_MEMORY),
                               self.NIGHT, recent_hours=recent)

    def test_october_science_hole_is_split_evenly(self):
        res = self.run_it(self.OCT, {"PN-A": 3.0, "Dolphin Nebula": 5.0})
        self.assertEqual(res["data"]["holes"], ["science"])
        sh = res["shares"]
        self.assertAlmostEqual(sh["science"]["target"], sh["science"]["share"], places=1)
        self.assertEqual(sh["science"]["base_target"], 35.0)
        self.assertAlmostEqual(sh["survey"]["target"], 45.38, places=2)
        self.assertAlmostEqual(sh["pretty"]["target"], 40.38, places=2)
        self.assertAlmostEqual(sum(v["target"] for v in sh.values()), 100.0, places=1)
        self.assertIn("Science has nothing up, so its spare 21 points are split: "
                      "survey 45.4, pretty 40.4.", res["detail"])

    def test_hole_role_keeps_the_hours_it_used(self):
        res = self.run_it(self.OCT, {"PN-A": 3.0, "Dolphin Nebula": 5.0})
        self.assertEqual(res["shares"]["science"]["hours"], 4.5)
        self.assertAlmostEqual(res["shares"]["science"]["share"], 14.24, places=2)
        self.assertEqual(res["bands"]["science"], "in")

    def test_no_hole_changes_nothing(self):
        recent = {"G7.7-3.7": 1.0, "PN-A": 3.0, "Dolphin Nebula": 5.0}
        res = self.run_it(self.OCT, recent)
        self.assertEqual(res["data"]["holes"], [])
        self.assertEqual([res["shares"][r]["target"] for r in balancer.ROLES], [35.0, 35.0, 30.0])
        self.assertNotIn("nothing up", res["detail"])
        plain = self.run_it(self.OCT, None)
        self.assertEqual(res["changes"], plain["changes"])

    def test_two_holes_give_everything_to_the_third(self):
        res = self.run_it(self.OCT, {"Dolphin Nebula": 5.0})
        self.assertEqual(res["data"]["holes"], ["science", "survey"])
        sh = res["shares"]
        self.assertAlmostEqual(sh["pretty"]["target"],
                               100 - sh["science"]["share"] - sh["survey"]["share"], places=1)
        self.assertEqual(sh["pretty"]["band"], "in")

    def test_weather_is_not_a_hole(self):
        res = self.run_it(self.OCT, {})
        self.assertEqual(res["data"]["holes"], [])
        self.assertEqual(res["shares"]["science"]["target"], 35.0)

    def test_without_recent_hours_nothing_changes(self):
        self.assertEqual(self.run_it(self.OCT, None)["data"]["holes"], [])

    def test_a_role_over_its_share_is_not_a_hole(self):
        res = self.run_it({"G7.7-3.7": 20.0, "PN-A": 5.0, "Dolphin Nebula": 5.0},
                          {"PN-A": 1.0, "Dolphin Nebula": 1.0})
        self.assertEqual(res["data"]["holes"], [])


if __name__ == "__main__":
    unittest.main()
