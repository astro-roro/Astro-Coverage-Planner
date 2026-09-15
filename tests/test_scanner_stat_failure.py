"""A stat that fails must not pass size 0 and mtime 0 on in silence.

Finding E1 of notes/security-run-plan.md, and the last one of Part E. The walk
read every file's size and date in one call and swallowed any error:

    try:
        st = p.stat()
        sz, mtime = st.st_size, st.st_mtime
    except Exception:
        sz, mtime = 0, 0.0

On a network share that is not hypothetical. An SMB reconnect, a NAS spinning
up from sleep, or a path longer than Windows will open all raise here. Two
things then went wrong quietly.

Size decides master against sub above 200MB, so a stacked master with no
subframe count and no master-ish name was counted as a single sub and its hours
were wrong. And zero is a size a real file can have, so the entry written into
the scan cache would match again on the next failure and pin the invented
numbers in place for as long as the failure lasted.

The file is still scanned, because its header may read fine when stat did not.
What changed is that the failure is now counted, named in the scan log, kept out
of the cache, and carried into the manifest.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


def _archive(d: Path, names=("light_0001.fits", "light_0002.fits")) -> Path:
    for n in names:
        (d / n).write_bytes(b"x" * 4096)
    return d


def _stat_raises_for(victim: str, err: Exception):
    """Real stat everywhere except the named file, which raises.

    A plain function, not a callable object: patched onto the class, only a
    function becomes a bound method, and an instance would be handed no self.
    """
    real = Path.stat

    def fake(self, *a, **kw):
        if Path(self).name == victim:
            raise err
        return real(self, *a, **kw)

    return fake


class TestTheFailureIsRecorded(unittest.TestCase):
    def _walk(self, victim="light_0002.fits",
              err=OSError(13, "Permission denied")):
        with tempfile.TemporaryDirectory() as d:
            root = _archive(Path(d))
            with mock.patch.object(Path, "stat", _stat_raises_for(victim, err)):
                return bam.glob_archive([root], bam.EXTENSIONS,
                                        log=lambda _m: None)

    def test_the_file_is_still_in_the_scan(self):
        """Dropping it would repeat the silent loss this run keeps finding."""
        walk = self._walk()
        self.assertEqual({p.name for p, _s, _m in walk.files},
                         {"light_0001.fits", "light_0002.fits"})

    def test_the_failure_is_recorded_with_its_path_and_cause(self):
        walk = self._walk()
        self.assertEqual(len(walk.stat_failures), 1)
        row = walk.stat_failures[0]
        self.assertTrue(row["path"].endswith("light_0002.fits"))
        self.assertIn("Permission denied", row["error"])

    def test_a_readable_archive_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            walk = bam.glob_archive([_archive(Path(d))], bam.EXTENSIONS,
                                    log=lambda _m: None)
            self.assertEqual(walk.stat_failures, [])

    def test_the_long_path_error_windows_raises_is_recorded_too(self):
        """WinError 3 is what a path past 260 characters gives you."""
        walk = self._walk(err=OSError(2, "The system cannot find the path specified"))
        self.assertEqual(len(walk.stat_failures), 1)
        self.assertIn("cannot find the path", walk.stat_failures[0]["error"])

    def test_a_value_error_is_recorded_rather_than_ending_the_scan(self):
        """A null byte in a name off a hostile share raises ValueError, not OSError."""
        walk = self._walk(err=ValueError("embedded null byte"))
        self.assertEqual(len(walk.stat_failures), 1)
        self.assertIn("ValueError", walk.stat_failures[0]["error"])
        self.assertEqual(len(walk.files), 2)

    def test_the_size_and_date_fall_back_to_zero(self):
        """Pinned because the rest of the run still has to read something."""
        walk = self._walk()
        sz, mtime = [(s, m) for p, s, m in walk.files
                     if p.name == "light_0002.fits"][0]
        self.assertEqual((sz, mtime), (0, 0.0))


class TestTheFailureIsSaidOutLoud(unittest.TestCase):
    def test_the_log_gives_a_count_and_the_consequence(self):
        lines = []
        with tempfile.TemporaryDirectory() as d:
            root = _archive(Path(d))
            victim = _stat_raises_for("light_0002.fits",
                                    OSError(13, "Permission denied"))
            with mock.patch.object(Path, "stat", victim):
                bam.glob_archive([root], bam.EXTENSIONS, log=lines.append)
        warn = [l for l in lines if "could not read the size" in l]
        self.assertEqual(len(warn), 1)
        self.assertIn("1 file(s)", warn[0])
        self.assertIn("master", warn[0])

    def test_the_failing_paths_are_named(self):
        lines = []
        with tempfile.TemporaryDirectory() as d:
            root = _archive(Path(d))
            victim = _stat_raises_for("light_0002.fits",
                                    OSError(13, "Permission denied"))
            with mock.patch.object(Path, "stat", victim):
                bam.glob_archive([root], bam.EXTENSIONS, log=lines.append)
        self.assertTrue(any("light_0002.fits" in l and "Permission denied" in l
                            for l in lines))

    def test_a_clean_walk_says_nothing_about_stat(self):
        lines = []
        with tempfile.TemporaryDirectory() as d:
            bam.glob_archive([_archive(Path(d))], bam.EXTENSIONS,
                             log=lines.append)
        self.assertEqual([l for l in lines if "could not read the size" in l], [])


class TestAPathTooLongForTheSystem(unittest.TestCase):
    """The case that sent me looking, and it turns out to be handled already.

    Windows refuses a path past 260 characters unless LongPathsEnabled is 1,
    and 0 is the default. macOS refuses one past 1024. Either way the failure
    lands when the directory is listed rather than when the file is stat'd, so
    os.walk's onerror fires and the folder is reported as unreadable.

    Written to hold on both platforms whatever the limit is, because a runner
    with long paths enabled simply walks it. What must never happen is the
    third outcome: the frame absent and nothing said.
    """

    def _bury(self, root: Path, depth: int, segment_len: int) -> None:
        import os
        seg = "d" * segment_len
        cwd = os.getcwd()
        os.chdir(root)
        try:
            for _ in range(depth):
                os.mkdir(seg)
                os.chdir(seg)
            Path("light_deep.fits").write_bytes(b"x" * 4096)
        finally:
            os.chdir(cwd)

    def test_the_frame_is_either_found_or_reported_never_silently_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "light_0001.fits").write_bytes(b"x" * 4096)
            try:
                self._bury(root, depth=12, segment_len=200)
            except OSError:
                self.skipTest("cannot build a path that long here")
            walk = bam.glob_archive([root], bam.EXTENSIONS, log=lambda _m: None)
            found = {p.name for p, _s, _m in walk.files}
            self.assertIn("light_0001.fits", found)
            reported = bool(walk.unreadable_dirs) or bool(walk.stat_failures)
            self.assertTrue("light_deep.fits" in found or reported,
                            "the deep frame was neither scanned nor reported")


class TestTheErrorsAreGrouped(unittest.TestCase):
    """One cause usually hits many files at once, so group by the message."""

    def test_forty_files_behind_one_cause_make_one_row(self):
        rows = bam._group_by_error(
            [{"path": f"/a/light_{i:04d}.fits", "error": "OSError: Permission denied"}
             for i in range(40)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows["OSError: Permission denied"]), 40)

    def test_two_causes_stay_apart(self):
        rows = bam._group_by_error([
            {"path": "/a/x.fits", "error": "OSError: Permission denied"},
            {"path": "/a/y.fits", "error": "OSError: Host is down"},
        ])
        self.assertEqual(sorted(rows), ["OSError: Host is down",
                                        "OSError: Permission denied"])


class TestTheScanHealthPanel(unittest.TestCase):
    def _panel(self, flags):
        import app
        return app._scan_health({"integrity_flags": flags})

    def test_the_count_and_the_grouped_examples_reach_the_panel(self):
        panel = self._panel({
            "stat_failure_count": 40,
            "stat_failures": [{"error": "OSError: Permission denied",
                               "files": 40,
                               "examples": ["/a/light_0001.fits"]}],
        })
        self.assertEqual(panel["stat_failures"], 40)
        self.assertEqual(panel["stat_failures_examples"][0]["files"], 40)

    def test_a_manifest_written_before_today_still_renders(self):
        panel = self._panel({})
        self.assertEqual(panel["stat_failures"], 0)
        self.assertEqual(panel["stat_failures_examples"], [])

    def test_junk_does_not_take_the_panel_down(self):
        panel = self._panel({"stat_failure_count": "lots",
                             "stat_failures": "not a list"})
        self.assertEqual(panel["stat_failures"], 0)
        self.assertEqual(panel["stat_failures_examples"], [])


class TestTheSkippedLinkIsSurfaced(unittest.TestCase):
    """The other half of E1d, which the scan log carried and nothing else did.

    Refusing to follow a link out of the archive is correct. A user who moved a
    capture folder off C: with a junction still sees fewer hours than they
    expect, and the panel is where they would look for the reason.
    """

    def _panel(self, flags):
        import app
        return app._scan_health({"integrity_flags": flags})

    def test_the_count_and_the_paths_reach_the_panel(self):
        panel = self._panel({"skipped_link_count": 2,
                             "skipped_links": ["/archive/elsewhere",
                                               "/archive/old"]})
        self.assertEqual(panel["skipped_links"], 2)
        self.assertEqual(panel["skipped_links_examples"],
                         ["/archive/elsewhere", "/archive/old"])

    def test_a_manifest_written_before_today_still_renders(self):
        panel = self._panel({})
        self.assertEqual(panel["skipped_links"], 0)
        self.assertEqual(panel["skipped_links_examples"], [])

    def test_junk_does_not_take_the_panel_down(self):
        panel = self._panel({"skipped_link_count": None,
                             "skipped_links": "not a list"})
        self.assertEqual(panel["skipped_links"], 0)
        self.assertEqual(panel["skipped_links_examples"], [])

    def test_the_walk_still_reports_the_link_it_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "archive"
            root.mkdir()
            outside = Path(d) / "outside"
            outside.mkdir()
            (outside / "secret_0001.fits").write_bytes(b"x" * 4096)
            try:
                (root / "link").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("cannot create a directory symlink here")
            walk = bam.glob_archive([root], bam.EXTENSIONS, log=lambda _m: None)
            self.assertEqual(walk.files, [])
            self.assertEqual([Path(x).name for x in walk.skipped_links], ["link"])


if __name__ == "__main__":
    unittest.main()
