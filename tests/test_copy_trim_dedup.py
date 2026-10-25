"""Copies that overlap a folder without being wholly inside it still count once.

The Christmas Tree Cluster case. Its red light sits in a raw folder (70 subs)
and in two pipeline job folders under Images/calibrated/<hash> (94 and 156
subs), every file a ``R_pp_lights_seq_NNNNN.fits`` copy carrying the capture
time of the raw it came from. Whole-folder containment (collapse_copied_sub_blocks)
only collapses a folder that sits entirely inside a bigger one. These folders
each hold a different slice of the same night, so none sits inside another and
the old rule counted all three.

trim_partially_copied_sub_blocks credits each capture time to one folder, the
earliest pipeline stage first, and takes the frames already credited off the
others.
"""
from __future__ import annotations

import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import (  # noqa: E402
    build_folder_sub_blocks,
    collapse_copied_sub_blocks,
    trim_partially_copied_sub_blocks,
)

RIG = {"telescope": "190MN", "camera": "ASI2600MM Pro"}
RAW = "/Images/RAW_SCANNED/190MN/Christmas Tree Cluster/LIGHTS/R"
CAL_A = "/Images/calibrated/11e1dc0a18e2"
CAL_B = "/Images/calibrated/7084edf06536"


def _block(bucket, times, *, filt="R", exp=60.0, **over):
    times = list(times)
    b = {
        "bucket": bucket,
        "filter": filt,
        "exptime": exp,
        "n_subs": len(times),
        "total_hours": len(times) * exp / 3600.0,
        "_capture_times": Counter(times),
        "first_date_obs": min(times) if times else None,
        "last_date_obs": max(times) if times else None,
        "has_wcs": False,
    }
    b.update(RIG)
    b.update(over)
    return b


def _night(day, count, offset=0):
    """`count` distinct capture times on one December night, 75 s apart."""
    out = []
    for i in range(offset, offset + count):
        secs = i * 75
        out.append(f"2024-12-{day:02d}T{20 + secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}")
    return out


def _hours(blocks):
    return sum(b["total_hours"] for b in blocks)


def _run(blocks):
    """The two passes in the order main() runs them."""
    survivors, _ = collapse_copied_sub_blocks(blocks)
    return trim_partially_copied_sub_blocks(survivors)


class TestChristmasTreeShape(unittest.TestCase):
    def setUp(self):
        # 70 raw subs from the night of 28 December. The pipeline saw 150 of
        # that night's frames plus later ones, split across two jobs so that
        # neither job holds all of the raw.
        self.night = _night(28, 150)
        self.raw_times = self.night[:70]
        self.raw = _block(RAW, self.raw_times)
        self.cal_a = _block(CAL_A, self.night[:50] + _night(4, 44))
        self.cal_b = _block(CAL_B, self.night[20:] + _night(5, 26))

    def test_the_old_rule_counts_all_three(self):
        survivors, dropped = collapse_copied_sub_blocks([self.raw, self.cal_a, self.cal_b])
        self.assertEqual(len(survivors), 3)
        self.assertEqual(dropped, [])

    def test_every_capture_time_counts_once(self):
        survivors, _ = _run([self.raw, self.cal_a, self.cal_b])
        unique = Counter()
        for b in (self.raw, self.cal_a, self.cal_b):
            unique |= b["_capture_times"]
        self.assertEqual(sum(b["n_subs"] for b in survivors), sum(unique.values()))
        self.assertAlmostEqual(_hours(survivors), sum(unique.values()) * 60.0 / 3600.0)

    def test_the_raw_folder_keeps_all_its_frames(self):
        survivors, _ = _run([self.raw, self.cal_a, self.cal_b])
        raw = next(b for b in survivors if b["bucket"] == RAW)
        self.assertEqual(raw["n_subs"], 70)

    def test_trimmed_folders_lose_only_what_the_raw_already_holds(self):
        survivors, rows = _run([self.raw, self.cal_a, self.cal_b])
        by_bucket = {b["bucket"]: b for b in survivors}
        # CAL_B is reached before CAL_A because it is larger. It shares frames
        # 20..69 with the raw. CAL_A then finds all 50 of its night-28 frames
        # already credited.
        self.assertEqual(by_bucket[CAL_B]["n_subs"], 156 - 50)
        self.assertEqual(by_bucket[CAL_A]["n_subs"], 94 - 50)
        self.assertEqual({r["bucket"] for r in rows}, {CAL_A, CAL_B})

    def test_hours_follow_the_frames_that_stay(self):
        survivors, _ = _run([self.raw, self.cal_a, self.cal_b])
        for b in survivors:
            self.assertAlmostEqual(b["total_hours"], b["n_subs"] * 60.0 / 3600.0)

    def test_trimmed_dates_come_from_the_remaining_capture_times(self):
        survivors, _ = _run([self.raw, self.cal_a, self.cal_b])
        cal_a = next(b for b in survivors if b["bucket"] == CAL_A)
        self.assertEqual(cal_a["first_date_obs"], min(cal_a["_capture_times"]))
        self.assertEqual(cal_a["last_date_obs"], max(cal_a["_capture_times"]))
        self.assertTrue(cal_a["first_date_obs"].startswith("2024-12-"))

    def test_the_result_does_not_depend_on_input_order(self):
        a, _ = _run([self.raw, self.cal_a, self.cal_b])
        fresh = [_block(CAL_B, self.night[20:] + _night(5, 26)),
                 _block(CAL_A, self.night[:50] + _night(4, 44)),
                 _block(RAW, self.raw_times)]
        b, _ = _run(fresh)
        self.assertEqual(sorted((x["bucket"], x["n_subs"]) for x in a),
                         sorted((x["bucket"], x["n_subs"]) for x in b))


