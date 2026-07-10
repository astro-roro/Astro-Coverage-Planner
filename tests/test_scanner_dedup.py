"""Regression tests for content-dedup key strength (audit fix 6).

The content-dedup key used to be (filter, exptime, filename-set). Two genuinely
different targets shot with generic auto-numbered filenames at the same filter
and exposure shared a name set and collided, so one target's hours were dropped
as a "content-identical duplicate". The key now also carries pointing (RA/Dec
rounded to ~0.1 deg) and observation date, so different sessions stay distinct
while true backup copies (identical files reached twice) still collapse.
"""
from __future__ import annotations

import sys
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import content_dedup_key  # noqa: E402


def _block(*, filt="Ha", exptime=300.0, names, ra, dec, date="2021-07-30"):
    return {
        "filter": filt,
        "exptime": exptime,
        "_basenames": frozenset(names),
        "ra_deg": ra,
        "dec_deg": dec,
        "date_obs": date,
    }


# Two different targets, identical generic filenames, same filter/exposure.
GENERIC = ["Light_0001.fit", "Light_0002.fit", "Light_0003.fit"]


class TestContentDedupKey(unittest.TestCase):

    def test_different_targets_do_not_collide(self):
        a = _block(names=GENERIC, ra=311.4, dec=30.7)   # NGC 6960
        b = _block(names=GENERIC, ra=85.0, dec=-2.5)    # Orion region
        self.assertNotEqual(content_dedup_key(a), content_dedup_key(b))

    def test_true_backup_copy_still_collapses(self):
        a = _block(names=GENERIC, ra=311.4, dec=30.7)
        b = _block(names=GENERIC, ra=311.41, dec=30.72)  # same pointing (~0.1 deg)
        self.assertEqual(content_dedup_key(a), content_dedup_key(b))

    def test_same_pointing_different_night_kept(self):
        a = _block(names=GENERIC, ra=311.4, dec=30.7, date="2021-07-30")
        b = _block(names=GENERIC, ra=311.4, dec=30.7, date="2021-08-15")
        self.assertNotEqual(content_dedup_key(a), content_dedup_key(b))

    def test_empty_name_set_returns_none(self):
        self.assertIsNone(content_dedup_key(_block(names=[], ra=1.0, dec=1.0)))

    def test_missing_coords_do_not_crash(self):
        k = content_dedup_key(_block(names=GENERIC, ra=None, dec=None))
        self.assertIsNotNone(k)

    def test_grouping_behaviour_end_to_end(self):
        """Mirror the main() grouping: two different targets stay two groups."""
        blocks = [
            _block(names=GENERIC, ra=311.4, dec=30.7),
            _block(names=GENERIC, ra=85.0, dec=-2.5),
            _block(names=GENERIC, ra=311.41, dec=30.72),  # backup of the first
        ]
        groups = defaultdict(list)
        for b in blocks:
            groups[content_dedup_key(b)].append(b)
        self.assertEqual(len(groups), 2)
        sizes = sorted(len(g) for g in groups.values())
        self.assertEqual(sizes, [1, 2])  # target1 + its backup collapse; target2 alone


if __name__ == "__main__":
    unittest.main()
