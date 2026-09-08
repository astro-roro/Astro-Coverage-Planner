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


class TestTokenOff(unittest.TestCase):

    def setUp(self):
        self._prev = os.environ.pop("ACP_API_TOKEN", None)
        if self._prev is not None:
            self.addCleanup(os.environ.__setitem__, "ACP_API_TOKEN", self._prev)
        self.client = app.test_client()

    def test_everything_is_open_when_no_token_is_set(self):
        self.assertEqual(self.client.get("/api/version").status_code, 200)


if __name__ == "__main__":
    unittest.main()
