"""One master sets integrated hours, and it is the deepest one.

Product owner's decision of 2026-09-12, taken against the maintainer's archive.
Adding masters together read Sh2-27 in Ha as 81 subs when 41 were ever taken:
the same stack sat in two folders, one exported in 2023 and one re-exported in
2026, sharing a capture instant to the second. Across the archive 116 of 311
rig-band rows hold more than one master, and summing them reported 778.6 hours
where the deepest-master rule reports 593.3.

Newest lost on the evidence. Horsehead in Ha holds a 23 hour master and a 2 hour
one stacked later, so newest would report 2 and throw the stack away. When a
shallower master is the newer one, the stale flag is what says to rebuild.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_archive_manifest as bam  # noqa: E402
from hours_fixtures import RIG, _master, _subs  # noqa: E402

H = 300 / 3600.0


def _band(members, band="Ha"):
    return bam.build_filters_data(members)[band]


def _entry(hours, *, depth_known=True, date="2026-01-01T00:00:00", path="/m.xisf"):
    return {"path": path, "hours": hours, "depth_known": depth_known,
            "date": date, "label": "Ha", "session_root": None}


class TestPickIntegrationMaster(unittest.TestCase):
    def test_no_masters_means_no_choice(self):
        self.assertIsNone(bam.pick_integration_master([]))

    def test_the_deepest_wins(self):
        deep = _entry(23.0, path="/deep.xisf")
        chosen = bam.pick_integration_master([_entry(2.0, path="/shallow.xisf"), deep])
        self.assertIs(chosen, deep)

    def test_the_deepest_wins_even_when_a_shallower_master_is_newer(self):
        """Horsehead in Ha: a 23 hour stack, then a 2 hour one shot later."""
        deep = _entry(23.0, date="2024-03-01T00:00:00", path="/full.xisf")
        newer = _entry(2.0, date="2025-11-01T00:00:00", path="/onenight.xisf")
        self.assertIs(bam.pick_integration_master([deep, newer]), deep)

    def test_a_master_with_a_subframe_count_beats_one_without(self):
        """A depth-unknown master counts one exposure, which must never win."""
        counted = _entry(0.5, path="/counted.xisf")
        uncounted = _entry(9.0, depth_known=False, path="/uncounted.xisf")
        self.assertIs(bam.pick_integration_master([counted, uncounted]), counted)

    def test_with_no_counts_anywhere_the_deepest_still_comes_back(self):
        deep = _entry(2.0, depth_known=False, path="/b.xisf")
        chosen = bam.pick_integration_master(
            [_entry(1.0, depth_known=False, path="/a.xisf"), deep])
        self.assertIs(chosen, deep)

    def test_equal_depth_breaks_on_date_then_path_so_the_choice_is_stable(self):
        older = _entry(5.0, date="2024-01-01T00:00:00", path="/a.xisf")
        newer = _entry(5.0, date="2025-01-01T00:00:00", path="/b.xisf")
        self.assertIs(bam.pick_integration_master([older, newer]), newer)
        self.assertIs(bam.pick_integration_master([newer, older]), newer)

    def test_equal_depth_and_date_breaks_on_path(self):
        a = _entry(5.0, path="/a.xisf")
        b = _entry(5.0, path="/b.xisf")
        self.assertIs(bam.pick_integration_master([a, b]), b)
        self.assertIs(bam.pick_integration_master([b, a]), b)


class TestIntegratedHoursNeverSum(unittest.TestCase):
    def test_the_same_stack_in_two_folders_counts_once(self):
        """Sh2-27 in Ha: 41 subs, exported in 2023 and re-exported in 2026."""
        b = _band([
            _master(ncombine=41, date="2023-07-13T09:39:15",
                    path="/Images/Working/Sh2-27/H.xisf"),
            _master(ncombine=40, date="2023-07-13T09:39:15",
                    path="/Library/Sh2_27/masters/full_master_Ha.fit"),
        ])
        self.assertAlmostEqual(b["integrated_hours"], 41 * H, places=6)
        self.assertAlmostEqual(b["headline_hours"], 41 * H, places=6)

    def test_three_masters_of_one_stack_still_count_once(self):
        b = _band([_master(ncombine=36, path=f"/a/m{i}.xisf") for i in range(3)])
        self.assertAlmostEqual(b["integrated_hours"], 36 * H, places=6)

    def test_nightly_masters_do_not_add_up(self):
        """The deliberate cost of the rule: a nightly-only archive reads its
        biggest night, and the stale flag carries the rest of the story."""
        b = _band([_master(ncombine=10, date="2026-02-01T00:00:00", path="/n1.xisf"),
                   _master(ncombine=12, date="2026-02-02T00:00:00", path="/n2.xisf"),
                   _subs(n=22, bucket="/S/sess1/og")])
        self.assertAlmostEqual(b["integrated_hours"], 12 * H, places=6)
        self.assertAlmostEqual(b["captured_hours"], 22 * H, places=6)
        self.assertTrue(b["stale_master"])

    def test_the_rig_row_names_the_master_it_used(self):
        row = _band([
            _master(ncombine=41, path="/deep.xisf"),
            _master(ncombine=12, path="/shallow.xisf"),
        ])["rigs"][RIG]
        self.assertEqual(row["integrated_master"], "/deep.xisf")
        self.assertAlmostEqual(row["integrated_hours"], 41 * H, places=6)

    def test_two_rigs_on_one_field_each_pick_their_own_master(self):
        """Only the band total combines rigs, so their masters do add there."""
        fd = bam.build_filters_data([
            _master(ncombine=36, path="/a/m.xisf"),
            _master(ncombine=20, scope="RedCat 51", cam="ASI2600MC",
                    path="/b/m.xisf"),
        ])
        self.assertAlmostEqual(fd["Ha"]["integrated_hours"], 56 * H, places=6)
        self.assertAlmostEqual(
            fd["Ha"]["rigs"][RIG]["integrated_hours"], 36 * H, places=6)


class TestDepthUnknownFollowsTheChosenMaster(unittest.TestCase):
    def test_a_deep_master_beside_an_uncounted_one_is_not_depth_unknown(self):
        b = _band([_master(ncombine=36, path="/good.xisf"),
                   _master(ncombine=None, path="/nocount.xisf")])
        self.assertFalse(b["master_depth_unknown"])
        self.assertEqual(b["headline_basis"], "integrated")
        self.assertAlmostEqual(b["integrated_hours"], 36 * H, places=6)

    def test_only_uncounted_masters_leave_the_depth_unknown(self):
        b = _band([_master(ncombine=None, path="/a.xisf"),
                   _master(ncombine=None, path="/b.xisf"),
                   _subs(n=45)])
        self.assertTrue(b["master_depth_unknown"])
        self.assertEqual(b["headline_basis"], "captured")


if __name__ == "__main__":
    unittest.main()
