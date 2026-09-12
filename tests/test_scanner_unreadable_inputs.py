"""A file or folder the scanner cannot read has to say so somewhere.

Findings E1b and E1c of notes/security-run-plan.md, both confirmed against a
real archive on 2026-09-06 and fixed here.

E1b was the worse one. A directory the scanner could not enter disappeared in
silence: rglob swallows the PermissionError and the walk carries on, so a user
with one awkwardly permissioned folder on a NAS lost those hours and was never
told. It looked exactly like an archive that held fewer frames.

E1c was quieter but affected more files. Unreadable files were counted and
printed to stdout, and nowhere else. 33 files in the maintainer's own archive
were being dropped that way, and a scan run from Task Scheduler or the
container's own thread showed the user nothing at all.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402

# Computed once, because a skipIf argument is evaluated when the class body runs
# whatever the decorators above it say, and os.geteuid does not exist on Windows.
ON_WINDOWS = os.name == "nt"
# getattr rather than a platform test: geteuid is absent on Windows and the
# default of 1 just means "not root", which is the answer that matters here.
AS_ROOT = getattr(os, "geteuid", lambda: 1)() == 0
CAN_DENY_OURSELVES = not (ON_WINDOWS or AS_ROOT)
DENY_REASON = "mode bits do not deny the owner on Windows, nor root anywhere"


class TestAnUnreadableDirectoryIsReported(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "readable").mkdir()
        (self.root / "readable" / "light_0001.fits").write_bytes(b"x" * 16)
        self.blocked = self.root / "Unreadable"
        self.blocked.mkdir()
        (self.blocked / "light_0002.fits").write_bytes(b"x" * 16)
        self.addCleanup(self._restore)

    def _restore(self):
        try:
            self.blocked.chmod(stat.S_IRWXU)
        except OSError:
            pass
        self.tmp.cleanup()

    def _walk(self):
        return bam.glob_archive([self.root], bam.EXTENSIONS, log=lambda _m: None)

    def test_a_readable_tree_reports_no_unreadable_directory(self):
        files, unreadable = self._walk()
        self.assertEqual(unreadable, [])
        self.assertEqual(len(files), 2)

    @unittest.skipUnless(CAN_DENY_OURSELVES, DENY_REASON)
    def test_a_mode_000_directory_is_named_with_its_error(self):
        self.blocked.chmod(0o000)
        files, unreadable = self._walk()
        self.assertEqual(len(files), 1, "the readable frame is still found")
        self.assertEqual(len(unreadable), 1)
        row = unreadable[0]
        self.assertEqual(row["path"], str(self.blocked))
        self.assertIn("Error", row["error"])

    @unittest.skipUnless(CAN_DENY_OURSELVES, DENY_REASON)
    def test_the_warning_reaches_the_log(self):
        self.blocked.chmod(0o000)
        lines = []
        bam.glob_archive([self.root], bam.EXTENSIONS, log=lines.append)
        warnings = [l for l in lines if "could not read" in l]
        self.assertEqual(len(warnings), 1)
        self.assertIn("Unreadable", warnings[0])
        self.assertIn("missing from this scan", warnings[0])

    def test_a_missing_root_is_still_skipped_not_reported_as_unreadable(self):
        files, unreadable = bam.glob_archive(
            [self.root / "nope"], bam.EXTENSIONS, log=lambda _m: None)
        self.assertEqual(files, [])
        self.assertEqual(unreadable, [])


class TestTheWalkStillRefusesToLeaveTheArchive(unittest.TestCase):
    """E1d: a symlinked directory must not pull in files from outside the root.

    os.walk defaults to followlinks=False and that default is load bearing, so
    this test fails if anyone turns it on.
    """

    @unittest.skipIf(ON_WINDOWS, "symlinks need a privilege on Windows")
    def test_a_symlinked_directory_is_not_followed(self):
        with tempfile.TemporaryDirectory() as outside_dir, \
                tempfile.TemporaryDirectory() as inside_dir:
            outside, inside = Path(outside_dir), Path(inside_dir)
            (outside / "secret_0001.fits").write_bytes(b"x" * 16)
            (inside / "light_0001.fits").write_bytes(b"x" * 16)
            (inside / "escape").symlink_to(outside, target_is_directory=True)
            files, unreadable = bam.glob_archive(
                [inside], bam.EXTENSIONS, log=lambda _m: None)
            names = [p.name for p, _s, _m in files]
            self.assertEqual(names, ["light_0001.fits"])
            self.assertEqual(unreadable, [])


class TestExtensionMatching(unittest.TestCase):
    """The walk replaced four rglob passes, so it has to match the same files."""

    def test_every_supported_extension_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            expected = set()
            for i, ext in enumerate(bam.EXTENSIONS):
                name = f"light_{i}{ext}"
                (root / name).write_bytes(b"x" * 16)
                expected.add(name)
            files, _ = bam.glob_archive([root], bam.EXTENSIONS, log=lambda _m: None)
            self.assertEqual({p.name for p, _s, _m in files}, expected)

    def test_an_uppercase_extension_is_found_too(self):
        """Deliberate change from the four rglob passes this walk replaced.

        Pattern matching in pathlib is case sensitive, so on macOS and Linux a
        frame named LIGHT.FIT was skipped, while the same archive on Windows
        picked it up because the filesystem itself folds case. Windows is where
        most people run ACP and plenty of capture software writes .FIT, so the
        two platforms now agree and the stricter one is the one that changed.

        Stems differ per file because a case-insensitive filesystem would let
        two spellings of one name overwrite each other.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for i, ext in enumerate(bam.EXTENSIONS):
                (root / f"upper_{i}{ext.upper()}").write_bytes(b"x" * 16)
            files, _ = bam.glob_archive([root], bam.EXTENSIONS, log=lambda _m: None)
            self.assertEqual(len(files), len(bam.EXTENSIONS))

    def test_a_file_with_no_matching_extension_is_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "notes.txt").write_bytes(b"x")
            (root / "light.fits").write_bytes(b"x")
            files, _ = bam.glob_archive([root], bam.EXTENSIONS, log=lambda _m: None)
            self.assertEqual([p.name for p, _s, _m in files], ["light.fits"])

    def test_a_nested_folder_is_walked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            deep = root / "a" / "b" / "c"
            deep.mkdir(parents=True)
            (deep / "light.fit").write_bytes(b"x")
            files, _ = bam.glob_archive([root], bam.EXTENSIONS, log=lambda _m: None)
            self.assertEqual(len(files), 1)

    def test_size_and_mtime_come_back_with_each_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "light.fits").write_bytes(b"x" * 4096)
            (path, size, mtime), = bam.glob_archive(
                [root], bam.EXTENSIONS, log=lambda _m: None)[0]
            self.assertEqual(size, 4096)
            self.assertGreater(mtime, 0)


