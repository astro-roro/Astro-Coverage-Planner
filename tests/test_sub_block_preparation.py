"""A master no longer erases the subs that fed it, and acceptance is a folder
name question rather than a pipeline stage.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def _fs(bucket, filt="Ha", n=10, exptime=300.0, ra=10.0, dec=41.0,
        has_wcs=True, date="2026-01-10T21:00:00", scope="190MN",
        cam="ASI2600MM", naxis=(6248, 4176), pix=1.0):
    return {"bucket": bucket, "filter": filt, "n_subs": n, "exptime": exptime,
            "total_hours": n * exptime / 3600.0,
            "ra_deg": ra, "dec_deg": dec, "has_wcs": has_wcs,
            "date_obs": date, "object": "M31",
            "telescope": scope, "camera": cam, "colour": False,
            "sample_path": f"{bucket}/l_0001.fits",
            "naxis1": naxis[0], "naxis2": naxis[1],
            "wcs_naxis1": None, "wcs_naxis2": None,
            "pix_arcsec": pix, "pix_arcsec_focal": pix,
            "_basenames": frozenset(f"l_{i:04d}.fits" for i in range(n))}


def _m(path, filt="Ha", ncombine=36, exptime=300.0):
    return {"path": path, "filter": filt, "ncombine": ncombine, "exptime": exptime,
            "role": "master", "has_wcs": True, "ra_deg": 10.0, "dec_deg": 41.0,
            "telescope": "190MN", "camera": "ASI2600MM"}


def _by_bucket(out):
    return {b["bucket"]: b for b in out["blocks"]}


class TestMasterNoLongerSuppressesSubs(unittest.TestCase):
    def test_subs_survive_a_master_in_the_same_session(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45), _fs("/S/sess1/calibrated", n=45)],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        self.assertIn("/S/sess1/og", _by_bucket(out))

    def test_no_block_is_ever_dropped_for_master_presence(self):
        out = bam.prepare_sub_blocks([_fs("/S/sess1/og", n=45)],
                                     [_m("/S/sess1/master/M31_Ha.xisf")])
        self.assertEqual(
            [r for r in out["dedup_log"] if "master present" in r["action"]], [])

    def test_the_session_master_link_is_recorded_per_filter(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", filt="Ha"), _fs("/S/sess1/calibrated", filt="OIII")],
            [_m("/S/sess1/master/M31_Ha.xisf", filt="Ha"),
             _m("/S/sess1/master/M31_OIII.xisf", filt="OIII")])
        self.assertEqual(out["session_masters"][("/S/sess1", "Ha")],
                         ["/S/sess1/master/M31_Ha.xisf"])
        self.assertEqual(out["session_masters"][("/S/sess1", "OIII")],
                         ["/S/sess1/master/M31_OIII.xisf"])

    def test_a_master_stage_block_is_not_capture_evidence(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/master", n=1)], [_m("/S/sess1/master/M31_Ha.xisf")])
        b = _by_bucket(out)["/S/sess1/master"]
        self.assertFalse(b["_counts_captured"])
        self.assertIn("not captured (integrated master file present)",
                      [r["action"] for r in out["dedup_log"]])


class TestTagging(unittest.TestCase):
    def test_originals_are_the_capture_and_the_acceptance(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45), _fs("/S/sess1/calibrated", n=45),
             _fs("/S/sess1/registered", n=42)],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        og = _by_bucket(out)["/S/sess1/og"]
        self.assertTrue(og["_counts_captured"])
        self.assertTrue(og["_counts_accepted"])
        self.assertEqual(og["_accepted_basis"], "no_rejects")

    def test_a_later_stage_counts_as_neither_and_stays_in_the_block_list(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45), _fs("/S/sess1/registered", n=42)],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        reg = _by_bucket(out)["/S/sess1/registered"]
        self.assertFalse(reg["_counts_captured"])
        self.assertFalse(reg["_counts_accepted"])

    def test_a_rejected_subfolder_is_captured_but_not_accepted(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=40), _fs("/S/sess1/og/rejected", n=5)], [])
        rej = _by_bucket(out)["/S/sess1/og/rejected"]
        self.assertTrue(rej["_counts_captured"])
        self.assertFalse(rej["_counts_accepted"])
        self.assertEqual(rej["_accepted_basis"], "rejected_folders")

    def test_a_lone_raw_folder_is_captured_and_accepted(self):
        out = bam.prepare_sub_blocks([_fs("/Raw/M31/Ha", n=20)], [])
        b = out["blocks"][0]
        self.assertTrue(b["_counts_captured"])
        self.assertTrue(b["_counts_accepted"])

    def test_derivatives_are_gone_from_the_block_list(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45), _fs("/S/sess1/starless", n=45),
             _fs("/S/sess1/stars", n=45)],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        self.assertEqual(set(_by_bucket(out)), {"/S/sess1/og"})

    def test_an_og_only_session_contributes_its_hours(self):
        """The delta from taking og out of DERIVATIVE_STAGES, pinned at last."""
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45), _fs("/S/sess1/starless", n=45)], [])
        og = _by_bucket(out)["/S/sess1/og"]
        self.assertTrue(og["_counts_captured"])

    def test_every_returned_block_carries_the_full_tag_set(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og"), _fs("/S/sess1/registered")],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        for b in out["blocks"]:
            for key in ("_session_root", "_stage", "_counts_captured",
                        "_counts_accepted", "_accepted_basis", "_coords_inherited"):
                self.assertIn(key, b, f"{b['bucket']} missing {key}")


class TestDedupLogRows(unittest.TestCase):
    def test_a_row_carries_exactly_the_seven_keys_the_log_renders(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og"), _fs("/S/sess1/stars")], [])
        self.assertEqual(set(out["dedup_log"][0]),
                         {"session_root", "filter", "bucket", "stage", "n_subs",
                          "hours", "action"})

    def test_every_action_is_from_the_closed_vocabulary(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og"), _fs("/S/sess1/registered"),
             _fs("/S/sess1/stars"), _fs("/S/sess1/master", n=1)],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        allowed = {"dropped (derivative product)",
                   "not captured (wider stage og present)",
                   "not captured (integrated master file present)"}
        self.assertTrue({r["action"] for r in out["dedup_log"]} <= allowed)


class TestCoordInheritance(unittest.TestCase):
    def test_an_unsolved_originals_block_borrows_a_sibling_solve(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45, ra=None, dec=None, has_wcs=False),
             _fs("/S/sess1/registered", n=42, ra=10.5, dec=41.2, has_wcs=True)],
            [])
        og = _by_bucket(out)["/S/sess1/og"]
        self.assertEqual((og["ra_deg"], og["dec_deg"]), (10.5, 41.2))
        self.assertTrue(og["_coords_inherited"])

    def test_inheriting_coords_does_not_claim_a_plate_solve(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", ra=None, dec=None, has_wcs=False),
             _fs("/S/sess1/registered", ra=10.5, dec=41.2, has_wcs=True)], [])
        self.assertFalse(_by_bucket(out)["/S/sess1/og"]["has_wcs"])

    def test_geometry_is_never_inherited_from_a_drizzled_donor(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", ra=None, dec=None, has_wcs=False,
                 naxis=(6248, 4176), pix=1.0),
             _fs("/S/sess1/registered", ra=10.5, dec=41.2, has_wcs=True,
                 naxis=(12496, 8352), pix=0.5)], [])
        og = _by_bucket(out)["/S/sess1/og"]
        self.assertEqual((og["naxis1"], og["naxis2"]), (6248, 4176))
        self.assertEqual(og["pix_arcsec"], 1.0)
        self.assertIsNone(og["wcs_naxis1"])

    def test_inheritance_never_crosses_a_session(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", ra=None, dec=None, has_wcs=False),
             _fs("/S/sess1/starless", n=45),
             _fs("/S/sess2/og", ra=99.0, dec=1.0),
             _fs("/S/sess2/registered", ra=10.5, dec=41.2)], [])
        self.assertIsNone(_by_bucket(out)["/S/sess1/og"]["ra_deg"])

    def test_inheritance_never_crosses_a_filter(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", filt="Ha", ra=None, dec=None, has_wcs=False),
             _fs("/S/sess1/registered", filt="OIII", ra=10.5, dec=41.2)], [])
        self.assertIsNone(_by_bucket(out)["/S/sess1/og"]["ra_deg"])

    def test_a_block_that_already_has_coords_keeps_them(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", ra=9.0, dec=40.0, has_wcs=False),
             _fs("/S/sess1/registered", ra=10.5, dec=41.2)], [])
        og = _by_bucket(out)["/S/sess1/og"]
        self.assertEqual((og["ra_deg"], og["dec_deg"]), (9.0, 40.0))
        self.assertFalse(og["_coords_inherited"])

    def test_a_solved_sibling_is_preferred_over_a_pointing_only_one(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", ra=None, dec=None, has_wcs=False),
             _fs("/S/sess1/calibrated", ra=8.0, dec=39.0, has_wcs=False),
             _fs("/S/sess1/registered", ra=10.5, dec=41.2, has_wcs=True)], [])
        self.assertEqual(
            (_by_bucket(out)["/S/sess1/og"]["ra_deg"],
             _by_bucket(out)["/S/sess1/og"]["dec_deg"]), (10.5, 41.2))

    def test_a_block_with_no_sibling_to_borrow_from_is_still_returned(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", ra=None, dec=None, has_wcs=False),
             _fs("/S/sess1/starless", n=45)], [])
        og = _by_bucket(out)["/S/sess1/og"]
        self.assertIsNone(og["ra_deg"])
        self.assertTrue(og["_counts_captured"])


class TestUncoordinatedHours(unittest.TestCase):
    def test_the_helper_sums_captured_blocks_that_still_have_no_coords(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45, ra=None, dec=None, has_wcs=False),
             _fs("/S/sess1/starless", n=45)], [])
        self.assertAlmostEqual(bam.uncoordinated_captured_hours(out["blocks"]),
                               45 * 300 / 3600.0, places=4)

    def test_prepare_sub_blocks_does_not_return_the_figure_itself(self):
        out = bam.prepare_sub_blocks([_fs("/S/sess1/og")], [])
        self.assertNotIn("uncoordinated_captured_hours", out)


class TestDroppedHoursAndPurity(unittest.TestCase):
    def test_dropped_hours_counts_derivatives_only(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45), _fs("/S/sess1/starless", n=45)],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        self.assertAlmostEqual(out["dropped_hours"], 45 * 300 / 3600.0, places=4)

    def test_a_reclassified_stage_is_not_counted_as_dropped(self):
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45), _fs("/S/sess1/calibrated", n=45)],
            [_m("/S/sess1/master/M31_Ha.xisf")])
        self.assertEqual(out["dropped_hours"], 0.0)

    def test_the_masters_list_is_not_mutated(self):
        masters = [_m("/S/sess1/master/M31_Ha.xisf")]
        snapshot = [dict(m) for m in masters]
        bam.prepare_sub_blocks([_fs("/S/sess1/og")], masters)
        self.assertEqual([dict(m) for m in masters], snapshot)

    def test_calling_twice_on_an_inheriting_block_gives_the_same_answer(self):
        blocks = [_fs("/S/sess1/og", n=45, ra=None, dec=None, has_wcs=False),
                  _fs("/S/sess1/registered", n=42, ra=10.5, dec=41.2)]
        masters = [_m("/S/sess1/master/M31_Ha.xisf")]
        bam.prepare_sub_blocks(blocks, masters)
        b = bam.prepare_sub_blocks(blocks, masters)
        og = _by_bucket(b)["/S/sess1/og"]
        self.assertEqual((og["ra_deg"], og["dec_deg"]), (10.5, 41.2))
        self.assertTrue(og["_coords_inherited"])
        self.assertEqual(b["dropped_hours"], 0.0)


class TestEmptyInput(unittest.TestCase):
    def test_no_blocks_and_no_masters(self):
        out = bam.prepare_sub_blocks([], [])
        self.assertEqual(out["blocks"], [])
        self.assertEqual(dict(out["session_masters"]), {})
        self.assertEqual(out["dedup_log"], [])
        self.assertEqual(out["dropped_hours"], 0.0)

    def test_masters_with_no_subs_at_all(self):
        out = bam.prepare_sub_blocks([], [_m("/S/sess1/master/M31_Ha.xisf")])
        self.assertEqual(out["blocks"], [])


if __name__ == "__main__":
    unittest.main()