class TestFromFileHeaders(unittest.TestCase):
    """The same shape built from per-file header meta, as the scanner builds it."""

    @staticmethod
    def _meta(date_obs):
        return {"ok": True, "filter": "R", "exptime": 60.0, "date_obs": date_obs,
                "telescope": RIG["telescope"], "camera": RIG["camera"]}

    def test_raw_and_pp_copies_in_job_folders_count_the_raw_hours_once(self):
        night = _night(28, 90)
        meta, folders = {}, {}
        for bucket, name, times in (
            (RAW, "LIGHT_R_60.00_DAY-2024-12-28_#{:04d}.fits", night[:70]),
            (CAL_A, "R_pp_lights_seq_{:05d}.fits", night[:60]),
            (CAL_B, "R_pp_lights_seq_{:05d}.fits", night[40:]),
        ):
            paths = []
            for i, t in enumerate(times):
                p = f"{bucket}/{name.format(i)}"
                meta[p] = self._meta(t)
                paths.append(p)
            folders[bucket] = paths
        blocks = []
        for bucket, paths in folders.items():
            blocks.extend(build_folder_sub_blocks(bucket, paths, meta))
        before = _hours(blocks)
        survivors, _ = _run(blocks)
        self.assertAlmostEqual(before, 180 * 60.0 / 3600.0)
        self.assertAlmostEqual(_hours(survivors), 90 * 60.0 / 3600.0)
        raw = next(b for b in survivors if b["bucket"] == RAW)
        self.assertEqual((raw["first_date_obs"], raw["last_date_obs"]), (night[0], night[69]))


class TestGuards(unittest.TestCase):
    def test_exact_copies_still_collapse_to_the_raw(self):
        times = _night(28, 70)
        survivors, _ = _run([
            _block(CAL_A, times), _block(RAW, times), _block(CAL_B, times)])
        self.assertEqual([(b["bucket"], b["n_subs"]) for b in survivors], [(RAW, 70)])
        self.assertAlmostEqual(_hours(survivors), 70 * 60.0 / 3600.0)

    def test_a_raw_folder_beats_calibrated_copies_of_the_same_size(self):
        times = _night(28, 70)
        survivors, _ = collapse_copied_sub_blocks([_block(CAL_A, times), _block(RAW, times)])
        self.assertEqual([b["bucket"] for b in survivors], [RAW])

    def test_two_different_nights_are_untouched(self):
        survivors, rows = _run([_block("/n1", _night(28, 40)), _block("/n2", _night(29, 40))])
        self.assertEqual(len(survivors), 2)
        self.assertEqual(rows, [])

    def test_a_handful_of_shared_times_is_not_a_copy(self):
        shared = _night(28, 3)
        a = _block("/a", shared + _night(29, 40))
        b = _block("/b", shared + _night(30, 40))
        survivors, rows = _run([a, b])
        self.assertEqual([x["n_subs"] for x in survivors], [43, 43])
        self.assertEqual(rows, [])

    def test_a_small_share_of_a_big_folder_is_not_a_copy(self):
        """Eight shared frames out of a hundred is under a tenth of the folder."""
        shared = _night(28, 8)
        a = _block("/a", shared + _night(29, 92))
        b = _block("/b", shared + _night(30, 92))
        survivors, rows = _run([a, b])
        self.assertEqual([x["n_subs"] for x in survivors], [100, 100])
        self.assertEqual(rows, [])

    def test_a_different_rig_or_filter_or_exposure_is_untouched(self):
        times = _night(28, 40)
        other = _night(28, 20) + _night(29, 20)
        for over in ({"telescope": "RedCat 51"}, {"filt": "G"}, {"exp": 300.0}):
            survivors, rows = _run([_block("/a", times), _block("/b", other, **over)])
            self.assertEqual([x["n_subs"] for x in survivors], [40, 40], over)
            self.assertEqual(rows, [])

    def test_a_block_without_times_is_left_alone(self):
        times = _night(28, 40)
        blind = _block("/blind", [], n_subs=40, total_hours=40 * 60 / 3600.0)
        survivors, rows = _run([_block("/a", times), blind])
        self.assertEqual(len(survivors), 2)
        self.assertEqual(rows, [])

    def test_a_block_stamped_with_one_time_is_left_alone(self):
        times = _night(28, 40)
        flat = _block("/flat", ["2024-09-01T11:51:08"] * 40)
        survivors, rows = _run([_block("/a", times), flat])
        self.assertEqual(len(survivors), 2)
        self.assertEqual(rows, [])

    def test_inputs_are_not_mutated(self):
        raw = _block(RAW, _night(28, 70))
        cal = _block(CAL_A, _night(28, 50) + _night(4, 44))
        trim_partially_copied_sub_blocks([raw, cal])
        self.assertEqual(cal["n_subs"], 94)
        self.assertEqual(sum(cal["_capture_times"].values()), 94)

    def test_a_rejected_copy_hands_its_rejection_to_the_folder_that_keeps_the_frames(self):
        times = _night(28, 70)
        keeper = _block("/Images/RAW_SCANNED/190MN/X/LIGHTS/R", times)
        rejected = _block("/Images/RAW_SCANNED/190MN/X/rejected/R", times[:40] + _night(29, 20))
        survivors, rows = _run([keeper, rejected])
        kept = next(b for b in survivors if b["bucket"] == keeper["bucket"])
        self.assertEqual(sum(kept["_rejected_copy_times"].values()), 40)
        self.assertTrue(rows[0]["rejection_carried"])


if __name__ == "__main__":
    unittest.main()
