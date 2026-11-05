"""Old names an extension registers (docs/extensions.md, "Old names")."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import filter_rows as fr  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402
from test_activity import ActivityCase, ev  # noqa: E402
from app import app  # noqa: E402


class TestActivityAliases(ActivityCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(activity.reset_registrations)
        saved = dict(fr.BLOCK_ALIASES)
        self.addCleanup(lambda: (fr.BLOCK_ALIASES.clear(), fr.BLOCK_ALIASES.update(saved)))
        load_dummy("old_names").register(app)

    def test_hook_is_offered_and_blocks_are_recorded(self):
        self.assertIn("name_aliases", app.acp_extension_hooks)
        self.assertEqual(fr.BLOCK_ALIASES["legacy_block"], "grader")

    def test_old_names_are_stored_as_the_new_ones(self):
        r = self.post(ev("a1", source="legacy_src", type="legacy_check", title="Old",
                         refs={"legacy_job": "j1"})).get_json()
        self.assertEqual(r["stored"], 1, r)
        e = self.all_events()[0]
        self.assertEqual((e["source"], e["type"], e["refs"]), ("external", "calibration", {"bucket": "j1"}))
        again = self.post(ev("a1", source="external", type="calibration", title="Old",
                             refs={"bucket": "j1"})).get_json()
        self.assertEqual((again["stored"], again["unchanged"]), (0, 1))

    def test_a_new_name_already_sent_wins_over_an_old_ref(self):
        self.post(ev("a2", source="external", refs={"bucket": "new", "legacy_job": "old"}))
        self.assertEqual(self.all_events()[0]["refs"], {"bucket": "new"})

    def test_stored_rows_under_old_names_are_renamed(self):
        self.post(ev("a3", source="external", type="calibration", refs={"bucket": "j3"}))
        activity.close()
        db = sqlite3.connect(str(self.td / "activity.sqlite"))
        db.execute("""UPDATE events SET source='legacy_src', type='legacy_check', refs='{"legacy_job": "j3"}'""")
        db.commit()
        db.close()
        e = self.all_events()[0]
        self.assertEqual((e["source"], e["type"], e["refs"]), ("external", "calibration", {"bucket": "j3"}))

    def test_an_alias_to_a_name_this_acp_lacks_does_nothing_yet(self):
        activity.register_alias("source", "legacy_two", "not_a_source_yet")
        r = self.post(ev("a4", source="legacy_two")).get_json()
        self.assertEqual(r["stored"], 0)
        self.assertIn("source", r["rejected"][0]["error"])


@pytest.mark.parametrize("args", [("colour", "a", "b"), ("source", "", "b"), ("source", "a", "a"),
                                  ("source", "acp", "b"), ("type", "a", "acp"), ("ref", None, "b")])
def test_register_alias_rejects_bad_arguments(args):
    with pytest.raises(ValueError):
        activity.register_alias(*args)


@pytest.mark.parametrize("args", [("Bad", "grader"), ("old", "old"), ("old", ""), (None, "grader")])
def test_register_block_alias_rejects_bad_arguments(args):
    with pytest.raises(ValueError):
        fr.register_block_alias(*args)
