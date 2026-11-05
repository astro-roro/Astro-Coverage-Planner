"""Tests for scripts/check_private_terms.py.

Every term here is made up. The real list lives outside the repository and
must never be copied into a test.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_private_terms.py"
TERMS = "# made-up list for tests\n\nzebra-secret\nexample-host\\d+\n"


class CheckPrivateTermsTest(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.pop("ACP_PRIVATE_TERMS", None)
        self.env.update(
            GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid",
            GIT_CONFIG_NOSYSTEM="1", HOME=str(self.repo),
        )
        self.terms = self.repo / ".terms.txt"
        self.terms.write_text(TERMS, encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.hooksPath", "/dev/null")
        (self.repo / ".gitignore").write_text(".terms.txt\n", encoding="utf-8")
        (self.repo / "notes.txt").write_text("one\nzebra-secret lives here\nthree\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env, check=True,
            capture_output=True, text=True,
        ).stdout

    def run_check(self, *args, terms=True, env_terms=None):
        env = dict(self.env)
        if env_terms is not None:
            env["ACP_PRIVATE_TERMS"] = env_terms
        cmd = [sys.executable, str(SCRIPT)]
        if terms:
            cmd += ["--terms", str(self.terms)]
        proc = subprocess.run(
            cmd + list(args), cwd=self.repo, env=env, capture_output=True, text=True,
        )
        out = proc.stdout + proc.stderr
        self.assertNotIn("zebra", out.lower())
        self.assertNotIn("example-host", out.lower())
        return proc

    def commit(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def test_added_line_hits(self):
        (self.repo / "app.py").write_text("x = 1\nhost = 'EXAMPLE-HOST42'\n", encoding="utf-8")
        self.commit()
        proc = self.run_check("--diff", f"{self.base}..HEAD")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("app.py:2: private term (rule 2)", proc.stdout)

    def test_removed_line_does_not_hit(self):
        (self.repo / "notes.txt").write_text("one\nthree\n", encoding="utf-8")
        self.commit()
        proc = self.run_check("--diff", f"{self.base}..HEAD")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(proc.stdout, "")

    def test_untouched_existing_hit_is_ignored_in_diff(self):
        with open(self.repo / "notes.txt", "a", encoding="utf-8") as fh:
            fh.write("four\n")
        self.commit()
        self.assertEqual(self.run_check("--diff", f"{self.base}..HEAD").returncode, 0)

    def test_staged(self):
        (self.repo / "cfg.ini").write_text("[a]\nb = zebra-secret\n", encoding="utf-8")
        self.git("add", "cfg.ini")
        proc = self.run_check("--staged")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cfg.ini:2: private term (rule 1)", proc.stdout)

    def test_new_file_name_hits(self):
        (self.repo / "zebra-secret.md").write_text("clean\n", encoding="utf-8")
        self.git("add", "-A")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--terms", str(self.terms), "--staged"],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("private term in file name (rule 1)", proc.stdout)

    def test_messages(self):
        (self.repo / "a.txt").write_text("fine\n", encoding="utf-8")
        self.commit("Tidy up\n\nRan it on example-host7 last night")
        proc = self.run_check("--messages", f"{self.base}..HEAD")
        self.assertEqual(proc.returncode, 1)
        self.assertRegex(proc.stdout, r"commit [0-9a-f]{12}:3: private term \(rule 2\)")

    def test_clean_messages(self):
        (self.repo / "a.txt").write_text("fine\n", encoding="utf-8")
        self.commit("Tidy up")
        self.assertEqual(self.run_check("--messages", f"{self.base}..HEAD").returncode, 0)

    def test_message_file_ignores_comments(self):
        msg = self.repo / "MSG"
        msg.write_text("Fix a bug\n# zebra-secret in a git comment\n", encoding="utf-8")
        self.assertEqual(self.run_check("--message-file", str(msg)).returncode, 0)
        msg.write_text("Fix a bug for zebra-secret\n", encoding="utf-8")
        proc = self.run_check("--message-file", str(msg))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("commit message:1: private term (rule 1)", proc.stdout)

    def test_all_mode(self):
        proc = self.run_check("--all")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("notes.txt:2: private term (rule 1)", proc.stdout)

    def test_terms_from_env(self):
        proc = self.run_check("--all", terms=False, env_terms=TERMS)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("notes.txt:2:", proc.stdout)

    def test_no_list_warns_and_passes(self):
        proc = self.run_check("--all", terms=False)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("warning", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_empty_env_list_warns_and_passes(self):
        proc = self.run_check("--all", terms=False, env_terms="# only a comment\n\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("warning", proc.stderr)

    def test_bad_regex_names_rule_only(self):
        self.terms.write_text("zebra-secret\nexample-host(\n", encoding="utf-8")
        proc = self.run_check("--all")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("rule 2", proc.stderr)


REPO_ROOT = Path(__file__).resolve().parent.parent


@unittest.skipIf(shutil.which("sh") is None, "pre-push hook needs a POSIX sh")
class PrePushHookTest(unittest.TestCase):
    """Runs real pushes to a local bare remote with .githooks/pre-push on."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        (self.home / ".config").mkdir(parents=True)
        self.terms = self.home / ".config" / "acp-private-terms.txt"
        self.terms.write_text(TERMS, encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.pop("ACP_PRIVATE_TERMS", None)
        self.env.update(HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1")
        self.remote = self.tmp / "remote.git"
        self.work = self.tmp / "work"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.work))
        self.identity("Test", "test@example.invalid")
        (self.work / "scripts").mkdir()
        shutil.copy(SCRIPT, self.work / "scripts" / "check_private_terms.py")
        shutil.copytree(REPO_ROOT / ".githooks", self.work / ".githooks")
        # An old hit that is already public must not block later pushes.
        (self.work / "notes.txt").write_text("zebra-secret was here\n", encoding="utf-8")
        self.commit("base")
        self.git(self.work, "remote", "add", "origin", str(self.remote))
        self.git(self.work, "push", "-q", "origin", "main")
        self.git(self.work, "fetch", "-q", "origin")
        self.git(self.work, "config", "core.hooksPath", ".githooks")

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", *args], cwd=cwd, env=self.env, check=True,
            capture_output=True, text=True,
        ).stdout

    def identity(self, name, email):
        self.env.update(
            GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
            GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email,
        )

    def commit(self, message="change"):
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "-q", "--no-verify", "-m", message)

    def push(self, *refs):
        proc = subprocess.run(
            ["git", "push", "origin", *refs], cwd=self.work, env=self.env,
            capture_output=True, text=True,
        )
        out = proc.stdout + proc.stderr
        self.assertNotIn("zebra", out.lower())
        self.assertNotIn("example-host", out.lower())
        return proc

    def test_clean_push_passes(self):
        (self.work / "a.txt").write_text("fine\n", encoding="utf-8")
        self.commit("Tidy up")
        self.assertEqual(self.push("main").returncode, 0)

    def test_added_line_blocks_existing_branch(self):
        (self.work / "a.txt").write_text("host = example-host3\n", encoding="utf-8")
        self.commit()
        proc = self.push("main")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("a.txt:1: private term (rule 2)", proc.stdout + proc.stderr)

    def test_new_branch_checked_against_origin_main(self):
        self.git(self.work, "checkout", "-q", "-b", "feature")
        (self.work / "b.txt").write_text("ok\n", encoding="utf-8")
        self.commit("first")
        (self.work / "c.txt").write_text("zebra-secret\n", encoding="utf-8")
        self.commit("second")
        proc = self.push("feature")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("c.txt:1: private term (rule 1)", proc.stdout + proc.stderr)

    def test_message_blocks(self):
        (self.work / "a.txt").write_text("fine\n", encoding="utf-8")
        self.commit("Ran it on example-host9")
        proc = self.push("main")
        self.assertNotEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout + proc.stderr, r"commit [0-9a-f]{12}:1: private term \(rule 2\)")

    def test_identity_blocks(self):
        self.identity("Test", "me@zebra-secret.example")
        (self.work / "a.txt").write_text("fine\n", encoding="utf-8")
        self.commit()
        proc = self.push("main")
        self.assertNotEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout + proc.stderr, r"commit [0-9a-f]{12}:author: private term \(rule 1\)")

    def test_branch_deletion_passes(self):
        self.git(self.work, "push", "-q", "--no-verify", "origin", "main:old")
        self.assertEqual(self.push(":old").returncode, 0)

    def test_no_list_passes(self):
        self.terms.unlink()
        (self.work / "a.txt").write_text("zebra-secret\n", encoding="utf-8")
        self.commit()
        self.assertEqual(self.push("main").returncode, 0)


if __name__ == "__main__":
    unittest.main()
