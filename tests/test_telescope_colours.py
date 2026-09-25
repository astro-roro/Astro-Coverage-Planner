"""Telescope colours Rohan picks are saved in gear.json and survive gear edits.

The legend used to colour telescopes by their place in a sorted list, so a new
telescope reshuffled every colour and there was no way to choose one. Picks now
live under ``telescope_colours`` in gear.json, keyed by the telescope name
shown next to the swatch, and change only through /api/gear/telescope-colour.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

ROUTE = "/api/gear/telescope-colour"


class TelescopeColourCase(unittest.TestCase):
    def setUp(self):
        self._saved = (app_module.GEAR_PATH, app_module._gear_cache, app_module._gear_cache_mtime)
        self.addCleanup(self._restore)
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        app_module.GEAR_PATH = Path(self._td.name) / "gear.json"
        app_module._gear_cache = None
        app_module._gear_cache_mtime = None
        self.client = app.test_client()

    def _restore(self):
        app_module.GEAR_PATH, app_module._gear_cache, app_module._gear_cache_mtime = self._saved

    def write_gear(self, doc):
        app_module.GEAR_PATH.write_text(json.dumps(doc), encoding="utf-8")

    def on_disk(self):
        return json.loads(app_module.GEAR_PATH.read_text(encoding="utf-8"))


class TestSaveRoute(TelescopeColourCase):
    GEAR = {"version": 2, "telescopes": [{"id": "redcat", "name": "RedCat 51"}], "cameras": []}

    def test_a_pick_is_saved_and_returned_by_get(self):
        self.write_gear(self.GEAR)
        r = self.client.post(ROUTE, json={"name": "RedCat 51", "colour": "#7CC4A0"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["telescope_colours"], {"RedCat 51": "#7cc4a0"})
        self.assertEqual(self.on_disk()["telescope_colours"], {"RedCat 51": "#7cc4a0"})
        self.assertEqual(self.on_disk()["telescopes"], self.GEAR["telescopes"],
                         "saving a colour leaves the telescopes alone")
        g = self.client.get("/api/gear").get_json()
        self.assertEqual(g["telescope_colours"], {"RedCat 51": "#7cc4a0"})

    def test_null_goes_back_to_automatic(self):
        self.write_gear(dict(self.GEAR, telescope_colours={"RedCat 51": "#7cc4a0", "Edge HD 8": "#e0a370"}))
        r = self.client.post(ROUTE, json={"name": "RedCat 51", "colour": None})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.on_disk()["telescope_colours"], {"Edge HD 8": "#e0a370"})

    def test_works_before_gear_json_exists(self):
        r = self.client.post(ROUTE, json={"name": "RedCat 51", "colour": "#6fa8dc"})
        self.assertEqual(r.status_code, 200)
        doc = self.on_disk()
        self.assertEqual(doc["version"], 2)
        self.assertEqual(doc["telescopes"], [])
        self.assertEqual(doc["telescope_colours"], {"RedCat 51": "#6fa8dc"})

    def test_bad_input_is_refused_and_nothing_is_written(self):
        self.write_gear(self.GEAR)
        for body in (
            {"name": "RedCat 51", "colour": "red"},
            {"name": "RedCat 51", "colour": "#abc"},
            {"name": "RedCat 51", "colour": "#7cc4a0\" onmouseover=\"x"},
            {"name": "", "colour": "#7cc4a0"},
            {"name": "x" * 201, "colour": "#7cc4a0"},
            {"name": 5, "colour": "#7cc4a0"},
            {"colour": "#7cc4a0"},
        ):
            with self.subTest(body=body):
                r = self.client.post(ROUTE, json=body)
                self.assertEqual(r.status_code, 400)
        r = self.client.post(ROUTE, data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("telescope_colours", self.on_disk())

    def test_the_map_has_a_size_cap(self):
        many = {f"Scope {i}": "#6fa8dc" for i in range(app_module._TELESCOPE_COLOURS_MAX)}
        self.write_gear(dict(self.GEAR, telescope_colours=many))
        r = self.client.post(ROUTE, json={"name": "One more", "colour": "#6fa8dc"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(ROUTE, json={"name": "Scope 3", "colour": "#e0a370"})
        self.assertEqual(r.status_code, 200, "changing an existing pick still works at the cap")


class TestGearEditorSave(TelescopeColourCase):
    def test_saving_gear_keeps_the_picked_colours(self):
        """The gear editor posts telescopes and cameras only, and must not wipe the picks."""
        self.write_gear({"version": 2, "telescopes": [], "cameras": [],
                         "telescope_colours": {"RedCat 51": "#7cc4a0"}})
        r = self.client.post("/api/gear", json={
            "telescopes": [{"id": "redcat", "name": "RedCat 51"}], "cameras": []})
        self.assertEqual(r.status_code, 200)
        doc = self.on_disk()
        self.assertEqual(doc["telescope_colours"], {"RedCat 51": "#7cc4a0"})
        self.assertEqual(doc["telescopes"], [{"id": "redcat", "name": "RedCat 51"}])

    def test_saving_gear_with_no_picks_adds_no_key(self):
        self.write_gear({"version": 2, "telescopes": [], "cameras": []})
        self.client.post("/api/gear", json={"telescopes": [], "cameras": []})
        self.assertNotIn("telescope_colours", self.on_disk())


if __name__ == "__main__":
    unittest.main()
