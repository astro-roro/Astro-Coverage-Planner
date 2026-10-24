"""Shortcuts (spec docs/specs/windows-download-and-setup.md, section 3.5; tests
W1, X1 and the route half of S15).

The Windows logic runs on any machine with PowerShell stubbed. The real links
are made and read back only on a Windows runner (W1).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

import launcher  # noqa: E402
import shortcuts  # noqa: E402
import app as app_module  # noqa: E402
from test_settings import EnvCase  # noqa: E402

# A folder with spaces and non-ASCII characters, as a Windows user could have.
AWKWARD_ROOT = "C:\\Users\\Zoë Müller\\My Astro\\ACP 1.0"


class Recorder:
    """Stands in for subprocess.run and keeps what PowerShell would have got."""

    def __init__(self, stdout=""):
        self.calls = []
        self.stdout = stdout

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": argv, **kwargs})
        return mock.Mock(returncode=0, stdout=self.stdout, stderr="")


class TestWindowsLogic(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-lnk-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.menu = str(self.tmp / "Start Menu")
        self.desk = str(self.tmp / "Desktop é")

    def test_the_link_points_at_pythonw_with_the_flags_and_the_launcher(self):
        spec = shortcuts.windows_link_spec(AWKWARD_ROOT)
        self.assertEqual(spec["target"], AWKWARD_ROOT + "\\python\\pythonw.exe")
        self.assertEqual(spec["args"], f'-X utf8 -B "{AWKWARD_ROOT}\\app\\launcher.py"')
        self.assertEqual(spec["workdir"], AWKWARD_ROOT)
        self.assertEqual(spec["icon"], AWKWARD_ROOT + "\\app\\static\\acp.ico")

    def test_paths_reach_powershell_through_the_environment_never_the_script(self):
        run = Recorder()
        created = shortcuts.create(AWKWARD_ROOT, "win32", {}, run,
                                   start_menu_dir=self.menu, desktop_dir=self.desk)
        call = run.calls[0]
        self.assertEqual(call["argv"][:4],
                         ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"])
        script = call["argv"][4]
        self.assertEqual(script, shortcuts.LINK_SCRIPT)
        for text in (AWKWARD_ROOT, "Müller", self.menu, self.desk):
            self.assertNotIn(text, script)
        spec = shortcuts.windows_link_spec(AWKWARD_ROOT)
        env = call["env"]
        self.assertEqual(env["ACP_LNK_TARGET"], spec["target"])
        self.assertEqual(env["ACP_LNK_ARGS"], spec["args"])
        self.assertEqual(env["ACP_LNK_WORKDIR"], spec["workdir"])
        self.assertEqual(env["ACP_LNK_ICON"], spec["icon"])
        self.assertEqual(env["ACP_LNK_FOLDERS"].split("|"), [self.menu, self.desk])
        self.assertEqual(env["ACP_LNK_NAME"], "Astro Coverage Planner.lnk")
        self.assertEqual(created, [os.path.join(self.menu, shortcuts.LINK_NAME),
                                   os.path.join(self.desk, shortcuts.LINK_NAME)])

    def test_the_real_folders_come_from_getfolderpath(self):
        run = Recorder(stdout=f"{self.menu}\n{self.desk}\n")
        shortcuts.create(AWKWARD_ROOT, "win32", {}, run)
        self.assertEqual(len(run.calls), 2)
        self.assertIn("GetFolderPath('Desktop')", run.calls[0]["argv"][4])
        self.assertEqual(run.calls[1]["env"]["ACP_LNK_FOLDERS"].split("|"), [self.menu, self.desk])

    def test_a_powershell_failure_is_a_shortcut_error(self):
        def failing(argv, **kw):
            return mock.Mock(returncode=1, stdout="", stderr="boom")
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.create(AWKWARD_ROOT, "win32", {}, failing,
                             start_menu_dir=self.menu, desktop_dir=self.desk)

    def make_links(self, *folders):
        for folder in folders:
            Path(folder).mkdir(parents=True, exist_ok=True)
            (Path(folder) / shortcuts.LINK_NAME).write_text("link")

    def test_w1_remove_deletes_the_files_acp_made_and_nothing_else(self):
        self.make_links(self.menu, self.desk)
        (Path(self.desk) / "Other.lnk").write_text("keep")
        gone = shortcuts.remove("win32", {}, Recorder(), start_menu_dir=self.menu,
                                desktop_dir=self.desk)
        self.assertEqual(len(gone), 2)
        self.assertFalse((Path(self.menu) / shortcuts.LINK_NAME).exists())
        self.assertFalse((Path(self.desk) / shortcuts.LINK_NAME).exists())
        self.assertTrue((Path(self.desk) / "Other.lnk").exists())

    def test_w1_a_moved_folder_rewrites_only_the_links_that_still_exist(self):
        self.make_links(self.menu)               # the user deleted the desktop one
        run = Recorder()
        done = shortcuts.rewrite(AWKWARD_ROOT, "win32", {}, run, start_menu_dir=self.menu,
                                 desktop_dir=self.desk)
        self.assertEqual(done, [os.path.join(self.menu, shortcuts.LINK_NAME)])
        self.assertEqual(run.calls[0]["env"]["ACP_LNK_FOLDERS"], self.menu)

    def test_rewrite_with_no_links_does_nothing(self):
        run = Recorder()
        self.assertEqual(shortcuts.rewrite(AWKWARD_ROOT, "win32", {}, run, start_menu_dir=self.menu,
                                           desktop_dir=self.desk), [])
        self.assertEqual(run.calls, [])


@unittest.skipUnless(sys.platform == "win32", "makes real .lnk files; runs on the Windows runner")
class TestWindowsLinksForReal(unittest.TestCase):

    def test_w1_the_links_read_back_with_the_right_target_arguments_folder_and_icon(self):
        tmp = Path(tempfile.mkdtemp(prefix="acp lnk é "))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = str(tmp / "My ACP é")
        menu, desk = str(tmp / "menu"), str(tmp / "desk")
        made = shortcuts.create(root, start_menu_dir=menu, desktop_dir=desk)
        self.assertEqual(len(made), 2)
        spec = shortcuts.windows_link_spec(root)
        for path in made:
            got = shortcuts.read_windows_link(path)
            self.assertEqual(got["target"].lower(), spec["target"].lower())
            self.assertEqual(got["args"], spec["args"])
            self.assertEqual(got["workdir"].lower(), spec["workdir"].lower())
            self.assertEqual(got["icon"].split(",")[0].lower(), spec["icon"].lower())
        self.assertEqual(len(shortcuts.remove(start_menu_dir=menu, desktop_dir=desk)), 2)
        self.assertEqual(shortcuts.existing(start_menu_dir=menu, desktop_dir=desk), [])
        shortcuts.create(root, start_menu_dir=menu, desktop_dir=desk)
        os.remove(made[1])
        rewritten = shortcuts.rewrite(str(tmp / "Moved"), start_menu_dir=menu, desktop_dir=desk)
        self.assertEqual(rewritten, [made[0]])
        self.assertFalse(os.path.exists(made[1]))
        self.assertIn("Moved", shortcuts.read_windows_link(made[0])["target"])


class TestLinuxEntry(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-desktop-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {"XDG_DATA_HOME": str(self.tmp / "share")}
        self.root = str(self.tmp / "My Astro" / 'ACP "1"')

    def entry(self) -> Path:
        return self.tmp / "share" / "applications" / "acp.desktop"

    def test_x1_the_entry_has_an_absolute_quoted_exec_and_an_absolute_icon(self):
        made = shortcuts.create(self.root, "linux", self.env)
        self.assertEqual(made, [str(self.entry())])
        lines = self.entry().read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "[Desktop Entry]")
        self.assertIn("Type=Application", lines)
        self.assertIn("Name=Astro Coverage Planner", lines)
        self.assertIn("Terminal=false", lines)
        self.assertIn("Categories=Science;Astronomy;", lines)
        exec_line = next(l for l in lines if l.startswith("Exec="))
        self.assertEqual(exec_line, 'Exec="' + self.root.replace('"', '\\"') + '/start-acp"')
        icon = next(l for l in lines if l.startswith("Icon="))
        self.assertEqual(icon, f"Icon={self.root}/app/static/acp.png")
        self.assertTrue(icon[5:].startswith("/"))
        self.assertEqual(oct(self.entry().stat().st_mode & 0o777), "0o644")

    def test_x1_special_characters_in_the_folder_are_escaped_in_exec(self):
        text = shortcuts.linux_entry_text("/home/a b/$HOME/`x`/100%/back\\slash")
        exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
        self.assertEqual(exec_line, 'Exec="/home/a b/\\$HOME/\\`x\\`/100%%/back\\\\slash/start-acp"')

    @unittest.skipUnless(shutil.which("desktop-file-validate"), "desktop-file-validate not installed")
    def test_x1_the_file_passes_desktop_file_validate(self):
        shortcuts.create("/opt/ACP", "linux", self.env)
        result = subprocess.run(["desktop-file-validate", str(self.entry())],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_x1_remove_deletes_it_and_rewrite_only_touches_an_entry_that_exists(self):
        self.assertEqual(shortcuts.rewrite("/opt/new", "linux", self.env), [])
        self.assertFalse(self.entry().exists(), "a deleted shortcut is not recreated")
        shortcuts.create("/opt/old", "linux", self.env)
        self.assertEqual(shortcuts.rewrite("/opt/new", "linux", self.env), [str(self.entry())])
        self.assertIn('Exec="/opt/new/start-acp"', self.entry().read_text())
        self.assertEqual(shortcuts.remove("linux", self.env), [str(self.entry())])
        self.assertFalse(self.entry().exists())

    def test_the_default_location_is_under_local_share(self):
        path = shortcuts.linux_entry_path({}, home=lambda: Path("/home/u"))
        self.assertEqual(path, Path("/home/u/.local/share/applications/acp.desktop"))

    def test_mac_and_unknown_platforms_have_no_shortcuts(self):
        self.assertFalse(shortcuts.available("darwin"))
        self.assertTrue(shortcuts.available("win32"))
        self.assertTrue(shortcuts.available("linux"))
        with self.assertRaises(shortcuts.ShortcutError):
            shortcuts.create("/x", "darwin", {})
        self.assertEqual(shortcuts.existing("darwin", {}), [])


class TestSettingsRoutes(EnvCase):
    """S15, the route half: the answer is recorded only after the files change."""

    def setUp(self):
        super().setUp()
        os.environ["ACP_PACKAGED"] = "1"
        for name in ("create", "remove"):
            patcher = mock.patch.object(app_module.acp_shortcuts, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(app_module.acp_shortcuts, "available", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_available_only_in_a_packaged_build_on_windows_or_linux(self):
        self.assertTrue(app_module._shortcuts_available())
        os.environ.pop("ACP_PACKAGED")
        self.assertFalse(app_module._shortcuts_available())
        os.environ["ACP_PACKAGED"] = "1"
        with mock.patch.object(app_module.acp_shortcuts, "available", return_value=False):
            self.assertFalse(app_module._shortcuts_available())

    def test_true_makes_the_shortcuts_and_records_the_folder(self):
        resp = self.put({"shortcuts": True})
        self.assertEqual(resp.status_code, 200)
        root = shortcuts.app_root(app_module.REPO_ROOT)
        self.create.assert_called_once_with(root)
        saved = self.read_settings()
        self.assertIs(saved["shortcuts"], True)
        self.assertEqual(saved["shortcuts_app_dir"], str(root))

    def test_false_removes_them_and_ends_the_offer(self):
        self.put({"shortcuts": True})
        resp = self.put({"shortcuts": False})
        self.assertEqual(resp.status_code, 200)
        self.remove.assert_called_once()
        saved = self.read_settings()
        self.assertIs(saved["shortcuts"], False)
        self.assertIsNone(saved["shortcuts_app_dir"])
        self.assertEqual(self.client.get("/api/settings").get_json()["shortcuts"],
                         {"available": True, "value": False})

    def test_null_changes_no_files(self):
        self.assertEqual(self.put({"shortcuts": None}).status_code, 200)
        self.create.assert_not_called()
        self.remove.assert_not_called()

    def test_a_failure_leaves_the_offer_open(self):
        self.create.side_effect = shortcuts.ShortcutError("PowerShell failed: boom")
        resp = self.put({"shortcuts": True})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (500, "shortcuts_failed"))
        self.assertNotIn("boom", resp.get_data(as_text=True), "no operating system text in the answer")
        self.assertFalse(self.settings_file.exists())

    def test_the_view_says_whether_the_build_is_packaged(self):
        self.assertTrue(self.client.get("/api/settings").get_json()["packaged"])
        os.environ.pop("ACP_PACKAGED")
        self.assertFalse(self.client.get("/api/settings").get_json()["packaged"])

    def test_shortcuts_need_an_authorised_client(self):
        resp = self.put({"shortcuts": True}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
        self.assertEqual(resp.status_code, 403)
        self.create.assert_not_called()


class TestLauncherRefresh(EnvCase):

    def setUp(self):
        super().setUp()
        self.root = str(shortcuts.app_root(launcher.APP_DIR))
        patcher = mock.patch.object(shortcuts, "rewrite", return_value=["x"])
        self.rewrite = patcher.start()
        self.addCleanup(patcher.stop)

    def refresh(self):
        launcher.refresh_shortcuts(dict(os.environ), "win32")

    def test_a_moved_folder_rewrites_the_links_and_records_the_new_folder(self):
        self.write_settings({"shortcuts": True, "shortcuts_app_dir": "D:\\Old\\ACP"})
        self.refresh()
        self.rewrite.assert_called_once_with(self.root, "win32")
        self.assertEqual(self.read_settings()["shortcuts_app_dir"], self.root)

    def test_the_same_folder_changes_nothing(self):
        self.write_settings({"shortcuts": True, "shortcuts_app_dir": self.root})
        self.refresh()
        self.rewrite.assert_not_called()

    def test_declined_or_unanswered_shortcuts_are_left_alone(self):
        for value in (False, None):
            self.write_settings({"shortcuts": value, "shortcuts_app_dir": "D:\\Old\\ACP"})
            self.refresh()
        self.rewrite.assert_not_called()

    def test_a_failure_is_logged_and_does_not_raise(self):
        self.write_settings({"shortcuts": True, "shortcuts_app_dir": "D:\\Old\\ACP"})
        self.rewrite.side_effect = shortcuts.ShortcutError("boom")
        with self.assertLogs("acp.launcher", "ERROR"):
            self.refresh()
        self.assertEqual(self.read_settings()["shortcuts_app_dir"], "D:\\Old\\ACP")


if __name__ == "__main__":
    unittest.main()
