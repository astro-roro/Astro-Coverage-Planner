"""Tests for the TELESCOP header canonicalisation in the archive scanner.

The UI draws one colour chip per distinct telescope name, so two spellings of
the same rig ("HyperStar" and "Hyperstar") split a single scope into two chips.
sanitize_telescope folds those together, drops mount names that some capture
software writes into TELESCOP, and maps known aliases to a display name.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import (  # noqa: E402
    TELESCOPE_CANONICAL,
    sanitize_telescope,
)


class TestEmptyAndMountValues(unittest.TestCase):
    def test_none_and_blank_return_none(self):
        self.assertIsNone(sanitize_telescope(None))
        self.assertIsNone(sanitize_telescope(""))
        self.assertIsNone(sanitize_telescope("   "))

    def test_mount_names_are_dropped(self):
        for raw in ("EQ6-R Pro", "Sky-Watcher EQ6", "iOptron CEM70", "RainbowAstro RST-135"):
            with self.subTest(raw=raw):
                self.assertIsNone(sanitize_telescope(raw))


class TestAliases(unittest.TestCase):
    def test_known_aliases_map_to_display_name(self):
        cases = {
            "SW MakNewt 190": "190MN",
            "190MAK": "190MN",
            "SkyWatcher 190MakN": "190MN",
            "RedCat51": "RedCat 51",
            "William Optics RedCat 51": "RedCat 51",
            "AP 110GTX": "110GTX",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_telescope(raw), want)


class TestCanonicalCaseFolding(unittest.TestCase):
    def test_casing_variants_collapse_to_one_spelling(self):
        for raw in ("HyperStar", "Hyperstar", "HYPERSTAR", "hyperstar"):
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_telescope(raw), "HyperStar")

    def test_whitespace_variants_collapse(self):
        for raw in ("RedCat  51", " redcat 51 ", "REDCAT\t51"):
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_telescope(raw), "RedCat 51")

    def test_every_canonical_name_is_a_fixed_point(self):
        for name in TELESCOPE_CANONICAL:
            with self.subTest(name=name):
                self.assertEqual(sanitize_telescope(name), name)
                self.assertEqual(sanitize_telescope(name.lower()), name)
                self.assertEqual(sanitize_telescope(name.upper()), name)

    def test_unknown_scope_passes_through_unchanged(self):
        self.assertEqual(sanitize_telescope("Esprit 100"), "Esprit 100")
        self.assertEqual(sanitize_telescope("  Esprit 100 "), "Esprit 100")


if __name__ == "__main__":
    unittest.main()
