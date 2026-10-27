"""The package inputs and the build checks (spec docs/specs/windows-download-and-setup.md,
tests K5 and K6, and the pinning rules of section 3.7).

None of this builds a package. It checks the files the build reads, the tree
check that K5 runs, the zip step, and that scan subprocesses repeat the
interpreter flags the bundled Python would otherwise lose.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packaging"))

import app as app_module  # noqa: E402
import check_tree  # noqa: E402
import make_notices  # noqa: E402
import smoke  # noqa: E402
import zip_tree  # noqa: E402
from test_settings import EnvCase  # noqa: E402

# Packages that the later phases pin when they first build for those platforms.
PINNED_LATER = {"pyobjc-framework-quartz", "python-xlib"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match:
            names.add(norm(match.group(1)))
    return names


def constraint_lines(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


class TestPins(unittest.TestCase):

    def test_every_constraint_is_an_exact_pin(self):
        for line in constraint_lines(REPO / "packaging" / "constraints.txt"):
            self.assertRegex(line, r"^[A-Za-z0-9][A-Za-z0-9._-]*==[A-Za-z0-9.!+_-]+(\s*;.*)?$", line)

    def test_every_direct_dependency_is_pinned(self):
        pinned = {norm(re.split(r"==", l)[0]) for l in constraint_lines(REPO / "packaging" / "constraints.txt")}
        wanted = (requirement_names(REPO / "requirements.txt")
                  | requirement_names(REPO / "requirements-packaged.txt")) - PINNED_LATER
        self.assertEqual(sorted(wanted - pinned), [])

    def test_no_pin_is_below_the_floor_in_requirements(self):
        floors = {}
        for name in ("requirements.txt", "requirements-packaged.txt"):
            for line in (REPO / name).read_text(encoding="utf-8").splitlines():
                m = re.match(r"([A-Za-z0-9._-]+)>=([0-9.]+)", line.strip())
                if m:
                    floors[norm(m.group(1))] = m.group(2)

        def key(v):
            return [int(x) for x in re.findall(r"\d+", v)]
        for line in constraint_lines(REPO / "packaging" / "constraints.txt"):
            m = re.match(r"([A-Za-z0-9._-]+)==([^\s;]+)", line)
            floor = floors.get(norm(m.group(1)))
            if floor:
                self.assertGreaterEqual(key(m.group(2)), key(floor), line)

    def test_the_packaged_requirements_are_not_in_the_main_file(self):
        main = requirement_names(REPO / "requirements.txt")
        self.assertTrue({"pystray", "pillow"}.isdisjoint(main),
                        "source and Docker installs must get nothing new")

    def test_pins_file_has_a_python_version_and_a_hash_or_a_todo(self):
        pins = dict(line.split("=", 1) for line in
                    (REPO / "packaging" / "pins.txt").read_text().splitlines()
                    if re.match(r"^[A-Z0-9_]+=", line))
        self.assertRegex(pins["PYTHON_VERSION"], r"^3\.12\.\d+$")
        self.assertRegex(pins["PYTHON_WINDOWS_EMBED_SHA256"], r"^(TODO|[0-9a-f]{64})$")

    def test_the_audit_and_dependabot_cover_the_new_files(self):
        tests_yml = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("requirements.txt requirements-packaged.txt", tests_yml)
        dependabot = (REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("directory: /packaging", dependabot)


class TestStartFiles(unittest.TestCase):

    def test_the_bat_builds_every_path_from_its_own_folder_and_passes_the_flags(self):
        bat = (REPO / "packaging" / "windows" / "Start ACP.bat").read_text(encoding="utf-8")
        self.assertIn('if not exist "%~dp0python\\pythonw.exe"', bat)
        self.assertIn("Extract the whole zip first", bat)
        self.assertIn('start "" "%~dp0python\\pythonw.exe" -X utf8 -B "%~dp0app\\launcher.py"', bat)
        bat.encode("ascii")                      # cmd reads the file in the OEM code page

    def test_the_build_script_follows_the_spec(self):
        ps1 = (REPO / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8")
        for needle in ("--only-binary=:all:", "constraints.txt", "requirements-packaged.txt",
                       "..\\site-packages", "..\\app", "compileall", "SHA256SUMS-windows.txt",
                       "ACP-windows-x64.zip", "check_tree.py", "make_icons.py"):
            self.assertIn(needle, ps1)
        self.assertIn("'--invalidation-mode', 'checked-hash'", ps1)

    def test_source_only_packages_are_built_first_and_still_pinned(self):
        ps1 = (REPO / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("'pip', 'wheel'", ps1)
        self.assertIn("'--find-links', $wheels", ps1)
        self.assertIn("'--only-binary=:all:'", ps1)
        pins = {l for l in constraint_lines(REPO / "packaging" / "constraints.txt")}
        listed = constraint_lines(REPO / "packaging" / "sdist-only.txt")
        self.assertIn("xisf==0.9.7", listed)
        self.assertTrue(set(listed) <= pins, "each source-only package must match its constraint")

    def test_the_start_check_reads_the_listener_port_from_its_endpoint(self):
        # TcpListener has no LocalPort property; under strict mode that throws.
        ps1 = (REPO / "packaging" / "windows" / "smoke-start.ps1").read_text(encoding="utf-8")
        self.assertNotIn(".LocalPort", ps1)
        self.assertIn("$listener.LocalEndpoint.Port", ps1)

    def test_the_readme_covers_the_points_the_spec_lists(self):
        text = (REPO / "packaging" / "windows" / "README.txt").read_text(encoding="utf-8")
        for needle in ("C:\\ACP", "Unblock", "Run anyway", "notification area", "Quit ACP",
                       "%APPDATA%\\ACP", "http://127.0.0.1:5555", "only be reached from this computer"):
            self.assertIn(needle, text)


GOOD_NOTICES = (make_notices.HEADER + "\n[This is the first released version of the Lesser GPL.]\n"
                "Mozilla Public License, v. 2.0\n")


class TestCheckTree(unittest.TestCase):
    """K5."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-tree-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tree = self.tmp / "ACP"
        for rel in check_tree.REQUIRED["windows"]:
            path = self.tree / rel
            if rel.endswith("nina_ts_sync"):
                path.mkdir(parents=True)
            elif rel == check_tree.NOTICES:
                path.write_text(GOOD_NOTICES)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
        self.zip = self.tmp / "ACP-windows-x64.zip"
        self.zip.write_bytes(b"x" * 1024)

    def run_check(self, **kwargs):
        return check_tree.check(self.tree, self.zip, "windows", version="1.0.0", **kwargs)

    def test_a_good_tree_passes(self):
        self.assertEqual(self.run_check(tag="v1.0.0"), [])

    def test_a_tag_that_is_not_the_version_fails(self):
        self.assertTrue(any("tag" in p for p in self.run_check(tag="v1.0.1")))

    def test_a_big_archive_fails(self):
        problems = check_tree.check(self.tree, self.zip, "windows", version="1.0.0",
                                    max_zip_mb=0)
        self.assertTrue(any("MB" in p for p in problems))

    def test_a_long_path_fails_and_names_it(self):
        deep = self.tree / "site-packages" / ("d" * 60) / ("e" * 60) / ("f" * 40) / "file.py"
        deep.parent.mkdir(parents=True)
        deep.write_text("x")
        problems = self.run_check()
        self.assertTrue(any("longest path" in p and "ddd" in p for p in problems), problems)

    def test_a_path_just_under_the_limit_passes(self):
        # "ACP/" is 4 characters, so 145 more is 149, which is under 150.
        name = "g" * (149 - len("ACP/"))
        (self.tree / name).write_text("x")
        self.assertEqual(self.run_check(), [])
        (self.tree / ("h" * 146)).write_text("x")
        self.assertTrue(self.run_check())

    def test_missing_parts_fail(self):
        (self.tree / "app" / "launcher.py").unlink()
        shutil.rmtree(self.tree / "app" / "bundled_extensions")
        problems = self.run_check()
        self.assertTrue(any("launcher.py" in p for p in problems))
        self.assertTrue(any("nina_ts_sync" in p for p in problems))

    def test_the_licence_and_notices_files_are_required(self):
        (self.tree / "LICENSE").unlink()
        (self.tree / check_tree.NOTICES).unlink()
        problems = self.run_check()
        self.assertTrue(any("LICENSE" in p for p in problems), problems)
        self.assertTrue(any(check_tree.NOTICES in p for p in problems), problems)

    def test_credits_are_required(self):
        (self.tree / "CREDITS.md").unlink()
        self.assertTrue(any("CREDITS.md" in p for p in self.run_check()))

    def test_a_notices_file_without_the_licence_texts_fails(self):
        (self.tree / check_tree.NOTICES).write_text(make_notices.HEADER)
        problems = self.run_check()
        self.assertTrue(any("lesser general public license" in p for p in problems), problems)
        self.assertTrue(any("mozilla public license" in p for p in problems), problems)
        (self.tree / check_tree.NOTICES).write_text(
            "first released version of the Lesser GPL. Mozilla Public License")
        problems = self.run_check()
        self.assertTrue(any("astro-coverage-planner" in p for p in problems), problems)
        self.assertTrue(any("version 3 or later" in p for p in problems), problems)

    def test_folders_that_must_be_left_out_fail(self):
        (self.tree / "app" / "tests").mkdir()
        self.assertTrue(any("app/tests/" in p for p in self.run_check()))

    def test_the_version_is_read_from_app_py(self):
        self.assertEqual(check_tree.app_version(), app_module.VERSION)

    def test_the_command_line_exits_1_on_a_failure(self):
        self.assertEqual(check_tree.main(["--tree", str(self.tree), "--zip", str(self.zip),
                                          "--platform", "windows", "--tag", "v9"]), 1)
        self.assertEqual(check_tree.main(["--tree", str(self.tree), "--zip", str(self.zip),
                                          "--platform", "windows", "--tag",
                                          "v" + app_module.VERSION]), 0)


