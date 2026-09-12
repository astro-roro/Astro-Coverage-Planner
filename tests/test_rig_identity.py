"""Base semantics of the rig helpers. Written from the contract, not the code."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


class TestRigKey(unittest.TestCase):
    def test_a_plain_pair_joins_on_one_pipe(self):
        self.assertEqual(bam.rig_key("190MN", "ASI2600MM"), "190MN|ASI2600MM")

    def test_missing_telescope_becomes_a_question_mark(self):
        self.assertEqual(bam.rig_key(None, "ASI2600MM"), "?|ASI2600MM")

    def test_missing_camera_becomes_a_question_mark(self):
        self.assertEqual(bam.rig_key("190MN", None), "190MN|?")

    def test_both_missing_is_the_unknown_rig(self):
        self.assertEqual(bam.rig_key(None, None), "?|?")

    def test_a_none_half_never_stringifies_to_the_word_none(self):
        self.assertNotIn("None", bam.rig_key(None, None))

    def test_whitespace_only_is_the_same_as_missing(self):
        self.assertEqual(bam.rig_key("  ", "\t"), "?|?")


class TestRigLabel(unittest.TestCase):
    def test_a_full_pair_reads_as_gear(self):
        self.assertEqual(bam.rig_label("190MN|ASI2600MM"), "190MN + ASI2600MM")

    def test_the_unknown_rig_has_a_name_of_its_own(self):
        self.assertEqual(bam.rig_label("?|?"), "Unknown rig")

    def test_a_half_known_rig_keeps_the_unknown_half_visible(self):
        self.assertEqual(bam.rig_label("190MN|?"), "190MN + ?")


class TestRejectedFolders(unittest.TestCase):
    def test_a_rejected_folder_is_recognised(self):
        self.assertTrue(bam.is_rejected_bucket("/S/sess1/registered/rejected"))

    def test_bad_is_recognised_and_case_does_not_matter(self):
        self.assertTrue(bam.is_rejected_bucket("/S/sess1/BadFrames"))

    def test_a_windows_path_is_split_too(self):
        self.assertTrue(bam.is_rejected_bucket(r"D:\\Astro\\M31\\Rejected"))

    def test_an_ordinary_folder_is_not(self):
        self.assertFalse(bam.is_rejected_bucket("/S/sess1/og"))


class TestCountHelpersGateOnRole(unittest.TestCase):
    def test_a_master_is_never_captured_or_accepted(self):
        m = {"role": "master", "_counts_captured": True, "_counts_accepted": True}
        self.assertFalse(bam.counts_captured(m))
        self.assertFalse(bam.counts_accepted(m))
        self.assertIsNone(bam.accepted_basis_of(m))

    def test_an_untagged_sub_block_is_both_captured_and_accepted(self):
        m = {"role": "folder_sub", "_folder_sub": {"bucket": "/a"}}
        self.assertTrue(bam.counts_captured(m))
        self.assertTrue(bam.counts_accepted(m))
        self.assertEqual(bam.accepted_basis_of(m), "no_rejects")

    def test_session_root_is_read_off_the_block(self):
        m = {"role": "folder_sub", "_folder_sub": {"_session_root": "/S/sess1"}}
        self.assertEqual(bam.session_root_of(m), "/S/sess1")

    def test_a_master_carries_its_session_root_at_member_level(self):
        self.assertEqual(
            bam.session_root_of({"role": "master", "_session_root": "/S/sess1"}),
            "/S/sess1")


if __name__ == "__main__":
    unittest.main()
