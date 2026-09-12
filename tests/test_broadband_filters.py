"""A light pollution filter is a wide luminance, not a band of its own.

26.2 hours of the maintainer's archive sat under bands called IDAS and LPS,
where nothing planning luminance would ever look for it. These filters cut the
mercury and sodium lines and pass the rest, so on a mono camera they are L and
on a colour camera they are RGB at once, exactly like shooting with no filter.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import (  # noqa: E402
    _BROADBAND_LIKE_NOFILTER,
    _MULTI_BAND,
    bands_for,
    canon_filter,
)

IDAS_BROADBAND = ("IDAS", "LPS", "LPS-D1", "LPS-D2", "LPS-D3",
                  "LPS-P2", "LPS-P3", "LPS-V4", "LPS-A1")


class TestTheIdasFamilyReadsAsLuminance(unittest.TestCase):
    def test_each_one_credits_l_on_a_mono_camera(self):
        for name in IDAS_BROADBAND:
            with self.subTest(name=name):
                self.assertEqual(bands_for(canon_filter(name), False), ["L"])

    def test_each_one_credits_full_colour_on_a_colour_camera(self):
        for name in IDAS_BROADBAND:
            with self.subTest(name=name):
                self.assertEqual(bands_for(canon_filter(name), True), ["R", "G", "B"])

    def test_the_makers_name_in_front_does_not_change_it(self):
        self.assertEqual(canon_filter("IDAS LPS-D1"), "LPS-D1")
        self.assertEqual(bands_for(canon_filter("IDAS LPS-D1"), False), ["L"])

    def test_a_space_reads_the_same_as_a_hyphen(self):
        for spaced, hyphened in (("LPS D1", "LPS-D1"), ("LPS P2", "LPS-P2")):
            with self.subTest(name=spaced):
                self.assertEqual(canon_filter(spaced), hyphened)

    def test_they_are_recognised_so_they_never_reach_the_unknown_report(self):
        for name in IDAS_BROADBAND:
            with self.subTest(name=name):
                self.assertIn(canon_filter(name), _BROADBAND_LIKE_NOFILTER)


class TestNarrowbandIsNotSweptUp(unittest.TestCase):
    def test_the_idas_dual_band_filters_still_credit_two_bands(self):
        """NBZ is an IDAS filter too, and it is emphatically not a luminance."""
        self.assertEqual(bands_for(canon_filter("NBZ"), False), ["Ha", "OIII"])
        self.assertNotIn("NBZ", _BROADBAND_LIKE_NOFILTER)

    def test_no_filter_is_both_broadband_and_multi_band(self):
        self.assertEqual(_BROADBAND_LIKE_NOFILTER & set(_MULTI_BAND), set())

    def test_an_ordinary_narrowband_filter_is_untouched(self):
        for name, band in (("Ha", "Ha"), ("O3", "OIII"), ("S2", "SII")):
            with self.subTest(name=name):
                self.assertEqual(bands_for(canon_filter(name), False), [band])


class TestAWheelSlotNumberNamesNoBand(unittest.TestCase):
    """Some wheels report the slot rather than the filter in it."""

    def test_a_bare_number_reads_as_no_filter_recorded(self):
        for raw in ("2", "07", "3", "12"):
            with self.subTest(raw=raw):
                self.assertIsNone(canon_filter(raw))

    def test_it_lands_in_the_same_band_as_a_missing_filter(self):
        self.assertEqual(bands_for(canon_filter("2"), False),
                         bands_for(canon_filter(None), False))

    def test_a_number_inside_a_real_filter_name_is_safe(self):
        self.assertEqual(canon_filter("O3"), "OIII")
        self.assertEqual(canon_filter("S2"), "SII")
        self.assertEqual(canon_filter("LPS-D1"), "LPS-D1")

    def test_a_negative_or_decimal_is_not_treated_as_a_slot(self):
        """Only a plain run of digits, so an odd name is still reported."""
        self.assertIsNotNone(canon_filter("2.5"))
        self.assertIsNotNone(canon_filter("-2"))


if __name__ == "__main__":
    unittest.main()
