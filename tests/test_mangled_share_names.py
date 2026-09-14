"""A file the SMB server renamed must not vanish from the scan.

Finding E3 of notes/security-run-plan.md, measured against a Synology share on
2026-09-13 from both Windows and macOS.

A share hands every client an 8.3 name in place of one Windows cannot represent.
A frame called nul.fits arrives as NSQ1NJ~K, folders called "trailing dot." and
"con" arrive as TQEBZE~2 and CSHOFG~F. Both platforms see the same invented
names, so this is not a Windows quirk and there is no divergence to reconcile.

Two consequences, and the first one was silent. The mangled name has no
extension, so the extension test could never match it and the frame was simply
absent: 17 files on disk, 16 in the scan, nothing said. The second is that any
folder name in the manifest may be a name nobody chose, which matters because
rejects detection and session naming both read folder names for sense.

The real name cannot be recovered. The server never sends it. So the frame is
read anyway, and the invented names are reported.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_archive_manifest as bam  # noqa: E402


class TestLooksMangled(unittest.TestCase):
    def test_the_names_the_synology_actually_produced(self):
        for name in ("NSQ1NJ~K", "TQEBZE~2", "TIXEP6~1", "CSHOFG~F"):
            self.assertTrue(bam.looks_mangled(name), name)

    def test_an_8_3_name_that_kept_an_extension(self):
        self.assertTrue(bam.looks_mangled("LIGHT~1.FIT"))

    def test_ordinary_names_are_not_mangled(self):
        for name in ("light_0001.fits", "M42_Ha_300s.fit", "e2 case",
                     "depth1_xxxx", "Ωmega_light_0001.fits", "nul.fits",
                     "trailing dot.", "con"):
            self.assertFalse(bam.looks_mangled(name), name)

    def test_a_tilde_alone_is_not_enough(self):
        """Editors and sync tools leave tildes in perfectly real names."""
        for name in ("backup~", "~$notes.docx", "light~0001.fits",
                     "M42~final_stack.xisf"):
            self.assertFalse(bam.looks_mangled(name), name)

    def test_lowercase_is_not_mangled(self):
        """The server produces upper case; a lower case lookalike is a real name."""
        self.assertFalse(bam.looks_mangled("tqebze~2"))


class TestTheFrameIsFoundAnyway(unittest.TestCase):
    def test_a_mangled_name_with_no_extension_is_scanned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "light_0001.fits").write_bytes(b"x" * 16)
            (root / "NSQ1NJ~K").write_bytes(b"x" * 16)
            files, _ = bam.glob_archive([root], bam.EXTENSIONS,
                                        log=lambda _m: None)
            self.assertEqual({p.name for p, _s, _m in files},
                             {"light_0001.fits", "NSQ1NJ~K"})

    def test_an_ordinary_extensionless_file_is_still_left_out(self):
        """The point is mangled names, not every file without a dot in it."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "README").write_bytes(b"x" * 16)
            (root / "notes").write_bytes(b"x" * 16)
            files, _ = bam.glob_archive([root], bam.EXTENSIONS,
                                        log=lambda _m: None)
            self.assertEqual(files, [])

    def test_a_frame_inside_a_mangled_folder_is_still_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "TQEBZE~2").mkdir()
            (root / "TQEBZE~2" / "light_0001.fits").write_bytes(b"x" * 16)
            files, _ = bam.glob_archive([root], bam.EXTENSIONS,
                                        log=lambda _m: None)
            self.assertEqual([p.name for p, _s, _m in files],
                             ["light_0001.fits"])


