"""The header cache must go cold whenever the code that filled it changes.

Hashing the two reader bodies alone was not enough, and the failure was silent.
Both readers call canon_filter. When it was taught on 2026-09-12 that a FILTER
of "unknown" names no filter, every warm scan kept serving the old answer, and
no run said so.
"""
from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def _reset_memo():
    """Forget the computed fingerprint, the way starting a new process would."""
    bam._SCAN_CACHE_FINGERPRINT = None


class TestTheFingerprintIsStable(unittest.TestCase):
    def test_the_same_code_gives_the_same_answer_twice(self):
        self.assertEqual(bam.scan_cache_fingerprint(), bam.scan_cache_fingerprint())

    def test_reading_files_does_not_move_it(self):
        """An accumulator in the hash would make every scan look cold."""
        before = bam.scan_cache_fingerprint()
        bam.canon_filter("AFilterNobodyHasHeardOf")
        bam.canon_filter("AndAnother")
        self.assertEqual(bam.scan_cache_fingerprint(), before)

    def test_it_is_a_full_length_hex_digest(self):
        fp = bam.scan_cache_fingerprint()
        self.assertEqual(len(fp), 64)
        int(fp, 16)


class TestItCoversWhatTheReadersUse(unittest.TestCase):
    def _names(self):
        parts = bam._reader_code_and_tables()
        funcs = {p.split("(")[0][4:] for p in parts if p.startswith("def ")}
        tables = {p.split("=")[0] for p in parts if not p.startswith("def ")}
        return funcs, tables

    def test_both_readers_are_in_it(self):
        funcs, _ = self._names()
        self.assertIn("read_fits_meta", funcs)
        self.assertIn("read_xisf_meta", funcs)

    def test_a_helper_a_reader_calls_is_in_it(self):
        funcs, _ = self._names()
        for name in ("canon_filter", "sanitize_camera", "sanitize_telescope",
                     "object_from_filename", "ncombine_from_history"):
            with self.subTest(name=name):
                self.assertIn(name, funcs)

    def test_a_helper_reached_only_through_another_helper_is_in_it(self):
        """canon_filter calls _catalogue_lookup, which neither reader calls."""
        funcs, _ = self._names()
        self.assertIn("_catalogue_lookup", funcs)

    def test_a_table_those_helpers_consult_is_in_it(self):
        _, tables = self._names()
        for name in ("FILTER_CANON", "_FILTER_PLACEHOLDERS", "TELESCOPE_ALIAS"):
            with self.subTest(name=name):
                self.assertIn(name, tables)

    def test_the_filter_catalogue_is_in_it_so_editing_the_file_goes_cold(self):
        _, tables = self._names()
        self.assertIn("_FILTER_CATALOGUE", tables)

    def test_an_accumulator_is_left_out(self):
        _, tables = self._names()
        self.assertNotIn("UNRECOGNISED_FILTER_COUNTS", tables)

    def test_code_unrelated_to_reading_headers_is_left_out(self):
        """Or a four hour cold scan would follow any edit anywhere."""
        funcs, _ = self._names()
        for name in ("build_filters_data", "collapse_copied_sub_blocks",
                     "fov_corners", "main"):
            with self.subTest(name=name):
                self.assertNotIn(name, funcs)


class TestItIsComputedOncePerRun(unittest.TestCase):
    """A scan reaches the cache about twenty minutes in, after globbing the tree.

    The source is read from disk, not from the running interpreter, so an edit
    saved during that window used to change the answer under a run still
    executing the old code. That scan went cold for no reason and stamped the new
    identity on metadata the old code produced, which the next scan would trust.
    """

    def test_the_source_changing_mid_run_does_not_move_it(self):
        before = bam.scan_cache_fingerprint()
        original = bam._FILTER_PLACEHOLDERS
        try:
            bam._FILTER_PLACEHOLDERS = original | {"NO IDEA"}
            self.assertEqual(bam.scan_cache_fingerprint(), before)
        finally:
            bam._FILTER_PLACEHOLDERS = original


class TestItChangesWhenItShould(unittest.TestCase):
    """Each of these clears the memo first, the way a fresh process would."""

    def setUp(self):
        self.addCleanup(_reset_memo)
        _reset_memo()

    def test_a_new_placeholder_filter_name_makes_the_cache_cold(self):
        before = bam.scan_cache_fingerprint()
        original = bam._FILTER_PLACEHOLDERS
        try:
            bam._FILTER_PLACEHOLDERS = original | {"NO IDEA"}
            _reset_memo()
            self.assertNotEqual(bam.scan_cache_fingerprint(), before)
        finally:
            bam._FILTER_PLACEHOLDERS = original
            _reset_memo()
        self.assertEqual(bam.scan_cache_fingerprint(), before)

    def test_a_changed_schema_version_makes_the_cache_cold(self):
        before = bam.scan_cache_fingerprint()
        original = bam.SCAN_CACHE_SCHEMA
        try:
            bam.SCAN_CACHE_SCHEMA = original + 1
            _reset_memo()
            self.assertNotEqual(bam.scan_cache_fingerprint(), before)
        finally:
            bam.SCAN_CACHE_SCHEMA = original


class TestEditingAHelperMakesTheCacheCold(unittest.TestCase):
    """Edit the module source the fingerprint reads, then recompute it.

    The structural tests above show a helper is in the hash. These show the
    hash moves when that helper's code does, which is what a stale cache needs.
    """

    def setUp(self):
        self.addCleanup(_reset_memo)
        _reset_memo()
        self.real_getsource = inspect.getsource

    def _with_edit(self, func_name):
        """Fingerprint of the module as if ``func_name`` gained one statement."""
        tree = ast.parse(self.real_getsource(bam))
        node = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == func_name)
        node.body.append(ast.Expr(ast.Constant("an edit")))
        edited = ast.unparse(tree)

        def fake(obj):
            return edited if obj is bam else self.real_getsource(obj)

        _reset_memo()
        with mock.patch.object(bam.inspect, "getsource", side_effect=fake):
            return bam.scan_cache_fingerprint()

    def test_editing_a_helper_a_reader_calls_changes_it(self):
        before = bam.scan_cache_fingerprint()
        for name in ("canon_filter", "sanitize_telescope", "sanitize_camera"):
            with self.subTest(helper=name):
                self.assertNotEqual(self._with_edit(name), before)

    def test_editing_a_helper_reached_through_another_helper_changes_it(self):
        before = bam.scan_cache_fingerprint()
        self.assertNotEqual(self._with_edit("_catalogue_lookup"), before)

    def test_editing_a_reader_changes_it(self):
        before = bam.scan_cache_fingerprint()
        self.assertNotEqual(self._with_edit("read_fits_meta"), before)

    def test_editing_unrelated_code_does_not(self):
        before = bam.scan_cache_fingerprint()
        self.assertEqual(self._with_edit("fov_corners"), before)


if __name__ == "__main__":
    unittest.main()