class TestMakeNotices(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-notices-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.site = self.tmp / "site-packages"
        self.py_licence = self.tmp / "LICENSE.txt"
        self.py_licence.write_text("PSF LICENSE TEXT\r\nline two\r\n")
        for name, licence in (("paramiko", "LGPL 2.1 text. This is the first released version of the Lesser GPL."),
                              ("pystray", "LGPL 2.1 text. This is the first released version of the Lesser GPL."),
                              ("certifi", "Mozilla Public License Version 2.0"),
                              ("astropy", "BSD 3")):
            self.add_package(name, licence)
        wcs = self.site / "astropy-1.0.dist-info" / "licenses" / "licenses"
        wcs.mkdir(parents=True)
        (wcs / "WCSLIB_LICENSE.rst").write_text("WCSLIB is LGPL")

    def add_package(self, name, licence, folder="LICENSE"):
        info = self.site / f"{name}-1.0.dist-info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
                                       "License: Some Licence\n")
        (info / "RECORD").write_text("not a licence")
        if licence:
            (info / folder).write_text(licence)

    def test_it_writes_every_licence_text_and_the_header(self):
        text, problems = make_notices.build(self.site, self.py_licence)
        self.assertEqual(problems, [])
        for needle in ("GNU General Public License version 3 or later", make_notices.SOURCE_URL,
                       "PSF LICENSE TEXT", "paramiko 1.0", "pystray 1.0", "certifi 1.0",
                       "Mozilla Public License Version 2.0", "WCSLIB is LGPL"):
            self.assertIn(needle, text)
        self.assertNotIn("not a licence", text)
        self.assertNotIn("\r", text)

    def test_a_shared_text_is_printed_once(self):
        text, _ = make_notices.build(self.site, self.py_licence)
        self.assertEqual(text.count("LGPL 2.1 text. This is the first released version of the Lesser GPL."), 1)
        self.assertIn("the same text as paramiko 1.0", text)

    def test_the_output_passes_the_tree_check(self):
        text, _ = make_notices.build(self.site, self.py_licence)
        lowered = text.lower()
        for marker in check_tree.NOTICE_MARKERS:
            options = (marker,) if isinstance(marker, str) else marker
            self.assertTrue(any(option in lowered for option in options), marker)

    def test_a_required_package_without_a_licence_file_fails(self):
        (self.site / "pystray-1.0.dist-info" / "LICENSE").unlink()
        _, problems = make_notices.build(self.site, self.py_licence)
        self.assertTrue(any("pystray" in p for p in problems), problems)

    def test_a_missing_required_package_fails(self):
        shutil.rmtree(self.site / "certifi-1.0.dist-info")
        _, problems = make_notices.build(self.site, self.py_licence)
        self.assertTrue(any("certifi" in p for p in problems), problems)

    def test_a_missing_python_licence_fails(self):
        _, problems = make_notices.build(self.site, self.tmp / "nope.txt")
        self.assertTrue(any("Python" in p for p in problems), problems)

    def test_the_command_line_writes_crlf_and_exits_0(self):
        out = self.tmp / "THIRD-PARTY-NOTICES.txt"
        self.assertEqual(make_notices.main(["--site", str(self.site), "--python-license",
                                            str(self.py_licence), "--out", str(out)]), 0)
        self.assertIn(b"\r\n", out.read_bytes())

    def test_the_build_writes_the_licence_files_into_the_zip(self):
        ps1 = (REPO / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8")
        for needle in ("make_notices.py", "THIRD-PARTY-NOTICES.txt", "'LICENSE'",
                       "docs\\credits.md", "CREDITS.md", "python\\LICENSE.txt"):
            self.assertIn(needle, ps1)


class TestZipTree(unittest.TestCase):

    def test_the_folder_is_the_top_entry_and_files_keep_their_paths(self):
        tmp = Path(tempfile.mkdtemp(prefix="acp-zip-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "ACP" / "app").mkdir(parents=True)
        (tmp / "ACP" / "app" / "é.py").write_text("x")
        (tmp / "ACP" / "Start ACP.bat").write_text("y")
        self.assertEqual(zip_tree.zip_tree(tmp / "ACP", tmp / "out.zip"), 2)
        with zipfile.ZipFile(tmp / "out.zip") as archive:
            self.assertEqual(sorted(archive.namelist()), ["ACP/Start ACP.bat", "ACP/app/é.py"])
            self.assertIsNone(archive.testzip())


class TestSmokeFixture(unittest.TestCase):
    """The smoke test writes its own FITS files; they must be real ones."""

    def test_the_hand_written_files_read_back_with_astropy(self):
        fits = __import__("astropy.io.fits", fromlist=["fits"])
        tmp = Path(tempfile.mkdtemp(prefix="acp-fix-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        folder = smoke.make_fixture(tmp)
        files = sorted(folder.rglob("*.fits"))
        self.assertEqual(len(files), 3)
        self.assertTrue(all(ord(c) > 127 for c in folder.name[:1]) or "è" in folder.name)
        with fits.open(files[0]) as hdul:
            header = hdul[0].header
            self.assertEqual(header["OBJECT"], "M 31")
            self.assertEqual(header["IMAGETYP"], "Light")
            self.assertEqual(header["EXPTIME"], 300.0)
            self.assertEqual(hdul[0].data.shape, (16, 16))
        for path in files:
            self.assertEqual(path.stat().st_size % 2880, 0)


class TestInterpreterFlags(EnvCase):
    """K6, the half that runs here: a child Python repeats -X utf8 and -B, because
    the bundled Python ignores the environment variables that would set them."""

    def fake_flags(self, **values):
        base = {"utf8_mode": 0, "dont_write_bytecode": 0}
        base.update(values)
        return mock.Mock(**base)

    def test_a_normal_install_adds_nothing(self):
        with mock.patch.object(app_module.sys, "flags", self.fake_flags()):
            self.assertEqual(app_module._interpreter_args(), [])

    def test_the_flags_are_repeated_when_the_parent_has_them(self):
        with mock.patch.object(app_module.sys, "flags",
                               self.fake_flags(utf8_mode=1, dont_write_bytecode=1)):
            self.assertEqual(app_module._interpreter_args(), ["-X", "utf8", "-B"])

    def test_the_scan_and_catalogue_commands_carry_them(self):
        root = self.folder("archive")
        self.write_settings({"fits_roots": [root]})
        seen = []
        self.addCleanup(setattr, app_module, "_run_scan_subprocess", app_module._run_scan_subprocess)
        self.addCleanup(setattr, app_module, "_run_catalogue_subprocess",
                        app_module._run_catalogue_subprocess)
        app_module._run_scan_subprocess = lambda args: seen.append(args) or 0
        app_module._run_catalogue_subprocess = lambda args: seen.append(args) or 0
        with mock.patch.object(app_module.sys, "flags",
                               self.fake_flags(utf8_mode=1, dont_write_bytecode=1)):
            self.assertEqual(app_module.claim_scan("page"), "claimed")
            app_module.run_claimed_scan()
            app_module._run_catalogue_fetch()
        for args in seen:
            self.assertEqual(args[1:4], ["-X", "utf8", "-B"], args)
            self.assertTrue(args[4].endswith(".py"))


if __name__ == "__main__":
    unittest.main()
