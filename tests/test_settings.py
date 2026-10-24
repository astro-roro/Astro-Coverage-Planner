"""Settings file and settings routes (spec docs/specs/windows-download-and-setup.md,
tests S1 to S15, plus the authorisation rules of section 7 for the routes 1a adds).

The module tests pass the platform and environment in, so every OS branch runs
on any machine. The route tests point ACP_HOME at a temp folder so nothing
touches the real profile. Anything that depends on import-time state (the path
constants in app.py and the builder) runs in a subprocess with a scrubbed
environment, because the test process has already imported both.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import acp_settings  # noqa: E402
import app as app_module  # noqa: E402

CLEAN_VARS = ("FITS_ROOTS", "MANIFEST_PATH", "PORT", "ACP_SCAN_CRON", "ACP_API_TOKEN",
              "ACP_DATA_DIR", "ACP_PACKAGED", "ACP_HOME", "ACP_BIND_HOST", "HOST")


def run_python(code: str, env_extra: dict, cwd: Path = REPO) -> str:
    """Run code in a fresh interpreter with a scrubbed environment."""
    env = {k: v for k, v in os.environ.items()
           if k not in CLEAN_VARS and k not in ("PYTHONPATH",)
           and not k.endswith("_PATH") and not k.startswith("ACP_")}
    env.update({"ACP_SKIP_STARTUP_MIGRATION": "1", "ACP_DISABLE_BUNDLED_EXTENSIONS": "1"})
    env.update(env_extra)
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    if proc.returncode != 0:
        raise AssertionError(f"subprocess failed:\n{proc.stderr[-2000:]}")
    return proc.stdout.strip().splitlines()[-1]


class EnvCase(unittest.TestCase):
    """A temp ACP_HOME, a scrubbed environment and a test client."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-settings-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in CLEAN_VARS:
            os.environ.pop(name, None)
        os.environ["ACP_HOME"] = str(self.tmp / "home")
        self.client = app_module.app.test_client()
        self.addCleanup(self._reset_scan_state)
        self.addCleanup(setattr, app_module, "MANIFEST_PATH", app_module.MANIFEST_PATH)
        app_module.MANIFEST_PATH = self.tmp / "no-manifest.json"

    def _reset_scan_state(self):
        with app_module.SCAN_STATE_LOCK:
            app_module._scan_state["running"] = False

    @property
    def settings_file(self) -> Path:
        return self.tmp / "home" / "settings.json"

    def write_settings(self, data) -> None:
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        text = data if isinstance(data, str) else json.dumps(data)
        self.settings_file.write_text(text, encoding="utf-8")

    def read_settings(self) -> dict:
        return json.loads(self.settings_file.read_text(encoding="utf-8"))

    def folder(self, name: str = "images") -> str:
        path = self.tmp / name
        path.mkdir(exist_ok=True)
        return str(path)

    def put(self, body, **kwargs):
        return self.client.put("/api/settings", json=body, **kwargs)


# --------------------------------------------------------------------------
# The settings module
# --------------------------------------------------------------------------

