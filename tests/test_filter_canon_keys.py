"""Every FILTER_CANON key must survive the upper-casing canon_filter does.

canon_filter upper-cases its input before looking it up, so a mixed-case key is
unreachable. Three were: "Ha", "Halpha" and "H-alpha". The first two happened to
have upper-case twins already, so only "H-alpha" was actually broken, and a
filter named that way fell through to being its own band instead of landing on
Ha. Found by an audit on 2026-09-05.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_archive_manifest import FILTER_CANON, canon_filter  # noqa: E402


class TestEveryKeyIsReachable(unittest.TestCase):
    def test_every_key_has_an_upper_case_twin(self):
        """A mixed-case key is fine as documentation, but the upper-case form
        has to be present or the spelling it documents does not work."""
        missing = [k for k in FILTER_CANON if k.upper() not in FILTER_CANON]
        self.assertEqual(missing, [], f"unreachable spellings: {missing}")

    def test_every_key_maps_to_its_own_value(self):
        for key, value in FILTER_CANON.items():
            self.assertEqual(canon_filter(key), value, key)


class TestHalphaSpellings(unittest.TestCase):
    def test_all_documented_spellings_reach_ha(self):
        for name in ("H", "Ha", "HA", "ha", "Halpha", "HALPHA",
                     "H_ALPHA", "H-alpha", "H-ALPHA", "h-alpha"):
            self.assertEqual(canon_filter(name), "Ha", name)

    def test_surrounding_whitespace_does_not_defeat_it(self):
        self.assertEqual(canon_filter("  H-alpha  "), "Ha")


if __name__ == "__main__":
    unittest.main()