class TestUnreadableDirGrouping(unittest.TestCase):
    def test_rows_group_by_the_error_that_stopped_the_walk(self):
        grouped = bam._group_unreadable_dirs([
            {"path": "/a/one", "error": "PermissionError: denied"},
            {"path": "/a/two", "error": "PermissionError: denied"},
            {"path": "/b/three", "error": "OSError: host is down"},
        ])
        self.assertEqual(sorted(grouped), ["OSError: host is down",
                                          "PermissionError: denied"])
        self.assertEqual(len(grouped["PermissionError: denied"]), 2)

    def test_a_row_with_no_error_still_groups(self):
        grouped = bam._group_unreadable_dirs([{"path": "/a"}])
        self.assertEqual(list(grouped), ["unknown"])

    def test_nothing_in_means_nothing_out(self):
        self.assertEqual(bam._group_unreadable_dirs([]), {})


if __name__ == "__main__":
    unittest.main()


class TestErrorMessagesDoNotCarryTheArchiveLayout(unittest.TestCase):
    """These messages become manifest keys, so they reach the page.

    The API shortens a value that is a path. It cannot shorten a path sitting
    inside a sentence, and FileNotFoundError and astropy both name the file
    inside the message.
    """

    def test_a_quoted_posix_path_is_cut_to_its_file_name(self):
        self.assertEqual(
            bam.scrub_paths_from_error(
                "FileNotFoundError: [Errno 2] No such file or directory: "
                "'/Users/someone/Astro/M42/light.fit'"),
            "FileNotFoundError: [Errno 2] No such file or directory: 'light.fit'")

    def test_a_bare_windows_path_is_cut_to_its_file_name(self):
        self.assertEqual(
            bam.scrub_paths_from_error(r"OSError: cannot open D:\Astro\M42\light.fit"),
            "OSError: cannot open light.fit")

    def test_a_message_with_no_path_is_untouched(self):
        for msg in ("OSError: Empty or corrupt FITS file",
                    "PermissionError: Permission denied",
                    "ParseError: no element found: line 1, column 0"):
            with self.subTest(msg=msg):
                self.assertEqual(bam.scrub_paths_from_error(msg), msg)

    def test_a_known_root_becomes_archive_and_the_rest_stays_readable(self):
        """Roots with spaces in them are normal: the maintainer's has three."""
        out = bam.scrub_paths_from_error(
            "OSError: /Volumes/Singularity/Astro With RoRo/M42/light.fit is broken",
            ["/Volumes/Singularity/Astro With RoRo"])
        self.assertEqual(out, "OSError: <archive>/M42/light.fit is broken")
        self.assertNotIn("Singularity", out)
        self.assertNotIn("RoRo", out)

    def test_a_trailing_separator_on_the_root_does_not_matter(self):
        out = bam.scrub_paths_from_error("OSError: /a/b/M42/light.fit is broken",
                                        ["/a/b/"])
        self.assertEqual(out, "OSError: <archive>/M42/light.fit is broken")

    def test_the_longest_matching_root_wins(self):
        """Two roots where one contains the other must not leave half a path."""
        out = bam.scrub_paths_from_error("OSError: /a/b/c/M42/light.fit broken",
                                        ["/a/b", "/a/b/c"])
        self.assertEqual(out, "OSError: <archive>/M42/light.fit broken")

    def test_a_spaced_path_under_no_known_root_loses_all_of_its_layout(self):
        """The pattern allows spaces, so a few trailing words come out attached
        to the file name. That reads fine, and it never leaves half a path
        behind, which is what a whitespace-bounded pattern did."""
        out = bam.scrub_paths_from_error(
            "OSError: /Some Other Place/M42/light.fit is broken", ["/a/b"])
        self.assertEqual(out, "OSError: light.fit is broken")
        self.assertNotIn("Some Other Place", out)

    def test_no_separator_survives_anywhere_in_the_message(self):
        for msg, roots in (
            ("OSError: /Volumes/Sing/Astro With RoRo/M42/a.fit is broken",
             ["/Volumes/Sing/Astro With RoRo"]),
            ("OSError: /Some Other Place/M42/a.fit is broken", ()),
            (r"OSError: cannot open D:\Astro\M42\a.fit", ()),
            ("FileNotFoundError: no file: '/Users/x/A/a.fit'", ()),
        ):
            with self.subTest(msg=msg):
                out = bam.scrub_paths_from_error(msg, roots)
                tail = out.replace("<archive>/M42/a.fit", "")
                self.assertNotIn("\\", tail)
                self.assertNotRegex(tail, r"/[A-Za-z]")

    def test_grouping_collapses_one_error_per_file_into_one_row(self):
        """Without the scrub, every missing file made its own group."""
        msgs = [bam.scrub_paths_from_error(
            f"FileNotFoundError: [Errno 2] No such file or directory: '/a/b/f{i}.fit'")
            for i in range(3)]
        self.assertEqual(len(set(msgs)), 3, "file names are kept, so still distinct")
        dirs = [{"path": f"/a/b/{i}", "error": bam.scrub_paths_from_error(
            "PermissionError: Permission denied")} for i in range(3)]
        self.assertEqual(len(bam._group_unreadable_dirs(dirs)), 1)
