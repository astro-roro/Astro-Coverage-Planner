"""The shipped container image, checked structurally rather than by building.

Against the image built from this tree on 2026-09-07:

  - `docker exec <id> id` reported uid 0. The process had no need of root.
  - `docker exec <id> ls -a /app` showed a 68 MB copy of the maintainer's own
    .venv-scan virtualenv, whose shebangs and pyvenv.cfg name absolute paths
    under their home directory, plus .github and a dangling `notes` symlink
    pointing at a personal Obsidian vault. The image was 835 MB.

CI builds from a clean checkout, so the published image never carried those.
A maintainer or contributor building locally did.

These assertions read the two files rather than running docker, so they cost
nothing and run everywhere.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


class TestDockerfile(unittest.TestCase):

    def setUp(self):
        self.lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()

    def users(self):
        return [ln.split(None, 1)[1].strip()
                for ln in self.lines if ln.strip().upper().startswith("USER ")]

    def test_the_image_declares_a_user(self):
        self.assertTrue(self.users(), "Dockerfile has no USER line, so it runs as root")

    def test_that_user_is_not_root(self):
        for user in self.users():
            self.assertNotIn(user.split(":")[0], ("root", "0"), user)

    def test_the_user_is_created_before_it_is_switched_to(self):
        body = "\n".join(self.lines)
        name = self.users()[-1].split(":")[0]
        self.assertRegex(body, rf"useradd[^\n]*\b{re.escape(name)}\b")

    def test_the_data_directory_is_writable_by_that_user(self):
        body = "\n".join(self.lines)
        self.assertRegex(body, r"chown[^\n]*/app/data")


class TestDockerignore(unittest.TestCase):

    def setUp(self):
        self.entries = {ln.strip().rstrip("/")
                        for ln in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.startswith("#")}

    def test_the_obvious_ones_are_excluded(self):
        for wanted in (".git", "data", "tests", "docs"):
            self.assertIn(wanted, self.entries, wanted)

    def test_local_working_directories_are_excluded(self):
        """The three that actually shipped in a local build."""
        self.assertIn("notes", self.entries)
        self.assertIn(".github", self.entries)
        self.assertTrue(any(e.startswith(".venv") for e in self.entries),
                        "no .venv pattern; a local virtualenv will ship in the image")


if __name__ == "__main__":
    unittest.main()
