"""Extension UI hooks: ``rail_tabs`` in the manifest and ``fill_opacity`` in
a tile source's metadata.

``rail_tabs`` pages are shown in an iframe in the right rail, so the host
checks each one points back into the extension's own ``/api/ext/<prefix>``
space. ``fill_opacity`` is passed to the frontend only when it is a number
from 0 to 1.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP_EXT_DIR = Path(tempfile.mkdtemp(prefix="acp_test_extdir_"))
os.environ["ACP_EXTENSIONS_DIR"] = str(_TMP_EXT_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app, _source_extension  # noqa: E402
from extensions import rail_page_ok, sanitise_rail_tabs  # noqa: E402


def _tab(**over):
    tab = {"id": "nights", "label": "Nights", "icon": "calendar",
           "page": "/api/ext/nights/ui"}
    tab.update(over)
    return tab


class TestRailPageOk(unittest.TestCase):
    def test_path_under_the_extension_prefix(self):
        self.assertTrue(rail_page_ok("nights", "/api/ext/nights/ui"))
        self.assertTrue(rail_page_ok("nights", "/api/ext/nights"))
        self.assertTrue(rail_page_ok("nights", "/api/ext/nights/ui?tab=2"))

    def test_underscore_id_may_use_hyphenated_routes(self):
        self.assertTrue(rail_page_ok("my_ext", "/api/ext/my-ext/ui"))
        self.assertTrue(rail_page_ok("my_ext", "/api/ext/my_ext/ui"))

    def test_another_extensions_prefix_is_refused(self):
        self.assertFalse(rail_page_ok("nights", "/api/ext/other/ui"))

    def test_prefix_that_only_shares_leading_letters_is_refused(self):
        self.assertFalse(rail_page_ok("nights", "/api/ext/nights-evil/ui"))

    def test_other_origins_and_schemes_are_refused(self):
        for page in ("https://example.com/api/ext/nights/ui",
                     "//example.com/api/ext/nights/ui",
                     "javascript:alert(1)",
                     "api/ext/nights/ui",
                     "/static/app.js",
                     "/"):
            self.assertFalse(rail_page_ok("nights", page), page)

    def test_dot_segments_and_encoded_tricks_are_refused(self):
        for page in ("/api/ext/nights/../other/ui",
                     "/api/ext/nights/%2e%2e/other",
                     "/api/ext/nights/%2E%2E/other",
                     "/api/ext/nights\\..\\x",
                     "/api/ext/nights/ui\n"):
            self.assertFalse(rail_page_ok("nights", page), page)

    def test_non_strings_and_bad_extension_ids_are_refused(self):
        self.assertFalse(rail_page_ok("nights", None))
        self.assertFalse(rail_page_ok("nights", 5))
        self.assertFalse(rail_page_ok("", "/api/ext//ui"))
        self.assertFalse(rail_page_ok("a/b", "/api/ext/a/b/ui"))
        self.assertFalse(rail_page_ok(None, "/api/ext/nights/ui"))


class TestSanitiseRailTabs(unittest.TestCase):
    def test_entry_without_rail_tabs_is_untouched(self):
        entry = {"extension": "nights", "actions": []}
        self.assertIs(sanitise_rail_tabs(entry), entry)

    def test_valid_tab_is_kept_with_its_fields(self):
        out = sanitise_rail_tabs({"extension": "nights", "rail_tabs": [_tab()]})
        self.assertEqual(out["rail_tabs"], [_tab()])

    def test_missing_icon_becomes_empty_string(self):
        t = _tab()
        del t["icon"]
        out = sanitise_rail_tabs({"extension": "nights", "rail_tabs": [t]})
        self.assertEqual(out["rail_tabs"][0]["icon"], "")

    def test_bad_tabs_are_dropped_and_good_ones_kept(self):
        tabs = [
            _tab(id="ok"),
            _tab(id="offsite", page="https://example.com/x"),
            _tab(id="wrongext", page="/api/ext/other/ui"),
            _tab(id="nolabel", label=""),
            _tab(id="bad id!"),
            "not a dict",
        ]
        with self.assertLogs("acp.extensions", level=logging.WARNING) as cm:
            out = sanitise_rail_tabs({"extension": "nights", "rail_tabs": tabs})
        self.assertEqual([t["id"] for t in out["rail_tabs"]], ["ok"])
        self.assertGreaterEqual(len(cm.records), 4)

    def test_duplicate_ids_keep_the_first(self):
        out = sanitise_rail_tabs({"extension": "nights", "rail_tabs": [
            _tab(label="First"), _tab(label="Second")]})
        self.assertEqual([t["label"] for t in out["rail_tabs"]], ["First"])

    def test_rail_tabs_that_is_not_a_list_becomes_empty(self):
        out = sanitise_rail_tabs({"extension": "nights", "rail_tabs": "nope"})
        self.assertEqual(out["rail_tabs"], [])

    def test_other_keys_pass_through_and_input_is_not_mutated(self):
        entry = {"extension": "nights", "name": "Nights", "actions": [{"id": "a"}],
                 "rail_tabs": [_tab(page="/elsewhere")]}
        out = sanitise_rail_tabs(entry)
        self.assertEqual(out["name"], "Nights")
        self.assertEqual(out["actions"], [{"id": "a"}])
        self.assertEqual(len(entry["rail_tabs"]), 1)


class TestManifestEndpointRailTabs(unittest.TestCase):
    def setUp(self):
        self._saved = list(app.extensions_manifest)
        app.extensions_manifest.clear()
        self.client = app.test_client()

    def tearDown(self):
        app.extensions_manifest.clear()
        app.extensions_manifest.extend(self._saved)

    def test_endpoint_drops_invalid_tabs(self):
        app.extensions_manifest.append({
            "extension": "nights",
            "actions": [],
            "rail_tabs": [_tab(), _tab(id="bad", page="https://example.com/")],
        })
        body = self.client.get("/api/extensions/manifest").get_json()
        self.assertEqual([t["id"] for t in body[0]["rail_tabs"]], ["nights"])

    def test_endpoint_leaves_entries_without_rail_tabs_alone(self):
        entry = {"extension": "plain", "actions": [{"id": "x", "kind": "button"}]}
        app.extensions_manifest.append(entry)
        body = self.client.get("/api/extensions/manifest").get_json()
        self.assertEqual(body, [entry])


class _FakeTiles:
    def __init__(self, sid, **meta):
        self._sid = sid
        self._meta = meta

    def id(self):
        return self._sid

    def metadata(self):
        return {"label": self._sid, "color": "#abc", "kind": "inventory",
                "attribution": "", "enabled_default": False, **self._meta}

    def tiles(self):
        return []


class TestFillOpacity(unittest.TestCase):
    def setUp(self):
        self._saved = list(getattr(app, "tile_sources", []))
        app.tile_sources.clear()
        self.client = app.test_client()

    def tearDown(self):
        app.tile_sources.clear()
        app.tile_sources.extend(self._saved)

    def _opacity(self, **meta):
        app.tile_sources[:] = [_FakeTiles("s", **meta)]
        src = self.client.get("/api/tile-sources").get_json()["sources"][0]
        return src["fill_opacity"]

    def test_unset_is_null(self):
        self.assertIsNone(self._opacity())

    def test_number_in_range_passes_through(self):
        self.assertEqual(self._opacity(fill_opacity=0.25), 0.25)
        self.assertEqual(self._opacity(fill_opacity=0), 0.0)
        self.assertEqual(self._opacity(fill_opacity=1), 1.0)

    def test_out_of_range_or_wrong_type_is_null(self):
        for bad in (-0.1, 1.5, "0.5", True, None, float("nan")):
            self.assertIsNone(self._opacity(fill_opacity=bad), repr(bad))


class TestTilePanel(unittest.TestCase):
    def setUp(self):
        self._saved = list(getattr(app, "tile_sources", []))
        app.tile_sources.clear()
        self.client = app.test_client()

    def tearDown(self):
        app.tile_sources.clear()
        app.tile_sources.extend(self._saved)

    def _panel(self, **meta):
        app.tile_sources[:] = [_FakeTiles("s", **meta)]
        src = self.client.get("/api/tile-sources").get_json()["sources"][0]
        return src["tile_panel"]

    def test_unset_is_null(self):
        self.assertIsNone(self._panel())

    def test_replace_and_append_pass_through(self):
        self.assertEqual(self._panel(tile_panel="replace"), "replace")
        self.assertEqual(self._panel(tile_panel="append"), "append")

    def test_anything_else_is_null(self):
        for bad in ("Replace", "none", "", True, 1, None, ["replace"]):
            self.assertIsNone(self._panel(tile_panel=bad), repr(bad))


class _Boom:
    """A tile source whose chosen method raises."""

    def __init__(self, sid, fail, status=None, tiles=()):
        self._sid, self._fail, self._status, self._tiles = sid, fail, status, list(tiles)

    def id(self):
        if self._fail == "id":
            raise RuntimeError("no id")
        return self._sid

    def metadata(self):
        if self._fail == "metadata":
            raise ValueError("bad meta")
        meta = {"label": self._sid, "color": "#abc", "kind": "inventory",
                "attribution": "", "enabled_default": False}
        if self._status:
            meta["status"] = self._status
        return meta

    def tiles(self):
        if self._fail == "tiles":
            raise TypeError("'NoneType' object is not subscriptable")
        return self._tiles


class TestSourceStatus(unittest.TestCase):
    def setUp(self):
        self._saved = list(getattr(app, "tile_sources", []))
        app.tile_sources.clear()
        self.client = app.test_client()

    def tearDown(self):
        app.tile_sources.clear()
        app.tile_sources.extend(self._saved)

    def _sources(self, *srcs):
        app.tile_sources[:] = list(srcs)
        return {s["id"]: s for s in self.client.get("/api/tile-sources").get_json()["sources"]}

    def test_tiles_raising_still_lists_the_source_with_the_message(self):
        got = self._sources(_Boom("a", "tiles"))
        self.assertEqual(got["a"]["status"], "error")
        self.assertIn("NoneType", got["a"]["error"])

    def test_metadata_raising_still_lists_the_source(self):
        got = self._sources(_Boom("b", "metadata"))
        self.assertEqual(got["b"]["status"], "error")
        self.assertIn("bad meta", got["b"]["error"])

    def test_id_raising_lists_it_under_the_class_name(self):
        got = self._sources(_Boom("c", "id"))
        self.assertEqual(got["_Boom"]["status"], "error")

    def test_one_broken_source_does_not_hide_the_others(self):
        got = self._sources(_Boom("a", "tiles"), _Boom("ok", None, tiles=[{"id": "t"}]))
        self.assertEqual(got["ok"]["status"], "ready")
        self.assertIsNone(got["ok"]["error"])
        self.assertEqual(got["ok"]["n_tiles"], 1)

    def test_empty_and_loading(self):
        got = self._sources(_Boom("e", None), _Boom("l", None, status="loading", tiles=[{"id": "t"}]))
        self.assertEqual(got["e"]["status"], "empty")
        self.assertEqual(got["l"]["status"], "loading")
        self.assertEqual(got["l"]["n_tiles"], 0)

    def test_only_loading_is_an_accepted_reported_status(self):
        got = self._sources(_Boom("x", None, status="error", tiles=[{"id": "t"}]))
        self.assertEqual(got["x"]["status"], "ready")

    def test_extension_is_named_by_metadata_or_module(self):
        self.assertEqual(_source_extension(_Boom("a", None), {"extension": " nights "}), "nights")
        self.assertEqual(_source_extension(_Boom("a", None), {}), "")
        cls = type("S", (), {"__module__": "acp_ext_nights.sources"})
        self.assertEqual(_source_extension(cls(), {}), "nights")


class TestRailTabWidth(unittest.TestCase):
    def test_width_kept_only_when_valid(self):
        from extensions import sanitise_rail_tabs
        base = {"id": "t", "label": "T", "page": "/api/ext/nights/ui"}
        out = sanitise_rail_tabs({"extension": "nights", "rail_tabs": [
            {**base, "width": "wide"}, {**base, "id": "u", "width": "huge"},
            {**base, "id": "v"}]})["rail_tabs"]
        self.assertEqual(out[0]["width"], "wide")
        self.assertNotIn("width", out[1])
        self.assertNotIn("width", out[2])


if __name__ == "__main__":
    unittest.main()