class TestPrecedence(unittest.TestCase):

    def resolved(self, env, file_data):
        return acp_settings.resolve_all(env=env, file_data=file_data)

    def test_s1_environment_beats_file_beats_default(self):
        file_data = {"fits_roots": ["/file"], "port": 6000, "manifest_path": "/file/m.json"}
        env = {"FITS_ROOTS": "/env", "PORT": "7000", "MANIFEST_PATH": "/env/m.json"}
        got = self.resolved(env, file_data)
        self.assertEqual(got["fits_roots"]["value"], ["/env"])
        self.assertEqual(got["port"]["value"], 7000)
        self.assertEqual(got["manifest_path"]["value"], "/env/m.json")
        for key in ("fits_roots", "port", "manifest_path"):
            self.assertEqual(got[key]["source"], "env")
            self.assertTrue(got[key]["locked"])

        got = self.resolved({}, file_data)
        self.assertEqual(got["fits_roots"]["value"], ["/file"])
        self.assertEqual(got["port"]["value"], 6000)
        self.assertEqual(got["manifest_path"]["value"], "/file/m.json")
        for key in ("fits_roots", "port", "manifest_path"):
            self.assertEqual(got[key]["source"], "file")
            self.assertFalse(got[key]["locked"])

        got = self.resolved({}, {})
        self.assertEqual(got["fits_roots"]["value"], [])
        self.assertEqual(got["port"]["value"], 5555)
        self.assertIsNone(got["manifest_path"]["value"])
        for key in ("fits_roots", "port", "manifest_path"):
            self.assertEqual(got[key]["source"], "default")

    def test_s1_scan_cron_follows_the_same_order(self):
        self.assertEqual(self.resolved({"ACP_SCAN_CRON": "0 3 * * *"},
                                       {"scan_cron": "1 1 * * *"})["scan_cron"]["value"],
                         "0 3 * * *")
        self.assertEqual(self.resolved({}, {"scan_cron": "1 1 * * *"})["scan_cron"]["source"],
                         "file")
        self.assertIsNone(self.resolved({}, {})["scan_cron"]["value"])

    def test_s2_empty_environment_variable_is_unset(self):
        file_data = {"fits_roots": ["/file"], "port": 6000, "manifest_path": "/file/m.json",
                     "scan_cron": "1 1 * * *"}
        env = {"FITS_ROOTS": "", "PORT": "  ", "MANIFEST_PATH": "", "ACP_SCAN_CRON": ""}
        got = self.resolved(env, file_data)
        for key in ("fits_roots", "port", "manifest_path", "scan_cron"):
            self.assertEqual(got[key]["source"], "file", key)
            self.assertFalse(got[key]["locked"], key)

    def test_s3_environment_roots_split_trim_and_drop_empties(self):
        self.assertEqual(acp_settings.parse_roots(" D:/a ; ;E:/b;"), ["D:/a", "E:/b"])
        self.assertEqual(acp_settings.parse_roots(""), [])
        got = self.resolved({"FITS_ROOTS": " /a ;; /b "}, {})
        self.assertEqual(got["fits_roots"]["value"], ["/a", "/b"])

    def test_s3_file_accepts_a_list_and_ignores_junk(self):
        got = self.resolved({}, {"fits_roots": ["/a", " /b ", "", 7]})
        self.assertEqual(got["fits_roots"]["value"], ["/a", "/b"])
        got = self.resolved({}, {"fits_roots": "not a list"})
        self.assertEqual(got["fits_roots"]["source"], "default")

    def test_a_bad_port_falls_through_instead_of_crashing(self):
        got = self.resolved({"PORT": "banana"}, {"port": 6001})
        self.assertEqual(got["port"]["value"], 6001)
        got = self.resolved({"PORT": "99999"}, {})
        self.assertEqual(got["port"]["value"], 5555)

    def test_s9_host_is_not_a_settings_key(self):
        got = self.resolved({"HOST": "0.0.0.0"}, {"host": "0.0.0.0"})
        self.assertNotIn("host", got)
        self.assertEqual(set(got), {"fits_roots", "manifest_path", "port", "scan_cron"})


class TestFileHandling(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-settings-file-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "cfg" / "settings.json"

    def test_s4_write_keeps_unknown_keys_and_stamps_a_version(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"future_key": {"a": 1}, "fits_roots": ["/old"]}),
                             encoding="utf-8")
        acp_settings.save(self.path, {"fits_roots": ["/new"]})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["fits_roots"], ["/new"])
        self.assertEqual(data["future_key"], {"a": 1})
        self.assertEqual(data["version"], 1)

    def test_s4_write_is_atomic(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"fits_roots": ["/old"]}), encoding="utf-8")
        with mock.patch("os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                acp_settings.save(self.path, {"fits_roots": ["/new"]})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["fits_roots"],
                         ["/old"])
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["settings.json"])

    def test_s4_missing_file_is_defaults_with_no_error(self):
        self.assertEqual(acp_settings.load_file(self.path), ({}, None))
        self.assertFalse(self.path.parent.exists())

    def test_s4_corrupt_file_gives_defaults_an_error_and_one_backup(self):
        self.path.parent.mkdir()
        self.path.write_text("{ not json", encoding="utf-8")
        data, error = acp_settings.load_file(self.path)
        self.assertEqual(data, {})
        self.assertEqual(error, "corrupt")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ not json")
        bak = self.path.with_name("settings.json.bak")
        self.assertEqual(bak.read_text(encoding="utf-8"), "{ not json")

        self.path.write_text("[1, 2", encoding="utf-8")
        data, error = acp_settings.load_file(self.path)
        self.assertEqual(error, "corrupt")
        self.assertEqual(bak.read_text(encoding="utf-8"), "{ not json")

    def test_s4_a_json_value_that_is_not_an_object_counts_as_corrupt(self):
        self.path.parent.mkdir()
        self.path.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(acp_settings.load_file(self.path)[1], "corrupt")

    def test_s15_shortcuts_round_trips_null_true_false(self):
        for value in (None, True, False):
            acp_settings.save(self.path, {"shortcuts": value})
            data, error = acp_settings.load_file(self.path)
            self.assertIsNone(error)
            self.assertIn("shortcuts", data)
            self.assertIs(data["shortcuts"], value)
        self.assertIsNone(acp_settings.shortcuts_value({}))
        self.assertIsNone(acp_settings.shortcuts_value({"shortcuts": "yes"}))
        self.assertIs(acp_settings.shortcuts_value({"shortcuts": False}), False)


