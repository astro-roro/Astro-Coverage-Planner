"""The same light saved in several folders must count once.

A rig exposes one frame at one instant, so two frames of the same rig, filter
and exposure sharing a capture time are the same photons. The maintainer's
archive holds the 40 blue subs of Sh2-27 seven times over, across eleven
folders, every copy carrying the capture time to the millisecond under a
different filename. Content dedup misses them because it keys on filenames.

One shared time could be a coincidence, so the rule compares the whole set
rather than each frame. That also survives the tools that stamp every frame
they write with one time: such a folder is left alone, because its times say
nothing about which frame is which.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import (  # noqa: E402
    capture_times_are_trustworthy,
    collapse_copied_sub_blocks,
    rejected_copy_hours,
)

RIG = {"telescope": "RedCat 51", "camera": "ASI2600MM Pro"}


def _block(bucket, times, *, filt="B", exp=120.0, n=None, **over):
    """A folder-sub block with one capture time per frame unless told otherwise."""
    times = list(times)
    b = {
        "bucket": bucket,
        "filter": filt,
        "exptime": exp,
        "n_subs": n if n is not None else len(times),
        "total_hours": (n if n is not None else len(times)) * exp / 3600.0,
        "_capture_times": frozenset(times),
        "has_wcs": True,
    }
    b.update(RIG)
    b.update(over)
    return b


def _stamps(day, start, count):
    return [f"2023-07-{day:02d}T{start + i // 60:02d}:{i % 60:02d}:11.148" for i in range(count)]


class TestTrustworthyTimes(unittest.TestCase):
    def test_one_distinct_time_per_frame_is_trusted(self):
        self.assertTrue(capture_times_are_trustworthy(_block("/a", _stamps(12, 9, 40))))

    def test_ninety_frames_sharing_one_time_are_not_trusted(self):
        """The real case: a tool stamped every aligned frame identically."""
        b = _block("/a", ["2024-09-01T11:51:08"], n=90)
        self.assertFalse(capture_times_are_trustworthy(b))

    def test_a_block_with_no_times_is_not_trusted(self):
        self.assertFalse(capture_times_are_trustworthy(_block("/a", [], n=5)))


class TestCollapse(unittest.TestCase):
    def test_the_known_case_collapses_to_one_folder(self):
        """Sh2-27 blue: 40 subs, one folder holding all of them, six copies."""
        forty = _stamps(12, 9, 30) + _stamps(13, 9, 10)
        raw = _block("/Images/RAW_SCANNED/RedCat 51/Sh2-27/LIGHTS/B", forty)
        copies = [
            _block("/Images/calibrated/4992e28ff87a", forty[:30]),
            _block("/Images/calibrated/9cae557dea11", forty[30:]),
            _block("/Images/jobs/4992e28ff87a/lights_B", forty[:30]),
            _block("/Images/jobs/9cae557dea11/lights_B", forty[30:]),
            _block("/Library/Sh2_27/calibrated/2023-07-12/B", forty[:30]),
            _block("/Library/Sh2_27/registered/2023-07-12/B", forty[:30]),
        ]
        survivors, dropped = collapse_copied_sub_blocks([raw] + copies)
        self.assertEqual([b["bucket"] for b in survivors], [raw["bucket"]])
        self.assertEqual(len(dropped), 6)
        self.assertAlmostEqual(sum(b["total_hours"] for b in survivors), 40 * 120 / 3600.0)

    def test_a_session_split_across_folders_keeps_the_whole_one(self):
        forty = _stamps(12, 9, 40)
        whole = _block("/whole", forty)
        halves = [_block("/part-a", forty[:20]), _block("/part-b", forty[20:])]
        survivors, _ = collapse_copied_sub_blocks([halves[0], halves[1], whole])
        self.assertEqual([b["bucket"] for b in survivors], ["/whole"])

    def test_two_different_nights_both_survive(self):
        a = _block("/night-one", _stamps(12, 9, 30))
        b = _block("/night-two", _stamps(13, 9, 30))
        survivors, dropped = collapse_copied_sub_blocks([a, b])
        self.assertEqual(len(survivors), 2)
        self.assertEqual(dropped, [])

    def test_one_shared_time_out_of_thirty_is_not_a_copy(self):
        """Containment, not overlap. A single coincidence must not collapse a night."""
        shared = _stamps(12, 9, 1)
        a = _block("/night-one", shared + _stamps(12, 10, 29))
        b = _block("/night-two", shared + _stamps(13, 9, 29))
        survivors, _ = collapse_copied_sub_blocks([a, b])
        self.assertEqual(len(survivors), 2)

    def test_a_different_filter_never_collapses(self):
        times = _stamps(12, 9, 30)
        a = _block("/a", times, filt="B")
        b = _block("/b", times, filt="Ha")
        survivors, _ = collapse_copied_sub_blocks([a, b])
        self.assertEqual(len(survivors), 2)

    def test_a_different_exposure_never_collapses(self):
        times = _stamps(12, 9, 30)
        survivors, _ = collapse_copied_sub_blocks(
            [_block("/a", times, exp=120.0), _block("/b", times, exp=300.0)])
        self.assertEqual(len(survivors), 2)

    def test_a_different_rig_never_collapses(self):
        """Two scopes running at once are two rigs, so concurrency is expected."""
        times = _stamps(12, 9, 30)
        a = _block("/scope-one", times)
        b = _block("/scope-two", times, telescope="190MN")
        survivors, _ = collapse_copied_sub_blocks([a, b])
        self.assertEqual(len(survivors), 2)

    def test_an_unknown_rig_is_left_alone(self):
        """Bare headers make two telescopes look like one, so the rule stands down."""
        times = _stamps(12, 9, 30)
        a = _block("/a", times, telescope=None, camera=None)
        b = _block("/b", times, telescope=None, camera=None)
        survivors, dropped = collapse_copied_sub_blocks([a, b])
        self.assertEqual(len(survivors), 2)
        self.assertEqual(dropped, [])

    def test_half_an_unknown_rig_is_left_alone(self):
        times = _stamps(12, 9, 30)
        survivors, _ = collapse_copied_sub_blocks(
            [_block("/a", times, camera=None), _block("/b", times, camera=None)])
        self.assertEqual(len(survivors), 2)

    def test_frames_sharing_one_stamped_time_are_left_alone(self):
        """The 90 aligned frames: both blocks survive, so no real sub is lost."""
        a = _block("/aligned", ["2024-09-01T11:51:08"], n=90)
        b = _block("/aligned-copy", ["2024-09-01T11:51:08"], n=90)
        survivors, dropped = collapse_copied_sub_blocks([a, b])
        self.assertEqual(len(survivors), 2)
        self.assertEqual(dropped, [])

    def test_a_dropped_row_names_both_folders_and_its_hours(self):
        forty = _stamps(12, 9, 40)
        survivors, dropped = collapse_copied_sub_blocks(
            [_block("/keep", forty), _block("/copy", forty[:10])])
        self.assertEqual(len(dropped), 1)
        row = dropped[0]
        self.assertEqual(row["bucket"], "/copy")
        self.assertEqual(row["kept"], "/keep")
        self.assertEqual(row["n_subs"], 10)
        self.assertAlmostEqual(row["hours"], 10 * 120 / 3600.0, places=3)
        self.assertIn("capture times", row["action"])

    def test_nothing_but_pointing_is_ever_written_back(self):
        forty = _stamps(12, 9, 40)
        blocks = [_block("/keep", forty, ra_deg=251.9, dec_deg=-12.1),
                  _block("/copy", forty[:10], ra_deg=251.9, dec_deg=-12.1)]
        before = [dict(b) for b in blocks]
        collapse_copied_sub_blocks(blocks)
        self.assertEqual([dict(b) for b in blocks], before)

    def test_an_unsolved_survivor_inherits_the_pointing_of_its_copy(self):
        """The Sh2-27 shape: 40 unsolved raws, 30 of them solved in another tree."""
        forty = _stamps(12, 9, 40)
        raw = _block("/raws", forty, ra_deg=None, dec_deg=None, has_wcs=False,
                     pix_arcsec=None)
        solved = _block("/registered", forty[:30], ra_deg=251.94, dec_deg=-12.07,
                        pix_arcsec=3.1)
        survivors, _ = collapse_copied_sub_blocks([raw, solved])
        self.assertEqual([b["bucket"] for b in survivors], ["/raws"])
        self.assertAlmostEqual(survivors[0]["ra_deg"], 251.94)
        self.assertAlmostEqual(survivors[0]["dec_deg"], -12.07)
        self.assertTrue(survivors[0]["_coords_inherited"])
        self.assertFalse(survivors[0]["has_wcs"], "an inherited position is not a solve")
        self.assertIsNone(survivors[0]["pix_arcsec"],
                          "pixel scale stays native so a drizzled copy cannot poison it")

    def test_a_survivor_with_its_own_pointing_keeps_it(self):
        forty = _stamps(12, 9, 40)
        keep = _block("/keep", forty, ra_deg=10.0, dec_deg=20.0)
        copy = _block("/keep-copy", forty, ra_deg=99.0, dec_deg=-99.0)
        survivors, _ = collapse_copied_sub_blocks([keep, copy])
        self.assertEqual([b["bucket"] for b in survivors], ["/keep"])
        self.assertAlmostEqual(survivors[0]["ra_deg"], 10.0)
        self.assertNotIn("_coords_inherited", survivors[0])

    def test_at_equal_size_the_solved_block_survives(self):
        forty = _stamps(12, 9, 40)
        unsolved = _block("/a-unsolved", forty, has_wcs=False)
        solved = _block("/b-solved", forty, has_wcs=True)
        survivors, _ = collapse_copied_sub_blocks([unsolved, solved])
        self.assertEqual([b["bucket"] for b in survivors], ["/b-solved"])

    def test_a_bigger_unsolved_block_still_beats_a_smaller_solved_one(self):
        """Size outranks the solve, or set containment stops working."""
        forty = _stamps(12, 9, 40)
        big = _block("/all-forty", forty, has_wcs=False)
        small = _block("/thirty-solved", forty[:30], has_wcs=True)
        survivors, _ = collapse_copied_sub_blocks([big, small])
        self.assertEqual([b["bucket"] for b in survivors], ["/all-forty"])

    def test_three_copies_collapse_to_one_not_a_chain(self):
        """Each copy must be dropped against a survivor, never against a dropped one."""
        forty = _stamps(12, 9, 40)
        blocks = [_block(f"/copy-{i}", forty) for i in range(3)] + [_block("/keep", forty)]
        survivors, dropped = collapse_copied_sub_blocks(blocks)
        self.assertEqual(len(survivors), 1)
        self.assertEqual(len(dropped), 3)
        for row in dropped:
            self.assertEqual(row["kept"], survivors[0]["bucket"])

    def test_the_log_reports_the_hours_it_removed(self):
        forty = _stamps(12, 9, 40)
        lines = []
        collapse_copied_sub_blocks(
            [_block("/keep", forty), _block("/copy", forty)], log=lines.append)
        self.assertEqual(len(lines), 1)
        self.assertIn("1.3h", lines[0])

    def test_nothing_to_do_logs_nothing(self):
        lines = []
        survivors, dropped = collapse_copied_sub_blocks(
            [_block("/a", _stamps(12, 9, 5))], log=lines.append)
        self.assertEqual(lines, [])
        self.assertEqual(len(survivors), 1)


if __name__ == "__main__":
    unittest.main()


class TestRejectionSurvivesTheCollapse(unittest.TestCase):
    """A reject folder absorbed as a copy must keep condemning its frames.

    The user moved those frames aside on purpose. Dropping the folder as a copy
    of the session it came from would otherwise hand the same light back to
    accepted, which is what happened to 368 reject folders holding 34.0h of the
    maintainer's archive.
    """

    def test_an_absorbed_reject_folder_condemns_its_frames_in_the_keeper(self):
        forty = _stamps(12, 9, 40)
        keep = _block("/session/lights", forty)
        rejected = _block("/session/rejected/stars", forty[:3])
        survivors, dropped = collapse_copied_sub_blocks([keep, rejected])
        self.assertEqual([b["bucket"] for b in survivors], ["/session/lights"])
        self.assertAlmostEqual(rejected_copy_hours(survivors[0]), 3 * 120 / 3600.0)
        self.assertTrue(dropped[0]["rejection_carried"])

    def test_two_overlapping_reject_folders_condemn_each_frame_once(self):
        forty = _stamps(12, 9, 40)
        keep = _block("/session/lights", forty)
        a = _block("/session/rejected/round-one", forty[:3])
        b = _block("/session/bad/round-two", forty[1:4])
        survivors, _ = collapse_copied_sub_blocks([keep, a, b])
        self.assertAlmostEqual(rejected_copy_hours(survivors[0]), 4 * 120 / 3600.0)

    def test_an_ordinary_copy_condemns_nothing(self):
        forty = _stamps(12, 9, 40)
        survivors, dropped = collapse_copied_sub_blocks(
            [_block("/session/lights", forty), _block("/jobs/copy", forty[:10])])
        self.assertEqual(rejected_copy_hours(survivors[0]), 0.0)
        self.assertFalse(dropped[0]["rejection_carried"])

    def test_a_reject_keeper_records_nothing_because_it_is_already_rejected(self):
        """The whole folder is excluded from accepted, so there is nothing to subtract."""
        forty = _stamps(12, 9, 40)
        keep = _block("/session/rejected/all-of-it", forty)
        copy = _block("/session/rejected/again", forty[:10])
        survivors, dropped = collapse_copied_sub_blocks([keep, copy])
        self.assertEqual(rejected_copy_hours(survivors[0]), 0.0)
        self.assertFalse(dropped[0]["rejection_carried"])

    def test_a_reject_folder_holding_light_of_its_own_is_not_a_copy(self):
        """It survives whole, so the reject folder rule excludes it directly."""
        forty = _stamps(12, 9, 40)
        keep = _block("/session/lights", forty)
        rejected = _block("/session/rejected/mixed", forty[:2] + ["2099-01-01T00:00:00"])
        survivors, dropped = collapse_copied_sub_blocks([keep, rejected])
        self.assertEqual(len(survivors), 2)
        self.assertEqual(dropped, [])
        self.assertEqual(rejected_copy_hours(keep), 0.0)

    def test_a_block_no_reject_folder_touched_reports_no_hours(self):
        self.assertEqual(rejected_copy_hours(_block("/a", _stamps(12, 9, 5))), 0.0)
