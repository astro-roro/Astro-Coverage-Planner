"""What the extension loader does with what it is handed.

Finding B4 of notes/security-run-plan.md. Loading arbitrary Python from a
configured directory is the feature working as designed, so nothing here
restricts it. Two things were worth settling.

A route mounted outside /api/ is served like any page. An operator reasons
about a setting called ACP_API_TOKEN and can reasonably read the gate as
covering the API, so an extension that mounts /backdoor beside the pages
deserves to be named at startup rather than discovered. It is gated once a
token is set, since the gate covers every path, and answers anyone on the LAN
when no token is set.

And the module docstring claims a failure in one extension does not stop the
others. That was a claim, not a test. It is a test now.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import extensions  # noqa: E402
from flask import Flask  # noqa: E402


def _write(d: Path, name: str, body: str) -> None:
    (d / name).write_text(body, encoding="utf-8")


ROUTE = """
def register(app):
    @app.route({path!r})
    def _view():
        return "ok"
"""

RAISES_ON_IMPORT = "raise RuntimeError('bad extension')\n"
NOT_PYTHON = "this is not python at all ((((\n"
NO_REGISTER = "def something_else():\n    return 1\n"


class _Loaded:
    """Load a directory of extensions and keep the app, names and log lines."""

    def __init__(self, test, files: dict[str, str]):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        for name, body in files.items():
            _write(d, name, body)
        self.app = Flask(f"acp_test_{id(self)}")
        with test.assertLogs("acp.extensions", level=logging.INFO) as cm:
            # A run that logs nothing would fail assertLogs, so every case here
            # loads at least one extension or trips at least one warning.
            self.names = self._load(d)
            self.records = cm.output

    def _load(self, d: Path):
        import os
        old = os.environ.get("ACP_EXTENSIONS_DIR")
        os.environ["ACP_EXTENSIONS_DIR"] = str(d)
        try:
            return extensions.load_extensions(self.app)
        finally:
            if old is None:
                os.environ.pop("ACP_EXTENSIONS_DIR", None)
            else:
                os.environ["ACP_EXTENSIONS_DIR"] = old

    def warning_about(self, text: str) -> list[str]:
        return [r for r in self.records
                if r.startswith("WARNING") and text in r]


class TestARouteOutsideTheApi(unittest.TestCase):
    def test_it_is_named_at_startup(self):
        loaded = _Loaded(self, {"back.py": ROUTE.format(path="/backdoor")})
        self.assertEqual(loaded.names, ["back.py"])
        warned = loaded.warning_about("/backdoor")
        self.assertEqual(len(warned), 1)
        self.assertIn("outside /api/", warned[0])

    def test_a_route_under_the_api_is_not_warned_about(self):
        loaded = _Loaded(self, {"good.py": ROUTE.format(path="/api/ext/thing")})
        self.assertEqual(loaded.names, ["good.py"])
        self.assertEqual(loaded.warning_about("outside /api/"), [])

    def test_the_warning_names_the_extension_that_did_it(self):
        loaded = _Loaded(self, {"culprit.py": ROUTE.format(path="/backdoor")})
        self.assertIn("culprit.py", loaded.warning_about("/backdoor")[0])

    def test_the_route_still_works(self):
        """A warning, not a restriction. The feature is doing what it is for."""
        loaded = _Loaded(self, {"back.py": ROUTE.format(path="/backdoor")})
        self.assertEqual(loaded.app.test_client().get("/backdoor").status_code,
                         200)


class TestWhichRoutesCount(unittest.TestCase):
    """The comparison itself, away from the filesystem."""

    def test_only_what_this_extension_added(self):
        self.assertEqual(
            extensions.routes_outside_api({"/", "/plan"},
                                          {"/", "/plan", "/backdoor"}),
            ["/backdoor"])

    def test_an_extension_that_added_nothing(self):
        self.assertEqual(extensions.routes_outside_api({"/"}, {"/"}), [])

    def test_api_routes_are_left_out(self):
        self.assertEqual(
            extensions.routes_outside_api(set(), {"/api/x", "/api/ext/y"}), [])

    def test_a_path_that_merely_starts_with_the_letters(self):
        """/apifoo is not under /api/ and must be reported."""
        self.assertEqual(extensions.routes_outside_api(set(), {"/apifoo"}),
                         ["/apifoo"])

    def test_several_come_back_sorted(self):
        self.assertEqual(
            extensions.routes_outside_api(set(), {"/z", "/a", "/api/keep"}),
            ["/a", "/z"])


class TestOneBadExtensionDoesNotStopTheOthers(unittest.TestCase):
    """The module docstring claimed this. B4 asked for it to be confirmed."""

    def test_a_file_that_raises_on_import(self):
        loaded = _Loaded(self, {"aaa_bad.py": RAISES_ON_IMPORT,
                                "zzz_good.py": ROUTE.format(path="/api/ok")})
        self.assertEqual(loaded.names, ["zzz_good.py"])
        self.assertTrue(any("aaa_bad.py" in r for r in loaded.records))

    def test_a_file_that_is_not_python(self):
        loaded = _Loaded(self, {"aaa_bad.py": NOT_PYTHON,
                                "zzz_good.py": ROUTE.format(path="/api/ok")})
        self.assertEqual(loaded.names, ["zzz_good.py"])

    def test_an_extension_whose_register_raises(self):
        loaded = _Loaded(self, {
            "aaa_bad.py": "def register(app):\n    raise RuntimeError('no')\n",
            "zzz_good.py": ROUTE.format(path="/api/ok")})
        self.assertEqual(loaded.names, ["zzz_good.py"])

    def test_a_file_with_no_register_is_simply_skipped(self):
        loaded = _Loaded(self, {"plain.py": NO_REGISTER,
                                "zzz_good.py": ROUTE.format(path="/api/ok")})
        self.assertEqual(loaded.names, ["zzz_good.py"])


if __name__ == "__main__":
    unittest.main()