class TestConfigFolder(unittest.TestCase):

    def home(self):
        return Path("/home/pat")

    def test_s5_windows(self):
        env = {"APPDATA": r"C:\Users\Pat\AppData\Roaming"}
        got = acp_settings.config_dir("win32", env, lambda: PureWindowsPath(r"C:\Users\Pat"))
        self.assertEqual(str(got), r"C:\Users\Pat\AppData\Roaming\ACP")

    def test_s5_mac(self):
        got = acp_settings.config_dir("darwin", {}, self.home)
        self.assertEqual(got, Path("/home/pat/Library/Application Support/ACP"))

    def test_s5_linux_with_and_without_xdg(self):
        self.assertEqual(acp_settings.config_dir("linux", {}, self.home),
                         Path("/home/pat/.config/acp"))
        self.assertEqual(acp_settings.config_dir("linux", {"XDG_CONFIG_HOME": "/x/cfg"}, self.home),
                         Path("/x/cfg/acp"))
        self.assertEqual(acp_settings.config_dir("linux", {"XDG_CONFIG_HOME": ""}, self.home),
                         Path("/home/pat/.config/acp"))

    def test_s5_acp_home_replaces_the_folder_on_every_os(self):
        for plat in ("win32", "darwin", "linux"):
            got = acp_settings.config_dir(plat, {"ACP_HOME": "/tmp/acp-home"}, self.home)
            self.assertEqual(got, Path("/tmp/acp-home"), plat)

    def test_s5_settings_file_sits_in_the_folder(self):
        got = acp_settings.settings_file("linux", {"ACP_HOME": "/h"}, self.home)
        self.assertEqual(got, Path("/h/settings.json"))

    def test_s12_no_home_folder_means_no_settings_file_and_no_raise(self):
        def no_home():
            raise RuntimeError("Could not determine home directory.")
        for plat in ("win32", "darwin", "linux"):
            self.assertIsNone(acp_settings.config_dir(plat, {}, no_home), plat)
            self.assertIsNone(acp_settings.settings_file(plat, {}, no_home), plat)
        self.assertEqual(acp_settings.config_dir("linux", {"ACP_HOME": "/h"}, no_home),
                         Path("/h"))

    def test_s12_a_missing_appdata_on_windows_falls_back_to_the_home_folder(self):
        got = acp_settings.config_dir("win32", {}, lambda: PureWindowsPath(r"C:\Users\Pat"))
        self.assertEqual(str(got), r"C:\Users\Pat\AppData\Roaming\ACP")


class TestDataPath(unittest.TestCase):

    def test_default_is_the_repo_data_folder(self):
        self.assertEqual(acp_settings.data_path("gear.json", env={}),
                         REPO / "data" / "gear.json")

    def test_acp_data_dir_moves_it_and_empty_counts_as_unset(self):
        self.assertEqual(acp_settings.data_path("gear.json", env={"ACP_DATA_DIR": "/elsewhere"}),
                         Path("/elsewhere/gear.json"))
        self.assertEqual(acp_settings.data_path("gear.json", env={"ACP_DATA_DIR": ""}),
                         REPO / "data" / "gear.json")


# --------------------------------------------------------------------------
# Import-time paths, in a subprocess
# --------------------------------------------------------------------------

