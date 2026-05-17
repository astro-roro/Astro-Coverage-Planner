"""Path-traversal hardening tests for /api/sync/download/<path:filename>.

The route uses Flask's <path:> converter which DOES pass slashes through,
so without the _SYNC_ZIP_NAME_RE gate it'd be a textbook path-traversal
hole. send_from_directory also blocks traversal in defence-in-depth,
but the regex is the contract we control — these tests pin it down so
nobody loosens it without thinking.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _fresh_exports_dir() -> Path:
    td = Path(tempfile.mkdtemp())
    app_module.ZIP_OUTPUT_DIR = td
    return td


def _write_legit_zip(exports_dir: Path, stamp: str = "20260517T120000Z") -> str:
    """Create a zip that matches the _SYNC_ZIP_NAME_RE pattern so the
    legitimate-file path has a target."""
    name = f"acp-sync-{stamp}.zip"
    p = exports_dir / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("dummy.txt", "hello")
    return name


class TestSyncDownloadLegitFile(unittest.TestCase):
    def setUp(self):
        self.exports = _fresh_exports_dir()
        self.legit_name = _write_legit_zip(self.exports)
        self.client = app.test_client()

    def test_legit_filename_returns_zip(self):
        r = self.client.get(f"/api/sync/download/{self.legit_name}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content_type, "application/zip")
        self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
        self.assertIn(self.legit_name, r.headers.get("Content-Disposition", ""))

    def test_legit_filename_actually_returns_zip_bytes(self):
        r = self.client.get(f"/api/sync/download/{self.legit_name}")
        self.assertEqual(r.status_code, 200)
        # First 2 bytes of any zip are "PK".
        self.assertEqual(r.data[:2], b"PK")


class TestSyncDownloadPathTraversal(unittest.TestCase):
    """Every shape of path-traversal attempt should hit the regex gate
    and return 400 — never 200 with file contents from outside
    ZIP_OUTPUT_DIR."""

    def setUp(self):
        self.exports = _fresh_exports_dir()
        # Create a "sensitive" file outside the exports dir to assert
        # we don't accidentally serve it.
        sensitive = self.exports.parent / "secret.txt"
        sensitive.write_text("SHOULD-NEVER-BE-SERVED", encoding="utf-8")
        self.client = app.test_client()

    def _assert_blocked(self, url: str):
        r = self.client.get(url, follow_redirects=False)
        # Anything that's not "200 with content" is acceptable — could be
        # 400 (regex rejection), 404 (route miss), 308 (Flask URL
        # normalisation), or 500 (impossible to construct safely). What
        # MUST never happen is a successful response carrying sensitive
        # file content.
        self.assertNotEqual(r.status_code, 200,
            f"{url} returned 200 — possible exposure")
        self.assertNotIn(b"SHOULD-NEVER-BE-SERVED", r.data,
            f"{url} response body contained sensitive content")

    def test_dotdot_traversal_blocked(self):
        self._assert_blocked("/api/sync/download/../secret.txt")

    def test_url_encoded_dotdot_blocked(self):
        # Flask normalises %2F → / before routing, but %2e%2e stays as
        # ".." literal in the path segment and STILL fails the regex.
        self._assert_blocked("/api/sync/download/%2e%2e/secret.txt")

    def test_absolute_path_blocked(self):
        self._assert_blocked("/api/sync/download//etc/passwd")

    def test_wrong_extension_blocked(self):
        # File matches naming pattern except for extension.
        self._assert_blocked("/api/sync/download/acp-sync-20260517T120000Z.txt")

    def test_double_extension_blocked(self):
        self._assert_blocked("/api/sync/download/acp-sync-20260517T120000Z.zip.bak")

    def test_wrong_prefix_blocked(self):
        self._assert_blocked("/api/sync/download/not-acp-sync-20260517T120000Z.zip")

    def test_wrong_timestamp_format_blocked(self):
        self._assert_blocked("/api/sync/download/acp-sync-yesterday.zip")

    def test_timestamp_with_wrong_length_blocked(self):
        # 7 digits in date → off-by-one
        self._assert_blocked("/api/sync/download/acp-sync-2026051T120000Z.zip")

    def test_empty_filename_segment_blocked(self):
        # /api/sync/download// → empty filename
        r = self.client.get("/api/sync/download/")
        # Flask routing rules vary — could be 404 (no match) or 400.
        # Either is fine; what matters is no 200.
        self.assertNotEqual(r.status_code, 200)

    def test_subdir_traversal_blocked(self):
        # Even within exports, sub-paths should not be accessible.
        self._assert_blocked("/api/sync/download/sub/acp-sync-20260517T120000Z.zip")

    def test_dotzip_only_blocked(self):
        # Filename is just ".zip" — matches no prefix.
        self._assert_blocked("/api/sync/download/.zip")

    def test_legit_pattern_but_file_missing_returns_404(self):
        # The regex passes but the file doesn't exist → send_from_directory
        # returns 404 (not 200, not 500).
        r = self.client.get(
            "/api/sync/download/acp-sync-20990101T000000Z.zip"
        )
        self.assertEqual(r.status_code, 404)


class TestRegexGateContract(unittest.TestCase):
    """Pin down the regex's accept/reject set directly so any future
    relaxation surfaces here loudly."""

    def test_regex_accepts_canonical_format(self):
        self.assertIsNotNone(
            app_module._SYNC_ZIP_NAME_RE.match("acp-sync-20260517T120000Z.zip"))

    def test_regex_rejects_uppercase_extension(self):
        # Case-sensitive — Windows users uploading manually shouldn't
        # produce surprises if the regex is loosened later.
        self.assertIsNone(
            app_module._SYNC_ZIP_NAME_RE.match("acp-sync-20260517T120000Z.ZIP"))

    def test_regex_rejects_trailing_whitespace(self):
        self.assertIsNone(
            app_module._SYNC_ZIP_NAME_RE.match("acp-sync-20260517T120000Z.zip "))

    def test_regex_rejects_leading_whitespace(self):
        self.assertIsNone(
            app_module._SYNC_ZIP_NAME_RE.match(" acp-sync-20260517T120000Z.zip"))


if __name__ == "__main__":
    unittest.main()