class TestTheInventedNamesAreReported(unittest.TestCase):
    def test_a_mangled_folder_is_named_with_an_example(self):
        rows = bam.mangled_name_examples(
            ["/archive/TQEBZE~2/light_0001.fits",
             "/archive/TQEBZE~2/light_0002.fits"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "TQEBZE~2")
        self.assertEqual(rows[0]["example"], "/archive/TQEBZE~2/light_0001.fits")

    def test_a_mangled_file_is_named_too(self):
        rows = bam.mangled_name_examples(["/archive/reserved file/NSQ1NJ~K"])
        self.assertEqual([r["name"] for r in rows], ["NSQ1NJ~K"])

    def test_windows_separators_are_split_the_same_way(self):
        rows = bam.mangled_name_examples([r"Z:\_tests\TQEBZE~2\light_0001.fits"])
        self.assertEqual([r["name"] for r in rows], ["TQEBZE~2"])

    def test_an_ordinary_archive_reports_nothing(self):
        rows = bam.mangled_name_examples(
            ["/archive/M42/Ha/light_0001.fits", "/archive/M42/Ha/light_0002.fits"])
        self.assertEqual(rows, [])

    def test_each_name_appears_once_however_many_frames_it_holds(self):
        rows = bam.mangled_name_examples(
            [f"/archive/CSHOFG~F/light_{i:04d}.fits" for i in range(50)])
        self.assertEqual(len(rows), 1)

    def test_the_list_is_capped(self):
        paths = [f"/archive/ABCDE{i}~1/light_0001.fits" for i in range(9)]
        self.assertEqual(len(bam.mangled_name_examples(paths)), 5)
        self.assertEqual(len(bam.mangled_name_examples(paths, limit=2)), 2)

    def test_names_come_back_in_a_stable_order(self):
        paths = ["/a/ZZZZZZ~1/x.fits", "/a/AAAAAA~1/x.fits", "/a/MMMMMM~1/x.fits"]
        self.assertEqual([r["name"] for r in bam.mangled_name_examples(paths)],
                         ["AAAAAA~1", "MMMMMM~1", "ZZZZZZ~1"])


class TestTheScanHealthPanel(unittest.TestCase):
    def _panel(self, flags):
        import app
        return app._scan_health({"integrity_flags": flags})

    def test_the_count_and_examples_reach_the_panel(self):
        panel = self._panel({
            "mangled_name_count": 2,
            "mangled_names": [
                {"name": "TQEBZE~2", "example": "/archive/TQEBZE~2/light.fits"},
                {"name": "NSQ1NJ~K", "example": "/archive/x/NSQ1NJ~K"},
            ],
        })
        self.assertEqual(panel["mangled_names"], 2)
        self.assertEqual([r["name"] for r in panel["mangled_names_examples"]],
                         ["TQEBZE~2", "NSQ1NJ~K"])

    def test_a_manifest_without_the_key_still_renders(self):
        """Every manifest written before today lacks it."""
        panel = self._panel({})
        self.assertEqual(panel["mangled_names"], 0)
        self.assertEqual(panel["mangled_names_examples"], [])

    def test_junk_in_the_manifest_does_not_take_the_panel_down(self):
        panel = self._panel({"mangled_name_count": "many",
                             "mangled_names": "not a list"})
        self.assertEqual(panel["mangled_names"], 0)
        self.assertEqual(panel["mangled_names_examples"], [])

    def test_a_row_that_is_not_a_dict_is_skipped(self):
        panel = self._panel({"mangled_names": ["TQEBZE~2", {"name": "NSQ1NJ~K"}]})
        self.assertEqual([r["name"] for r in panel["mangled_names_examples"]],
                         ["NSQ1NJ~K"])


class TestTheNeighbouringCountsAreGuardedToo(unittest.TestCase):
    """The same unguarded int sat on the two counts added last week.

    Found by the junk-manifest test above, which crashed on the new line and
    would have crashed on either of these. A count that reads "many" must cost
    the panel that one number, not the whole payload.
    """

    def _panel(self, flags):
        import app
        return app._scan_health({"integrity_flags": flags})

    def test_junk_unreadable_counts_do_not_take_the_panel_down(self):
        panel = self._panel({"unreadable_file_count": "many",
                             "unreadable_dir_count": None})
        self.assertEqual(panel["unreadable_files"], 0)
        self.assertEqual(panel["unreadable_dirs"], 0)

    def test_a_real_count_still_comes_through(self):
        panel = self._panel({"unreadable_file_count": 33,
                             "unreadable_dir_count": 1,
                             "mangled_name_count": 4})
        self.assertEqual(panel["unreadable_files"], 33)
        self.assertEqual(panel["unreadable_dirs"], 1)
        self.assertEqual(panel["mangled_names"], 4)

    def test_a_count_written_as_a_string_of_digits_is_accepted(self):
        panel = self._panel({"unreadable_file_count": "33"})
        self.assertEqual(panel["unreadable_files"], 33)


if __name__ == "__main__":
    unittest.main()
