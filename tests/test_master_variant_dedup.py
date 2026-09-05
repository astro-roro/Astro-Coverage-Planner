"""One integration saved as several files must count once (issue #63).

PixInsight and WBPP write extra versions of a master beside the original:
autocrop, drizzle, DBE, starless, and a FITS/XISF pair of the same frame. Each
one carries the integration's own EXPTIME and NCOMBINE, so counting every file
multiplies the archive's reported hours by the number of versions saved.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402

FOLDER = "/archive/Sh2-27/master"
BASE = "masterLight_BIN-1_6248x4176_EXPOSURE-120.00s_FILTER-B_mono"


def _m(name, *, filt="B", ra=251.9375, dec=-12.0669, has_wcs=True,
       ncombine=None, exptime=120.0, folder=FOLDER):
    return {"path": f"{folder}/{name}", "filter": filt, "ra_deg": ra,
            "dec_deg": dec, "has_wcs": has_wcs, "ncombine": ncombine,
            "exptime": exptime}


class TestCanonicalMasterStem(unittest.TestCase):
    def test_strips_chained_variant_tokens(self):
        self.assertEqual(
            bam.canonical_master_stem(BASE + "_drizzle_2x_autocrop"), BASE)

    def test_strips_repeated_token(self):
        self.assertEqual(
            bam.canonical_master_stem("drizzle_integration_DBE_DBE"),
            "drizzle_integration")

    def test_strips_numbered_copy_marker(self):
        self.assertEqual(bam.canonical_master_stem("LN_Reference_L_mono_(1)"),
                         "LN_Reference_L_mono")

    def test_keeps_a_stem_that_is_only_a_token(self):
        """Never strip a stem to nothing."""
        self.assertEqual(bam.canonical_master_stem("final"), "final")

    def test_strips_a_numbered_variant_token(self):
        """People number repeated versions: L_stars2 beside L_stars."""
        self.assertEqual(bam.canonical_master_stem("L_stars2"), "L")
        self.assertEqual(bam.canonical_master_stem("drizzle_integration_DBE2"),
                         "drizzle_integration")

    def test_a_bare_number_is_not_a_variant_token(self):
        """Binning and filter names end in digits; those are not versions."""
        self.assertEqual(bam.canonical_master_stem("M42_2"), "M42_2")
        self.assertEqual(bam.canonical_master_stem("masterLight_BIN-1_FILTER-L"),
                         "masterLight_BIN-1_FILTER-L")

    def test_leaves_a_plain_master_alone(self):
        self.assertEqual(bam.canonical_master_stem(BASE), BASE)

    def test_does_not_strip_integration(self):
        """'integration' is a base name, not a version of one."""
        self.assertEqual(bam.canonical_master_stem("drizzle_integration"),
                         "drizzle_integration")


class TestCollapseMasterVariants(unittest.TestCase):
    def test_variants_collapse_to_the_original(self):
        masters = [_m(BASE + ".xisf"), _m(BASE + "_autocrop.xisf"),
                   _m(BASE + "_drizzle_2x.xisf"),
                   _m(BASE + "_drizzle_2x_autocrop.xisf")]
        kept, dropped = bam.collapse_master_variants(masters)
        self.assertEqual([Path(k["path"]).name for k in kept], [BASE + ".xisf"])
        self.assertEqual(len(dropped), 3)
        self.assertTrue(all(d["kept"].endswith(BASE + ".xisf") for d in dropped))

    def test_fits_and_xisf_of_one_master_collapse(self):
        kept, dropped = bam.collapse_master_variants(
            [_m(BASE + ".fits"), _m(BASE + ".xisf")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_solved_version_is_preferred(self):
        """A crop that kept the solve beats an original that lost it."""
        kept, _ = bam.collapse_master_variants([
            _m(BASE + ".xisf", has_wcs=False),
            _m(BASE + "_autocrop.xisf", has_wcs=True),
        ])
        self.assertEqual(len(kept), 1)
        self.assertTrue(kept[0]["path"].endswith("_autocrop.xisf"))

    def test_larger_subframe_count_wins_among_solved(self):
        kept, _ = bam.collapse_master_variants([
            _m(BASE + ".xisf", ncombine=12),
            _m(BASE + "_autocrop.xisf", ncombine=40),
        ])
        self.assertEqual(kept[0]["ncombine"], 40)

    def test_different_filters_are_never_merged(self):
        a = _m("masterLight_FILTER-Ha_mono.xisf", filt="Ha")
        b = _m("masterLight_FILTER-Ha_mono_final.xisf", filt="OIII")
        kept, dropped = bam.collapse_master_variants([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_different_pointings_are_never_merged(self):
        """A shared stem across two fields is a name collision, not a variant."""
        a = _m(BASE + ".xisf")
        b = _m(BASE + "_final.xisf", ra=10.0, dec=20.0)
        kept, dropped = bam.collapse_master_variants([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_same_name_in_two_folders_stays_separate(self):
        a = _m(BASE + ".xisf", folder="/archive/night1/master")
        b = _m(BASE + ".xisf", folder="/archive/night2/master")
        kept, dropped = bam.collapse_master_variants([a, b])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_missing_coords_do_not_block_a_collapse(self):
        a = _m(BASE + ".xisf")
        b = _m(BASE + "_starless.xisf", ra=None, dec=None, has_wcs=False)
        kept, dropped = bam.collapse_master_variants([a, b])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_hours_are_counted_once_after_collapse(self):
        """The point of the whole exercise."""
        masters = [_m(BASE + ".xisf", ncombine=40),
                   _m(BASE + "_autocrop.xisf", ncombine=40),
                   _m(BASE + "_drizzle_2x.xisf", ncombine=40)]
        before = bam.build_filters_data(masters)["B"]["total_hours"]
        kept, _ = bam.collapse_master_variants(masters)
        after = bam.build_filters_data(kept)["B"]["total_hours"]
        self.assertAlmostEqual(before, 4.0, places=3)
        self.assertAlmostEqual(after, 40 * 120.0 / 3600.0, places=3)

    def test_empty_input(self):
        self.assertEqual(bam.collapse_master_variants([]), ([], []))


if __name__ == "__main__":
    unittest.main()