PATHS_SNIPPET = """
import json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
import app, build_archive_manifest as b, fetch_catalogs as fc, target_ids as t
import activity
out = {
 "app": {n: str(getattr(app, n)) for n in (
   "MANIFEST_PATH", "CATALOGS_PATH", "GEAR_PATH", "PLANS_PATH", "FINGERPRINTS_PATH",
   "LIVE_OUT_DIR", "SITES_PATH", "DESTINATIONS_PATH", "SAVED_SEARCHES_PATH",
   "TARGET_OVERRIDES_PATH", "HIDDEN_PATH", "TS_TEMPLATES_PATH", "ZIP_OUTPUT_DIR",
   "SURVEYS_PATH", "CATALOG_REGISTRY_PATH", "MOC_CACHE_DIR")},
 "activity_db": str(activity.db_path()),
 "balancer": str(app._balancer_path()),
 "builder": {"MANIFEST_PATH": str(b.MANIFEST_PATH), "SCAN_CACHE_PATH": str(b.SCAN_CACHE_PATH),
             "TARGET_IDS_PATH": str(b.TARGET_IDS_PATH),
             "FILTER_CATALOGUE_PATH": str(b.FILTER_CATALOGUE_PATH)},
 "fetch_catalogs": str(fc.CACHE_PATH),
 "stores": [str(s.path()) for s in t.TARGET_STORES],
 "port": app.effective_port(),
}
print(json.dumps(out))
"""

APP_FILES = {
    "MANIFEST_PATH": "manifest.json", "CATALOGS_PATH": "catalogs.json", "GEAR_PATH": "gear.json",
    "PLANS_PATH": "plans.json", "FINGERPRINTS_PATH": "fingerprints.json",
    "LIVE_OUT_DIR": "live", "SITES_PATH": "sites.json", "DESTINATIONS_PATH": "destinations.json",
    "SAVED_SEARCHES_PATH": "saved_searches.json", "TARGET_OVERRIDES_PATH": "target_overrides.json",
    "HIDDEN_PATH": "hidden.json", "TS_TEMPLATES_PATH": "ts_templates.json",
    "ZIP_OUTPUT_DIR": "exports", "MOC_CACHE_DIR": "moc_cache",
}
TRACKED = {"SURVEYS_PATH": "surveys.json", "CATALOG_REGISTRY_PATH": "catalog_registry.json"}


