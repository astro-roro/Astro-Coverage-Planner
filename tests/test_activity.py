"""The Activity tab's storage, API and ACP's own events.

docs/specs/activity-tab.md, Phase 1 acceptance tests 1 to 35. The test
numbers are in the method names so a failure points back at the spec.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity  # noqa: E402
import app as app_module  # noqa: E402
from app import app  # noqa: E402

TOKEN = "activity-test-token-0123456789"
HA_ID = "6f1c2a9e-4b7d-4e21-9a35-0c8d1e2f3a4b"
OIII_ID = "7a2d3b0f-5c8e-4f32-8b46-1d9e2f3a4b5c"
DOLPHIN_HA = ("/mnt/remotes/SINGULARITY.ERKO_Singularity/Astro With RoRo/Library/"
              "Sh2_308_Dolphin/110GTX_ASI6200MM/masters/full_master_Ha.fit")
PATH_MAP = json.dumps([{"from": "/mnt/remotes/SINGULARITY.ERKO_Singularity",
                        "mac": "/Volumes/Singularity",
                        "windows": "\\\\Singularity\\Singularity"}])


def iso_ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def ev(key="k1", **kw):
    base = {"source": "plugin", "key": key, "type": "imaging_target", "title": "Imaging",
            "at": datetime.now(timezone.utc).isoformat()}
    base.update(kw)
    return base


class ActivityCase(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        patcher = mock.patch.dict(os.environ, {
            "ACP_API_TOKEN": TOKEN,
            "ACP_ACTIVITY_DB": str(self.td / "activity.sqlite"),
            "ACP_ACTIVITY_TZ": "Australia/Sydney",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("ACP_ACTIVITY", "ACP_ACTIVITY_KEEP_NIGHTS", "ACP_ACTIVITY_PATH_MAP"):
            os.environ.pop(name, None)
        activity.close()
        self.addCleanup(activity.close)
        self._saved = {n: getattr(app_module, n) for n in (
            "PLANS_PATH", "GEAR_PATH", "DESTINATIONS_PATH", "MANIFEST_PATH")}
        self.addCleanup(self._restore)
        app_module.PLANS_PATH = self.td / "plans.json"
        app_module.GEAR_PATH = self.td / "gear.json"
        app_module.DESTINATIONS_PATH = self.td / "destinations.json"
        app_module.MANIFEST_PATH = self.td / "manifest.json"
        self._reset_caches()
        self.client = app.test_client()
        self.h = {"Authorization": f"Bearer {TOKEN}"}

    def _restore(self):
        for n, v in self._saved.items():
            setattr(app_module, n, v)
        self._reset_caches()

    def _reset_caches(self):
        for name in ("_plans_cache", "_gear_cache", "_destinations_cache", "_manifest_cache",
                     "_plans_cache_mtime", "_gear_cache_mtime", "_destinations_cache_mtime",
                     "_manifest_cache_mtime"):
            setattr(app_module, name, None)

    # -- helpers -----------------------------------------------------------

    def post(self, *events, headers=None):
        return self.client.post("/api/activity/events", json={"events": list(events)},
                                headers=headers if headers is not None else self.h)

    def get(self, url):
        return self.client.get(url, headers=self.h)

    def all_events(self):
        body = self.get("/api/activity?nights=30").get_json()
        return [e for n in body["nights"] for e in n["events"]]

    def of_type(self, etype):
        return [e for e in self.all_events() if e["type"] == etype]

    def summary(self):
        return self.get("/api/activity/summary").get_json()


class TestPosting(ActivityCase):
    def test_01_identical_content_is_stored_once(self):
        first = self.post(ev()).get_json()
        again = self.post(ev()).get_json()
        self.assertEqual((first["stored"], again["unchanged"], again["stored"]), (1, 1, 0))
        self.assertEqual(len(self.all_events()), 1)

    def test_02_a_new_title_updates_in_place(self):
        self.post(ev(title="one"))
        before = self.all_events()[0]
        out = self.post(ev(title="two")).get_json()
        after = self.all_events()[0]
        self.assertEqual(out["updated"], 1)
        self.assertEqual((after["id"], after["night"], after["received_at"]),
                         (before["id"], before["night"], before["received_at"]))
        self.assertEqual(after["title"], "two")
        self.assertGreater(after["updated_at"], before["updated_at"])

    def test_02_identical_content_leaves_updated_at_alone(self):
        self.post(ev())
        before = self.all_events()[0]["updated_at"]
        self.post(ev())
        self.assertEqual(self.all_events()[0]["updated_at"], before)

    def _night_for(self, at):
        self.post(ev(key="n", at=at))
        return self.all_events()[0]["night"]

    def test_03_local_noon_is_the_cut_over(self):
        self.assertEqual(self._night_for("2026-10-14T10:00+11:00"), "2026-10-13")
        activity.close()
        os.environ["ACP_ACTIVITY_DB"] = str(self.td / "b.sqlite")
        self.assertEqual(self._night_for("2026-10-14T12:00+11:00"), "2026-10-14")

    def test_04_utc_times_are_read_in_the_configured_zone(self):
        self.assertEqual(self._night_for("2026-10-14T00:30:00Z"), "2026-10-13")

    def test_05_daylight_saving_day_does_not_move_the_cut_over(self):
        self.assertEqual(self._night_for("2026-10-04T11:59+11:00"), "2026-10-03")
        activity.close()
        os.environ["ACP_ACTIVITY_DB"] = str(self.td / "b.sqlite")
        self.assertEqual(self._night_for("2026-10-04T12:00+11:00"), "2026-10-04")

    def test_06_an_explicit_night_wins(self):
        self.post(ev(at="2026-10-20T15:00+11:00", night="2026-10-13"))
        self.assertEqual(self.all_events()[0]["night"], "2026-10-13")

    def test_07_validation_rejects_alone_and_stores_the_rest(self):
        batch = [
            {"source": "plugin", "title": "no key"},
            ev("acp-source", source="acp"),
            ev("bogus-source", source="bogus"),
            ev("long-title", title="x" * 201),
            ev("big-data", data={"blob": "y" * 9000}),
            ev("bad-severity", severity="fatal"),
            ev("weird-type", type="not_a_type"),
            ev("long-detail", detail="d" * 3000),
            {"source": "plugin", "key": "no-at", "type": "imaging_target", "title": "Imaging"},
        ]
        out = self.post(*batch).get_json()
        self.assertEqual(out["stored"], 3)
        self.assertEqual(len(out["rejected"]), 6)
        by_key = {e["key"]: e for e in self.all_events()}
        self.assertEqual(set(by_key), {"weird-type", "long-detail", "no-at"})
        self.assertEqual(by_key["weird-type"]["type"], "other")
        self.assertEqual(len(by_key["long-detail"]["detail"]), 2000)
        stamp = datetime.fromisoformat(by_key["no-at"]["at"])
        self.assertLess(abs((datetime.now(timezone.utc) - stamp).total_seconds()), 60)

    def test_08_batch_and_body_limits_are_413_and_store_nothing(self):
        r = self.post(*[ev(f"k{i}") for i in range(101)])
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.all_events(), [])
        pad = b"x" * (2 * 1024 * 1024 + 16)
        r = self.client.post("/api/activity/events", headers=self.h,
                             data=b'{"events":[],"pad":"' + pad + b'"}',
                             content_type="application/json")
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.all_events(), [])

    def test_08_the_route_takes_more_than_the_one_megabyte_default(self):
        pad = "x" * (1_500_000)
        r = self.client.post("/api/activity/events", headers=self.h,
                             data=json.dumps({"events": [ev()], "pad": pad}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)

    def test_09_no_token_set_is_403_and_a_missing_token_is_401(self):
        r = self.client.post("/api/activity/events", json={"events": []}, headers={})
        self.assertEqual(r.status_code, 401)
        del os.environ["ACP_API_TOKEN"]
        r = self.client.post("/api/activity/events", json={"events": [ev()]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/activity").status_code, 200)

    def test_10_needs_you_wants_a_resolve_key(self):
        out = self.post(ev("a", needs_you=True), ev("b", needs_you=True, resolve_key="r")).get_json()
        self.assertEqual((out["stored"], len(out["rejected"])), (1, 1))
        self.assertEqual(out["rejected"][0]["key"], "a")

    def test_11_the_same_key_from_two_sources_is_two_events(self):
        self.post(ev("k1", source="plugin"), ev("k1", source="aac"))
        self.assertEqual(len(self.all_events()), 2)

    def test_12_a_lower_seq_is_stale(self):
        self.post(ev(seq=5, title="five"))
        out = self.post(ev(seq=3, title="three")).get_json()
        self.assertEqual((out["stale"], out["updated"]), (1, 0))
        self.assertEqual(self.all_events()[0]["title"], "five")

    def test_refs_and_rejected_events_have_a_stable_response_shape(self):
        out = self.post(ev("a"), ev("b", severity="nope")).get_json()
        self.assertEqual(set(out), {"stored", "updated", "unchanged", "stale", "rejected"})


class TestResolving(ActivityCase):
    def queued(self, **kw):
        return ev("q", type="master_queued", needs_you=True, title="Dolphin OIII queued",
                  resolve_key="master:Dolphin:110GTX:OIII", source="aac", **kw)

    def built(self, **kw):
        return ev("b", type="master_built", title="Dolphin OIII built", source="aac",
                  resolves=["master:Dolphin:110GTX:OIII"], **kw)

    def test_13_a_build_closes_the_queued_item(self):
        self.post(self.queued())
        self.assertEqual(self.summary()["needs_you"], 1)
        self.post(self.built())
        self.assertEqual(self.summary()["needs_you"], 0)
        queued = self.of_type("master_queued")[0]
        built = self.of_type("master_built")[0]
        self.assertEqual(queued["resolved_by"], built["id"])

    def test_14_arrival_order_does_not_matter(self):
        # The rule compares event times, so the build is stamped after the item
        # it closes and the two can arrive either way round.
        queued = self.queued(at=iso_ago(minutes=10))
        built = self.built(at=iso_ago(minutes=5))
        self.post(built)
        self.post(queued)
        self.assertEqual(self.summary()["needs_you"], 0)

    def test_14_posted_needs_you_and_resolves_must_carry_at(self):
        for source in ("plugin", "aac"):
            out = self.post(
                {"source": source, "key": "a", "title": "t", "needs_you": True, "resolve_key": "r"},
                {"source": source, "key": "b", "title": "t", "resolves": ["r"]}).get_json()
            self.assertEqual(out["stored"], 0)
            self.assertEqual(len(out["rejected"]), 2)
            self.assertTrue(all("at" in r["error"].split() for r in out["rejected"]), out)
        ok = self.post(ev("c", needs_you=True, resolve_key="r")).get_json()
        self.assertEqual(ok["stored"], 1)
        # ACP's own events always carry a time.
        app.record_activity(type="ts_push", key="own", title="t", needs_you=True, resolve_key="own")
        own = [e for e in self.all_events() if e["key"] == "own"][0]
        self.assertTrue(own["at"])

    def test_14_a_resolve_from_before_a_new_queueing_does_not_close_it(self):
        self.post(self.built(at=iso_ago(days=1)))
        self.post(self.queued())
        self.assertEqual(self.summary()["needs_you"], 1)

    def test_14_a_resolve_with_no_item_stores_and_changes_nothing_else(self):
        self.post(self.built())
        self.assertEqual(len(self.all_events()), 1)
        self.assertEqual(self.summary()["needs_you"], 0)

    def ready(self, frames=45):
        return ev("r", type="master_ready", needs_you=True, title="Dolphin Ha ready",
                  resolve_key="edit:/m/full_master_Ha.fit", source="aac",
                  data={"frames": frames})

    def test_15_changed_content_reopens_and_identical_content_does_not(self):
        self.post(self.ready())
        item = self.of_type("master_ready")[0]
        self.client.post(f"/api/activity/events/{item['id']}/dismiss", headers=self.h)
        self.assertEqual(self.summary()["needs_you"], 0)
        self.post(self.ready())
        self.assertEqual(self.summary()["needs_you"], 0)
        self.post(self.ready(frames=46))
        self.assertEqual(self.summary()["needs_you"], 1)
        self.assertIsNone(self.of_type("master_ready")[0]["dismissed_at"])

    def test_16_dismiss_and_restore(self):
        self.post(self.ready())
        item = self.of_type("master_ready")[0]
        r = self.client.post(f"/api/activity/events/{item['id']}/dismiss", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.of_type("master_ready")[0]["dismissed_at"])
        self.assertEqual(self.summary()["needs_you"], 0)
        self.client.post(f"/api/activity/events/{item['id']}/restore", headers=self.h)
        self.assertEqual(self.summary()["needs_you"], 1)
        self.assertEqual(self.client.post("/api/activity/events/9999/dismiss",
                                          headers=self.h).status_code, 404)

    def test_17_expiry_after_seven_days(self):
        for key, etype, ago in (("old-up", "ts_upload_received", 8), ("new-up", "ts_upload_received", 6),
                                ("old-m", "master_ready", 8), ("new-m", "master_ready", 6)):
            self.post(ev(key, type=etype, needs_you=True, resolve_key="r:" + key,
                         at=iso_ago(days=ago)))
        open_keys = {e["key"] for e in self.summary()["open"]}
        self.assertEqual(open_keys, {"new-up", "new-m"})
        self.assertEqual(self.summary()["needs_you"], 2)

    def test_summary_open_is_capped_at_fifty_oldest_first(self):
        self.post(*[ev(f"k{i}", needs_you=True, resolve_key=f"r{i}", at=iso_ago(minutes=100 - i))
                    for i in range(60)])
        s = self.summary()
        self.assertEqual((s["needs_you"], s["open_total"], len(s["open"])), (60, 60, 50))
        self.assertEqual(s["open"][0]["key"], "k0")


class TestReading(ActivityCase):
    def test_18_nights_page_newest_first_and_skip_empty_ones(self):
        for day in (10, 11, 12, 13):
            self.post(ev(f"n{day}", night=f"2026-10-{day}"))
        first = self.get("/api/activity?nights=2").get_json()
        self.assertEqual([n["night"] for n in first["nights"]], ["2026-10-13", "2026-10-12"])
        self.assertEqual((first["next_before"], first["total_nights"]), ("2026-10-12", 4))
        second = self.get("/api/activity?nights=2&before=2026-10-12").get_json()
        self.assertEqual([n["night"] for n in second["nights"]], ["2026-10-11", "2026-10-10"])
        self.assertIsNone(second["next_before"])
        self.assertEqual(second["total_nights"], 4)

    def test_19_nights_is_capped_and_must_be_an_integer(self):
        self.post(*[ev(f"n{d}", night=f"2026-09-{d:02d}") for d in range(1, 29)],
                  *[ev(f"m{d}", night=f"2026-10-{d:02d}") for d in range(1, 6)])
        body = self.get("/api/activity?nights=31").get_json()
        self.assertEqual(len(body["nights"]), 30)
        self.assertEqual(self.get("/api/activity?nights=abc").status_code, 400)
        self.assertEqual(self.get("/api/activity?before=nope").status_code, 400)

    def test_20_a_busy_night_is_capped_and_pages(self):
        base = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)
        for start in range(0, 250, 50):
            self.post(*[ev(f"e{i}", night="2026-10-13", title=f"row {i}",
                           at=(base + timedelta(seconds=i)).isoformat())
                        for i in range(start, start + 50)])
        night = self.get("/api/activity?nights=1").get_json()["nights"][0]
        self.assertEqual((len(night["events"]), night["more"]), (200, 50))
        self.assertEqual([e["title"] for e in night["events"]][:2], ["row 0", "row 1"])
        rest = self.get("/api/activity/night/2026-10-13?offset=200").get_json()
        self.assertEqual((len(rest["events"]), rest["more"]), (50, 0))
        self.assertEqual(rest["events"][0]["title"], "row 200")
        self.assertEqual(self.get("/api/activity/night/2026-10-13?offset=x").status_code, 400)
        self.assertEqual(self.get("/api/activity/night/bad").status_code, 400)

    def test_21_has_new_follows_the_seen_marker(self):
        self.assertFalse(self.summary()["has_new"])
        self.post(ev())
        self.assertTrue(self.summary()["has_new"])
        self.client.post("/api/activity/seen", headers=self.h)
        self.assertFalse(self.summary()["has_new"])
        self.post(ev(title="changed"))
        self.assertTrue(self.summary()["has_new"])

    def test_22_seen_uses_the_server_clock_and_never_goes_back(self):
        first = self.client.post("/api/activity/seen", headers=self.h,
                                 json={"at": "2001-01-01T00:00:00Z"}).get_json()
        self.assertGreater(first["last_seen"], "2020")
        second = self.client.post("/api/activity/seen", headers=self.h).get_json()
        self.assertGreaterEqual(second["last_seen"], first["last_seen"])
        self.assertEqual(self.get("/api/activity").get_json()["last_seen"], second["last_seen"])

    def test_23_prefs_default_and_round_trip(self):
        want = {"chips": {"plans": True, "syncs": True, "imaging": True, "site": False,
                          "data": True, "processing": True, "masters": True, "coverage": True}}
        self.assertEqual(self.get("/api/activity/prefs").get_json(), want)
        r = self.client.put("/api/activity/prefs", headers=self.h,
                            json={"chips": {"imaging": False, "bogus": True}})
        self.assertEqual(r.status_code, 200)
        want["chips"]["imaging"] = False
        self.assertEqual(self.get("/api/activity/prefs").get_json(), want)
        self.assertEqual(self.client.put("/api/activity/prefs", headers=self.h,
                                         json={"chips": {"plans": "yes"}}).status_code, 400)

    def test_24_browser_writes_check_the_origin(self):
        for path, method in (("/api/activity/seen", "post"), ("/api/activity/prefs", "put")):
            call = getattr(self.client, method)
            bad = call(path, headers={**self.h, "Origin": "https://evil.example"}, json={"chips": {}})
            self.assertEqual(bad.status_code, 403, path)
            ok = call(path, headers={**self.h, "Origin": "http://localhost"}, json={"chips": {}})
            self.assertEqual(ok.status_code, 200, path)
        self.post(ev(needs_you=True, resolve_key="r"))
        item = self.all_events()[0]
        r = self.client.post(f"/api/activity/events/{item['id']}/dismiss",
                             headers={**self.h, "Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)


class TestForwardedOrigin(ActivityCase):
    def test_24_behind_a_proxy_the_forwarded_host_counts(self):
        proxy = {"Origin": "https://acp.example.org", "X-Forwarded-Host": "acp.example.org"}
        ok = self.client.post("/api/activity/seen", headers={**self.h, **proxy})
        self.assertEqual(ok.status_code, 200)
        other = {"Origin": "https://evil.example", "X-Forwarded-Host": "acp.example.org"}
        self.assertEqual(self.client.post("/api/activity/seen", headers={**self.h, **other}).status_code, 403)
        self.assertEqual(self.client.post("/api/activity/seen", headers=self.h).status_code, 200)


class TestPlanEvents(ActivityCase):
    def put_plan(self, plan, expect=200):
        r = self.client.put(f"/api/plans/{plan['id']}", json=plan, headers=self.h)
        self.assertEqual(r.status_code, expect, r.get_data(as_text=True))
        return r

    def write_plans(self, plans):
        app_module.PLANS_PATH.write_text(json.dumps(
            {"version": 1, "settings_migrated": 1, "filter_rows_migrated": 1, "plans": plans}),
            encoding="utf-8")
        self._reset_caches()

    def plan(self, **kw):
        base = {"id": "M42", "project_name": "Orion",
                "target": {"name": "M42", "center_ra_deg": 83.8, "center_dec_deg": -5.4},
                "min_altitude_deg": 20, "priority": "normal",
                "updated_at": "2020-01-01T00:00:00+00:00", "guid": "g1",
                "created_at": "2020-01-01T00:00:00+00:00", "last_synced_at": None}
        base.update(kw)
        return base

    def test_plan_created_and_deleted(self):
        self.assertEqual(self.client.post("/api/plans", json=self.plan(), headers=self.h).status_code, 201)
        created = self.of_type("plan_created")
        night = activity.current_night()
        self.assertEqual([e["key"] for e in created], [f"plan_created:M42:{night}"])
        self.assertEqual(created[0]["refs"]["plan_id"], "M42")
        self.assertEqual(self.client.delete("/api/plans/M42", headers=self.h).status_code, 204)
        self.assertEqual([e["key"] for e in self.of_type("plan_deleted")], [f"plan_deleted:M42:{night}"])

    def test_25_an_edit_is_recorded_and_the_nights_edits_accumulate(self):
        self.write_plans([self.plan()])
        self.put_plan(self.plan(min_altitude_deg=30))
        events = self.of_type("plan_edited")
        self.assertEqual(len(events), 1)
        night = activity.current_night()
        self.assertEqual(events[0]["key"], f"plan_edited:M42:{night}")
        self.assertIn("min altitude 20 to 30", events[0]["title"])
        self.put_plan(self.plan(min_altitude_deg=30, priority="high"))
        events = self.of_type("plan_edited")
        self.assertEqual(len(events), 1)
        changes = events[0]["data"]["changes"]
        self.assertEqual(changes["min_altitude_deg"], {"from": 20, "to": 30})
        self.assertEqual(changes["priority"], {"from": "normal", "to": "high"})
        self.assertIn("priority Normal to High", events[0]["title"])

    def test_25_an_edit_put_back_within_the_night_leaves_no_event(self):
        self.write_plans([self.plan()])
        self.put_plan(self.plan(min_altitude_deg=30))
        self.put_plan(self.plan(min_altitude_deg=20))
        self.assertEqual(self.of_type("plan_edited"), [])

    def test_26_bookkeeping_and_heals_are_not_edits(self):
        self.write_plans([self.plan()])
        self.put_plan(self.plan(updated_at="2030-01-01T00:00:00+00:00", guid="g1",
                                created_at="2019-01-01T00:00:00+00:00",
                                last_synced_at="2026-10-01T00:00:00+00:00"))
        plan = self.client.get("/api/plans/M42", headers=self.h).get_json()
        self.put_plan(plan)
        self.assertEqual(self.of_type("plan_edited"), [])

    def test_26_a_load_time_heal_is_not_an_edit(self):
        poisoned = self.plan(min_altitude_deg=float("nan"))
        self.write_plans([poisoned])
        self.put_plan(self.plan(min_altitude_deg=None))
        self.assertEqual(self.of_type("plan_edited"), [])

    def test_project_settings_and_gear_coalesce_per_night(self):
        self.write_plans([self.plan(), self.plan(id="M43", target={"name": "M43"})])
        r = self.client.put("/api/projects/Orion/settings", headers=self.h, json={"priority": "high"})
        self.assertEqual(r.status_code, 200)
        r = self.client.put("/api/projects/Orion/settings", headers=self.h, json={"minimum_time_min": 30})
        events = self.of_type("project_settings")
        self.assertEqual(len(events), 1)
        self.assertIn("priority Normal to High", events[0]["title"])
        self.assertIn("minimum time unset to 30", events[0]["title"])
        gear = {"telescopes": [{"id": "t1", "name": "Scope", "focal_length_mm": 540}],
                "cameras": [{"id": "c1", "name": "Cam", "pixel_size_um": 3.76, "sensor_px": [6248, 4176]}]}
        self.assertEqual(self.client.post("/api/gear", json=gear, headers=self.h).status_code, 200)
        self.assertEqual(len(self.of_type("gear_changed")), 1)

    def rows(self, actual=5.0, goal=6.0):
        return [{"id": HA_ID, "filter": "Ha", "template": "Ha", "template_sub_s": 300,
                 "unit": "h", "target_hours": goal, "actual_hours": actual}]

    def progress(self, filters):
        return self.client.post("/api/plans/M42/progress", json={"filters": filters}, headers=self.h)

    def test_27_unknown_filters_open_an_item_and_a_clean_report_closes_it(self):
        self.write_plans([self.plan(filter_rows=self.rows())])
        r = self.progress({"SII": {"acquired_hours": 1.0}})
        self.assertEqual(r.status_code, 200)
        night = activity.current_night()
        unknown = self.of_type("progress_unknown")
        self.assertEqual([(e["key"], e["resolve_key"]) for e in unknown],
                         [(f"progress_unknown:M42:{night}", "progress:M42")])
        self.assertEqual([e["key"] for e in self.summary()["open"]], [unknown[0]["key"]])
        self.progress({"Ha": {"acquired_hours": 5.5}})
        self.assertEqual(self.summary()["needs_you"], 0)
        report = self.of_type("progress_report")[0]
        self.assertEqual(report["key"], f"progress_report:M42:{night}")
        self.assertEqual(report["data"]["filters"]["Ha"]["hours"], 5.5)

    def test_27_an_unknown_filter_again_after_a_clean_report_reopens_the_item(self):
        self.write_plans([self.plan(filter_rows=self.rows())])
        self.progress({"SII": {"acquired_hours": 1.0}})
        self.progress({"Ha": {"acquired_hours": 5.5}})
        self.assertEqual(self.summary()["needs_you"], 0)
        self.progress({"SII": {"acquired_hours": 1.0}})
        self.assertEqual(self.summary()["needs_you"], 1)

    def test_progress_report_feeds_last_night(self):
        self.write_plans([self.plan(filter_rows=self.rows())])
        self.progress({"Ha": {"acquired_hours": 5.5}})
        self.progress({"Ha": {"acquired_hours": 5.75}})
        last = self.summary()["last_night"]
        self.assertEqual(last["night"], activity.current_night())
        [target] = last["targets"]
        self.assertEqual(target["plan_id"], "M42")
        self.assertEqual(target["filters"][0]["filter"], "Ha")
        self.assertAlmostEqual(target["filters"][0]["delta_h"], 0.75)
        self.assertAlmostEqual(target["filters"][0]["hours"], 5.75)
        self.assertAlmostEqual(target["filters"][0]["goal"], 6.0)

    def test_28_reaching_a_goal_records_once_through_progress(self):
        self.write_plans([self.plan(filter_rows=self.rows())])
        self.progress({"Ha": {"acquired_hours": 6.0}})
        goal = self.of_type("goal_reached")
        self.assertEqual([e["key"] for e in goal], [f"goal:M42:{HA_ID}:6"])
        self.progress({"Ha": {"acquired_hours": 6.5}})
        self.assertEqual(len(self.of_type("goal_reached")), 1)

    def test_28_raising_the_goal_makes_a_new_key(self):
        self.write_plans([self.plan(filter_rows=self.rows(actual=5.0))])
        self.progress({"Ha": {"acquired_hours": 6.0}})
        plan = self.client.get("/api/plans/M42", headers=self.h).get_json()
        plan["filter_rows"][0]["target_hours"] = 8.0
        self.put_plan(plan)
        self.progress({"Ha": {"acquired_hours": 8.0}})
        self.assertEqual(sorted(e["key"] for e in self.of_type("goal_reached")),
                         [f"goal:M42:{HA_ID}:6", f"goal:M42:{HA_ID}:8"])

    def accepted(self, complete, seconds=21600.0, frames=72):
        row = {"id": HA_ID, "filter": "Ha", "template": "Ha", "accepted_seconds": seconds,
               "accepted_frames": frames, "seen_seconds": seconds + 300, "seen_frames": frames + 1,
               "complete": complete, "estimated_hours": None, "measurement": None}
        return self.client.post("/api/plans/M42/accepted", headers=self.h, json={
            "source": "aac", "at": "2026-10-12T10:05:00Z", "filed_through_night": "2026-10-11",
            "min_total_frames": 10, "rows": [row]})

    def test_28_reaching_a_goal_through_accepted_counts(self):
        self.write_plans([self.plan(filter_rows=self.rows(actual=0.0))])
        self.assertEqual(self.accepted(False, seconds=3600.0, frames=12).status_code, 200)
        self.assertEqual(self.of_type("goal_reached"), [])
        self.assertEqual(self.accepted(True).status_code, 200)
        self.assertEqual([e["key"] for e in self.of_type("goal_reached")], [f"goal:M42:{HA_ID}:6"])
        self.accepted(True)
        self.assertEqual(len(self.of_type("goal_reached")), 1)
        counts = self.of_type("accepted_counts")
        self.assertEqual(counts[0]["key"], f"accepted_counts:M42:{activity.current_night()}")


class TestScanEvents(ActivityCase):
    def write_manifest(self, ha_hours):
        app_module.MANIFEST_PATH.write_text(json.dumps({"targets": [
            {"target_id": 7, "objects": ["M42"],
             "filters": {"Ha": {"total_hours": ha_hours, "files": 10}}}]}), encoding="utf-8")

    def setUp(self):
        super().setUp()
        self._real = app_module._run_scan_subprocess
        self.addCleanup(lambda: setattr(app_module, "_run_scan_subprocess", self._real))
        self.hours = 1.0
        self.write_manifest(self.hours)

    def scan_adding(self, extra):
        def fake(_args):
            self.hours += extra
            self.write_manifest(self.hours)
            return 0
        app_module._run_scan_subprocess = fake
        self.assertTrue(app_module.run_scan_now("test"))

    def test_29_two_scans_accumulate_into_one_coverage_row(self):
        self.scan_adding(0.25)
        self.scan_adding(0.25)
        rows = self.of_type("coverage_changed")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["key"], f"coverage:7:Ha:{activity.current_night()}")
        self.assertIn("+0.50 h", rows[0]["title"])
        self.assertEqual(len(self.of_type("scan_finished")), 1)

    def test_29_tiny_moves_are_not_coverage_changes(self):
        self.scan_adding(0.005)
        self.assertEqual(self.of_type("coverage_changed"), [])

    def test_30_a_failed_scan_opens_an_item_and_the_next_good_scan_closes_it(self):
        app_module._run_scan_subprocess = lambda _args: 1
        app_module.run_scan_now("test")
        night = activity.current_night()
        [err] = self.of_type("scan_error")
        self.assertEqual((err["key"], err["resolve_key"], err["needs_you"]),
                         (f"scan_error:{night}", "scan", True))
        self.assertEqual(self.of_type("scan_finished"), [])
        self.assertEqual(self.summary()["needs_you"], 1)
        self.scan_adding(0.0)
        self.assertEqual(self.of_type("scan_finished")[0]["key"], f"scan_finished:{night}")
        self.assertEqual(self.summary()["needs_you"], 0)

    def test_30_a_good_scan_after_a_good_scan_still_closes_a_later_failure(self):
        self.scan_adding(0.0)
        app_module._run_scan_subprocess = lambda _args: 1
        app_module.run_scan_now("test")
        self.assertEqual(self.summary()["needs_you"], 1)
        self.scan_adding(0.0)
        self.assertEqual(self.summary()["needs_you"], 0)


class TestStoreRules(ActivityCase):
    def test_31_an_unopenable_database_never_fails_a_save(self):
        os.environ["ACP_ACTIVITY_DB"] = str(self.td)  # a directory
        activity.close()
        app_module.PLANS_PATH.write_text(json.dumps(
            {"version": 1, "settings_migrated": 1, "filter_rows_migrated": 1,
             "plans": [{"id": "M42", "target": {"name": "M42"}, "min_altitude_deg": 20}]}),
            encoding="utf-8")
        with self.assertLogs("acp.activity", level="ERROR"):
            r = self.client.put("/api/plans/M42", headers=self.h,
                                json={"id": "M42", "target": {"name": "M42"}, "min_altitude_deg": 30})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/plans/M42", headers=self.h).get_json()["min_altitude_deg"], 30)

    def test_32_the_path_map(self):
        os.environ["ACP_ACTIVITY_PATH_MAP"] = PATH_MAP
        got = activity.map_master_path(DOLPHIN_HA)
        self.assertEqual(got["mac_folder"], "/Volumes/Singularity/Astro With RoRo/Library/"
                                            "Sh2_308_Dolphin/110GTX_ASI6200MM/masters/")
        self.assertEqual(got["windows_folder"],
                         "\\\\Singularity\\Singularity\\Astro With RoRo\\Library\\"
                         "Sh2_308_Dolphin\\110GTX_ASI6200MM\\masters\\")
        self.assertEqual(got["file_name"], "full_master_Ha.fit")
        os.environ.pop("ACP_ACTIVITY_PATH_MAP")
        self.assertEqual(activity.map_master_path(DOLPHIN_HA), {
            "mac_folder": "(unmapped) full_master_Ha.fit",
            "windows_folder": "(unmapped) full_master_Ha.fit", "file_name": "full_master_Ha.fit"})

    def test_32_first_entry_wins_and_a_segment_boundary_is_required(self):
        os.environ["ACP_ACTIVITY_PATH_MAP"] = json.dumps([
            {"from": "/mnt/a", "mac": "/first", "windows": "\\\\first"},
            {"from": "/mnt/a/x", "mac": "/second", "windows": "\\\\second"}])
        self.assertTrue(activity.map_master_path("/mnt/a/x/y.fit")["mac_folder"].startswith("/first/"))
        self.assertTrue(activity.map_master_path("/mnt/ab/x.fit")["mac_folder"].startswith("(unmapped)"))
        os.environ["ACP_ACTIVITY_PATH_MAP"] = PATH_MAP
        self.assertTrue(activity.map_master_path(
            "/mnt/remotes/SINGULARITY.ERKO_Singularity2/x.fit")["mac_folder"].startswith("(unmapped)"))

    def test_32_a_posted_master_gets_its_folders(self):
        os.environ["ACP_ACTIVITY_PATH_MAP"] = PATH_MAP
        self.post(ev(source="aac", type="master_built",
                     refs={"master": {"path": DOLPHIN_HA, "frames": 45, "hours": 3.75, "filter": "Ha"}}))
        master = self.all_events()[0]["refs"]["master"]
        self.assertEqual(master["file_name"], "full_master_Ha.fit")
        self.assertEqual(master["frames"], 45)
        self.assertTrue(master["mac_folder"].startswith("/Volumes/Singularity/"))

    def test_33_secrets_and_home_folders_are_scrubbed(self):
        self.post(ev(
            title="Authorization: Bearer abc123 token=abc password=hunter2 https://u:p@host/ "
                  "access_token=at1 api_token=at2 ACP_API_TOKEN=at3 db_password=at4",
            detail="saved to /Users/rohan/Pictures/x.fits",
            data={"note": "C:\\Users\\Astro\\Pictures\\x.fits", "nested": {"url": "https://u:p@host/"}},
            refs={"plan_id": "p1", "evil": "dropped", "target": "C:\\Users\\Astro\\Pictures\\x.fits"}))
        e = self.all_events()[0]
        for secret in ("abc123", "=abc", "hunter2", "u:p@"):
            self.assertNotIn(secret, e["title"])
        for secret in ("at1", "at2", "at3", "at4"):
            self.assertNotIn(secret, e["title"])
        self.assertEqual(e["title"].count("[redacted]"), 8)
        self.assertEqual(e["data"]["note"], "~\\Pictures\\x.fits")
        self.assertEqual(e["data"]["nested"]["url"], "https://[redacted]@host/")
        self.assertEqual(e["detail"], "saved to ~/Pictures/x.fits")
        self.assertEqual(e["refs"]["target"], "~\\Pictures\\x.fits")
        self.assertNotIn("evil", e["refs"])

    def test_33_a_windows_user_name_with_a_space_is_stripped(self):
        self.post(ev(detail="C:\\Users\\John Smith\\Pictures\\x.fits"))
        self.assertEqual(self.all_events()[0]["detail"], "~\\Pictures\\x.fits")

    def test_34_pruned_nights_leave_tombstones(self):
        os.environ["ACP_ACTIVITY_KEEP_NIGHTS"] = "2"
        for day in (10, 11, 12, 13):
            self.post(ev(f"n{day}", night=f"2026-10-{day}"))
        self.assertEqual(sorted(e["night"] for e in self.all_events()), ["2026-10-12", "2026-10-13"])
        db = sqlite3.connect(os.environ["ACP_ACTIVITY_DB"])
        self.addCleanup(db.close)
        tombs = sorted(r[1] for r in db.execute("SELECT source, key FROM tombstones"))
        self.assertEqual(tombs, ["n10", "n11"])
        out = self.post(ev("n10", night="2026-10-10")).get_json()
        self.assertEqual((out["stale"], out["stored"]), (1, 0))
        self.assertEqual(len(self.all_events()), 2)

    def test_the_schema_version_is_recorded(self):
        self.post(ev())
        db = sqlite3.connect(os.environ["ACP_ACTIVITY_DB"])
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], activity.SCHEMA_VERSION)
        self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_app_exposes_record_activity_for_extensions(self):
        self.assertTrue(callable(getattr(app, "record_activity", None)))
        app.record_activity(type="ts_push", key="ts_push:1", title="Pushed", night="2026-10-13")
        [e] = self.all_events()
        self.assertEqual((e["source"], e["type"]), ("acp", "ts_push"))


class TestSwitchedOff(ActivityCase):
    def setUp(self):
        super().setUp()
        app_module.MANIFEST_PATH = self.td / "none.json"

    def test_35_off_means_404s_no_events_and_no_rail_button(self):
        page_on = self.client.get("/", headers=self.h).get_data(as_text=True)
        self.assertIn('data-tab="activity"', page_on)
        os.environ["ACP_ACTIVITY"] = "off"
        for method, path in (("get", "/api/activity"), ("get", "/api/activity/summary"),
                             ("get", "/api/activity/prefs"), ("get", "/api/activity/night/2026-10-13"),
                             ("post", "/api/activity/events"), ("post", "/api/activity/seen"),
                             ("put", "/api/activity/prefs"), ("post", "/api/activity/events/1/dismiss")):
            r = getattr(self.client, method)(path, headers=self.h, json={})
            self.assertEqual(r.status_code, 404, path)
        app_module.PLANS_PATH.write_text(json.dumps(
            {"version": 1, "settings_migrated": 1, "filter_rows_migrated": 1,
             "plans": [{"id": "M42", "target": {"name": "M42"}, "min_altitude_deg": 20}]}), encoding="utf-8")
        self.client.put("/api/plans/M42", headers=self.h,
                        json={"id": "M42", "target": {"name": "M42"}, "min_altitude_deg": 30})
        self.assertFalse(Path(os.environ["ACP_ACTIVITY_DB"]).exists())
        page_off = self.client.get("/", headers=self.h).get_data(as_text=True)
        self.assertNotIn('data-tab="activity"', page_off)
        del os.environ["ACP_ACTIVITY"]
        self.assertEqual(self.all_events(), [])


if __name__ == "__main__":
    unittest.main()
