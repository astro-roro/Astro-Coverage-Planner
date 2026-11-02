"""Phase 4 packaging tests: the Linux download (spec section 5).

The Linux build itself needs a Linux machine and the network, so these tests pin down
the pieces that can be checked anywhere: the start script (run for real with a fake
``uname``), the build script's contents, the layout rules in check_tree, and the workflow.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packaging"))

import check_tree  # noqa: E402
from test_packaging import GOOD_NOTICES  # noqa: E402

LINUX = REPO / "packaging" / "linux"
POSIX = unittest.skipIf(sys.platform == "win32", "needs a POSIX shell")
EM_DASH = chr(0x2014)


class TestLinuxStartScript(unittest.TestCase):

    def script(self):
        return (LINUX / "start-acp").read_text(encoding="utf-8")

    def test_it_is_posix_shell_with_unix_line_endings(self):
        raw = (LINUX / "start-acp").read_bytes()
        self.assertTrue(raw.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r", raw)

    @POSIX
    def test_it_is_executable_in_the_repository(self):
        self.assertTrue(os.access(LINUX / "start-acp", os.X_OK))
        self.assertTrue(os.access(LINUX / "build.sh", os.X_OK))

    def test_it_runs_the_bundled_python_with_the_flags_from_its_own_folder(self):
        text = self.script()
        self.assertIn('dir=$(cd "$(dirname "$0")" && pwd)', text)
        self.assertIn('exec "$dir/python/bin/python3" -X utf8 -B "$dir/app/launcher.py"', text)

    def _install(self, folder):
        script = folder / "ACP" / "start-acp"
        script.parent.mkdir()
        script.write_text(self.script())
        script.chmod(0o755)
        return script

    def _fake_uname(self, folder, machine):
        bindir = folder / "fakebin"
        bindir.mkdir()
        fake = bindir / "uname"
        fake.write_text(f"#!/bin/sh\necho {machine}\n")
        fake.chmod(0o755)
        return dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def _folder(self):
        folder = Path(tempfile.mkdtemp(prefix="acp start é "))   # a space and a non-ASCII letter
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        return folder

    @POSIX
    def test_a_32_bit_system_stops_with_a_clear_message(self):
        for machine in ("armv7l", "i686"):
            with self.subTest(machine=machine):
                folder = self._folder()
                env = self._fake_uname(folder, machine)
                script = self._install(folder)
                done = subprocess.run(["/bin/sh", str(script)], capture_output=True,
                                      text=True, env=env, timeout=30)
                self.assertEqual(done.returncode, 1)
                self.assertIn("64-bit", done.stderr)
                self.assertIn(machine, done.stderr)

    @POSIX
    def test_a_64_bit_system_without_the_python_says_to_extract_everything(self):
        folder = self._folder()
        env = self._fake_uname(folder, "x86_64")
        script = self._install(folder)
        done = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True,
                              env=env, timeout=30)
        self.assertEqual(done.returncode, 1)
        self.assertIn("python/bin/python3", done.stderr)
        self.assertIn("Extract the whole archive", done.stderr)

    @POSIX
    def test_it_hands_over_to_the_bundled_python_and_passes_arguments_on(self):
        folder = self._folder()
        script = self._install(folder)
        root = script.parent
        (root / "python" / "bin").mkdir(parents=True)
        (root / "app").mkdir()
        (root / "app" / "launcher.py").write_text("")
        py = root / "python" / "bin" / "python3"
        py.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        py.chmod(0o755)
        done = subprocess.run([str(script), "--extra"], capture_output=True, text=True,
                              cwd=tempfile.gettempdir(), timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.splitlines(),
                         ["-X", "utf8", "-B", f"{root}/app/launcher.py", "--extra"])


class TestLinuxBuildScript(unittest.TestCase):

    def text(self):
        return (LINUX / "build.sh").read_text(encoding="utf-8")

    def test_the_script_follows_the_spec(self):
        text = self.text()
        for needle in ("--only-binary=:all:", "constraints.txt", "requirements-packaged.txt",
                       "compileall", "--invalidation-mode checked-hash",
                       "SHA256SUMS-linux-$arch.txt", "ACP-linux-$arch.tar.gz", "check_tree.py",
                       "make_icons.py", "--owner=0", "--group=0", "install_only_stripped",
                       "unknown-linux-gnu", "python/lib/python3.12/LICENSE.txt"):
            self.assertIn(needle, text)

    def test_it_stops_on_any_failure_and_refuses_the_wrong_download(self):
        text = self.text()
        self.assertIn("set -eu", text)
        self.assertIn("does not match packaging/pins.txt", text)
        self.assertIn("lost its executable bit", text)

    def test_it_names_both_architectures_and_the_pins_it_reads(self):
        text = self.text()
        for needle in ("x86_64", "aarch64", "PBS_LINUX_X86_64_SHA256", "PBS_LINUX_AARCH64_SHA256",
                       "PBS_RELEASE", "PBS_PYTHON_VERSION"):
            self.assertIn(needle, text)

    def test_source_only_packages_are_built_first_and_installed_from_wheels_only(self):
        text = self.text()
        self.assertIn("pip wheel", text)
        self.assertIn("--find-links", text)
        self.assertIn("sdist-only.txt", text)

    def test_it_installs_with_the_bundled_python_not_the_runners(self):
        text = self.text()
        self.assertIn('"$py" -m pip install', text)
        self.assertNotRegex(text, r"(?m)^\s*(python3?|pip3?) ")

    def test_it_leaves_out_the_same_folders_as_windows(self):
        ps1 = (REPO / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8")
        windows = re.search(r"\$excluded = @\(([^)]*)\)", ps1).group(1)
        windows_set = set(re.findall(r"'([^']+)/'", windows))
        linux = set(re.search(r"for dir in ([^;]+); do", self.text()).group(1).split())
        self.assertEqual(linux, windows_set)
        self.assertEqual(set(check_tree.EXCLUDED_FROM_APP), {d + "/" for d in windows_set})

    @POSIX
    def test_the_script_parses(self):
        done = subprocess.run(["/bin/sh", "-n", str(LINUX / "build.sh")], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    @POSIX
    def test_it_refuses_to_run_off_linux(self):
        if sys.platform.startswith("linux"):
            self.skipTest("this machine is Linux")
        out = tempfile.mkdtemp(prefix="acp-out-")
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        done = subprocess.run(["/bin/sh", str(LINUX / "build.sh"), "--out-dir", out],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertIn("must run on Linux", done.stderr)

    def test_the_readme_covers_the_points_the_spec_lists(self):
        text = (LINUX / "README.txt").read_text(encoding="utf-8")
        for needle in ("tar -xzf", "./ACP/start-acp", "ssh -L 5555:127.0.0.1:5555",
                       "applications menu", "tray", "Quit ACP", "XDG_CONFIG_HOME", "acp.log",
                       "glibc 2.28", "32-bit", "http://127.0.0.1:5555",
                       "only be reached from this computer", "sha256sum -c", "{{VERSION}}"):
            self.assertIn(needle, text)
        self.assertNotIn(EM_DASH, text)


class TestLinuxCheckTree(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-tree-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = self.tmp / "ACP"
        for rel in check_tree.REQUIRED["linux"]:
            path = self.tree / rel
            if rel.endswith("nina_ts_sync"):
                path.mkdir(parents=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if rel == check_tree.NOTICES:
                    path.write_text(GOOD_NOTICES)
                else:
                    # newline="\n" keeps the sample LF on Windows, where text mode writes CRLF.
                    path.write_text("#!/bin/sh\n" if rel == "start-acp" else "x", newline="\n")
        for rel in check_tree.LINUX_EXECUTABLE:
            (self.tree / rel).chmod(0o755)
        self.archive = self.tmp / "ACP-linux-x86_64.tar.gz"
        self.archive.write_bytes(b"x" * 1024)

    def run_check(self, **kwargs):
        return check_tree.check(self.tree, self.archive, "linux", version="1.0.0", **kwargs)

    def test_the_linux_layout_is_what_the_spec_lists(self):
        required = set(check_tree.REQUIRED["linux"])
        self.assertTrue({"start-acp", "README.txt", "VERSION.txt", "python/bin/python3",
                         "app/launcher.py", "app/static/offline-sky/properties"} <= required)
        self.assertNotIn("Start ACP.bat", required)

    def test_a_good_tree_passes(self):
        self.assertEqual(self.run_check(tag="v1.0.0"), [])

    def test_a_missing_start_script_or_python_fails(self):
        (self.tree / "start-acp").unlink()
        (self.tree / "python" / "bin" / "python3").unlink()
        problems = self.run_check()
        self.assertTrue(any("start-acp" in p for p in problems), problems)
        self.assertTrue(any("python/bin/python3" in p for p in problems), problems)

    @POSIX
    def test_a_start_script_without_its_executable_bit_fails(self):
        (self.tree / "start-acp").chmod(0o644)
        self.assertTrue(any("not executable: start-acp" in p for p in self.run_check()))

    @POSIX
    def test_a_python_without_its_executable_bit_fails(self):
        (self.tree / "python" / "bin" / "python3").chmod(0o644)
        self.assertTrue(any("not executable: python/bin/python3" in p for p in self.run_check()))

    def test_a_start_script_with_windows_line_endings_or_another_shell_fails(self):
        (self.tree / "start-acp").write_bytes(b"#!/bin/sh\r\necho hi\r\n")
        self.assertTrue(any("LF line endings" in p for p in self.run_check()))
        (self.tree / "start-acp").write_bytes(b"#!/bin/bash\n")
        self.assertTrue(any("#!/bin/sh" in p for p in self.run_check()))

    def test_a_tag_that_is_not_the_version_fails(self):
        self.assertTrue(any("tag" in p for p in self.run_check(tag="v1.0.1")))

    def test_the_excluded_folders_and_the_size_limit_still_apply(self):
        (self.tree / "app" / "tests").mkdir()
        self.assertTrue(any("app/tests/" in p for p in self.run_check()))
        problems = check_tree.check(self.tree, self.archive, "linux", version="1.0.0", max_zip_mb=0)
        self.assertTrue(any("MB" in p for p in problems))

    def test_the_command_line_accepts_linux(self):
        self.assertEqual(check_tree.main(["--tree", str(self.tree), "--platform", "linux"]), 0)

    def test_windows_does_not_need_the_linux_files(self):
        self.assertNotIn("start-acp", check_tree.REQUIRED["windows"])


class TestSmokeInAContainer(unittest.TestCase):
    """The ubuntu:20.04 smoke run is inside Docker, where ACP refuses to save settings
    (HTTP 409 not_writable), so the smoke test must pass the image folder another way."""

    def test_the_smoke_markers_match_the_ones_the_app_uses(self):
        import acp_settings
        import smoke
        self.assertEqual(tuple(smoke.CONTAINER_MARKERS), tuple(acp_settings.CONTAINER_MARKERS))

    def test_it_detects_a_container_by_the_marker_files(self):
        import smoke
        from unittest import mock
        with mock.patch("smoke.os.path.exists", side_effect=lambda p: p == "/.dockerenv"):
            self.assertTrue(smoke.in_container())
        with mock.patch("smoke.os.path.exists", return_value=False):
            self.assertFalse(smoke.in_container())

    def test_the_launcher_gets_fits_roots_only_when_asked(self):
        import smoke
        from unittest import mock
        with mock.patch("smoke.subprocess.Popen") as popen, \
                mock.patch.dict(os.environ, {"FITS_ROOTS": "/stale"}):
            smoke.start_launcher(Path("py"), Path("launcher.py"), Path("h"), 1234, tray=False)
            self.assertNotIn("FITS_ROOTS", popen.call_args.kwargs["env"])
            smoke.start_launcher(Path("py"), Path("launcher.py"), Path("h"), 1234, tray=False,
                                 fits_roots="/images")
            self.assertEqual(popen.call_args.kwargs["env"]["FITS_ROOTS"], "/images")

    def test_the_app_really_refuses_the_save_in_a_container(self):
        import acp_settings
        from unittest import mock
        with mock.patch("acp_settings.in_container", return_value=True):
            self.assertFalse(acp_settings.is_writable(Path(tempfile.gettempdir()) / "settings.json"))
        with mock.patch("acp_settings.in_container", return_value=False):
            self.assertTrue(acp_settings.is_writable(Path(tempfile.gettempdir()) / "settings.json"))


class TestLinuxWorkflow(unittest.TestCase):

    def setUp(self):
        base = REPO / ".github" / "workflows"
        self.text = (base / "release-linux.yml").read_text(encoding="utf-8")
        self.windows = (base / "release-windows.yml").read_text(encoding="utf-8")

    def test_it_builds_on_the_two_runners_the_spec_names(self):
        self.assertIn("runner: ubuntu-22.04\n", self.text)
        self.assertIn("runner: ubuntu-22.04-arm", self.text)

    def test_it_has_the_same_triggers_and_concurrency_as_windows(self):
        for needle in ("branches: [main, 'packaging/**']", "tags: ['v*']", "workflow_dispatch:",
                       "paths-ignore:",
                       "cancel-in-progress: ${{ !startsWith(github.ref, 'refs/tags/') }}",
                       "contents: read"):
            self.assertIn(needle, self.text)
            self.assertIn(needle, self.windows)

    def test_the_jobs_are_build_then_smoke_then_release(self):
        for job in ("  build:", "  smoke:", "  release:"):
            self.assertIn(job, self.text)
        self.assertIn("needs: build", self.text)
        self.assertIn("needs: smoke", self.text)

    def test_only_the_release_job_can_write_and_only_on_a_tag(self):
        release = self.text.split("  release:")[1]
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v')", release)
        self.assertIn("contents: write", release)
        self.assertEqual(self.text.count("contents: write"), 1)

    def test_the_smoke_job_uses_the_bundles_own_python_and_the_old_glibc_container(self):
        smoke_job = self.text.split("  smoke:")[1].split("  release:")[0]
        self.assertIn("$ACP_PACKAGE/python/bin/python3", smoke_job)
        self.assertIn("packaging/smoke.py", smoke_job)
        self.assertIn("ubuntu:20.04", smoke_job)
        self.assertIn("sha256sum -c", smoke_job)

    def test_it_uploads_to_the_same_release_the_same_way_as_windows(self):
        self.assertIn('gh release view "$TAG"', self.text)
        self.assertIn('gh release create "$TAG"', self.text)
        self.assertIn("gh release upload", self.text)
        for name in ("ACP-linux-x86_64.tar.gz", "SHA256SUMS-linux-x86_64.txt",
                     "ACP-linux-aarch64.tar.gz", "SHA256SUMS-linux-aarch64.txt"):
            self.assertIn(name, self.text)

    def test_the_workflow_file_has_no_em_dashes(self):
        self.assertNotIn(EM_DASH, self.text)


if __name__ == "__main__":
    unittest.main()
