"""One integration saved in two folders must count its hours once (issue #76).

The per-folder variant collapse groups by parent directory, so a processed
copy saved one level up escaped it and the band reported double the hours.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def master(path, filt="B", exptime=120.0, ncombine=40, **kw):
    m = {
        "path": path,
        "role": "master",
        "filter": filt,
        "exptime": exptime,
        "ncombine": ncombine,
        "has_wcs": True,
        "ra_deg": 10.0,
        "dec_deg": -20.0,
    }
    m.update(kw)
    return m


class TestCollapseClusterDuplicateMasters(unittest.TestCase):
    def test_same_integration_in_two_folders_collapses(self):
        a = master("/a/Sh2-27/B.xisf")
        b = master("/a/Sh2-27/master/masterLight_BIN-1_EXPOSURE-120.00s_FILTER-B_mono.xisf")
        kept, dropped = bam.collapse_cluster_duplicate_masters([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["kept"], kept[0]["path"])

    def test_different_subframe_counts_are_different_data(self):
        a = master("/a/M42/B.xisf", ncombine=40)
        b = master("/a/M42/master/B.xisf", ncombine=52)
        kept, dropped = bam.collapse_cluster_duplicate_masters([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_different_exposure_is_different_data(self):
        a = master("/a/M42/B.xisf", exptime=120.0)
        b = master("/a/M42/master/B.xisf", exptime=300.0)
        kept, _ = bam.collapse_cluster_duplicate_masters([a, b])
        self.assertEqual(len(kept), 2)

    def test_different_filters_never_merge(self):
        a = master("/a/M42/B.xisf", filt="B")
        b = master("/a/M42/master/R.xisf", filt="R")
        kept, dropped = bam.collapse_cluster_duplicate_masters([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_missing_subframe_count_leaves_both_alone(self):
        a = master("/a/M42/B.xisf", ncombine=None)
        b = master("/a/M42/master/B.xisf", ncombine=40)
        kept, dropped = bam.collapse_cluster_duplicate_masters([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_single_frame_master_is_never_grouped(self):
        a = master("/a/M42/B.xisf", ncombine=1)
        b = master("/a/M42/master/B.xisf", ncombine=1)
        kept, dropped = bam.collapse_cluster_duplicate_masters([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_folder_subs_pass_through_untouched(self):
        fs = {"path": "/a/M42/lights", "role": "folder_sub", "filter": "B",
              "exptime": 120.0, "ncombine": 40, "ra_deg": 10.0, "dec_deg": -20.0}
        kept, dropped = bam.collapse_cluster_duplicate_masters([fs])
        self.assertEqual(kept, [fs])
        self.assertEqual(dropped, [])

    def test_folder_sub_does_not_absorb_a_matching_master(self):
        m = master("/a/M42/master/B.xisf")
        fs = {"path": "/a/M42/lights", "role": "folder_sub", "filter": "B",
              "exptime": 120.0, "ncombine": 40, "ra_deg": 10.0, "dec_deg": -20.0}
        kept, dropped = bam.collapse_cluster_duplicate_masters([m, fs])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_solved_master_wins_over_unsolved_copy(self):
        unsolved = master("/a/M42/B.xisf", has_wcs=False)
        solved = master("/a/M42/master/masterLight_FILTER-B.xisf", has_wcs=True)
        kept, dropped = bam.collapse_cluster_duplicate_masters([unsolved, solved])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["path"], solved["path"])

    def test_three_copies_collapse_to_one(self):
        ms = [master(f"/a/M42/{n}") for n in ("B.xisf", "sub/B.xisf", "master/B.xisf")]
        kept, dropped = bam.collapse_cluster_duplicate_masters(ms)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 2)


class TestHoursCountedOnce(unittest.TestCase):
    def test_band_hours_halve_after_the_collapse(self):
        a = master("/a/Sh2-27/B.xisf")
        b = master("/a/Sh2-27/master/masterLight_FILTER-B_mono.xisf")
        before = bam.build_filters_data([a, b])
        self.assertAlmostEqual(before["B"]["total_hours"], 2.667, places=2)
        kept, _ = bam.collapse_cluster_duplicate_masters([a, b])
        after = bam.build_filters_data(kept)
        self.assertAlmostEqual(after["B"]["total_hours"], 1.333, places=2)


if __name__ == "__main__":
    unittest.main()


class TestKeepsTheRicherCopy(unittest.TestCase):
    def test_copy_with_instrument_keywords_wins(self):
        stripped = master("/a/M42/B.xisf")
        full = master("/a/M42/master/masterLight_FILTER-B_mono.xisf",
                      camera="ASI2600MM", focallen=250.0, xpixsz=3.76,
                      telescope="RedCat 51")
        kept, dropped = bam.collapse_cluster_duplicate_masters([stripped, full])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["path"], full["path"])

    def test_shorter_stem_still_wins_when_metadata_matches(self):
        gear = dict(camera="ASI2600MM", focallen=250.0, xpixsz=3.76,
                    telescope="RedCat 51")
        short = master("/a/M42/B.xisf", **gear)
        long = master("/a/M42/master/masterLight_FILTER-B_mono.xisf", **gear)
        kept, _ = bam.collapse_cluster_duplicate_masters([short, long])
        self.assertEqual(kept[0]["path"], short["path"])
