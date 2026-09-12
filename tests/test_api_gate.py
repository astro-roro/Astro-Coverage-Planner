"""The bearer-token gate, including tokens that are not ASCII.

hmac.compare_digest refuses two str values carrying non-ASCII characters, so
a token with an accent in it raised TypeError inside the before_request hook
and every request under /api/ came back 500, whether the token was right or
wrong. Confirmed on 2026-09-07 with ACP_API_TOKEN set to "cafe-senor" spelt
properly.

Header values reach a WSGI app already decoded as latin-1, one character per
byte, so these tests send raw bytes the way a real client does rather than
relying on how the Flask test client happens to encode a native string.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402

ASCII_TOKEN = "a-perfectly-ordinary-token"
ACCENTED_TOKEN = "café-señor-token"


class TokenCase(unittest.TestCase):
    token = ASCII_TOKEN

    def setUp(self):
        self._prev = os.environ.get("ACP_API_TOKEN")
        os.environ["ACP_API_TOKEN"] = self.token
        self.addCleanup(self._restore)
        self.client = app.test_client()

    def _restore(self):
        if self._prev is None:
            os.environ.pop("ACP_API_TOKEN", None)
        else:
            os.environ["ACP_API_TOKEN"] = self._prev

    def get(self, sent: str | None, *, encoding: str = "utf-8"):
        """One GET carrying `sent` as a bearer token, put on the wire the way
        a real HTTP client would: encoded, then read back as latin-1."""
        headers = {}
        if sent is not None:
            raw = ("Bearer " + sent).encode(encoding)
            headers["Authorization"] = raw.decode("latin-1")
        return self.client.get("/api/version", headers=headers)


class TestAsciiToken(TokenCase):

    def test_the_right_token_is_accepted(self):
        self.assertEqual(self.get(ASCII_TOKEN).status_code, 200)

    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self.get("not-the-token").status_code, 401)

    def test_no_token_is_refused(self):
        self.assertEqual(self.get(None).status_code, 401)


class TestAccentedToken(TokenCase):
    token = ACCENTED_TOKEN

    def test_the_right_token_does_not_raise(self):
        r = self.get(ACCENTED_TOKEN)
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(r.status_code, 200)

    def test_a_client_that_sends_latin_1_also_works(self):
        self.assertEqual(self.get(ACCENTED_TOKEN, encoding="latin-1").status_code, 200)

    def test_a_wrong_token_is_refused_not_crashed(self):
        r = self.get("not-the-token")
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(r.status_code, 401)

    def test_no_token_is_refused_not_crashed(self):
        r = self.get(None)
        self.assertNotEqual(r.status_code, 500)
        self.assertEqual(r.status_code, 401)


class TestTheWholeAppIsGated(TokenCase):
    """The gate used to cover /api/* only, so setting the token served the
    page shell and then 401d every fetch it made. Nobody would turn on the
    one thing that closes the LAN exposure. Confirmed on a running instance:
    GET / returned 200, GET /static/app.js returned 200, and every /api/ call
    returned 401."""

    def test_the_app_shell_is_not_served_without_a_credential(self):
        r = self.client.get("/", headers={"Accept": "text/html"})
        self.assertEqual(r.status_code, 401)
        body = r.get_data(as_text=True)
        self.assertIn("Access token", body)
        self.assertNotIn("<div id=\"map\"", body)

    def test_static_files_are_not_served_without_a_credential(self):
        self.assertEqual(self.client.get("/static/app.js").status_code, 401)

    def test_a_json_client_gets_json_not_a_login_page(self):
        r = self.client.get("/api/manifest", headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json(), {"error": "unauthorized"})

    def test_a_client_sending_no_accept_header_gets_json(self):
        r = self.client.get("/api/manifest")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json(), {"error": "unauthorized"})

    def test_the_login_page_itself_is_reachable(self):
        self.assertEqual(self.client.get("/login").status_code, 200)

    def test_the_bearer_token_still_works_everywhere(self):
        h = {"Authorization": "Bearer " + ASCII_TOKEN}
        self.assertEqual(self.client.get("/api/version", headers=h).status_code, 200)
        self.assertEqual(self.client.get("/", headers=h).status_code, 200)


class TestSigningIn(TokenCase):

    def sign_in(self, token=ASCII_TOKEN, next_path="/"):
        return self.client.post("/login", data={"token": token, "next": next_path})

    def test_the_right_token_sets_a_cookie_and_redirects(self):
        r = self.sign_in()
        self.assertEqual(r.status_code, 302)
        self.assertIn("/", r.headers["Location"])
        self.assertTrue(self.client.get_cookie(app_session_name()))

    def test_the_cookie_then_opens_the_whole_app(self):
        self.sign_in()
        self.assertEqual(self.client.get("/api/manifest").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(
            self.client.get("/", headers={"Accept": "text/html"}).status_code, 200)

    def test_the_cookie_is_httponly_and_samesite_strict(self):
        r = self.sign_in()
        header = r.headers["Set-Cookie"]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)

    def test_the_cookie_is_not_the_token(self):
        r = self.sign_in()
        self.assertNotIn(ASCII_TOKEN, r.headers["Set-Cookie"])

    def test_a_wrong_token_sets_no_cookie(self):
        r = self.sign_in(token="not-the-token")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("Set-Cookie", r.headers)
        self.assertIn("not accepted", r.get_data(as_text=True))

    def test_a_forged_cookie_is_refused(self):
        self.client.set_cookie(app_session_name(), "deadbeef", domain="localhost")
        self.assertEqual(self.client.get("/api/manifest").status_code, 401)

    def test_signing_in_returns_to_the_page_that_was_asked_for(self):
        r = self.sign_in(next_path="/?target=42")
        self.assertTrue(r.headers["Location"].endswith("/?target=42"))

    def test_an_offsite_next_is_refused(self):
        for hostile in ("https://evil.example/", "//evil.example/"):
            r = self.sign_in(next_path=hostile)
            self.assertNotIn("evil.example", r.headers["Location"], hostile)

    def test_logging_out_clears_the_cookie(self):
        self.sign_in()
        self.client.post("/logout")
        self.assertEqual(self.client.get("/api/manifest").status_code, 401)


def app_session_name():
    import app as m
    return m.SESSION_COOKIE_NAME


class TestTokenOff(unittest.TestCase):

    def setUp(self):
        self._prev = os.environ.pop("ACP_API_TOKEN", None)
        if self._prev is not None:
            self.addCleanup(os.environ.__setitem__, "ACP_API_TOKEN", self._prev)
        self.client = app.test_client()

    def test_everything_is_open_when_no_token_is_set(self):
        self.assertEqual(self.client.get("/api/version").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)

    def test_no_login_page_is_shown_when_no_token_is_set(self):
        r = self.client.get("/", headers={"Accept": "text/html"})
        self.assertNotIn("Access token", r.get_data(as_text=True))

    def test_visiting_login_goes_straight_to_the_app(self):
        self.assertEqual(self.client.get("/login").status_code, 302)


if __name__ == "__main__":
    unittest.main()
