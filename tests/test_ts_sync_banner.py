"""The TS upload banner hook on ACP's main page.

The banner itself is filled client-side by loadTsSyncBanner() in app.js,
which fetches /api/ext/nina-ts-sync/import/uploads/pending (owned by the
nina-ts-sync extension, not this repo). What this repo owns is the hook:
the container element and the script tag that can find it. If either goes
missing, the banner silently stops working with nothing in the test suite
to say so.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402


def _redirect_manifest():
    td = Path(tempfile.mkdtemp())
    app_module.MANIFEST_PATH = td / "manifest.json"
    app_module._manifest_cache = None
    app_module._manifest_cache_mtime = None
    return app_module.MANIFEST_PATH


class TestTsSyncBannerHook(unittest.TestCase):
    def setUp(self):
        _redirect_manifest()  # root must render with no manifest present
        self.client = app.test_client()

    def test_root_page_has_the_banner_container(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('id="tsSyncBanner"', html)
        # Starts hidden: a fresh load with nothing pending shows nothing.
        self.assertIn('hidden', html.split('id="tsSyncBanner"', 1)[1].split(">", 1)[0])

    def test_root_page_loads_app_js_as_a_module(self):
        # loadTsSyncBanner() lives in app.js, which only runs if the page
        # actually loads it as a module script.
        r = self.client.get("/")
        html = r.get_data(as_text=True)
        self.assertIn('type="module" src="/static/app.js"', html)


if __name__ == "__main__":
    unittest.main()
