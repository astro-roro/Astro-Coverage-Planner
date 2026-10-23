"""Review of the 23 October 2026 merges: tests that show the findings.

Each test states what Rohan's rules or the spec say should happen. They came
from the review of that day's merges and pass now the findings are fixed.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import balancer  # noqa: E402
from shooting_publish import build_shooting_document  # noqa: E402
from test_balancer import Sim, done, hours  # noqa: E402
from test_shooting_publish import GEAR, _manifest, _plan  # noqa: E402


class PastWinnerStaysLow(unittest.TestCase):
    """Finding: a vote commitment met in November stays "current" in every
    later month, so rule 3 holds that project at low for good. In December
    it is an ordinary pretty project, and pretty is Under and the month is
    calm, so rule 6 says normal (as it does for M42)."""

    def test_november_winner_is_an_ordinary_pretty_project_in_december(self):
        s = Sim()
        ngc = next(p for p in s.plans if p["id"] == "ngc300")
        ngc["commitments"][0]["met_at"] = "2026-11-20"
        res = s.run("2026-12-15", hours(science=20, survey=20, pretty=2),
                    done(ha=3.0, oiii=3.0))
        self.assertFalse(res["urgent"])
        self.assertEqual(res["bands"]["pretty"], "under")
        self.assertEqual(s.changed().get("M42"), ("low", "normal"))
        self.assertEqual(s.changed().get("NGC 300"), ("low", "normal"))

    def test_march_report_does_not_still_talk_about_the_november_vote(self):
        s = Sim()
        ngc = next(p for p in s.plans if p["id"] == "ngc300")
        ngc["commitments"][0]["met_at"] = "2026-11-20"
        res = s.run("2027-03-15", hours(science=20, survey=20, pretty=2),
                    done(ha=3.0, oiii=3.0))
        self.assertNotIn("November vote goal met", res["detail"])


class LivePageMonthUsesContainerClock(unittest.TestCase):
    """Finding: build_shooting_document takes "today" from the container's
    local clock, which is UTC on Brutix (no TZ in the compose file or the
    Dockerfile). The daily publish runs at 07:15 Sydney, which is 20:15 UTC
    the day before, so on 1 November the page still thinks it is 31 October
    and a November vote winner is left off for a day."""

    def test_november_winner_shows_on_the_1_november_publish(self):
        rows = [{"id": "r-ha", "filter": "Ha", "template": "Ha", "template_sub_s": 300,
                 "unit": "h", "target_hours": 10.0, "actual_hours": 1.0}]
        plan = _plan(finished_at=None, filter_rows=rows, commitments=[
            {"kind": "vote", "month": "2026-11", "goals": {"Ha": 4.0}, "baseline": {"Ha": 0.0}}])
        # 07:15 on 1 November in Sydney (AEDT, UTC+11), as a UTC container sees it.
        now = datetime(2026, 10, 31, 20, 15, tzinfo=timezone.utc)
        projects = build_shooting_document([plan], _manifest(), GEAR, now=now)["projects"]
        self.assertEqual([p["status"] for p in projects], ["vote_winner"])


class PastWinnerCannotReenterTheVote(unittest.TestCase):
    """Finding (live page): a met commitment from a past month keeps the plan
    "in play", so a former winner put back in the vote section for a later
    round never shows in the vote section again."""

    def test_former_winner_back_in_the_vote_shows_in_the_vote_section(self):
        rows = [{"id": "r-ha", "filter": "Ha", "template": "Ha", "template_sub_s": 300,
                 "unit": "h", "target_hours": 10.0, "actual_hours": 5.0}]
        plan = _plan(finished_at=None, filter_rows=rows, public_section="vote",
                     vote_goals={"Ha": 3.0}, vote_baseline={"Ha": 5.0}, commitments=[
                         {"kind": "vote", "month": "2026-05", "goals": {"Ha": 4.0},
                          "baseline": {"Ha": 0.0}, "met_at": "2026-05-20"}])
        now = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)
        projects = build_shooting_document([plan], _manifest(), GEAR, now=now)["projects"]
        self.assertEqual([(p["status"], p["section"]) for p in projects], [("vote", "vote")])


if __name__ == "__main__":
    unittest.main()
