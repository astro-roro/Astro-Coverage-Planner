"""A filter name that says "I don't know" means the same as no filter keyword.

Three of the maintainer's targets carried both an "Unknown" band and an
"unknown" band, because one came from the missing-keyword fallback and the other
from a header whose FILTER field literally read "unknown". Each row counted the
same hours, so Helix reported 3.17 hours twice.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import (  # noqa: E402
    FILTER_CANON,
    _FILTER_PLACEHOLDERS,
    bands_for,
    canon_filter,
    filter_label,
)


class TestPlaceholdersBecomeNoFilterAtAll(unittest.TestCase):
    def test_every_placeholder_reads_as_absent(self):
        for raw in _FILTER_PLACEHOLDERS:
            with self.subTest(raw=raw):
                self.assertIsNone(canon_filter(raw))

    def test_case_and_padding_do_not_matter(self):
        for raw in ("unknown", "Unknown", "UNKNOWN", "  Unknown  ", "uNkNoWn"):
            with self.subTest(raw=raw):
                self.assertIsNone(canon_filter(raw))

    def test_an_unknown_filter_lands_in_the_same_band_as_a_missing_one(self):
        self.assertEqual(bands_for(canon_filter("unknown"), False),
                         bands_for(canon_filter(None), False))
        self.assertEqual(bands_for(canon_filter("Unknown"), False), ["Unknown"])

    def test_a_colour_frame_with_an_unknown_filter_is_still_full_colour(self):
        self.assertEqual(bands_for(canon_filter("unknown"), True), ["R", "G", "B"])

    def test_the_label_matches_the_missing_case_too(self):
        self.assertEqual(filter_label(canon_filter("unknown"), False), "Unknown")
        self.assertEqual(filter_label(canon_filter("unknown"), True), "OSC")


class TestRealFilterNamesSurvive(unittest.TestCase):
    def test_no_filter_in_the_wheel_is_not_a_placeholder(self):
        """NINA writes None when the wheel holds no filter, which means L."""
        for raw in ("None", "NONE", "No Filter", "NoFilter"):
            with self.subTest(raw=raw):
                self.assertEqual(canon_filter(raw), "NoFilter")
        self.assertEqual(bands_for("NoFilter", False), ["L"])

    def test_a_sodium_filter_is_not_read_as_not_applicable(self):
        self.assertNotIn("NA", _FILTER_PLACEHOLDERS)
        self.assertIsNotNone(canon_filter("Na"))

    def test_no_placeholder_shadows_a_real_canon_key(self):
        self.assertEqual(_FILTER_PLACEHOLDERS & set(FILTER_CANON), set())

    def test_an_ordinary_filter_is_untouched(self):
        self.assertEqual(canon_filter("Ha"), "Ha")
        self.assertEqual(canon_filter("OIII"), "OIII")


if __name__ == "__main__":
    unittest.main()
