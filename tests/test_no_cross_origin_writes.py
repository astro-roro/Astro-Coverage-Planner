"""A web page the user visits must not be able to write to their ACP.

ACP normally runs on loopback, so the browser and the server share a machine
and any site the user opens can reach the API. The only thing standing between
a hostile page and a write is the browser's own rule: a cross-origin PUT or
DELETE carrying JSON is not a simple request, so the browser asks permission
with an OPTIONS preflight first and only proceeds if the answer grants the
origin and the method.

An earlier version granted exactly that. It answered the preflight 204 with
`Access-Control-Allow-Origin: *` and listed PUT and DELETE as allowed, on the
reasoning that withholding the header from write *responses* kept writes shut.
That stops the attacker reading the reply, not sending the request. Proved end
to end on 2026-09-05: a page could delete plans, or flip one public and publish
it, silently.

Nothing needs the header. The NINA plugin is a desktop HTTP client, and CORS is
a browser mechanism it never sees. These tests exist so it cannot come back by
accident.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402

EVIL = "https://evil.example"
WRITE_METHODS = ("POST", "PUT", "DELETE")


class TestPreflightGrantsNothing(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def _preflight(self, path, method):
        return self.client.options(path, headers={
            "Origin": EVIL,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        })

    def test_no_preflight_grants_a_write_anywhere(self):
        """The browser proceeds only when the origin is allowed. If the header
        is absent the request never leaves, whatever the status code is."""
        paths = ["/api/plans", "/api/plans/p1", "/api/gear",
                 "/api/publish/shooting", "/api/saved-searches/s1"]
        for path in paths:
            for method in WRITE_METHODS:
                with self.subTest(path=path, method=method):
                    r = self._preflight(path, method)
                    self.assertIsNone(
                        r.headers.get("Access-Control-Allow-Origin"),
                        f"{method} {path} preflight granted an origin",
                    )

    def test_reads_do_not_advertise_a_wildcard_origin(self):
        for path in ("/api/manifest", "/api/plans", "/api/gear", "/api/version"):
            with self.subTest(path=path):
                r = self.client.get(path, headers={"Origin": EVIL})
                self.assertNotEqual(r.headers.get("Access-Control-Allow-Origin"), "*", path)

    def test_no_response_advertises_writable_methods(self):
        for path in ("/api/manifest", "/api/plans"):
            for call in (self.client.get, self.client.options):
                with self.subTest(path=path, call=call.__name__):
                    r = call(path, headers={"Origin": EVIL})
                    self.assertIsNone(r.headers.get("Access-Control-Allow-Methods"), path)


class TestOrdinaryUseIsUnaffected(unittest.TestCase):
    """The app's own page is same-origin and never needed any of this."""

    def setUp(self):
        self.client = app.test_client()

    def test_the_page_and_its_api_still_answer(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/manifest").status_code, 200)

    def test_a_desktop_client_is_unaffected_by_any_of_this(self):
        """No Origin header at all, which is what a non-browser sends."""
        self.assertEqual(self.client.get("/api/version").status_code, 200)


if __name__ == "__main__":
    unittest.main()
