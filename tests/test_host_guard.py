"""With no token, every route answers only to a loopback Host name, plus any
names the owner lists in ACP_ALLOWED_HOSTS. A web page that points its own
DNS name at the server still sends that name, so it is refused."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402

TOKEN = "host-guard-test-token-0123456789"


class TestHostGuard(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        for var in ("ACP_API_TOKEN", "ACP_ALLOWED_HOSTS", "ACP_PACKAGED"):
            prev = os.environ.pop(var, None)
            if prev is not None:
                self.addCleanup(os.environ.__setitem__, var, prev)
            else:
                self.addCleanup(os.environ.pop, var, None)

    def test_foreign_name_is_refused_on_an_ordinary_route(self):
        r = self.client.get("/api/plans", headers={"Host": "rebound.example:5555"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json(), {"error": "bad_host"})

    def test_foreign_name_is_refused_on_a_write(self):
        r = self.client.post("/api/plans", json={"id": "p1"},
                             headers={"Host": "rebound.example"})
        self.assertEqual(r.status_code, 400)

    def test_loopback_names_pass(self):
        for host in ("localhost:5555", "127.0.0.1", "[::1]:5555"):
            with self.subTest(host=host):
                r = self.client.get("/api/plans", headers={"Host": host})
                self.assertEqual(r.status_code, 200)

    def test_listed_name_passes(self):
        os.environ["ACP_ALLOWED_HOSTS"] = "acp.home.example, 192.0.2.10"
        for host in ("acp.home.example", "ACP.home.example:443", "192.0.2.10:5555"):
            with self.subTest(host=host):
                r = self.client.get("/api/plans", headers={"Host": host})
                self.assertEqual(r.status_code, 200)

    def test_listed_name_does_not_open_restricted_routes(self):
        os.environ["ACP_ALLOWED_HOSTS"] = "acp.home.example"
        r = self.client.put("/api/settings", json={"update_check": False},
                            headers={"Host": "acp.home.example"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json(), {"error": "bad_host"})

    def test_packaged_build_ignores_the_list(self):
        os.environ["ACP_PACKAGED"] = "1"
        os.environ["ACP_ALLOWED_HOSTS"] = "acp.home.example"
        r = self.client.get("/api/plans", headers={"Host": "acp.home.example"})
        self.assertEqual(r.status_code, 400)

    def test_with_a_token_the_name_is_not_checked(self):
        os.environ["ACP_API_TOKEN"] = TOKEN
        r = self.client.get("/api/plans", headers={
            "Host": "rig-a.example:5555", "Authorization": "Bearer " + TOKEN})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
