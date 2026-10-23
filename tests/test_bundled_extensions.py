"""Extensions that ship inside ACP: they load by default and can be switched off.

The Target Scheduler sync lives in bundled_extensions/nina_ts_sync. It is a
separate package that core never imports; the loader finds it by path, the same
way it finds a user's extension. Three things are worth pinning down:

  - it loads with no configuration at all;
  - ACP_DISABLE_BUNDLED_EXTENSIONS switches it off;
  - a copy of the same name in the user's extensions directory wins, so a
    user who installed the standalone extension before it was bundled does not
    get every route registered twice.

The logic tests use a throwaway bundled folder with tiny stand-in extensions,
so they never start the real extension's upload worker.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import extensions  # noqa: E402
from flask import Flask  # noqa: E402

ROUTE = """
def register(app):
    @app.route({path!r}, endpoint={path!r})
    def _view():
        return "ok"
"""


def _rules(app: Flask) -> set[str]:
    return {str(r) for r in app.url_map.iter_rules()}


class _Dirs:
    """A throwaway bundled folder and user extensions folder, wired in."""

    def __init__(self, test: unittest.TestCase, bundled: dict[str, str],
                 user: dict[str, str] | None = None, disable: str | None = None):
        self.bundled = Path(tempfile.mkdtemp(prefix="acp-bundled-"))
        self.user = Path(tempfile.mkdtemp(prefix="acp-user-ext-"))
        for name, body in bundled.items():
            self._write(self.bundled, name, body)
        for name, body in (user or {}).items():
            self._write(self.user, name, body)
        env = {"ACP_EXTENSIONS_DIR": str(self.user)}
        if disable is not None:
            env["ACP_DISABLE_BUNDLED_EXTENSIONS"] = disable
        for p in (mock.patch.object(extensions, "BUNDLED_EXTENSIONS_DIR", self.bundled),
                  mock.patch.dict(os.environ, env)):
            p.start()
            test.addCleanup(p.stop)
        if disable is None:
            # patch.dict restores the whole environment on stop, so a variable
            # set outside the test (CI sets it) is back afterwards.
            os.environ.pop("ACP_DISABLE_BUNDLED_EXTENSIONS", None)
        test.addCleanup(shutil.rmtree, self.bundled, True)
        test.addCleanup(shutil.rmtree, self.user, True)
        self.app = Flask(f"acp_bundled_test_{id(self)}")

    @staticmethod
    def _write(d: Path, name: str, body: str) -> None:
        # "pkg/" entries are package directories, anything else is a file.
        if name.endswith("/"):
            (d / name.rstrip("/")).mkdir()
            (d / name.rstrip("/") / "__init__.py").write_text(body, encoding="utf-8")
        else:
            (d / name).write_text(body, encoding="utf-8")

    def load(self) -> tuple[list[str], list[str]]:
        """Run the user loader then the bundled one. Returns both name lists."""
        with self._quiet():
            user = extensions.load_extensions(self.app)
            bundled = extensions.load_bundled_extensions(self.app)
        return user, bundled

    @staticmethod
    def _quiet():
        class _Ctx:
            def __enter__(self_inner):
                self_inner.prev = logging.getLogger("acp.extensions").level
                logging.getLogger("acp.extensions").setLevel(logging.CRITICAL)

            def __exit__(self_inner, *exc):
                logging.getLogger("acp.extensions").setLevel(self_inner.prev)
        return _Ctx()


class TestBundledLoadsByDefault(unittest.TestCase):
    def test_it_loads_with_nothing_configured(self):
        d = _Dirs(self, {"thing/": ROUTE.format(path="/api/ext/thing/ping")})
        user, bundled = d.load()
        self.assertEqual(user, [])
        self.assertEqual(bundled, ["thing"])
        self.assertIn("/api/ext/thing/ping", _rules(d.app))

    def test_it_loads_beside_a_different_user_extension(self):
        d = _Dirs(self, {"thing/": ROUTE.format(path="/api/ext/thing/ping")},
                  user={"other.py": ROUTE.format(path="/api/ext/other")})
        user, bundled = d.load()
        self.assertEqual(user, ["other.py"])
        self.assertEqual(bundled, ["thing"])
        self.assertTrue({"/api/ext/thing/ping", "/api/ext/other"} <= _rules(d.app))

    def test_a_missing_bundled_folder_is_not_an_error(self):
        d = _Dirs(self, {})
        d.bundled.rmdir()
        self.assertEqual(d.load(), ([], []))


class TestSwitchingItOff(unittest.TestCase):
    def test_off_with_one(self):
        d = _Dirs(self, {"thing/": ROUTE.format(path="/api/ext/thing/ping")},
                  disable="1")
        self.assertEqual(d.load(), ([], []))
        self.assertNotIn("/api/ext/thing/ping", _rules(d.app))

    def test_off_with_true_in_any_case(self):
        d = _Dirs(self, {"thing/": ROUTE.format(path="/api/ext/thing/ping")},
                  disable="True")
        self.assertEqual(d.load()[1], [])

    def test_zero_or_empty_leaves_it_on(self):
        for value in ("0", "", "no"):
            with self.subTest(value=value):
                d = _Dirs(self, {"thing/": ROUTE.format(path="/api/ext/thing/ping")},
                          disable=value)
                self.assertEqual(d.load()[1], ["thing"])

    def test_it_does_not_touch_the_users_own_extensions(self):
        d = _Dirs(self, {"thing/": ROUTE.format(path="/api/ext/thing/ping")},
                  user={"other.py": ROUTE.format(path="/api/ext/other")},
                  disable="1")
        user, bundled = d.load()
        self.assertEqual((user, bundled), (["other.py"], []))


class TestAUserCopyWins(unittest.TestCase):
    def test_same_name_package_in_the_user_dir(self):
        d = _Dirs(self,
                  {"thing/": ROUTE.format(path="/api/ext/bundled")},
                  user={"thing/": ROUTE.format(path="/api/ext/user")})
        user, bundled = d.load()
        self.assertEqual((user, bundled), (["thing"], []))
        rules = _rules(d.app)
        self.assertIn("/api/ext/user", rules)
        self.assertNotIn("/api/ext/bundled", rules)

    def test_a_single_file_copy_counts_as_the_same_name(self):
        d = _Dirs(self,
                  {"thing/": ROUTE.format(path="/api/ext/bundled")},
                  user={"thing.py": ROUTE.format(path="/api/ext/user")})
        user, bundled = d.load()
        self.assertEqual((user, bundled), (["thing.py"], []))
        self.assertNotIn("/api/ext/bundled", _rules(d.app))

    def test_a_broken_user_copy_does_not_fall_back_to_the_bundled_one(self):
        """The user's copy is the one they chose. If it fails, the log says so;
        quietly running a different version under it would hide that."""
        d = _Dirs(self,
                  {"thing/": ROUTE.format(path="/api/ext/bundled")},
                  user={"thing.py": "raise RuntimeError('broken')\n"})
        user, bundled = d.load()
        self.assertEqual((user, bundled), ([], []))
        self.assertNotIn("/api/ext/bundled", _rules(d.app))

    def test_the_skip_is_logged(self):
        d = _Dirs(self,
                  {"thing/": ROUTE.format(path="/api/ext/bundled")},
                  user={"thing.py": ROUTE.format(path="/api/ext/user")})
        with self.assertLogs("acp.extensions", level=logging.INFO) as cm:
            extensions.load_extensions(d.app)
            extensions.load_bundled_extensions(d.app)
        self.assertTrue(any("Bundled extension thing skipped" in r for r in cm.output))


class TestTheRealBundledFolder(unittest.TestCase):
    """Structural checks on what actually ships, without running it."""

    def test_the_sync_extension_is_a_package_with_register(self):
        pkg = REPO_ROOT / "bundled_extensions" / "nina_ts_sync"
        init = pkg / "__init__.py"
        self.assertTrue(init.is_file())
        self.assertRegex(init.read_text(encoding="utf-8"),
                         r"(?m)^def register\(app")

    def test_the_review_page_ships_with_it(self):
        self.assertTrue(
            (REPO_ROOT / "bundled_extensions" / "nina_ts_sync" / "review.html").is_file())

    def test_core_does_not_import_the_extension(self):
        """It stays a separate extension: found by path, never imported by name."""
        pattern = re.compile(r"(?m)^\s*(from|import)\s+(bundled_extensions|nina_ts_sync)\b")
        for name in ("app.py", "extensions.py", "sources.py", "shooting_publish.py"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text), name)

    def test_the_docker_image_copies_it_in(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^COPY \. \.")
        ignored = [ln.strip() for ln in
                   (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.startswith("#")]
        for entry in ignored:
            stripped = entry.rstrip("/")
            self.assertNotIn(stripped, ("bundled_extensions", "bundled_extensions/nina_ts_sync",
                                        "nina_ts_sync", "*.html", "*.json", "*.py"),
                             f".dockerignore drops the bundled extension via {entry!r}")


if __name__ == "__main__":
    unittest.main()
