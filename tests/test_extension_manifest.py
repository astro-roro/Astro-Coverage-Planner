"""Tests for the extension manifest pass-through.

Validates that arbitrary extension-declared fields (in particular the
new ``input_schema`` and ``preview_hint`` fields used by the form-render
flow) are surfaced verbatim via ``/api/extensions/manifest``. The host
does not enforce a schema — anything the extension's register() appends
to ``app.extensions_manifest`` must survive the round-trip.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Point ACP_EXTENSIONS_DIR at an empty temp dir BEFORE importing app, so
# the auto-loader doesn't try to load the developer's real extensions.
_TMP_EXT_DIR = Path(tempfile.mkdtemp(prefix="acp_test_extdir_"))
os.environ["ACP_EXTENSIONS_DIR"] = str(_TMP_EXT_DIR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402


class TestManifestPassthrough(unittest.TestCase):
    def setUp(self):
        # Reset the manifest registry before each test so we don't see
        # entries left behind by other tests in this file.
        self._saved = list(app.extensions_manifest)
        app.extensions_manifest.clear()
        self.client = app.test_client()

    def tearDown(self):
        app.extensions_manifest.clear()
        app.extensions_manifest.extend(self._saved)

    def test_empty_manifest_returns_empty_list(self):
        r = self.client.get("/api/extensions/manifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), [])

    def test_input_schema_and_preview_hint_survive_roundtrip(self):
        # Register a minimal extension entry with all the new fields, then
        # confirm the manifest endpoint passes them through verbatim. If
        # the host accidentally drops or rewrites a key, this fails.
        entry = {
            "extension": "test_form_ext",
            "name": "Test form extension",
            "actions": [{
                "id": "run",
                "kind": "button",
                "label": "Do thing",
                "endpoint": "/api/ext/test/apply",
                "preview_endpoint": "/api/ext/test/preview",
                "preview_hint": "Heads up: this writes to disk.",
                "input_schema": [
                    {"name": "count", "type": "int", "label": "Count",
                     "default": 5, "min": 1, "max": 10, "required": True},
                    {"name": "label", "type": "string", "label": "Label",
                     "default": "Hello {today}"},
                    {"name": "wet",   "type": "bool", "default": False},
                    {"name": "mode",  "type": "select",
                     "options": [
                         {"value": "a", "label": "Alpha"},
                         {"value": "b", "label": "Beta"},
                     ],
                     "default": "a"},
                ],
            }],
        }
        app.extensions_manifest.append(entry)

        r = self.client.get("/api/extensions/manifest")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(len(body), 1)
        got = body[0]
        # The whole entry must survive verbatim.
        self.assertEqual(got["extension"], "test_form_ext")
        action = got["actions"][0]
        self.assertEqual(action["preview_hint"], "Heads up: this writes to disk.")
        self.assertEqual(len(action["input_schema"]), 4)
        # Spot-check each field shape stayed intact.
        by_name = {f["name"]: f for f in action["input_schema"]}
        self.assertEqual(by_name["count"]["type"], "int")
        self.assertEqual(by_name["count"]["min"], 1)
        self.assertEqual(by_name["count"]["max"], 10)
        self.assertTrue(by_name["count"]["required"])
        self.assertEqual(by_name["label"]["default"], "Hello {today}")
        self.assertEqual(by_name["wet"]["type"], "bool")
        self.assertEqual(by_name["mode"]["type"], "select")
        self.assertEqual(len(by_name["mode"]["options"]), 2)
        self.assertEqual(by_name["mode"]["options"][0]["value"], "a")

    def test_manifest_does_not_mutate_registered_dict(self):
        # If the manifest endpoint accidentally normalised or filtered
        # fields, mutating the source dict could expose the bug. Confirm
        # the endpoint's view equals the source dict we appended.
        entry = {
            "extension": "ext_x",
            "actions": [{"id": "a", "input_schema": [{"name": "x", "type": "int"}]}],
        }
        app.extensions_manifest.append(entry)
        r = self.client.get("/api/extensions/manifest")
        self.assertEqual(r.get_json()[0], entry)


if __name__ == "__main__":
    unittest.main()
