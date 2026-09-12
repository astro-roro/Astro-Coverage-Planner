"""Paths keep the spelling they arrived with, on every platform.

Most users run ACP on Windows. Session roots, stage folders and bucket strings
are compared by plain string equality, so any step that rebuilds a path through
PurePath rewrites its separators to the platform's own and the comparison
silently stops matching. On Windows that emptied the session master link for
every path written with forward slashes, which is how people type a root by
hand, and it would also have turned stage dedup off without saying so.

These tests run both spellings on whatever platform they land on, so a
regression shows up on macOS and Linux too rather than only in the Windows job.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


class TestParentDir(unittest.TestCase):
    def test_forward_slashes_survive(self):
        self.assertEqual(bam.parent_dir("/S/sess1/master/M31_Ha.xisf"),
                         "/S/sess1/master")

    def test_backslashes_survive(self):
        self.assertEqual(bam.parent_dir(r"D:\Astro\sess1\master\M31_Ha.xisf"),
                         r"D:\Astro\sess1\master")

    def test_a_bare_name_has_no_parent_to_give(self):
        self.assertEqual(bam.parent_dir("M31_Ha.xisf"), "M31_Ha.xisf")

    def test_a_file_at_the_root_keeps_the_root(self):
        self.assertEqual(bam.parent_dir("/M31_Ha.xisf"), "/")

    def test_it_accepts_a_path_object(self):
        self.assertEqual(bam.parent_dir(Path("/S/sess1/m.xisf")), "/S/sess1")


class TestSessionRootKeepsItsSpelling(unittest.TestCase):
    def test_forward_slashes(self):
        self.assertEqual(bam.session_root_and_stage("/S/sess1/calibrated"),
                         ("/S/sess1", "calibrated"))

    def test_backslashes(self):
        self.assertEqual(bam.session_root_and_stage(r"D:\Astro\sess1\calibrated"),
                         (r"D:\Astro\sess1", "calibrated"))

    def test_a_path_with_no_stage_folder_comes_back_whole(self):
        for p in ("/S/sess1/lights", r"D:\Astro\sess1\lights"):
            with self.subTest(p=p):
                self.assertEqual(bam.session_root_and_stage(p), (p, "root"))

    def test_the_stage_can_sit_above_the_leaf(self):
        self.assertEqual(bam.session_root_and_stage("/S/sess1/calibrated/2024-09-01/Ha"),
                         ("/S/sess1", "calibrated"))


class TestTheDetectorsAgreeWithTheResolver(unittest.TestCase):
    """They must produce the same session root string, or dedup quietly stops."""

    def _buckets(self, sep):
        root = sep.join(["", "S", "sess1"]) if sep == "/" else "D:\\Astro\\sess1"
        return root, [sep.join([root, name]) for name in
                      ("calibrated", "registered", "master")]

    def test_a_session_root_is_recognised_in_both_spellings(self):
        for sep in ("/", "\\"):
            root, buckets = self._buckets(sep)
            with self.subTest(sep=sep):
                roots = bam.detect_wbpp_session_roots(buckets)
                self.assertIn(root, roots)
                self.assertEqual(bam.session_root_and_stage(buckets[0], roots),
                                 (root, "calibrated"))

    def test_originals_resolve_to_the_master_sibling_in_both_spellings(self):
        for sep in ("/", "\\"):
            root, buckets = self._buckets(sep)
            originals = sep.join([root, "original_lights"])
            with self.subTest(sep=sep):
                found = bam.detect_originals_master_siblings(buckets + [originals])
                self.assertEqual(found.get(originals), root)


class TestTheSessionMasterLinkInBothSpellings(unittest.TestCase):
    """The case the Windows job caught, run on every platform."""

    @staticmethod
    def _fs(bucket, filt):
        return {"bucket": bucket, "filter": filt, "n_subs": 10, "exptime": 300.0,
                "total_hours": 10 * 300 / 3600.0, "telescope": "190MN",
                "camera": "ASI2600MM", "colour": False, "has_wcs": True,
                "ra_deg": 10.0, "dec_deg": 41.0,
                "sample_path": f"{bucket}/l_0001.fits"}

    @staticmethod
    def _m(path, filt):
        return {"path": path, "filter": filt, "ncombine": 10, "exptime": 300.0,
                "role": "master", "has_wcs": True, "ra_deg": 10.0, "dec_deg": 41.0,
                "telescope": "190MN", "camera": "ASI2600MM"}

    def test_the_master_is_linked_to_its_session_in_both_spellings(self):
        for sep in ("/", "\\"):
            root = "/S/sess1" if sep == "/" else "D:\\Astro\\sess1"
            with self.subTest(sep=sep):
                out = bam.prepare_sub_blocks(
                    [self._fs(sep.join([root, "og"]), "Ha")],
                    [self._m(sep.join([root, "master", "M31_Ha.xisf"]), "Ha")])
                self.assertEqual(out["session_masters"][(root, "Ha")],
                                 [sep.join([root, "master", "M31_Ha.xisf"])])


if __name__ == "__main__":
    unittest.main()