class TestImportTimePaths(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acp-paths-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def rel(self, value: str) -> str:
        return Path(value).relative_to(REPO).as_posix()

    def test_s6_with_no_file_and_no_new_variables_every_path_is_todays_value(self):
        out = json.loads(run_python(PATHS_SNIPPET, {"ACP_HOME": str(self.tmp / "empty")}))
        self.assertEqual({k: self.rel(v) for k, v in out["app"].items()}, {
            "MANIFEST_PATH": "data/manifest.json",
            "CATALOGS_PATH": "data/catalogs.json",
            "GEAR_PATH": "data/gear.json",
            "PLANS_PATH": "data/plans.json",
            "FINGERPRINTS_PATH": "data/fingerprints.json",
            "LIVE_OUT_DIR": "data/live",
            "SITES_PATH": "data/sites.json",
            "DESTINATIONS_PATH": "data/destinations.json",
            "SAVED_SEARCHES_PATH": "data/saved_searches.json",
            "TARGET_OVERRIDES_PATH": "data/target_overrides.json",
            "HIDDEN_PATH": "data/hidden.json",
            "TS_TEMPLATES_PATH": "data/ts_templates.json",
            "ZIP_OUTPUT_DIR": "data/exports",
            "SURVEYS_PATH": "data/surveys.json",
            "CATALOG_REGISTRY_PATH": "data/catalog_registry.json",
            "MOC_CACHE_DIR": "data/moc_cache",
        })
        self.assertEqual(self.rel(out["activity_db"]), "data/activity.sqlite")
        self.assertEqual(self.rel(out["balancer"]), "data/balancer.json")
        self.assertEqual({k: self.rel(v) for k, v in out["builder"].items()}, {
            "MANIFEST_PATH": "data/manifest.json",
            "SCAN_CACHE_PATH": "data/scan_cache.json",
            "TARGET_IDS_PATH": "data/target_ids.json",
            "FILTER_CATALOGUE_PATH": "data/filter_catalogue.json",
        })
        self.assertEqual(self.rel(out["fetch_catalogs"]), "data/catalogs.json")
        self.assertEqual([self.rel(p) for p in out["stores"]],
                         ["data/target_overrides.json", "data/hidden.json"])
        self.assertEqual(out["port"], 5555)
        self.assertFalse((self.tmp / "empty").exists(), "reading settings must not create a folder")

    def test_s10_acp_data_dir_moves_every_user_written_file(self):
        data_dir = self.tmp / "userdata"
        out = json.loads(run_python(PATHS_SNIPPET, {"ACP_HOME": str(self.tmp / "h"),
                                                    "ACP_DATA_DIR": str(data_dir)}))
        for name, filename in APP_FILES.items():
            self.assertEqual(Path(out["app"][name]), data_dir / filename, name)
        self.assertEqual(Path(out["activity_db"]), data_dir / "activity.sqlite")
        self.assertEqual(Path(out["balancer"]), data_dir / "balancer.json")
        self.assertEqual(Path(out["builder"]["MANIFEST_PATH"]), data_dir / "manifest.json")
        self.assertEqual(Path(out["builder"]["SCAN_CACHE_PATH"]), data_dir / "scan_cache.json")
        self.assertEqual(Path(out["builder"]["TARGET_IDS_PATH"]), data_dir / "target_ids.json")
        self.assertEqual(Path(out["fetch_catalogs"]), data_dir / "catalogs.json")
        self.assertEqual([Path(p) for p in out["stores"]],
                         [data_dir / "target_overrides.json", data_dir / "hidden.json"])
        for name, filename in TRACKED.items():
            self.assertEqual(Path(out["app"][name]), REPO / "data" / filename, name)
        self.assertEqual(Path(out["builder"]["FILTER_CATALOGUE_PATH"]),
                         REPO / "data" / "filter_catalogue.json")

    def test_manifest_path_and_port_in_the_file_reach_the_app_at_import(self):
        home = self.tmp / "h"
        home.mkdir()
        (home / "settings.json").write_text(
            json.dumps({"manifest_path": str(self.tmp / "m.json"), "port": 6123}), encoding="utf-8")
        out = json.loads(run_python(PATHS_SNIPPET, {"ACP_HOME": str(home)}))
        self.assertEqual(out["app"]["MANIFEST_PATH"], str(self.tmp / "m.json"))
        self.assertEqual(out["builder"]["MANIFEST_PATH"], str(self.tmp / "m.json"))
        self.assertEqual(out["port"], 6123)

    def test_s13_a_hand_run_builder_finds_roots_in_the_settings_file(self):
        home = self.tmp / "h"
        home.mkdir()
        (home / "settings.json").write_text(
            json.dumps({"fits_roots": ["/from/file", "/second"]}), encoding="utf-8")
        code = (
            "import importlib.util, json, sys\n"
            f"spec = importlib.util.spec_from_file_location('b', {str(REPO / 'scripts' / 'build_archive_manifest.py')!r})\n"
            "b = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(b)\n"
            "print(json.dumps([str(p) for p in b.NAS_ROOTS]))\n")
        out = run_python(code, {"ACP_HOME": str(home)}, cwd=self.tmp)
        self.assertEqual([Path(p) for p in json.loads(out)],
                         [Path("/from/file"), Path("/second")])
        out = run_python(code, {"ACP_HOME": str(home), "FITS_ROOTS": "/env/one"}, cwd=self.tmp)
        self.assertEqual([Path(p) for p in json.loads(out)], [Path("/env/one")])
        out = run_python(code, {"ACP_HOME": str(self.tmp / "none")}, cwd=self.tmp)
        self.assertEqual([Path(p) for p in json.loads(out)], [Path("Z:/Astro/Images")])


class TestBindAddress(unittest.TestCase):

    def test_acp_bind_host_then_host_then_loopback(self):
        self.assertEqual(app_module.effective_bind_host({}), "127.0.0.1")
        self.assertEqual(app_module.effective_bind_host({"HOST": "0.0.0.0"}), "0.0.0.0")
        self.assertEqual(app_module.effective_bind_host(
            {"HOST": "0.0.0.0", "ACP_BIND_HOST": "127.0.0.1"}), "127.0.0.1")
        self.assertEqual(app_module.effective_bind_host({"HOST": ""}), "127.0.0.1")

    def test_s9_host_in_the_settings_file_has_no_effect(self):
        tmp = Path(tempfile.mkdtemp(prefix="acp-host-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "settings.json").write_text(json.dumps({"host": "0.0.0.0"}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"ACP_HOME": str(tmp)}):
            os.environ.pop("HOST", None)
            os.environ.pop("ACP_BIND_HOST", None)
            self.assertEqual(app_module.effective_bind_host(), "127.0.0.1")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

class TestGetSettings(EnvCase):

    def test_s11_source_is_env_file_or_default_for_the_same_key(self):
        body = self.client.get("/api/settings").get_json()
        self.assertEqual(body["keys"]["fits_roots"]["source"], "default")
        self.assertFalse(body["keys"]["fits_roots"]["locked"])

        self.write_settings({"fits_roots": [self.folder()]})
        body = self.client.get("/api/settings").get_json()
        self.assertEqual(body["keys"]["fits_roots"]["source"], "file")
        self.assertEqual(body["keys"]["fits_roots"]["value"], [self.folder()])

        os.environ["FITS_ROOTS"] = "/from/env"
        body = self.client.get("/api/settings").get_json()
        self.assertEqual(body["keys"]["fits_roots"]["source"], "env")
        self.assertTrue(body["keys"]["fits_roots"]["locked"])
        self.assertEqual(body["keys"]["fits_roots"]["value"], ["/from/env"])

    def test_shape_for_an_authorised_client(self):
        body = self.client.get("/api/settings").get_json()
        self.assertEqual(body["version"], app_module.VERSION)
        self.assertIs(body["writable"], True)
        self.assertIs(body["first_run"], True)
        self.assertIsNone(body["error"])
        self.assertEqual(body["keys"]["port"]["value"], 5555)
        self.assertEqual(body["keys"]["manifest_path"]["value"], str(app_module.MANIFEST_PATH))
        self.assertIn("data_dir", body)

    def test_s15_shortcuts_object_goes_to_every_client(self):
        body = self.client.get("/api/settings").get_json()
        self.assertEqual(body["shortcuts"], {"available": False, "value": None})
        self.write_settings({"shortcuts": False})
        remote = self.client.get("/api/settings", environ_base={"REMOTE_ADDR": "10.0.0.5"})
        self.assertEqual(remote.get_json()["shortcuts"], {"available": False, "value": False})

    def test_corrupt_file_is_reported_with_a_fixed_code(self):
        self.write_settings("{ nope")
        body = self.client.get("/api/settings").get_json()
        self.assertEqual(body["error"], "settings_file_corrupt")
        self.assertNotIn("nope", json.dumps(body))

    def test_first_run_is_false_with_roots_or_a_manifest(self):
        self.write_settings({"fits_roots": [self.folder()]})
        self.assertIs(self.client.get("/api/settings").get_json()["first_run"], False)
        self.settings_file.unlink()
        os.environ["FITS_ROOTS"] = "/x"
        self.assertIs(self.client.get("/api/settings").get_json()["first_run"], False)
        del os.environ["FITS_ROOTS"]
        manifest = self.tmp / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        app_module.MANIFEST_PATH = manifest
        self.assertIs(self.client.get("/api/settings").get_json()["first_run"], False)

    def test_no_home_folder_means_writable_false(self):
        os.environ.pop("ACP_HOME")
        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("no home")):
            body = self.client.get("/api/settings").get_json()
        self.assertIs(body["writable"], False)

    def test_an_unwritable_config_folder_means_writable_false(self):
        locked = self.tmp / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        os.environ["ACP_HOME"] = str(locked / "acp")
        if os.access(locked, os.W_OK):
            self.skipTest("running as a user that ignores directory modes")
        self.assertIs(self.client.get("/api/settings").get_json()["writable"], False)


class TestPutSettings(EnvCase):

    def test_saves_fits_roots_and_reports_them(self):
        folder = self.folder()
        resp = self.put({"fits_roots": [folder]})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.read_settings()["fits_roots"], [folder])
        self.assertEqual(resp.get_json()["keys"]["fits_roots"]["value"], [folder])

    def test_s7_a_key_set_by_the_environment_is_locked(self):
        os.environ["FITS_ROOTS"] = "/from/env"
        resp = self.put({"fits_roots": [self.folder()]})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "locked_by_env")
        self.assertFalse(self.settings_file.exists())

    def test_s8_fits_roots_cannot_change_during_a_scan(self):
        with app_module.SCAN_STATE_LOCK:
            app_module._scan_state["running"] = True
        resp = self.put({"fits_roots": [self.folder()]})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "scan_running")
        self.assertFalse(self.settings_file.exists())

    def test_s9_host_and_every_other_key_return_400(self):
        for key in ("host", "port", "manifest_path", "scan_cron", "version"):
            resp = self.put({key: "0.0.0.0" if key == "host" else 1})
            self.assertEqual(resp.status_code, 400, key)
            self.assertEqual(resp.get_json()["error"], "unknown_key", key)
        self.assertFalse(self.settings_file.exists())

    def test_syntactic_path_checks_refuse_the_whole_write(self):
        cases = {
            "not_absolute": ["relative/path"],
            "invalid": ["/ok\x00bad"],
            "duplicate": [self.folder(), self.folder()],
        }
        for code, roots in cases.items():
            resp = self.put({"fits_roots": roots})
            self.assertEqual(resp.status_code, 400, code)
            self.assertEqual(resp.get_json()["error"], code)
        resp = self.put({"fits_roots": [f"/folder{i}" for i in range(33)]})
        self.assertEqual(resp.get_json()["error"], "too_many")
        resp = self.put({"fits_roots": ["/" + "a" * 1000]})
        self.assertEqual(resp.get_json()["error"], "invalid")
        self.assertFalse(self.settings_file.exists())

    def test_duplicate_is_found_after_normalising_and_the_path_is_stored_as_typed(self):
        a = self.folder()
        resp = self.put({"fits_roots": [a, a + "/"]})
        self.assertEqual(resp.get_json()["error"], "duplicate")
        resp = self.put({"fits_roots": [a + "/"]})
        self.assertEqual(self.read_settings()["fits_roots"], [a + "/"])

    def test_quotes_and_whitespace_are_stripped(self):
        a = self.folder()
        self.put({"fits_roots": [f'  "{a}"  ']})
        self.assertEqual(self.read_settings()["fits_roots"], [a])

    def test_bad_body_shapes_return_400(self):
        for body in ([], "x", {}, {"fits_roots": "a string"}, {"fits_roots": [1]},
                     {"shortcuts": "yes"}):
            resp = self.put(body)
            self.assertEqual(resp.status_code, 400, body)

    def test_s15_shortcuts_is_refused_where_unavailable(self):
        resp = self.put({"shortcuts": True})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "unavailable")
        self.assertFalse(self.settings_file.exists())

    def test_s15_shortcuts_round_trips_through_the_route_when_available(self):
        with mock.patch.object(app_module, "_shortcuts_available", lambda: True):
            for value in (True, False, None):
                resp = self.put({"shortcuts": value})
                self.assertEqual(resp.status_code, 200)
                got = self.client.get("/api/settings").get_json()["shortcuts"]
                self.assertEqual(got, {"available": True, "value": value})

    def test_a_corrupt_file_is_replaced_after_one_backup(self):
        self.write_settings("{ broken")
        resp = self.put({"fits_roots": [self.folder()]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.read_settings()["fits_roots"], [self.folder()])
        self.assertEqual((self.settings_file.parent / "settings.json.bak").read_text(), "{ broken")

    def test_a_read_only_install_refuses_writes(self):
        os.environ.pop("ACP_HOME")
        with mock.patch("pathlib.Path.home", side_effect=RuntimeError("no home")):
            resp = self.put({"fits_roots": [self.folder()]})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "not_writable")

    def test_unknown_keys_in_the_file_survive_a_put(self):
        self.write_settings({"future": 1})
        self.put({"fits_roots": [self.folder()]})
        self.assertEqual(self.read_settings()["future"], 1)


