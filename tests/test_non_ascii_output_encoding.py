"""A path with an accented character must not be able to kill a scan.

Finding E3 of notes/security-run-plan.md, confirmed on Windows 11 with Python
3.14 on 2026-09-13 against a NAS share.

The scan crashed on the first line that printed a non-ASCII path. Windows gives
a redirected stdout the locale encoding, cp1252 there, and cp1252 has no mapping
for the character, so a plain print raised UnicodeEncodeError and took the whole
run down before it read a single file.

Two things made it worse than one failed line.

A console never shows the problem, because Python writes to a real console
through the wide character API whatever the code page says. So it looks fine
when a person runs it by hand and fails when a scheduled task or the web app
runs it, which is the way it normally runs.

Through a pipe the exit code came back 0, because a shell pipeline reports the
reader's status rather than Python's. app.py runs the builder with
capture_output=True, so a caller checking the exit code would have read a
crashed scan as a successful one.

These tests build a cp1252 stream directly rather than asking the platform for
one, so they fail on Linux and macOS too. The bug was Windows-only, a test that
only runs on Windows is not.
"""
from __future__ import annotations

import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402

# A real archive path shape: an accented folder name, which is ordinary in
# French, Spanish, Portuguese and German, plus a character cp1252 cannot hold
# at all so the encoder has no way to guess.
AWKWARD = "D:/Astro/Ωmega/naïve_light_0001.fits"


def cp1252_stream() -> io.TextIOWrapper:
    """What Windows hands a redirected stdout, built by hand.

    line_buffering off and write_through on so the bytes land without a flush,
    which keeps each test to one assertion about one write.
    """
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict",
                            write_through=True)


class TestTheBugItself(unittest.TestCase):
    """Proof the stream this is measured against really does refuse the path."""

    def test_a_cp1252_stream_cannot_carry_the_path(self):
        stream = cp1252_stream()
        with self.assertRaises(UnicodeEncodeError):
            print(AWKWARD, file=stream)


class TestForceUtf8Output(unittest.TestCase):
    def test_the_path_survives_a_cp1252_stream_afterwards(self):
        stream = cp1252_stream()
        with mock.patch.object(sys, "stdout", stream):
            bam.force_utf8_output()
            print(AWKWARD, file=sys.stdout)
        self.assertIn(AWKWARD, stream.buffer.getvalue().decode("utf-8"))

    def test_stderr_is_fixed_too(self):
        """A traceback naming the path goes to stderr, and app.py reads it."""
        stream = cp1252_stream()
        with mock.patch.object(sys, "stderr", stream):
            bam.force_utf8_output()
            print(AWKWARD, file=sys.stderr)
        self.assertIn(AWKWARD, stream.buffer.getvalue().decode("utf-8"))

    def test_a_stream_that_cannot_be_reconfigured_is_left_alone(self):
        """Tests and callers replace stdout with objects of their own."""
        plain = io.StringIO()
        with mock.patch.object(sys, "stdout", plain):
            bam.force_utf8_output()
            print(AWKWARD, file=sys.stdout)
        self.assertIn(AWKWARD, plain.getvalue())

    def test_a_detached_stream_does_not_raise(self):
        class Detached:
            def reconfigure(self, **_kwargs):
                raise ValueError("underlying buffer has been detached")

        with mock.patch.object(sys, "stdout", Detached()):
            bam.force_utf8_output()

    def test_nothing_that_cannot_encode_is_silently_dropped(self):
        """backslashreplace, not replace: a name stays identifiable.

        Surrogates survive a Windows filename that is not valid UTF-16, and even
        UTF-8 cannot encode those, so there is still a last resort to choose.
        """
        stream = cp1252_stream()
        with mock.patch.object(sys, "stdout", stream):
            bam.force_utf8_output()
            print("light_\udce9_0001.fits", file=sys.stdout)
        written = stream.buffer.getvalue().decode("utf-8")
        self.assertIn("\\udce9", written)
        self.assertNotIn("?", written)


class TestMainCallsIt(unittest.TestCase):
    def test_main_fixes_the_streams_before_it_prints_anything(self):
        """The crash was on main's own third line, so the order matters."""
        calls = []
        with mock.patch.object(bam, "force_utf8_output",
                              lambda: calls.append("fixed")), \
                mock.patch.object(bam, "REPORT_DIR") as report_dir:
            report_dir.mkdir.side_effect = RuntimeError("stop here")
            with self.assertRaises(RuntimeError):
                bam.main()
        self.assertEqual(calls, ["fixed"])


class TestTheAppDecodesWhatTheBuilderWrites(unittest.TestCase):
    """The other half: text=True alone decodes with the system encoding."""

    def test_the_subprocess_call_names_utf8(self):
        import app

        captured = {}

        class Done:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(args, **kwargs):
            captured.update(kwargs)
            return Done()

        with mock.patch.object(subprocess, "run", fake_run):
            app._run_scan_subprocess(["python", "builder.py"])
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")


if __name__ == "__main__":
    unittest.main()
