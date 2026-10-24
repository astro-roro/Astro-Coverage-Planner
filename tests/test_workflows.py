"""Lint for the release workflows (spec docs/specs/windows-download-and-setup.md, test M3
and the shape of section 3.9).

Phase 2 has one release workflow. Phases 3 and 4 add theirs to WORKFLOWS and
the same rules apply: no ``set -x``, no ``secrets.`` outside an ``env:`` block,
and ``contents: write`` only on the release job.
"""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    import yaml
except ImportError:                                    # PyYAML arrives with astropy
    yaml = None

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = [REPO / ".github" / "workflows" / "release-windows.yml"]


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # YAML 1.1 reads the key "on" as True.
    data["on"] = data.pop(True, data.get("on"))
    return data


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TestReleaseWorkflows(unittest.TestCase):

    def test_m3_no_trace_mode_in_any_step(self):
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("set -x", text, path.name)
            self.assertNotRegex(text, r"(?m)^\s*(bash|sh)\s+-\w*[xv]", path.name)

    def test_m3_secrets_appear_only_in_env_blocks(self):
        for path in WORKFLOWS:
            for name, job in load(path)["jobs"].items():
                for step in job["steps"]:
                    for field in ("run", "with", "name", "if"):
                        self.assertNotIn("secrets.", str(step.get(field, "")),
                                         f"{path.name} {name} {step.get('name')} {field}")
                self.assertNotIn("secrets.", str(job.get("if", "")), name)

    def test_only_the_release_job_can_write_to_the_repository(self):
        for path in WORKFLOWS:
            data = load(path)
            self.assertEqual(data["permissions"], {"contents": "read"}, path.name)
            for name, job in data["jobs"].items():
                writes = job.get("permissions", {}).get("contents") == "write"
                self.assertEqual(writes, name == "release", f"{path.name} {name}")

    def test_the_release_job_runs_only_on_a_tag_and_only_after_smoke(self):
        data = load(REPO / ".github" / "workflows" / "release-windows.yml")
        release = data["jobs"]["release"]
        self.assertIn("refs/tags/v", release["if"])
        self.assertEqual(release["needs"], "smoke")
        self.assertEqual(data["jobs"]["smoke"]["needs"], "build")

    def test_triggers_cover_tags_main_packaging_branches_dispatch_and_non_docs_prs(self):
        on = load(REPO / ".github" / "workflows" / "release-windows.yml")["on"]
        self.assertEqual(on["push"]["tags"], ["v*"])
        self.assertIn("main", on["push"]["branches"])
        self.assertIn("packaging/**", on["push"]["branches"])
        self.assertIn("workflow_dispatch", on)
        self.assertEqual(sorted(on["pull_request"]["paths-ignore"]), ["**.md", "docs/**"])

    def test_the_release_is_created_if_missing_and_uploads_clobber(self):
        text = (REPO / ".github" / "workflows" / "release-windows.yml").read_text(encoding="utf-8")
        self.assertIn("gh release view", text)
        self.assertIn("gh release create", text)
        self.assertIn("--clobber", text)
        self.assertIn("SHA256SUMS-windows.txt", text)
        self.assertIn("ACP-windows-x64.zip", text)

    def test_the_smoke_job_unzips_to_a_different_folder_from_the_build(self):
        data = load(REPO / ".github" / "workflows" / "release-windows.yml")
        steps = "\n".join(str(s.get("run", "")) for s in data["jobs"]["smoke"]["steps"])
        self.assertIn("RUNNER_TEMP", steps)
        self.assertIn("smoke.py", steps)
        self.assertIn("--tray", steps)
        self.assertIn("smoke-start.ps1", steps)

    def test_every_action_is_pinned_to_a_version(self):
        for path in WORKFLOWS:
            for name, job in load(path)["jobs"].items():
                for step in job["steps"]:
                    if "uses" in step:
                        self.assertRegex(step["uses"], r"@v\d", step["uses"])


if __name__ == "__main__":
    unittest.main()
