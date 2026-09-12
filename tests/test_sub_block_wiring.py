"""The seams this phase owns: which gear a header-stripped master inherits,
which folders count as one session, and which member draws the footprint.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402

from test_sub_block_preparation import _fs, _m  # noqa: E402


def _mk(root, *rel):
    for r in rel:
        os.makedirs(os.path.join(root, r), exist_ok=True)


class TestMasterRigHint(unittest.TestCase):
    def test_a_degenerate_master_takes_its_sessions_single_rig(self):
        m = _m("/S/sess1/master/M31_Ha.xisf")
        m["telescope"] = m["camera"] = "ASI2600MM"
        out = bam.prepare_sub_blocks([_fs("/S/sess1/og", n=45)], [m])
        self.assertEqual(out["master_rig_hint"]["/S/sess1/master/M31_Ha.xisf"],
                         "190MN|ASI2600MM")

    def test_a_master_with_its_own_complete_gear_is_left_alone(self):
        m = _m("/S/sess1/master/M31_Ha.xisf")
        m["telescope"], m["camera"] = "RedCat 51", "ASI2600MC"
        out = bam.prepare_sub_blocks([_fs("/S/sess1/og", n=45)], [m])
        self.assertEqual(out["master_rig_hint"], {})

    def test_two_rigs_in_one_session_produce_no_hint(self):
        m = _m("/S/sess1/master/M31_Ha.xisf")
        m["telescope"] = m["camera"] = "ASI2600MM"
        out = bam.prepare_sub_blocks(
            [_fs("/S/sess1/og", n=45),
             _fs("/S/sess1/og2", n=45, scope="RedCat 51", cam="ASI2600MC")], [m])
        self.assertEqual(out["master_rig_hint"], {})


class TestSessionDetectionWidening(unittest.TestCase):
    def test_a_tidied_calibrated_plus_registered_pair_is_one_session(self):
        with tempfile.TemporaryDirectory() as td:
            _mk(td, "sess1/calibrated", "sess1/registered")
            roots = bam.detect_wbpp_session_roots([
                os.path.join(td, "sess1/calibrated"),
                os.path.join(td, "sess1/registered")])
            self.assertEqual(set(roots.values()), {os.path.join(td, "sess1")})

    def test_a_calibrated_job_hash_store_is_still_not_a_session(self):
        with tempfile.TemporaryDirectory() as td:
            _mk(td, "store/calibrated/aaaa", "store/calibrated/bbbb")
            roots = bam.detect_wbpp_session_roots([
                os.path.join(td, "store/calibrated/aaaa"),
                os.path.join(td, "store/calibrated/bbbb")])
            self.assertNotIn(os.path.join(td, "store"), set(roots.values()))


class TestOriginalsGating(unittest.TestCase):
    def test_originals_beside_registered_resolve_without_a_master(self):
        with tempfile.TemporaryDirectory() as td:
            _mk(td, "sess1/original_lights", "sess1/registered")
            mapping = bam.detect_originals_master_siblings([
                os.path.join(td, "sess1/original_lights"),
                os.path.join(td, "sess1/registered")])
            self.assertIn(os.path.join(td, "sess1/original_lights"), mapping)

    def test_a_standalone_originals_folder_still_gets_no_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            _mk(td, "Raw/originals")
            mapping = bam.detect_originals_master_siblings(
                [os.path.join(td, "Raw/originals")])
            self.assertEqual(mapping, {})


class TestFovRepresentative(unittest.TestCase):
    def _mem(self, role, path, has_wcs=False, captured=True):
        m = {"role": role, "path": path, "has_wcs": has_wcs}
        if role == "folder_sub":
            m["_folder_sub"] = {"_counts_captured": captured,
                                "_counts_accepted": True}
        return m

    def test_a_master_wins_however_the_list_is_sorted(self):
        members = [self._mem("folder_sub", "/a/l.fits"),
                   self._mem("master", "/z/m.xisf")]
        self.assertEqual(bam.fov_representative(members)["path"], "/z/m.xisf")

    def test_a_captured_block_beats_an_earlier_sorting_unused_block(self):
        members = [self._mem("folder_sub", "/a/l.fits", captured=False),
                   self._mem("folder_sub", "/z/l.fits", captured=True)]
        self.assertEqual(bam.fov_representative(members)["path"], "/z/l.fits")

    def test_a_solved_block_beats_an_unsolved_one_at_the_same_rank(self):
        members = [self._mem("folder_sub", "/a/l.fits", has_wcs=False),
                   self._mem("folder_sub", "/z/l.fits", has_wcs=True)]
        self.assertEqual(bam.fov_representative(members)["path"], "/z/l.fits")


if __name__ == "__main__":
    unittest.main()
