"""First run and folder checks (spec docs/specs/windows-download-and-setup.md,
tests F1 to F4 and P1 to P9, and the page routes of section 2.1).

Each test is written against the spec, not the code: a folder that cannot be
listed must come back as ``not_readable`` whatever the operating system says,
and nothing in a response may repeat a path or an operating system message.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acp_settings  # noqa: E402
import app as app_module  # noqa: E402
from test_settings import EnvCase, run_python  # noqa: E402

HTML = {"Accept": "text/html,application/xhtml+xml"}


class SetupCase(EnvCase):

    def check(self, path, **kwargs):
        return self.client.post("/api/settings/check-path", json={"path": path}, **kwargs)

    def code(self, path):
        resp = self.check(path)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["code"]

    def add_image(self, folder: Path, name="frame.fits"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"SIMPLE  =                    T")


class TestFirstRun(SetupCase):

    def test_f1_no_roots_and_no_manifest_redirects_a_browser_to_setup(self):
        self.assertTrue(self.client.get("/api/settings").get_json()["first_run"])
        resp = self.client.get("/", headers=HTML)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(urlpath(resp.headers["Location"]), "/setup")

    def test_f2_setup_skip_and_non_html_requests_never_redirect(self):
        self.assertEqual(self.client.get("/?setup=skip", headers=HTML).status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/", headers={"Accept": "application/json"}).status_code, 200)
        self.assertEqual(self.client.get("/api/manifest", headers=HTML).status_code, 200)

    def test_f3_roots_from_the_environment_or_the_file_end_first_run(self):
        os.environ["FITS_ROOTS"] = "/from/env"
        self.assertFalse(self.client.get("/api/settings").get_json()["first_run"])
        self.assertEqual(self.client.get("/", headers=HTML).status_code, 200)
        del os.environ["FITS_ROOTS"]
        self.write_settings({"fits_roots": [self.folder()]})
        self.assertFalse(self.client.get("/api/settings").get_json()["first_run"])
        self.assertEqual(self.client.get("/", headers=HTML).status_code, 200)

    def test_f4_a_manifest_with_no_roots_is_not_first_run(self):
        manifest = self.tmp / "manifest.json"
        manifest.write_text(json.dumps({"targets": [], "total_targets": 0}), encoding="utf-8")
        app_module.MANIFEST_PATH = manifest
        self.assertFalse(self.client.get("/api/settings").get_json()["first_run"])
        self.assertEqual(self.client.get("/", headers=HTML).status_code, 200)

    def test_the_setup_page_serves_and_holds_no_local_path(self):
        resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(str(app_module.REPO_ROOT), text)

    def test_the_setup_page_is_behind_the_token_gate(self):
        os.environ["ACP_API_TOKEN"] = "t" * 24
        self.assertEqual(self.client.get("/setup", headers=HTML).status_code, 401)
        ok = self.client.get("/setup", headers={"Authorization": "Bearer " + "t" * 24})
        self.assertEqual(ok.status_code, 200)


class TestPathCodes(SetupCase):

    def test_p1_each_blocking_code(self):
        self.assertEqual(self.code("images/raw"), "not_absolute")
        self.assertEqual(self.code(str(self.tmp / "nowhere")), "not_found")
        a_file = self.tmp / "a-file.txt"
        a_file.write_text("x", encoding="utf-8")
        self.assertEqual(self.code(str(a_file)), "not_a_directory")
        self.assertEqual(self.code(str(self.tmp) + "\x00x"), "invalid")
        self.assertEqual(self.code(str(self.tmp) + "\x07"), "invalid")
        self.assertEqual(self.code("/" + "a" * 1001), "invalid")
        self.assertEqual(self.code(self.folder()), "ok")

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "mode 000 does not stop Windows or root")
    def test_p2_a_folder_that_cannot_be_listed_is_not_readable(self):
        locked = Path(self.folder("locked"))
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o755)
        self.assertEqual(self.code(str(locked)), "not_readable")

    def test_p3_quotes_and_spaces_are_stripped_and_tilde_is_expanded(self):
        folder = self.folder("Deep Sky")
        self.assertEqual(self.code(f'  "{folder}"  '), "ok")
        self.assertEqual(self.code(f"'{folder}'"), "ok")
        home = self.tmp / "homedir"
        (home / "astro").mkdir(parents=True)
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        self.assertEqual(self.code("~/astro"), "ok")
        saved = self.put({"fits_roots": [f'  "{folder}"  ']})
        self.assertEqual(saved.status_code, 200, saved.get_data(as_text=True))
        self.assertEqual(self.read_settings()["fits_roots"], [folder])

    def test_p4_duplicate_is_judged_after_normalising_and_stored_as_typed(self):
        folder = self.folder("dup")
        typed = folder + os.sep
        self.assertEqual(self.put({"fits_roots": [typed]}).status_code, 200)
        self.assertEqual(self.read_settings()["fits_roots"], [typed])
        self.assertEqual(self.code(folder), "duplicate")
        self.assertEqual(self.code(os.path.join(folder, "..", "dup")), "duplicate")
        other = self.folder("dup2")
        resp = self.put({"fits_roots": [other, other + os.sep + "."]})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (400, "duplicate"))

    def test_p4_too_many_at_33(self):
        folders = [self.folder(f"f{i}") for i in range(33)]
        resp = self.put({"fits_roots": folders})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (400, "too_many"))
        self.assertFalse(self.settings_file.exists())
        self.assertEqual(self.put({"fits_roots": folders[:32]}).status_code, 200)
        self.assertEqual(self.code(self.folder("one-more")), "too_many")

    def test_p5_overlaps_and_no_images_seen_are_warnings_not_blocks(self):
        outer = Path(self.folder("outer"))
        self.add_image(outer / "m31")
        self.assertEqual(self.put({"fits_roots": [str(outer)]}).status_code, 200)
        inner = outer / "m31"
        body = self.check(str(inner)).get_json()
        self.assertEqual(body["code"], "ok")
        self.assertIn("overlaps", body["warnings"])
        empty = self.folder("empty")
        body = self.check(empty).get_json()
        self.assertEqual(body["code"], "ok")
        self.assertIn("no_images_seen", body["warnings"])

    def test_p5_an_image_in_a_subfolder_counts_as_seen(self):
        root = Path(self.folder("lib"))
        self.add_image(root / "2026" / "night1" / "lights", "m42_L.XISF")
        body = self.check(str(root)).get_json()
        self.assertEqual(body, {"code": "ok", "warnings": []})

    def test_p5_the_top_of_a_drive_warns_very_large(self):
        self.assertIn("very_large", acp_settings.path_warnings("/", []))
        self.assertIn("very_large", acp_settings.path_warnings("C:\\", []))
        self.assertNotIn("very_large", acp_settings.path_warnings("C:\\Astro", []))
        self.assertNotIn("very_large", acp_settings.path_warnings("\\\\nas\\astro", []))

    def test_p5_a_unc_path_passes_the_syntax_check(self):
        cleaned, code = acp_settings.validate_roots(["\\\\nas\\astro\\raw"])
        self.assertIsNone(code)
        self.assertEqual(cleaned, ["\\\\nas\\astro\\raw"])
        # Past the syntax check it is just another path to look up.
        self.assertNotIn(self.code("\\\\nas\\astro\\raw"), ("not_absolute", "invalid"))

    def test_p6_no_response_carries_os_error_text_or_the_input_path(self):
        gone = str(self.tmp / "secret-name-9f3" / "x")
        a_file = self.tmp / "secret-file-9f3.txt"
        a_file.write_text("x", encoding="utf-8")
        bodies = [self.check(gone), self.check(str(a_file)), self.check("secret-rel-9f3"),
                  self.check("/secret-nul-9f3\x00"), self.put({"fits_roots": [gone]}),
                  self.put({"fits_roots": [str(a_file)]}), self.put({"fits_roots": ["secret-rel-9f3"]})]
        for resp in bodies:
            text = resp.get_data(as_text=True)
            self.assertNotIn("9f3", text)
            for phrase in ("Errno", "No such file", "Permission denied", "Traceback", "OSError"):
                self.assertNotIn(phrase, text)

    def test_p6_a_probe_that_raises_gives_a_code_not_the_exception(self):
        with mock.patch.object(acp_settings.os, "scandir", side_effect=OSError("secret-9f3 broke")):
            resp = self.check(self.folder())
        self.assertEqual(resp.get_json()["code"], "not_readable")
        self.assertNotIn("9f3", resp.get_data(as_text=True))

    def test_p7_a_folder_that_blocks_returns_timed_out_and_frees_the_request(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def stuck(_text):
            release.wait(10)
            return "ok"
        with mock.patch.object(acp_settings, "_probe_directory", stuck), \
                mock.patch.object(acp_settings, "READ_TIMEOUT", 0.3):
            began = time.monotonic()
            resp = self.check(self.folder())
            took = time.monotonic() - began
        self.assertEqual(resp.get_json()["code"], "timed_out")
        self.assertLess(took, 3.0)
        self.assertFalse(release.is_set(), "the probe was still running when the request returned")

    def test_p7_put_also_times_out_without_saving(self):
        release = threading.Event()
        self.addCleanup(release.set)
        with mock.patch.object(acp_settings, "_probe_directory", lambda _t: release.wait(10) and "ok"), \
                mock.patch.object(acp_settings, "READ_TIMEOUT", 0.3):
            resp = self.put({"fits_roots": [self.folder()]})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (400, "timed_out"))
        self.assertFalse(self.settings_file.exists())

    def test_p7_many_slow_folders_cost_one_wait_not_one_each(self):
        release = threading.Event()
        self.addCleanup(release.set)
        folders = [self.folder(f"slow{i}") for i in range(6)]
        with mock.patch.object(acp_settings, "_probe_directory", lambda _t: release.wait(10) and "ok"), \
                mock.patch.object(acp_settings, "READ_TIMEOUT", 0.4):
            began = time.monotonic()
            resp = self.put({"fits_roots": folders})
            took = time.monotonic() - began
        self.assertEqual(resp.get_json()["error"], "timed_out")
        self.assertLess(took, 2.0)

    def test_p8_a_bad_path_saves_nothing_and_port_is_refused(self):
        resp = self.put({"fits_roots": [self.folder(), "relative/dir"]})
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (400, "not_absolute"))
        resp = self.put({"fits_roots": [self.folder(), str(self.tmp / "missing")]})
        self.assertEqual((resp.status_code, resp.get_json()["error"], resp.get_json()["index"]),
                         (400, "not_found", 1))
        self.assertFalse(self.settings_file.exists())
        resp = self.put({"port": 6000})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self.settings_file.exists())

    def test_put_returns_the_warnings_for_a_folder_with_no_images(self):
        resp = self.put({"fits_roots": [self.folder("bare")]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["warnings"], [{"index": 0, "code": "no_images_seen"}])

    def test_removing_a_folder_does_not_walk_the_others_again(self):
        keep, drop = self.folder("keep"), self.folder("drop")
        self.assertEqual(self.put({"fits_roots": [keep, drop]}).status_code, 200)
        with mock.patch.object(acp_settings, "has_images", side_effect=AssertionError("walked")):
            resp = self.put({"fits_roots": [keep]})
        self.assertEqual(resp.status_code, 200)

    def test_check_path_needs_a_string_and_writes_nothing(self):
        for body in ({}, {"path": 5}, {"path": None}, [1]):
            resp = self.client.post("/api/settings/check-path", json=body)
            self.assertEqual(resp.status_code, 400, body)
        self.assertEqual(self.code(self.folder()), "ok")
        self.assertFalse(self.settings_file.exists())


class TestReadOnlyInstall(unittest.TestCase):

    SNIPPET = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
import app
def _no_home(cls=None):
    raise RuntimeError("no home folder for this uid")
Path.home = classmethod(_no_home)
c = app.app.test_client()
h = {"Authorization": "Bearer " + "t" * 24}
view = c.get("/api/settings", headers=h).get_json()
page = c.get("/setup", headers=h)
print(json.dumps({"writable": view["writable"], "first_run": view["first_run"],
                  "roots": view["keys"]["fits_roots"], "page": page.status_code,
                  "put": c.put("/api/settings", json={"fits_roots": []}, headers=h).status_code}))
"""

    def test_p9_a_user_with_no_home_folder_gets_the_read_only_page(self):
        out = json.loads(run_python(self.SNIPPET, {
            "ACP_API_TOKEN": "t" * 24, "FITS_ROOTS": "/mnt/nas/astro", "HOST": "0.0.0.0",
            "PORT": "5555", "MANIFEST_PATH": "/app/data/manifest.json",
            "APPDATA": ""}))  # Windows finds its settings folder from APPDATA before it needs a home
        self.assertIs(out["writable"], False)
        self.assertIs(out["first_run"], False)
        self.assertEqual(out["roots"]["source"], "env")
        self.assertTrue(out["roots"]["locked"])
        self.assertEqual(out["page"], 200)
        # The page itself switches on writable:false in its script, so the
        # HTML says nothing either way. What the install does is the PUT
        # refusal and the flag.
        self.assertEqual(out["put"], 409)


