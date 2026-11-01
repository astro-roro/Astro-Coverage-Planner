"""The Mac bundle (spec docs/specs/windows-download-and-setup.md, section 4, tests M1 to M4
and the Mac half of K5).

Nothing here builds or signs anything: that needs a Mac with an Apple chip, and for
signing, the five Apple secrets. These tests check the files the build reads, the
tree check for the Mac layout, the Mach-O finder, the stub's source, the signing
script's no-secrets path (which runs anywhere there is bash), and the workflow.
"""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:                                    # PyYAML arrives with astropy
    yaml = None

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "packaging"))
sys.path.insert(0, str(REPO / "packaging" / "macos"))

import check_tree  # noqa: E402
import macho  # noqa: E402
import smoke  # noqa: E402
from test_packaging import GOOD_NOTICES  # noqa: E402

MACOS = REPO / "packaging" / "macos"
WORKFLOW = REPO / ".github" / "workflows" / "release-macos.yml"
SECRETS = {"MACOS_CERT_P12_BASE64", "MACOS_CERT_PASSWORD", "APPLE_TEAM_ID", "APPLE_ID",
           "APPLE_APP_PASSWORD"}

MACHO_ARM64 = struct.pack("<I", 0xFEEDFACF) + b"\x0c\x00\x00\x01" + b"\x00" * 32
JAVA_CLASS = b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, 65) + b"\x00" * 16


def pins() -> dict:
    return dict(line.split("=", 1) for line in
                (REPO / "packaging" / "pins.txt").read_text().splitlines()
                if re.match(r"^[A-Z0-9_]+=", line))


class TestMacPins(unittest.TestCase):

    def test_the_standalone_python_is_a_3_12_with_a_real_hash(self):
        found = pins()
        self.assertRegex(found["PYTHON_MACOS_VERSION"], r"^3\.12\.\d+$")
        self.assertRegex(found["PYTHON_MACOS_STANDALONE_TAG"], r"^\d{8}$")
        self.assertRegex(found["PYTHON_MACOS_ARM64_SHA256"], r"^[0-9a-f]{64}$")

    def test_the_menu_bar_dependency_and_what_it_pulls_in_are_pinned_for_macos_only(self):
        text = (REPO / "packaging" / "constraints.txt").read_text()
        for name in ("pyobjc-framework-Quartz", "pyobjc-core", "pyobjc-framework-Cocoa"):
            self.assertRegex(text, rf'(?m)^{name}==[0-9.]+; sys_platform == "darwin"$', name)

    def test_the_packaged_requirements_ask_for_the_menu_bar_library_on_macos(self):
        text = (REPO / "requirements-packaged.txt").read_text()
        self.assertIn('pyobjc-framework-Quartz; sys_platform == "darwin"', text)


