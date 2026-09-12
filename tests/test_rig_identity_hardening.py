"""The rig key is an identity, not a paste of two raw header strings."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


class TestCameraCanonicalisation(unittest.TestCase):
    def test_a_vendor_prefix_is_not_part_of_the_camera_name(self):
        self.assertEqual(bam.sanitize_camera("ZWO ASI2600MM Pro"), "ASI2600MM Pro")

    def test_the_same_physical_camera_gives_one_rig_key(self):
        a = bam.rig_key("190MN", bam.sanitize_camera("ZWO ASI2600MM Pro"))
        b = bam.rig_key("190MN", bam.sanitize_camera("ASI2600MM Pro"))
        self.assertEqual(a, b)

    def test_internal_whitespace_is_collapsed(self):
        self.assertEqual(bam.sanitize_camera("  ASI2600MM   Pro "), "ASI2600MM Pro")

    def test_case_of_the_model_is_preserved(self):
        self.assertEqual(bam.sanitize_camera("QHY268M"), "QHY268M")

    def test_an_empty_camera_is_none(self):
        self.assertIsNone(bam.sanitize_camera("   "))
        self.assertIsNone(bam.sanitize_camera(None))

    def test_every_vendor_token_the_contract_names_is_stripped(self):
        expected = {"zwo", "qhy", "qhyccd", "svbony", "player one", "playerone",
                    "atik", "altair", "touptek", "risingcam", "omegon",
                    "starlight xpress"}
        self.assertEqual(set(bam.CAMERA_VENDOR_TOKENS), expected)
        for tok in expected:
            self.assertEqual(bam.sanitize_camera(f"{tok} Model9"), "Model9")

    def test_a_vendor_word_inside_the_model_is_left_alone(self):
        self.assertEqual(bam.sanitize_camera("Cam ZWO Edition"), "Cam ZWO Edition")


class TestTheReadersActuallySanitise(unittest.TestCase):
    """The wiring is this phase's only behaviour change, so it is pinned here.

    Reverting either assignment to the raw header must fail a test.
    """

    def test_a_fits_header_camera_comes_back_canonical(self):
        import tempfile

        from astropy.io import fits

        with tempfile.TemporaryDirectory() as td:
            p = str(Path(td) / "l_0001.fits")
            hdu = fits.PrimaryHDU()
            hdu.header["INSTRUME"] = "ZWO ASI1600MM-Cool"
            hdu.header["TELESCOP"] = "190MN"
            hdu.header["EXPTIME"] = 300.0
            hdu.writeto(p)
            self.assertEqual(bam.read_fits_meta(p)["camera"], "ASI1600MM-Cool")

    def test_the_xisf_properties_fallback_is_sanitised_too(self):
        """The third assignment site, missed by the previous contract."""
        import inspect

        src = inspect.getsource(bam.read_xisf_meta)
        for line in src.splitlines():
            if "Instrument:Camera:Name" in line and 'out["camera"]' in line:
                self.assertIn("sanitize_camera", line)
                break
        else:
            self.fail("no camera assignment from the XISF properties fallback")


class TestRigKeyIsSafeToPutInAManifest(unittest.TestCase):
    def test_a_path_in_a_header_never_survives_into_the_key(self):
        k = bam.rig_key("C:/Users/rohan/profiles/rig.json", "ASI2600MM")
        for ch in ("/", "\\", ":"):
            self.assertNotIn(ch, k)

    def test_a_unc_share_never_survives_into_the_key(self):
        k = bam.rig_key("\\\\NAS\\Astro\\rig", "cam")
        self.assertNotIn("\\", k)

    def test_each_half_is_capped_so_a_junk_header_cannot_bloat_the_manifest(self):
        k = bam.rig_key("S" * 500, "C" * 500)
        scope, cam = k.split("|")
        self.assertLessEqual(len(scope), 48)
        self.assertLessEqual(len(cam), 48)

    def test_the_key_still_splits_on_exactly_one_delimiter(self):
        self.assertEqual(bam.rig_key("a|b|c", "d|e").count("|"), 1)


class TestTagLookupFallsThroughToTheBlock(unittest.TestCase):
    def test_flags_are_read_off_the_folder_sub_block(self):
        m = {"role": "folder_sub", "_folder_sub": {
            "_counts_captured": True, "_counts_accepted": False,
            "_accepted_basis": "rejected_folders"}}
        self.assertTrue(bam.counts_captured(m))
        self.assertFalse(bam.counts_accepted(m))
        self.assertEqual(bam.accepted_basis_of(m), "rejected_folders")

    def test_a_member_level_flag_overrides_the_block(self):
        m = {"role": "folder_sub", "_counts_captured": True,
             "_folder_sub": {"_counts_captured": False}}
        self.assertTrue(bam.counts_captured(m))

    def test_a_member_level_none_means_absent_and_the_block_wins(self):
        m = {"role": "folder_sub", "_counts_accepted": None,
             "_folder_sub": {"_counts_accepted": False}}
        self.assertFalse(bam.counts_accepted(m))


if __name__ == "__main__":
    unittest.main()