class TestAdoptEnv(EnvCase):

    def adopt(self, **kwargs):
        return self.client.post("/api/settings/adopt-env", json={}, **kwargs)

    def test_s14_copies_environment_roots_into_the_file_and_stays_shadowed(self):
        a, b = self.folder("a"), self.folder("b")
        os.environ["FITS_ROOTS"] = f"{a};{b}"
        resp = self.adopt()
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(self.read_settings()["fits_roots"], [a, b])
        keys = self.client.get("/api/settings").get_json()["keys"]
        self.assertEqual(keys["fits_roots"]["source"], "env")
        del os.environ["FITS_ROOTS"]
        keys = self.client.get("/api/settings").get_json()["keys"]
        self.assertEqual(keys["fits_roots"]["source"], "file")
        self.assertEqual(keys["fits_roots"]["value"], [a, b])

    def test_with_no_environment_roots_there_is_nothing_to_adopt(self):
        resp = self.adopt()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "nothing_to_adopt")
        self.assertFalse(self.settings_file.exists())

    def test_refused_during_a_scan(self):
        os.environ["FITS_ROOTS"] = "/x"
        with app_module.SCAN_STATE_LOCK:
            app_module._scan_state["running"] = True
        self.assertEqual(self.adopt().status_code, 409)
        self.assertFalse(self.settings_file.exists())


