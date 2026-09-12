"""What a session's stage folders mean: one is the capture, and acceptance is
what is left after the rejected folders come out.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def _b(stage, hours=1.0, n=10, bucket=None, sr="/s", filt="Ha"):
    return {"_stage": stage, "total_hours": hours, "n_subs": n,
            "bucket": bucket if bucket is not None else f"{sr}/{stage}",
            "filter": filt, "_session_root": sr}


class TestCaptureSelection(unittest.TestCase):
    def test_originals_beat_every_later_stage(self):
        r = bam.resolve_session_stages([_b("og"), _b("calibrated"), _b("registered")])
        self.assertEqual([x["_stage"] for x in r["captured"]], ["og"])

    def test_root_is_preferred_over_calibrated(self):
        r = bam.resolve_session_stages([_b("calibrated"), _b("root")])
        self.assertEqual([x["_stage"] for x in r["captured"]], ["root"])

    def test_calibrated_is_preferred_over_registered(self):
        r = bam.resolve_session_stages([_b("registered"), _b("calibrated")])
        self.assertEqual([x["_stage"] for x in r["captured"]], ["calibrated"])

    def test_master_is_the_last_resort_capture(self):
        r = bam.resolve_session_stages([_b("master")])
        self.assertEqual([x["_stage"] for x in r["captured"]], ["master"])

    def test_every_block_at_the_winning_stage_is_captured(self):
        r = bam.resolve_session_stages([_b("og", bucket="/s/og/a"),
                                        _b("og", bucket="/s/og/b"),
                                        _b("registered")])
        self.assertEqual(len(r["captured"]), 2)

    def test_the_preference_order_is_the_one_the_contract_names(self):
        self.assertEqual(bam.CAPTURE_STAGE_PREFERENCE,
                         ("og", "root", "calibrated", "registered", "master"))

    def test_there_is_no_stage_based_acceptance_any_more(self):
        self.assertFalse(hasattr(bam, "ACCEPTED_STAGE_PREFERENCE"))


class TestAcceptance(unittest.TestCase):
    def test_acceptance_equals_capture_when_nothing_was_rejected(self):
        r = bam.resolve_session_stages([_b("og")])
        self.assertEqual(len(r["accepted"]), 1)
        self.assertIs(r["accepted"][0], r["captured"][0])
        self.assertEqual(r["accepted_basis"], "no_rejects")

    def test_a_rejected_folder_is_captured_but_not_accepted(self):
        r = bam.resolve_session_stages([_b("og", bucket="/s/og/good"),
                                        _b("og", bucket="/s/og/rejected")])
        self.assertEqual(len(r["captured"]), 2)
        self.assertEqual([x["bucket"] for x in r["accepted"]], ["/s/og/good"])
        self.assertEqual(r["accepted_basis"], "rejected_folders")

    def test_a_bad_folder_counts_the_same_way(self):
        r = bam.resolve_session_stages([_b("og", bucket="/s/og/Bad"),
                                        _b("og", bucket="/s/og/keep")])
        self.assertEqual([x["bucket"] for x in r["accepted"]], ["/s/og/keep"])

    def test_accepted_is_a_list_not_none_when_there_is_no_capture(self):
        r = bam.resolve_session_stages([_b("starless")])
        self.assertEqual(r["accepted"], [])
        self.assertIsNone(r["accepted_basis"])


class TestDerivatives(unittest.TestCase):
    def test_starless_and_stars_are_dropped_whatever_else_is_present(self):
        r = bam.resolve_session_stages([_b("og"), _b("starless"), _b("stars")])
        self.assertEqual({x["stage"] for x in r["dropped"]}, {"starless", "stars"})

    def test_og_is_out_of_the_derivative_set(self):
        self.assertEqual(bam.DERIVATIVE_STAGES, {"starless", "stars"})

    def test_og_is_still_a_wbpp_session_signature(self):
        self.assertIn("og", bam.WBPP_SIGNATURE_STAGES)

    def test_a_session_of_og_and_starless_now_captures_the_og(self):
        r = bam.resolve_session_stages([_b("og", 4.5, 45), _b("starless", 4.5, 45)])
        self.assertEqual([x["_stage"] for x in r["captured"]], ["og"])


class TestDroppedRows(unittest.TestCase):
    def test_a_row_has_exactly_the_seven_keys_the_log_renders(self):
        r = bam.resolve_session_stages([_b("og"), _b("stars")])
        self.assertEqual(set(r["dropped"][0]),
                         {"session_root", "filter", "bucket", "stage", "n_subs",
                          "hours", "action"})

    def test_every_action_is_from_the_closed_vocabulary(self):
        r = bam.resolve_session_stages([_b("og"), _b("calibrated"), _b("stars")])
        allowed = {"dropped (derivative product)",
                   "not captured (wider stage og present)",
                   "not captured (integrated master file present)"}
        for row in r["dropped"]:
            self.assertIn(row["action"], allowed)

    def test_a_derivative_says_so_and_a_loser_names_the_winning_stage(self):
        r = bam.resolve_session_stages([_b("og"), _b("calibrated"), _b("stars")])
        by_stage = {x["stage"]: x["action"] for x in r["dropped"]}
        self.assertEqual(by_stage["stars"], "dropped (derivative product)")
        self.assertEqual(by_stage["calibrated"],
                         "not captured (wider stage og present)")

    def test_a_dropped_row_names_the_session_and_filter_it_came_from(self):
        r = bam.resolve_session_stages([_b("og", sr="/S/n1", filt="OIII"),
                                        _b("stars", sr="/S/n1", filt="OIII")])
        row = [d for d in r["dropped"] if d["stage"] == "stars"][0]
        self.assertEqual(row["session_root"], "/S/n1")
        self.assertEqual(row["filter"], "OIII")

    def test_hours_are_rounded_the_way_the_existing_log_rounds_them(self):
        r = bam.resolve_session_stages([_b("og", 4.5, 45), _b("stars", 1.0 / 3.0, 4)])
        row = [d for d in r["dropped"] if d["stage"] == "stars"][0]
        self.assertEqual(row["hours"], round(1.0 / 3.0, 3))


class TestPurityAndEdges(unittest.TestCase):
    def test_the_input_blocks_are_not_mutated(self):
        blocks = [_b("og"), _b("stars")]
        before = [dict(b) for b in blocks]
        bam.resolve_session_stages(blocks)
        self.assertEqual([dict(b) for b in blocks], before)

    def test_empty_input_is_an_empty_answer(self):
        r = bam.resolve_session_stages([])
        self.assertEqual((r["captured"], r["accepted"], r["dropped"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
