"""Tests for flattened-archive dedup (issue #24).

Pure-function coverage — no FITS I/O — of the helpers that stop a manually
flattened WBPP archive from double-counting integration:

  1. ``collapse_fits_xisf_pairs`` — collapse an in-folder ``X.fits`` + ``X*.xisf``
     pair (same frame, two pipeline stages) to one physical frame.
  2. ``_strip_stage_suffix`` / ``canon_stage_name`` — anchored WBPP stage-suffix
     stripping and stage-folder alias resolution.
  3. ``session_root_and_stage`` / ``detect_originals_master_siblings`` — an
     ``original*/`` folder beside a ``master/`` folder resolving to the master's
     session root (gated on master-present).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path, PurePath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_archive_manifest import (  # noqa: E402
    canon_stage_name,
    collapse_fits_xisf_pairs,
    detect_originals_master_siblings,
    detect_wbpp_session_roots,
    session_root_and_stage,
    WBPP_STAGE_SEQUENCE,
    WBPP_STAGE_SUFFIXES,
    _candidate_stripped_stems,
    _strip_stage_suffix,
)


# ---------------------------------------------------------------------------
# collapse_fits_xisf_pairs
# ---------------------------------------------------------------------------

class TestCollapseFitsXisfPairs(unittest.TestCase):
    # (label, input_paths, expected_n_physical, expected_surviving_basenames)
    CASES = [
        # Raw FITS + its calibrated/registered XISF sibling → one frame, keep XISF.
        ("pair_c_r", ["d/light_001.fits", "d/light_001_c_r.xisf"], 1, {"light_001_c_r.xisf"}),
        # Bare _c suffix.
        ("pair_c", ["d/frame.fit", "d/frame_c.xisf"], 1, {"frame_c.xisf"}),
        # Cosmetic-corrected suffix.
        ("pair_cc", ["d/f.fits", "d/f_cc.xisf"], 1, {"f_cc.xisf"}),
        # Two raw frames, no XISF — no-op.
        ("noop_all_fits", ["d/light_001.fits", "d/light_002.fits"], 2,
         {"light_001.fits", "light_002.fits"}),
        # Two XISF frames, no FITS — no-op.
        ("noop_all_xisf", ["d/a_c.xisf", "d/b_c.xisf"], 2, {"a_c.xisf", "b_c.xisf"}),
        # Single file — no-op.
        ("noop_single", ["d/only.fits"], 1, {"only.fits"}),
        # Different frame numbers must NOT pair (no fuzzy matching).
        ("unrelated_prefix", ["d/m31_001.fits", "d/m31_002_c.xisf"], 2,
         {"m31_001.fits", "m31_002_c.xisf"}),
        # A stem ending in a non-allowlisted token is left alone (anchored allowlist).
        ("non_allowlisted_suffix", ["d/ngc6960_west.fits", "d/ngc6960_west_x.xisf"], 2,
         {"ngc6960_west.fits", "ngc6960_west_x.xisf"}),
        # Mixed folder: one true pair + one lone raw frame.
        ("pair_plus_lone", ["d/a.fits", "d/a_c.xisf", "d/b.fits"], 2, {"a_c.xisf", "b.fits"}),
        # .fts member of the FITS family still pairs.
        ("fts_family", ["d/x.fts", "d/x_r.xisf"], 1, {"x_r.xisf"}),
        # OSC / debayered flattened pair: the full _c_cc_d chain (calibrate ->
        # cosmetic -> debayer) must strip back to the raw stem and collapse.
        # This is the chain the old hand-written allowlist missed (issue #24).
        ("osc_flattened_c_cc_d", ["d/x.fits", "d/x_c_cc_d.xisf"], 1, {"x_c_cc_d.xisf"}),
        # Full four-stage OSC chain collapses too.
        ("osc_full_chain", ["d/Light_001.fits", "d/Light_001_c_cc_d_r.xisf"], 1,
         {"Light_001_c_cc_d_r.xisf"}),
        # Directional collapse: two distinct raw FITS frames that differ only by
        # a suffix token stay TWO frames even when an XISF shares the stripped
        # stem. x_c.xisf strips longest to "x" (a FITS) and is absorbed there;
        # x_c.fits stays its own frame. FITS never merges with FITS.
        ("directional_two_fits", ["d/x.fits", "d/x_c.fits", "d/x_c.xisf"], 2,
         {"x_c.xisf", "x_c.fits"}),
        # Same shape as the medium-severity finding: sub.fits + sub_r.fits +
        # sub.xisf must NOT collapse the two distinct raw frames into one.
        ("directional_under_count", ["d/sub.fits", "d/sub_r.fits", "d/sub.xisf"], 2,
         {"sub.xisf", "sub_r.fits"}),
    ]

    def test_cases(self):
        for label, paths, exp_n, exp_names in self.CASES:
            with self.subTest(case=label):
                collapsed, n_physical = collapse_fits_xisf_pairs(paths)
                self.assertEqual(n_physical, exp_n)
                self.assertEqual(len(collapsed), exp_n)
                self.assertEqual({Path(p).name for p in collapsed}, exp_names)

    def test_single_extension_folder_is_exact_noop(self):
        """Single-extension folders return the same paths, same order, unchanged."""
        for paths in (
            ["d/l1.fits", "d/l2.fits", "d/l3.fits"],
            ["d/c1.xisf", "d/c2.xisf"],
        ):
            with self.subTest(paths=paths):
                collapsed, n = collapse_fits_xisf_pairs(paths)
                self.assertEqual(collapsed, paths)
                self.assertEqual(n, len(paths))

    def test_empty_input(self):
        self.assertEqual(collapse_fits_xisf_pairs([]), ([], 0))

    def test_prefers_xisf_for_sample_meta(self):
        """The surviving representative of a pair is always the XISF member."""
        collapsed, _ = collapse_fits_xisf_pairs(["d/frame_001.fits", "d/frame_001_c.xisf"])
        self.assertEqual(len(collapsed), 1)
        self.assertTrue(collapsed[0].lower().endswith(".xisf"))

    def test_all_xisf_same_stem_is_intentional_noop(self):
        """INTENTIONAL LIMITATION: with no FITS anchor we cannot collapse XISFs.

        ``a.xisf`` + ``a_c.xisf`` is plainly a raw frame and its calibrated
        derivative, but the collapse is *directional* — it only fires when a
        FITS-family file is present to anchor the group. Without that raw FITS we
        cannot distinguish a genuine stage-derivative from two distinct frames
        whose names merely collide on a WBPP suffix token, and merging XISF-into-
        XISF would re-open the under-count/frame-loss hole the directional rule
        closes. So this case stays TWO frames by design. The reviewers flagged
        this boundary; this test locks it in so a future "improvement" that
        starts merging same-stem XISFs trips an explicit, documented failure.
        """
        collapsed, n = collapse_fits_xisf_pairs(["d/a.xisf", "d/a_c.xisf"])
        self.assertEqual(n, 2)
        self.assertEqual({Path(p).name for p in collapsed}, {"a.xisf", "a_c.xisf"})


# ---------------------------------------------------------------------------
# WBPP_STAGE_SUFFIXES generation (issue #24 finding 1)
# ---------------------------------------------------------------------------

class TestStageSuffixGeneration(unittest.TestCase):
    """The suffix allowlist is generated from the stage sequence, not hand-typed.

    The old hand-enumerated list silently dropped every chain containing both
    cosmetic-correction (_cc) and debayer (_d) — the exact path OSC/colour data
    takes — so flattened OSC archives never collapsed and still double-counted.
    """

    def test_sequence_is_pipeline_order(self):
        self.assertEqual(WBPP_STAGE_SEQUENCE, ("c", "cc", "d", "r"))

    def test_previously_missing_cc_d_chains_present(self):
        """The chains the hand-written allowlist missed must now exist."""
        for chain in ("_cc_d", "_c_cc_d", "_cc_d_r", "_c_cc_d_r"):
            with self.subTest(chain=chain):
                self.assertIn(chain, WBPP_STAGE_SUFFIXES)

    def test_full_combinatoric_set(self):
        """All 15 non-empty ordered subsets of 4 stages are generated, order kept."""
        expected = {
            "_c", "_cc", "_d", "_r",
            "_c_cc", "_c_d", "_c_r", "_cc_d", "_cc_r", "_d_r",
            "_c_cc_d", "_c_cc_r", "_c_d_r", "_cc_d_r",
            "_c_cc_d_r",
        }
        self.assertEqual(set(WBPP_STAGE_SUFFIXES), expected)
        self.assertEqual(len(WBPP_STAGE_SUFFIXES), len(expected))

    def test_chains_preserve_stage_order(self):
        """No chain reorders the pipeline (e.g. never _d_c or _r_cc)."""
        rank = {s: i for i, s in enumerate(WBPP_STAGE_SEQUENCE)}
        for chain in WBPP_STAGE_SUFFIXES:
            tokens = chain.lstrip("_").split("_")
            ranks = [rank[t] for t in tokens]
            with self.subTest(chain=chain):
                self.assertEqual(ranks, sorted(ranks))

    def test_ordered_longest_first(self):
        """The tuple is longest-chain-first so the most specific match strips."""
        lengths = [len(s) for s in WBPP_STAGE_SUFFIXES]
        self.assertEqual(lengths, sorted(lengths, reverse=True))

    def test_candidate_stems_longest_strip_first(self):
        """Candidate base stems run longest-strip-first, full stem last."""
        self.assertEqual(
            _candidate_stripped_stems("light_001_c_cc_d_r"),
            ["light_001", "light_001_c", "light_001_c_cc",
             "light_001_c_cc_d", "light_001_c_cc_d_r"],
        )
        # A stem with no recognised suffix yields just itself.
        self.assertEqual(_candidate_stripped_stems("ngc6960_west"), ["ngc6960_west"])


# ---------------------------------------------------------------------------
# _strip_stage_suffix
# ---------------------------------------------------------------------------

class TestStripStageSuffix(unittest.TestCase):
    CASES = [
        ("frame_001_c", "frame_001"),
        ("frame_001_cc", "frame_001"),
        ("frame_001_r", "frame_001"),
        ("frame_001_d", "frame_001"),
        ("frame_001_c_r", "frame_001"),
        ("frame_001_c_cc", "frame_001"),
        ("frame_001_cc_r", "frame_001"),
        ("frame_001_c_d", "frame_001"),
        ("frame_001_c_cc_r", "frame_001"),
        ("frame_001_c_d_r", "frame_001"),
        # The cc+d chains the old allowlist missed (issue #24, OSC data).
        ("frame_001_cc_d", "frame_001"),
        ("frame_001_c_cc_d", "frame_001"),
        ("frame_001_cc_d_r", "frame_001"),
        ("frame_001_c_cc_d_r", "frame_001"),
        # Longest chain wins over a shorter trailing match.
        ("light_c_cc_r", "light"),
        ("light_c_cc_d_r", "light"),
        # No recognised suffix — unchanged.
        ("ngc6960_west", "ngc6960_west"),
        ("m31", "m31"),
        # A token that is not in the allowlist is not stripped.
        ("frame_x", "frame_x"),
    ]

    def test_cases(self):
        for stem, expected in self.CASES:
            with self.subTest(stem=stem):
                self.assertEqual(_strip_stage_suffix(stem), expected)


# ---------------------------------------------------------------------------
# canon_stage_name (alias map)
# ---------------------------------------------------------------------------

class TestStageFolderAliases(unittest.TestCase):
    CASES = [
        ("cal", "calibrated"),
        ("reg", "registered"),
        ("aligned", "registered"),
        ("masters", "master"),
        ("integration", "master"),
        ("original", "og"),
        ("originals", "og"),
        ("original_fits", "og"),
        # Canonical names pass through unchanged.
        ("calibrated", "calibrated"),
        ("registered", "registered"),
        ("master", "master"),
        ("og", "og"),
        ("starless", "starless"),
        ("stars", "stars"),
        # Unknown names pass through unchanged.
        ("root", "root"),
        ("lights", "lights"),
    ]

    def test_cases(self):
        for name, expected in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(canon_stage_name(name), expected)


class TestAliasedStageResolution(unittest.TestCase):
    """Aliased stage folders resolve to canonical stages inside a session."""

    def test_aliased_folders_detected_as_wbpp_session(self):
        buckets = ["S/T/cal", "S/T/reg", "S/T/master"]
        roots = detect_wbpp_session_roots(buckets)
        self.assertEqual(roots, {str(PurePath("S/T"))})

    def test_session_root_and_stage_canonicalises(self):
        buckets = ["S/T/cal", "S/T/reg", "S/T/master"]
        roots = detect_wbpp_session_roots(buckets)
        cases = [
            ("S/T/cal", "calibrated"),
            ("S/T/reg", "registered"),
            ("S/T/master", "master"),
        ]
        for bucket, expected_stage in cases:
            with self.subTest(bucket=bucket):
                sr, stage = session_root_and_stage(bucket, roots)
                self.assertEqual(sr, str(PurePath("S/T")))
                self.assertEqual(stage, expected_stage)

    def test_non_wbpp_alias_not_deduped(self):
        """A lone aliased folder with no session siblings is not a WBPP root."""
        roots = detect_wbpp_session_roots(["Images/cal/job_hash_a"])
        self.assertEqual(roots, set())
        sr, stage = session_root_and_stage("Images/cal/job_hash_a", roots)
        self.assertEqual(stage, "root")
        self.assertEqual(sr, "Images/cal/job_hash_a")


# ---------------------------------------------------------------------------
# Originals-sibling resolution
# ---------------------------------------------------------------------------

class TestOriginalsSibling(unittest.TestCase):

    def test_originals_beside_master_maps_to_session_root(self):
        # original_lights/ is not a stage-folder name, but it sits beside master/.
        buckets = ["S/T/master", "S/T/original_lights"]
        osr = detect_originals_master_siblings(buckets)
        self.assertEqual(osr, {"S/T/original_lights": str(PurePath("S/T"))})

    def test_originals_variants_match(self):
        for name in ("original", "originals", "original_fits", "original_lights",
                     "originals_backup", "original-frames"):
            with self.subTest(name=name):
                buckets = [f"S/T/master", f"S/T/{name}"]
                osr = detect_originals_master_siblings(buckets)
                self.assertEqual(osr.get(f"S/T/{name}"), str(PurePath("S/T")))

    def test_standalone_originals_untouched(self):
        """An originals/ folder with no master sibling gets no mapping."""
        osr = detect_originals_master_siblings(["S/X/originals", "S/X/originals/sub"])
        self.assertEqual(osr, {})

    def test_unrelated_folder_not_matched(self):
        """A non-originals folder beside a master is not mapped."""
        osr = detect_originals_master_siblings(["S/T/master", "S/T/calibrated"])
        self.assertEqual(osr, {})

    def test_session_root_and_stage_resolves_originals(self):
        buckets = ["S/T/master", "S/T/original_lights"]
        osr = detect_originals_master_siblings(buckets)
        roots = detect_wbpp_session_roots(buckets)
        # The flattened session is detected even though original_lights/ is not a stage name.
        self.assertIn(str(PurePath("S/T")), roots)
        sr, stage = session_root_and_stage("S/T/original_lights", roots, osr)
        self.assertEqual(sr, str(PurePath("S/T")))
        self.assertEqual(stage, "og")
        # And the master resolves to the same session root, so suppression keys align.
        msr, mstage = session_root_and_stage("S/T/master", roots, osr)
        self.assertEqual(msr, str(PurePath("S/T")))
        self.assertEqual(mstage, "master")

    def test_standalone_originals_stay_root(self):
        """Without a master sibling, originals resolve to 'root' and are counted."""
        buckets = ["S/X/originals"]
        osr = detect_originals_master_siblings(buckets)
        roots = detect_wbpp_session_roots(buckets)
        sr, stage = session_root_and_stage("S/X/originals", roots, osr)
        self.assertEqual(stage, "root")
        self.assertEqual(sr, "S/X/originals")


if __name__ == "__main__":
    unittest.main()