class TestMachoFinder(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-macho-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, rel: str, data: bytes) -> Path:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_it_finds_mach_o_files_whatever_they_are_called(self):
        self.write("a/libx.dylib", MACHO_ARM64)
        self.write("a/b/ext.cpython-312-darwin.so", MACHO_ARM64)
        self.write("bin/tool", MACHO_ARM64)               # no extension at all
        self.write("a/readme.txt", b"hello world, not a Mach-O file")
        found = {p.relative_to(self.tmp).as_posix() for p in macho.find_macho(self.tmp)}
        self.assertEqual(found, {"a/libx.dylib", "a/b/ext.cpython-312-darwin.so", "bin/tool"})

    def test_a_fat_file_counts_and_a_java_class_file_does_not(self):
        fat = b"\xca\xfe\xba\xbe" + struct.pack(">I", 2) + b"\x00" * 40
        self.write("fat.dylib", fat)
        self.write("Thing.class", JAVA_CLASS)
        found = {p.name for p in macho.find_macho(self.tmp)}
        self.assertEqual(found, {"fat.dylib"})

    def test_the_deepest_paths_come_first_so_the_inside_is_signed_before_the_outside(self):
        self.write("top.dylib", MACHO_ARM64)
        self.write("a/mid.so", MACHO_ARM64)
        self.write("a/b/c/deep.so", MACHO_ARM64)
        names = [p.name for p in macho.find_macho(self.tmp)]
        self.assertEqual(names, ["deep.so", "mid.so", "top.dylib"])

    def test_symlinks_are_left_alone(self):
        target = self.write("lib/real.dylib", MACHO_ARM64)
        (self.tmp / "lib" / "link.dylib").symlink_to(target)
        self.assertEqual([p.name for p in macho.find_macho(self.tmp)], ["real.dylib"])

    def test_short_and_empty_files_are_not_mach_o(self):
        self.write("empty", b"")
        self.write("three", b"\xcf\xfa\xed")
        self.assertEqual(macho.find_macho(self.tmp), [])

    def test_the_command_line_lists_paths(self):
        self.write("a/x.so", MACHO_ARM64)
        out = subprocess.run([sys.executable, "-I", str(MACOS / "macho.py"), "list", str(self.tmp)],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), str(self.tmp / "a" / "x.so"))


def make_mac_tree(root: Path, version: str = "1.0.0") -> Path:
    tree = root / "ACP"
    for rel in check_tree.REQUIRED["macos"]:
        path = tree / rel
        if rel.endswith("nina_ts_sync"):
            path.mkdir(parents=True)
        elif rel == check_tree.NOTICES:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(GOOD_NOTICES)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    plist = {
        "CFBundleIdentifier": "com.astrowithroro.acp", "CFBundleExecutable": "ACP",
        "CFBundleShortVersionString": version, "CFBundleIconFile": "ACP",
        "LSMinimumSystemVersion": "11.0", "LSUIElement": True,
        "NSDocumentsFolderUsageDescription": "x", "NSDesktopFolderUsageDescription": "x",
        "NSRemovableVolumesUsageDescription": "x", "NSNetworkVolumesUsageDescription": "x",
    }
    (tree / "ACP.app/Contents/Info.plist").write_bytes(plistlib.dumps(plist))
    (tree / "ACP.app/Contents/MacOS/ACP").write_bytes(MACHO_ARM64)
    return tree