# --------------------------------------------------------------------------
# Who may call the restricted routes (spec section 7, C7 to C9)
# --------------------------------------------------------------------------

REMOTE = {"REMOTE_ADDR": "203.0.113.9"}


class TestRestrictedRoutes(EnvCase):

    def calls(self):
        yield "PUT", "/api/settings", {"fits_roots": []}
        yield "POST", "/api/settings/adopt-env", {}

    def send(self, method, path, body, **kwargs):
        return self.client.open(path, method=method, json=body, **kwargs)

    def test_c8_without_a_token_only_a_loopback_client_gets_in(self):
        os.environ["FITS_ROOTS"] = "/x"
        for method, path, body in self.calls():
            for environ in (REMOTE, {"REMOTE_ADDR": "192.168.1.20"}):
                resp = self.send(method, path, body, environ_base=environ)
                self.assertEqual(resp.status_code, 403, (path, environ))
            resp = self.send(method, path, body, headers={"X-Forwarded-For": "203.0.113.9"})
            self.assertEqual(resp.status_code, 403, path)
        self.assertFalse(self.settings_file.exists())

    def test_c8_loopback_in_its_three_spellings_is_accepted(self):
        for addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            resp = self.put({"fits_roots": []}, environ_base={"REMOTE_ADDR": addr})
            self.assertEqual(resp.status_code, 200, addr)

    def test_c8_a_remote_client_sees_no_paths_but_still_gets_the_page_state(self):
        self.write_settings({"fits_roots": [self.folder()]})
        resp = self.client.get("/api/settings", environ_base=REMOTE)
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        self.assertNotIn(str(self.tmp), text)
        body = resp.get_json()
        self.assertNotIn("value", body["keys"]["fits_roots"])
        self.assertNotIn("value", body["keys"]["manifest_path"])
        self.assertNotIn("data_dir", body)
        self.assertEqual(body["keys"]["fits_roots"]["source"], "file")
        for key in ("version", "first_run", "writable", "shortcuts"):
            self.assertIn(key, body)
        forwarded = self.client.get("/api/settings", headers={"X-Forwarded-For": "1.2.3.4"})
        self.assertNotIn(str(self.tmp), forwarded.get_data(as_text=True))

    def test_c7_a_non_json_content_type_returns_415(self):
        for method, path, _ in self.calls():
            resp = self.client.open(path, method=method, data="fits_roots=1",
                                    content_type="application/x-www-form-urlencoded")
            self.assertEqual(resp.status_code, 415, path)
            resp = self.client.open(path, method=method, data="{}", content_type="text/plain")
            self.assertEqual(resp.status_code, 415, path)

    def test_c7_a_mismatched_origin_returns_403(self):
        for method, path, body in self.calls():
            resp = self.send(method, path, body, headers={"Origin": "http://evil.example"})
            self.assertEqual(resp.status_code, 403, path)
            resp = self.send(method, path, body, headers={"Origin": "http://localhost"})
            self.assertIn(resp.status_code, (200, 400), path)

    def test_c7_a_non_loopback_host_header_returns_400(self):
        for method, path, body in self.calls():
            resp = self.send(method, path, body, headers={"Host": "evil.example:5555"})
            self.assertEqual(resp.status_code, 400, path)
            self.assertEqual(resp.get_json()["error"], "bad_host", path)
            for host in ("localhost:5555", "127.0.0.1", "[::1]:5555", "[::ffff:127.0.0.1]:5555"):
                resp = self.send(method, path, body, headers={"Host": host})
                self.assertNotEqual((resp.get_json() or {}).get("error"), "bad_host", (path, host))

    def test_c9_with_a_token_the_bearer_header_or_cookie_is_authorised(self):
        os.environ["ACP_API_TOKEN"] = "t" * 24
        resp = self.put({"fits_roots": []})
        self.assertEqual(resp.status_code, 401)
        resp = self.put({"fits_roots": []}, headers={"Authorization": "Bearer " + "t" * 24},
                        environ_base=REMOTE)
        self.assertEqual(resp.status_code, 200)
        cookie = app_module._session_cookie_value("t" * 24)
        self.client.set_cookie(app_module.SESSION_COOKIE_NAME, cookie, domain="acp.example")
        site = {"base_url": "http://acp.example", "environ_base": REMOTE}
        resp = self.put({"fits_roots": []}, **site)
        self.assertEqual(resp.status_code, 200)
        body = self.client.get("/api/settings", **site).get_json()
        self.assertIn("value", body["keys"]["manifest_path"])

    def test_c9_with_a_token_a_request_with_neither_gets_401_on_get_too(self):
        os.environ["ACP_API_TOKEN"] = "t" * 24
        self.assertEqual(self.client.get("/api/settings").status_code, 401)


if __name__ == "__main__":
    unittest.main()