class TestAboutCard(SetupCase):
    """The "About this install" card tells a container install the truth: the
    port is the one inside the container, and the settings come from the
    compose file. The page fills the card in with script, so these tests check
    the served markup and the script's switch on ``writable``."""

    def page(self):
        resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def script(self):
        return (Path(app_module.__file__).parent / "static" / "setup.js").read_text(encoding="utf-8")

    def test_writable_install_keeps_the_settings_file_line_and_shows_it_first(self):
        html = self.page()
        self.assertIn('id="infoHintWritable">The port and manifest path are changed in the settings file, '
                      'then ACP needs a restart.</p>', html)
        self.assertIn('<dt id="infoPortLabel">Port</dt>', html)
        # Not hidden in the markup, so a writable install shows it without script help.
        self.assertNotIn('id="infoHintWritable" hidden', html)

    def test_read_only_install_has_its_own_line_about_the_compose_file(self):
        html = self.page()
        self.assertIn('id="infoHintReadOnly" hidden>', html)
        start = html.index('id="infoHintReadOnly"')
        line = html[start:html.index("</p>", start)]
        self.assertIn("compose file", line)
        self.assertIn("container needs a restart", line)
        self.assertIn("inside the container", line)
        self.assertNotIn("settings file", line)

    def test_the_script_switches_label_and_lines_on_read_only(self):
        js = self.script()
        self.assertIn('readOnly ? "Port inside the container" : "Port"', js)
        self.assertIn('show($("infoHintWritable"), !readOnly)', js)
        self.assertIn('show($("infoHintReadOnly"), readOnly)', js)
        self.assertIn("const readOnly = s.writable === false;", js)


def urlpath(location: str) -> str:
    from urllib.parse import urlparse
    return urlparse(location).path


if __name__ == "__main__":
    unittest.main()