class TestCheckTreeMac(unittest.TestCase):
    """The Mac half of K5."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-mactree-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = make_mac_tree(self.tmp)
        self.zip = self.tmp / "ACP-macos-arm64.zip"
        self.zip.write_bytes(b"x" * 1024)
        self.app = self.tree / "ACP.app/Contents/Resources/app"

    def run_check(self, **kwargs):
        return check_tree.check(self.tree, self.zip, "macos", version="1.0.0", **kwargs)

    def test_a_good_tree_passes(self):
        self.assertEqual(self.run_check(), [])

    def test_the_tag_must_equal_the_version(self):
        self.assertEqual(self.run_check(tag="v1.0.0"), [])
        self.assertTrue(any("does not match" in p for p in self.run_check(tag="v1.0.1")))

    def test_a_big_archive_fails(self):
        problems = check_tree.check(self.tree, self.zip, "macos", version="1.0.0", max_zip_mb=0)
        self.assertTrue(any("MB" in p for p in problems), problems)

    def test_every_required_part_is_named_when_missing(self):
        for rel in check_tree.REQUIRED["macos"]:
            backup = self.tmp / "backup"
            target = self.tree / rel
            if backup.exists():
                shutil.rmtree(backup) if backup.is_dir() else backup.unlink()
            shutil.move(str(target), str(backup))
            problems = self.run_check()
            self.assertTrue(any(rel in p or "Info.plist" in p or check_tree.NOTICES in p
                                for p in problems), (rel, problems))
            shutil.move(str(backup), str(target))

    def test_the_menu_bar_icon_the_launcher_asks_for_is_required(self):
        self.assertIn("ACP.app/Contents/Resources/app/static/acp-menubar.png",
                      check_tree.REQUIRED["macos"])

    def test_the_stub_must_be_a_mach_o_file_not_a_script(self):
        (self.tree / "ACP.app/Contents/MacOS/ACP").write_text("#!/bin/sh\nexec python\n")
        self.assertTrue(any("not a Mach-O" in p for p in self.run_check()))

    def test_the_plist_version_must_equal_the_app_version(self):
        path = self.tree / "ACP.app/Contents/Info.plist"
        plist = plistlib.loads(path.read_bytes())
        plist["CFBundleShortVersionString"] = "0.9.0"
        path.write_bytes(plistlib.dumps(plist))
        self.assertTrue(any("CFBundleShortVersionString" in p for p in self.run_check()))

    def test_the_app_must_have_no_dock_icon(self):
        path = self.tree / "ACP.app/Contents/Info.plist"
        plist = plistlib.loads(path.read_bytes())
        plist["LSUIElement"] = False
        path.write_bytes(plistlib.dumps(plist))
        self.assertTrue(any("LSUIElement" in p for p in self.run_check()))

    def test_each_privacy_prompt_text_is_required(self):
        for key in ("NSDocumentsFolderUsageDescription", "NSDesktopFolderUsageDescription",
                    "NSRemovableVolumesUsageDescription", "NSNetworkVolumesUsageDescription"):
            path = self.tree / "ACP.app/Contents/Info.plist"
            original = path.read_bytes()
            plist = plistlib.loads(original)
            del plist[key]
            path.write_bytes(plistlib.dumps(plist))
            self.assertTrue(any(key in p for p in self.run_check()), key)
            path.write_bytes(original)

    def test_a_broken_plist_is_reported_and_not_raised(self):
        (self.tree / "ACP.app/Contents/Info.plist").write_text("not a plist")
        self.assertTrue(any("Info.plist cannot be read" in p for p in self.run_check()))

    def test_folders_left_out_of_the_app_fail_where_the_mac_keeps_it(self):
        (self.app / "tests").mkdir()
        problems = self.run_check()
        self.assertTrue(any("Resources/app/tests/" in p for p in problems), problems)

    def test_windows_still_looks_in_app(self):
        # The Mac rule must not have moved the Windows one.
        self.assertEqual(check_tree.APP_DIR["windows"], "app")

    def test_the_command_line_accepts_macos(self):
        argv = ["--tree", str(self.tree), "--zip", str(self.zip), "--platform", "macos"]
        self.assertEqual(check_tree.main(argv), 0)
        (self.tree / "VERSION.txt").unlink()
        self.assertEqual(check_tree.main(argv), 1)


class TestPlistTemplate(unittest.TestCase):

    def test_the_template_reads_as_a_plist_with_the_keys_the_spec_lists(self):
        text = (MACOS / "Info.plist.in").read_text()
        plist = plistlib.loads(text.replace("{{VERSION}}", "1.0.0").replace("{{MIN_MACOS}}", "11.0").encode())
        self.assertEqual(plist["CFBundleExecutable"], "ACP")
        self.assertEqual(plist["CFBundleIconFile"], "ACP")
        self.assertIs(plist["LSUIElement"], True)
        self.assertEqual(plist["CFBundleShortVersionString"], "1.0.0")
        self.assertEqual(plist["LSMinimumSystemVersion"], "11.0")
        self.assertEqual(plist["CFBundlePackageType"], "APPL")
        for key in ("NSDocumentsFolderUsageDescription", "NSDesktopFolderUsageDescription",
                    "NSRemovableVolumesUsageDescription", "NSNetworkVolumesUsageDescription"):
            self.assertTrue(plist[key])

    def test_the_template_has_no_placeholder_the_build_does_not_fill(self):
        text = (MACOS / "Info.plist.in").read_text()
        build = (MACOS / "build.sh").read_text()
        for name in set(re.findall(r"\{\{([A-Z_]+)\}\}", text)):
            self.assertIn("{{%s}}" % name, build, name)


class TestEntitlements(unittest.TestCase):

    def test_the_entitlements_file_reads_and_stays_small(self):
        plist = plistlib.loads((MACOS / "entitlements.plist").read_bytes())
        self.assertEqual(set(plist), {"com.apple.security.cs.disable-library-validation",
                                      "com.apple.security.cs.allow-unsigned-executable-memory"})
        self.assertTrue(all(value is True for value in plist.values()))

    def test_the_readme_explains_each_entitlement(self):
        text = (REPO / "packaging" / "README.md").read_text()
        for key in ("disable-library-validation", "allow-unsigned-executable-memory"):
            self.assertIn(key, text)


class TestStubSource(unittest.TestCase):

    def setUp(self):
        self.text = (MACOS / "stub.c").read_text()

    def test_it_replaces_itself_with_the_bundled_python_and_the_launcher(self):
        self.assertIn("execv(python, args)", self.text)
        self.assertIn("Resources/python/bin/python3", self.text)
        self.assertIn("Resources/app/launcher.py", self.text)

    def test_it_passes_the_flags_the_embedded_python_needs(self):
        self.assertRegex(self.text, r'"-X"[\s\S]*"utf8"[\s\S]*"-B"')

    def test_it_builds_absolute_paths_because_launchservices_starts_apps_in_slash(self):
        self.assertIn("realpath", self.text)
        self.assertIn("_NSGetExecutablePath", self.text)
        self.assertNotIn('"./', self.text)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "needs clang on a Mac")
    def test_it_compiles_without_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run(["clang", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                                  str(MACOS / "stub.c")], capture_output=True, text=True)
            # A sandbox that cannot write clang's cache prints errors about it; those are
            # not warnings about the source.
            noise = [l for l in out.stderr.splitlines() if "xcrun_db" not in l]
            self.assertEqual([l for l in noise if "warning" in l or "error" in l], [], tmp)


class TestBuildScript(unittest.TestCase):

    def setUp(self):
        self.text = (MACOS / "build.sh").read_text()

    def test_it_stops_on_the_first_error_and_is_valid_bash(self):
        self.assertIn("set -euo pipefail", self.text)
        if shutil.which("bash"):
            subprocess.run(["bash", "-n", str(MACOS / "build.sh")], check=True)

    def test_it_follows_the_spec(self):
        for needle in ("PYTHON_MACOS_ARM64_SHA256", "shasum -a 256", "python-build-standalone",
                       "install_only", "--only-binary=:all:", "packaging/constraints.txt",
                       "requirements-packaged.txt", "sdist-only.txt", "git -c core.quotepath=off ls-files",
                       "make_icons.py", "--icns", "stub.c", "clang", "-arch arm64",
                       "checked-hash", "ditto -c -k", "check_tree.py", "--platform macos",
                       "ACP-macos-arm64-UNSIGNED.zip", "codesign --force --sign -"):
            self.assertIn(needle, self.text, needle)

    def test_it_leaves_out_the_same_folders_as_windows(self):
        for folder in check_tree.EXCLUDED_FROM_APP:
            self.assertIn(folder + "*", self.text, folder)

    def test_it_installs_into_the_bundled_pythons_own_site_packages(self):
        # No --target: dependencies must land where the bundled Python looks without help.
        self.assertNotIn("--target", self.text)

    def test_it_does_not_zip_with_python_because_that_drops_symlinks(self):
        self.assertNotIn("zip_tree.py", self.text)

    def test_it_trims_pips_launcher_scripts_but_keeps_astropy_tests(self):
        self.assertIn("astropy/tests", self.text)
        self.assertRegex(self.text, r"! -name 'python3'")


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class TestSignScript(unittest.TestCase):

    def run_sign(self, env_extra: dict, tmp: Path):
        env = {k: v for k, v in os.environ.items() if k not in SECRETS}
        env.update(env_extra)
        output = tmp / "github-output"
        env["GITHUB_OUTPUT"] = str(output)
        proc = subprocess.run(["bash", str(MACOS / "sign.sh"), "--zip", str(tmp / "none.zip"),
                               "--out", str(tmp / "out")], capture_output=True, text=True, env=env)
        return proc, (output.read_text() if output.exists() else "")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-sign-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_m4_with_no_secrets_it_skips_says_so_and_exits_zero(self):
        proc, output = self.run_sign({}, self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SKIPPING signing and notarising", proc.stdout)
        self.assertIn("signed=false", output)
        for name in SECRETS:
            self.assertIn(name, proc.stdout)               # all five are named as missing
        self.assertFalse((self.tmp / "out").exists(), "nothing is written when it skips")

    def test_one_missing_secret_is_enough_to_skip_and_only_that_one_is_named(self):
        env = {name: "x" for name in SECRETS if name != "APPLE_APP_PASSWORD"}
        proc, output = self.run_sign(env, self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("signed=false", output)
        self.assertIn("APPLE_APP_PASSWORD", proc.stdout)
        self.assertNotIn("MACOS_CERT_PASSWORD", proc.stdout)

    def test_it_never_prints_a_secret_value(self):
        env = {name: f"SECRET-VALUE-{name}" for name in SECRETS if name != "APPLE_ID"}
        proc, _ = self.run_sign(env, self.tmp)
        self.assertNotIn("SECRET-VALUE", proc.stdout + proc.stderr)

    def test_the_script_has_no_trace_mode_and_follows_the_spec_steps(self):
        text = (MACOS / "sign.sh").read_text()
        self.assertNotRegex(text, r"(?m)^\s*set\s+-\w*x")
        self.assertNotIn("bash -x", text)
        for needle in ("security create-keychain", "security set-key-partition-list",
                       "apple-tool:,apple:", "security list-keychains", "--options runtime",
                       "--timestamp", "--force", "macho.py", "codesign --verify --deep --strict",
                       "notarytool submit", "--wait", "--timeout 30m", "notarytool log",
                       "stapler staple", "stapler validate", "ditto -c -k", "SHA256SUMS-macos.txt",
                       "trap cleanup EXIT", "security delete-keychain", "entitlements.plist",
                       "ACP-macos-arm64.zip"):
            self.assertIn(needle, text, needle)

    def test_it_reads_only_the_five_secrets(self):
        text = (MACOS / "sign.sh").read_text()
        names = set(re.findall(r"\b((?:MACOS|APPLE|NOTARY)_[A-Z0-9_]+)\b", text))
        self.assertEqual(names, SECRETS)

    def test_it_is_valid_bash(self):
        subprocess.run(["bash", "-n", str(MACOS / "sign.sh")], check=True)

    def test_the_log_is_printed_only_when_notarisation_fails(self):
        text = (MACOS / "sign.sh").read_text()
        self.assertLess(text.index('if [ "$STATUS" != "Accepted" ]'), text.index("notarytool log"))


class TestSmokeImports(unittest.TestCase):
    """M1: the Mac smoke test imports every package in requirements.txt."""

    def modules(self, platform="darwin"):
        return smoke.requirement_imports([REPO / "requirements.txt", REPO / "requirements-packaged.txt"],
                                         platform)

    def test_every_requirement_becomes_an_import(self):
        found = self.modules()
        for module in ("flask", "astropy", "numpy", "scipy", "astroquery", "mocpy", "xisf",
                       "waitress", "croniter", "paramiko", "pystray", "PIL", "Quartz"):
            self.assertIn(module, found)

    def test_windows_only_and_linux_only_lines_are_skipped_on_a_mac(self):
        found = self.modules("darwin")
        self.assertNotIn("tzdata", found)
        self.assertNotIn("Xlib", found)

    def test_the_menu_bar_library_is_skipped_elsewhere(self):
        self.assertNotIn("Quartz", self.modules("win32"))
        self.assertIn("tzdata", self.modules("win32"))

    def test_a_missing_module_is_reported_by_name(self):
        failures = smoke.failed_imports(Path(sys.executable), ["json", "not_a_real_module_acp"])
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0].startswith("not_a_real_module_acp"))

    def test_every_installed_module_passes(self):
        self.assertEqual(smoke.failed_imports(Path(sys.executable), ["json", "os"]), [])


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class TestMacWorkflow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.data = yaml.safe_load(cls.text)
        cls.data["on"] = cls.data.pop(True, cls.data.get("on"))
        cls.jobs = cls.data["jobs"]

    def steps_text(self, job: str) -> str:
        return "\n".join(str(s.get("run", "")) for s in self.jobs[job]["steps"])

    def test_it_has_the_same_triggers_as_windows(self):
        on = self.data["on"]
        self.assertEqual(on["push"]["tags"], ["v*"])
        self.assertIn("main", on["push"]["branches"])
        self.assertIn("packaging/**", on["push"]["branches"])
        self.assertIn("workflow_dispatch", on)
        self.assertEqual(sorted(on["pull_request"]["paths-ignore"]), ["**.md", "docs/**"])
        self.assertEqual(self.data["permissions"], {"contents": "read"})

    def test_it_builds_on_apple_silicon(self):
        for name in ("build", "sign", "smoke"):
            self.assertEqual(self.jobs[name]["runs-on"], "macos-14", name)

    def test_the_job_order_is_build_then_sign_then_smoke_then_release(self):
        self.assertEqual(self.jobs["sign"]["needs"], "build")
        self.assertEqual(set(self.jobs["smoke"]["needs"]), {"build", "sign"})
        self.assertEqual(set(self.jobs["release"]["needs"]), {"sign", "smoke"})

    def test_m3_signing_never_runs_on_a_pull_request(self):
        condition = " ".join(str(self.jobs["sign"]["if"]).split())
        self.assertIn("github.event_name != 'pull_request'", condition)
        self.assertIn("refs/heads/main", condition)
        self.assertIn("refs/tags/v", condition)
        for name, job in self.jobs.items():
            if name == "sign":
                continue
            self.assertNotIn("sign.sh", "\n".join(str(s.get("run", "")) for s in job["steps"]), name)

    def test_the_sign_job_runs_in_the_release_environment(self):
        self.assertEqual(self.jobs["sign"]["environment"], "release")
        for name in ("build", "smoke", "release"):
            self.assertNotIn("environment", self.jobs[name], name)

    def test_m3_the_secrets_reach_only_the_signing_step_and_only_through_env(self):
        used = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.text))
        self.assertEqual(used, SECRETS | {"GITHUB_TOKEN"})
        for name, job in self.jobs.items():
            for step in job["steps"]:
                for field in ("run", "with", "name", "if"):
                    self.assertNotIn("secrets.", str(step.get(field, "")), f"{name} {step.get('name')}")
                has_secret = "secrets." in str(step.get("env", ""))
                if has_secret:
                    self.assertIn(name, ("sign", "release"), step.get("name"))
            self.assertNotIn("secrets.", str(self.jobs[name].get("if", "")))
        sign_env = [s["env"] for s in self.jobs["sign"]["steps"] if "MACOS_CERT_P12_BASE64" in str(s.get("env", ""))]
        self.assertEqual(len(sign_env), 1)
        self.assertEqual(set(sign_env[0]), SECRETS)

    def test_m3_no_trace_mode_anywhere(self):
        self.assertNotIn("set -x", self.text)
        self.assertNotRegex(self.text, r"(?m)^\s*(bash|sh)\s+-\w*[xv]")

    def test_only_the_release_job_can_write_to_the_repository(self):
        for name, job in self.jobs.items():
            writes = job.get("permissions", {}).get("contents") == "write"
            self.assertEqual(writes, name == "release", name)

    def test_the_release_runs_only_on_a_tag_after_smoke_and_only_for_a_signed_build(self):
        condition = str(self.jobs["release"]["if"])
        self.assertIn("refs/tags/v", condition)
        self.assertIn("needs.sign.outputs.signed == 'true'", condition)
        text = "\n".join(str(s.get("run", "")) for s in self.jobs["release"]["steps"])
        self.assertIn("gh release view", text)
        self.assertIn("gh release create", text)
        self.assertIn("--clobber", text)
        self.assertIn("dist/ACP-macos-arm64.zip", text)
        self.assertIn("SHA256SUMS-macos.txt", text)
        self.assertNotIn("UNSIGNED", text)

    def test_m4_an_unsigned_run_uploads_an_unsigned_artifact_and_attaches_nothing(self):
        uploads = [s["with"]["name"] for j in self.jobs.values() for s in j["steps"]
                   if str(s.get("uses", "")).startswith("actions/upload-artifact")]
        self.assertIn("acp-macos-unsigned", uploads)
        self.assertIn("acp-macos-signed", uploads)
        self.assertNotIn("UNSIGNED", self.steps_text("release"))
        signed_upload = [s for s in self.jobs["sign"]["steps"]
                         if s.get("with", {}).get("name") == "acp-macos-signed"][0]
        self.assertEqual(signed_upload["if"], "steps.sign.outputs.signed == 'true'")

    def test_without_the_secrets_the_run_warns_but_does_not_fail(self):
        self.assertIn("::warning::", self.text)
        self.assertIn("UNSIGNED", self.text)

    def test_the_smoke_job_unzips_elsewhere_and_uses_the_bundles_own_python(self):
        text = self.steps_text("smoke")
        self.assertIn("RUNNER_TEMP", text)
        self.assertIn("Contents/Resources/python/bin/python3", text)
        self.assertIn("packaging/smoke.py", text)
        self.assertIn("--tray", text)
        self.assertIn("--import-requirements requirements.txt requirements-packaged.txt", text)
        self.assertNotIn("setup-python", self.text)        # the runner's Python is never used

    def test_the_signed_checks_use_a_quarantined_copy(self):
        step = [s for s in self.jobs["smoke"]["steps"] if "M2" in str(s.get("name", ""))][0]
        self.assertEqual(step["if"], "needs.sign.outputs.signed == 'true'")
        for needle in ("com.apple.quarantine", "codesign --verify --deep --strict", "spctl --assess",
                       "stapler validate"):
            self.assertIn(needle, step["run"])

    def test_the_bundle_is_verified_again_after_the_smoke_run(self):
        names = [s.get("name", "") for s in self.jobs["smoke"]["steps"]]
        after = [i for i, n in enumerate(names) if "unchanged after a run" in n]
        smoke_step = [i for i, n in enumerate(names) if n.startswith("Smoke test")]
        self.assertTrue(after and smoke_step and after[0] > smoke_step[0])

    def test_the_build_job_checks_the_tag_through_the_script(self):
        text = self.steps_text("build")
        self.assertIn("GITHUB_REF_TYPE", text)
        self.assertIn("--tag", text)

    def test_the_downloads_it_names_match_what_the_scripts_write(self):
        build = (MACOS / "build.sh").read_text()
        sign = (MACOS / "sign.sh").read_text()
        self.assertIn("ACP-macos-arm64-UNSIGNED.zip", build)
        self.assertIn("ACP-macos-arm64-UNSIGNED.zip", self.text)
        self.assertIn("ACP-macos-arm64.zip", sign)
        self.assertIn("SHA256SUMS-macos.txt", sign)


if __name__ == "__main__":
    unittest.main()
