"""H4: Activity types and sources an extension registers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402
from test_activity import ActivityCase, ev  # noqa: E402
from app import app  # noqa: E402


class TestExtensionTypes(ActivityCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(activity.reset_registrations)
        load_dummy("backup_events").register(app)

    def test_hook_is_offered(self):
        self.assertIn("activity_types", app.acp_extension_hooks)

    def test_a_registered_source_and_type_are_stored_as_sent(self):
        r = self.post(ev("b1", source="backup", type="backup_done", title="Backup done")).get_json()
        self.assertEqual(r["stored"], 1, r)
        self.assertEqual(self.of_type("backup_done")[0]["source"], "backup")

    def test_an_unregistered_source_is_still_refused(self):
        r = self.post(ev("b2", source="someone", type="backup_done", title="x")).get_json()
        self.assertEqual(r["stored"], 0)
        self.assertIn("source", r["rejected"][0]["error"])

    def test_record_from_the_extension_keeps_its_type(self):
        activity.record(type="backup_checked", key="chk1", title="Backup checked")
        self.assertEqual(len(self.of_type("backup_checked")), 1)

    def test_types_route_lists_the_new_chip_and_the_added_type(self):
        body = self.get("/api/activity/types").get_json()
        chips = {c["id"]: c for c in body["chips"]}
        self.assertEqual(chips["backups"]["label"], "Backups")
        self.assertEqual(chips["backups"]["types"], ["backup_done", "backup_failed", "backup_checked"])
        self.assertIn("frames_rejected", chips["processing"]["types"])
        self.assertIn("backup", body["sources"])

    def test_the_new_chip_can_be_switched_off_in_prefs(self):
        self.assertTrue(self.get("/api/activity/prefs").get_json()["chips"]["backups"])
        r = self.client.put("/api/activity/prefs", headers=self.h, json={"chips": {"backups": False}})
        self.assertFalse(r.get_json()["chips"]["backups"])

    def test_once_the_extension_is_gone_stored_events_keep_their_type(self):
        self.post(ev("b3", source="backup", type="backup_failed", title="Backup failed"))
        activity.reset_registrations()
        self.assertEqual(self.of_type("backup_failed")[0]["type"], "backup_failed")
        # A new post of the same type is now filed as other.
        self.post(ev("b4", source="external", type="backup_failed", title="Again"))
        self.assertEqual(len(self.of_type("other")), 1)


@pytest.mark.parametrize("args", [
    ("Bad Group", ["x"], "L"),
    ("newgroup", ["x"], None),
    ("plans", [], None),
    ("plans", ["Has Space"], None),
])
def test_register_types_rejects_bad_arguments(args):
    with pytest.raises(ValueError):
        activity.register_types(*args)
    activity.reset_registrations()


@pytest.mark.parametrize("name", ["acp", "Not A Slug", "", None])
def test_register_source_rejects_bad_names(name):
    with pytest.raises(ValueError):
        activity.register_source(name)


def test_a_known_type_is_not_moved_to_another_group():
    activity.register_types("newgroup", ["plan_edited", "fresh"], label="New")
    try:
        chips = {c["id"]: c for c in activity.chip_defs()}
        assert chips["newgroup"]["types"] == ["fresh"]
        assert "plan_edited" in chips["plans"]["types"]
    finally:
        activity.reset_registrations()
