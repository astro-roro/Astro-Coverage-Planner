"""Module scripts must be served as JavaScript on every platform.

Flask asks Python's mimetypes module for the Content-Type of static files.
On Windows that module also reads file-type entries from the registry, and
some programs register .mjs (and sometimes .js) as text/plain there. Browsers
enforce strict MIME checking for module scripts, so a text/plain .mjs never
runs and the page sits on "Loading targets" forever (issue #46).

These tests simulate that registry state by poisoning the mimetypes table
before app.py is imported, so the fix is exercised on macOS and Linux too.
"""
from __future__ import annotations

import importlib
import mimetypes
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestStaticModuleScriptMime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mimic a Windows registry that maps script extensions to text/plain.
        mimetypes.add_type("text/plain", ".mjs")
        mimetypes.add_type("text/plain", ".js")
        sys.modules.pop("app", None)
        cls.app_module = importlib.import_module("app")
        cls.client = cls.app_module.app.test_client()

    def _assert_javascript(self, path: str):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, path)
        self.assertTrue(
            r.content_type.startswith("text/javascript"),
            f"{path} served as {r.content_type!r}",
        )

    def test_mjs_served_as_javascript(self):
        self._assert_javascript("/static/init-error.mjs")
        self._assert_javascript("/static/search.mjs")

    def test_js_served_as_javascript(self):
        self._assert_javascript("/static/app.js")


if __name__ == "__main__":
    unittest.main()
