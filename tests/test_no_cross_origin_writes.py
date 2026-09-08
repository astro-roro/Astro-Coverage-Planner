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
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
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


class TestPreflightFreeWritesParseNothing(unittest.TestCase):
    """The preflight is only half the story.

    A cross-origin POST carrying one of the three CORS-safelisted content
    types, text/plain, application/x-www-form-urlencoded or
    multipart/form-data, is a simple request: the browser sends it without
    asking permission first and only withholds the reply. So the preflight
    tests above prove nothing about POST. What actually keeps those writes
    shut is that every write endpoint reads its body with request.get_json,
    which returns None unless the content type is application/json, and
    application/json is not safelisted.

    That protection is real but incidental. Adding force=True to one
    get_json call would reopen the whole write surface without touching
    anything that looks like security. Confirmed in a real browser on
    2026-09-07: a page on another origin sent these and every one was
    refused with a 400.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        td = Path(self._td.name)
        for name in ("SITES_PATH", "GEAR_PATH", "PLANS_PATH", "DESTINATIONS_PATH",
                     "TARGET_OVERRIDES_PATH", "SAVED_SEARCHES_PATH",
                     "FINGERPRINTS_PATH"):
            setattr(app_module, name, td / f"{name.lower()}.json")
        for cache in ("_sites_cache", "_gear_cache", "_plans_cache",
                      "_destinations_cache", "_fingerprints_cache"):
            if hasattr(app_module, cache):
                setattr(app_module, cache, None)
                setattr(app_module, cache + "_mtime", None)
        self.client = app.test_client()

    SAFELISTED = ("text/plain", "application/x-www-form-urlencoded",
                  "multipart/form-data")
    WRITE_PATHS = ("/api/sites", "/api/gear", "/api/plans", "/api/destinations",
                   "/api/target-overrides", "/api/saved-searches",
                   "/api/plans/match")

    def test_a_simple_post_is_never_parsed_as_a_body(self):
        body = '{"sites": [{"id": "x", "name": "pwned", "lat": 0, "lon": 0}], '\
               '"telescopes": [], "cameras": [], "id": "pwned", "target_id": "t1", '\
               '"name": "pwned", "destinations": []}'
        for path in self.WRITE_PATHS:
            for ctype in self.SAFELISTED:
                with self.subTest(path=path, content_type=ctype):
                    r = self.client.post(path, data=body,
                                         content_type=ctype,
                                         headers={"Origin": EVIL})
                    self.assertEqual(r.status_code, 400, f"{ctype} {path}")

    def test_the_same_body_as_json_is_accepted(self):
        """The refusal above is about the content type, not the body."""
        r = self.client.post("/api/sites", json={
            "sites": [{"id": "x", "name": "ok", "lat": 0, "lon": 0}]})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
