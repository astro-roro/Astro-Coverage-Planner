"""The upload progress card: Voyager's nightly copy to the NAS, watched by ACP.

Each test builds a night folder the way rclone leaves it part way through:
finished frames, an in-flight ``.partial``, and Voyager's ``upload_plan.json``.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import acp_settings  # noqa: E402
import activity  # noqa: E402
import app as app_module  # noqa: E402
import upload_progress as up  # noqa: E402
from app import app  # noqa: E402

SYD = ZoneInfo("Australia/Sydney")
START = datetime(2026, 11, 5, 8, 0, tzinfo=SYD)
FRAME = 64_000_000  # one full frame from the 6200MM, near enough
NIGHT = "2026-11-04"


def write_plan(folder: Path, **kw) -> dict:
    plan = {"version": 1, "night_id": folder.name, "started_at": START.isoformat(),
            "window_end": (START + timedelta(hours=9)).isoformat(), "path": "vpn",
            "total_files": 100, "total_bytes": 100 * FRAME,
            "already_on_nas_files": 0, "already_on_nas_bytes": 0}
    plan.update(kw)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / up.PLAN_NAME).write_text(json.dumps(plan), encoding="utf-8")
    return plan


def add_frames(folder: Path, count: int, start: int = 0, sub: str = "LIGHT") -> None:
    d = folder / sub
    d.mkdir(parents=True, exist_ok=True)
    for i in range(start, start + count):
        with open(d / f"frame_{i:04d}.fits", "wb") as fh:
            fh.truncate(FRAME)  # sparse, so the fixture costs no disk


def add_partial(folder: Path, size: int) -> None:
    d = folder / "LIGHT"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "frame_9999.fits.a1b2c3.partial", "wb") as fh:
        fh.truncate(size)


class FolderCase(unittest.TestCase):
    def setUp(self):
        self.incoming = Path(tempfile.mkdtemp())
        self.folder = self.incoming / NIGHT
        self.recorded: list[dict] = []
        self.watcher = up.UploadWatcher(
            self.incoming, record=lambda **f: self.recorded.append(f),
            already_recorded=lambda key: any(r["key"] == key for r in self.recorded),
            night_of=lambda dt: "derived", zone=SYD)


class TestCounting(FolderCase):
    def test_counts_frames_and_partial_bytes_and_skips_protocol_files(self):
        write_plan(self.folder)
        add_frames(self.folder, 3)
        add_frames(self.folder, 2, sub="FLAT")
        add_partial(self.folder, FRAME // 2)
        (self.folder / "upload_plan.json.tmp").write_text("{}")
        (self.folder / "_collisions").mkdir()
        add_frames(self.folder / "_collisions", 4)
        counts = up.count_folder(self.folder)
        self.assertEqual(counts, {"files": 5, "bytes": 5 * FRAME, "partial_bytes": FRAME // 2})


class TestProgressMaths(FolderCase):
    def test_progress_from_a_fixture_folder(self):
        """40 of 100 frames, 50 minutes in: 40 % done, 6.8 Mbps average,
        and the other 60 at that rate take 75 minutes, so finish at 10:05."""
        write_plan(self.folder)
        add_frames(self.folder, 40)
        now = START + timedelta(minutes=50)
        self.watcher.tick(now)
        [row] = self.watcher.snapshot()
        self.assertEqual(row["files_done"], 40)
        self.assertEqual(row["files_total"], 100)
        self.assertEqual(row["bytes_done"], 40 * FRAME)
        self.assertEqual(row["percent"], 40.0)
        self.assertEqual(row["elapsed_s"], 3000)
        self.assertEqual(row["avg_mbps"], round(40 * FRAME * 8 / 3000 / 1e6, 1))
        self.assertIsNone(row["recent_mbps"])  # one sample is no rate yet
        self.assertEqual(row["eta"], (START + timedelta(minutes=125)).isoformat())
        self.assertFalse(row["eta_after_cutoff"])
        self.assertEqual(row["state"], "uploading")
        self.assertEqual(row["path"], "vpn")
        self.assertEqual(row["started_at"], START.isoformat())

    def test_recent_rate_uses_the_last_five_minutes(self):
        write_plan(self.folder)
        add_frames(self.folder, 10)
        t0 = START + timedelta(minutes=20)
        self.watcher.tick(t0)
        add_frames(self.folder, 10, start=10)
        self.watcher.tick(t0 + timedelta(minutes=5))
        [row] = self.watcher.snapshot()
        # 10 frames in 300 s, which is faster than the 20 frames in 25 min average.
        self.assertEqual(row["recent_mbps"], round(10 * FRAME * 8 / 300 / 1e6, 1))
        self.assertGreater(row["recent_mbps"], row["avg_mbps"])
        # 80 frames left at 10 frames per 5 minutes: 40 minutes.
        self.assertEqual(row["eta"], (t0 + timedelta(minutes=45)).isoformat())

    def test_a_resumed_run_counts_only_what_it_sends(self):
        write_plan(self.folder, total_files=60, total_bytes=60 * FRAME,
                   already_on_nas_files=40, already_on_nas_bytes=40 * FRAME)
        add_frames(self.folder, 55)  # 40 from yesterday, 15 this run
        self.watcher.tick(START + timedelta(minutes=10))
        [row] = self.watcher.snapshot()
        self.assertEqual((row["files_done"], row["files_total"]), (15, 60))
        self.assertEqual(row["percent"], 25.0)

    def test_in_flight_bytes_count_towards_bytes_not_files(self):
        write_plan(self.folder)
        add_frames(self.folder, 1)
        add_partial(self.folder, FRAME // 2)
        self.watcher.tick(START + timedelta(minutes=1))
        [row] = self.watcher.snapshot()
        self.assertEqual(row["files_done"], 1)
        self.assertEqual(row["bytes_done"], FRAME + FRAME // 2)

    def test_finish_after_the_cutoff_is_flagged(self):
        write_plan(self.folder, window_end=(START + timedelta(hours=1)).isoformat())
        add_frames(self.folder, 10)
        self.watcher.tick(START + timedelta(minutes=30))  # 10 % in 30 min: 4.5 h to go
        [row] = self.watcher.snapshot()
        self.assertTrue(row["eta_after_cutoff"])


class TestTransitions(FolderCase):
    def test_finished_records_one_event_and_the_card_goes(self):
        write_plan(self.folder)
        add_frames(self.folder, 60)
        self.watcher.tick(START + timedelta(minutes=30))
        self.assertEqual(len(self.watcher.snapshot()), 1)

        add_frames(self.folder, 40, start=60)
        manifest = self.folder / up.MANIFEST_NAME
        manifest.write_text("{}")
        done_at = START + timedelta(minutes=80)
        os.utime(manifest, (done_at.timestamp(), done_at.timestamp()))
        for minutes in (81, 82, 83):
            self.watcher.tick(START + timedelta(minutes=minutes))
        self.assertEqual(self.watcher.snapshot(), [])
        self.assertEqual(len(self.recorded), 1, "one finished event per night")
        ev = self.recorded[0]
        self.assertEqual(ev["type"], "upload_finished")
        self.assertEqual(ev["key"], f"upload_finished:{NIGHT}")
        self.assertEqual(ev["night"], NIGHT)
        self.assertEqual(ev["title"], "Upload finished: 100 files, 6.4 GB")
        self.assertEqual(ev["data"]["avg_mbps"], round(100 * FRAME * 8 / 4800 / 1e6, 1))
        self.assertIn("1 h 20 min", ev["detail"])
        self.assertIn("over vpn", ev["detail"])

    def test_a_restart_does_not_announce_the_same_finish_again(self):
        write_plan(self.folder)
        (self.folder / up.MANIFEST_NAME).write_text("{}")
        now = datetime.now(timezone.utc)
        self.watcher.tick(now)
        fresh = up.UploadWatcher(self.incoming, record=lambda **f: self.recorded.append(f),
                                 already_recorded=lambda key: any(r["key"] == key for r in self.recorded),
                                 night_of=lambda dt: "derived")
        fresh.tick(now)
        self.assertEqual(len(self.recorded), 1)

    def test_an_old_manifest_is_history_not_news(self):
        write_plan(self.folder)
        manifest = self.folder / up.MANIFEST_NAME
        manifest.write_text("{}")
        old = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
        os.utime(manifest, (old, old))
        self.watcher.tick()
        self.assertEqual(self.recorded, [])

    def test_paused_at_the_cutoff_then_back_with_the_next_run(self):
        write_plan(self.folder)
        add_frames(self.folder, 70)
        cutoff = START + timedelta(hours=9)
        self.watcher.tick(cutoff + timedelta(minutes=1))
        [row] = self.watcher.snapshot()
        self.assertEqual(row["state"], "paused")
        self.assertIsNone(row["eta"])
        self.assertEqual(self.recorded, [])

        next_start = START + timedelta(days=1)
        write_plan(self.folder, started_at=next_start.isoformat(),
                   window_end=(next_start + timedelta(hours=9)).isoformat(),
                   total_files=30, total_bytes=30 * FRAME,
                   already_on_nas_files=70, already_on_nas_bytes=70 * FRAME)
        add_frames(self.folder, 3, start=70)
        self.watcher.tick(next_start + timedelta(minutes=5))
        [row] = self.watcher.snapshot()
        self.assertEqual(row["state"], "uploading")
        self.assertEqual((row["files_done"], row["files_total"]), (3, 30))
        self.assertEqual(row["started_at"], next_start.isoformat())

    def test_a_night_given_up_on_drops_off_after_a_day(self):
        write_plan(self.folder)
        self.watcher.tick(START + timedelta(hours=9 + 25))
        self.assertEqual(self.watcher.snapshot(), [])

    def test_a_missing_incoming_folder_shows_nothing(self):
        watcher = up.UploadWatcher(self.incoming / "nope", record=self.fail,
                                   already_recorded=lambda k: False, night_of=lambda d: d)
        watcher.tick()
        self.assertEqual(watcher.snapshot(), [])

    def test_a_folder_without_a_plan_or_with_a_broken_one_is_ignored(self):
        add_frames(self.incoming / "2026-11-03", 5)
        (self.folder).mkdir()
        (self.folder / up.PLAN_NAME).write_text("{not json")
        self.watcher.tick(START)
        self.assertEqual(self.watcher.snapshot(), [])


class TestWiring(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        patcher = mock.patch.dict(os.environ, {
            "ACP_ACTIVITY_DB": str(self.td / "activity.sqlite"),
            "ACP_ACTIVITY_TZ": "Australia/Sydney",
            "ACP_HOME": str(self.td / "home"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ACP_API_TOKEN", None)
        activity.close()
        self.addCleanup(activity.close)
        self.addCleanup(app_module.stop_upload_watcher)

    def test_settings_default_to_the_live_folder_and_a_minute(self):
        for var in ("ACP_UPLOAD_INCOMING", "ACP_UPLOAD_POLL_SECONDS"):
            os.environ.pop(var, None)
        self.assertEqual(acp_settings.effective("upload_incoming"), acp_settings.DEFAULT_UPLOAD_INCOMING)
        self.assertEqual(acp_settings.effective("upload_poll_seconds"), 60)
        os.environ["ACP_UPLOAD_POLL_SECONDS"] = "5"  # below the floor, so ignored
        self.assertEqual(acp_settings.effective("upload_poll_seconds"), 60)
        os.environ["ACP_UPLOAD_POLL_SECONDS"] = "120"
        self.assertEqual(acp_settings.effective("upload_poll_seconds"), 120)

    def test_the_watcher_is_off_when_the_setting_is_off(self):
        os.environ["ACP_UPLOAD_INCOMING"] = "off"
        self.assertIsNone(app_module._upload_incoming())
        with mock.patch.dict(os.environ, {"ACP_SKIP_STARTUP_MIGRATION": ""}):
            self.assertFalse(app_module.start_upload_watcher())
        self.assertIsNone(app_module._upload_thread)
        self.assertEqual(app_module.upload_snapshot(), [])

    def test_the_watcher_is_off_under_the_test_suite_by_default(self):
        os.environ["ACP_UPLOAD_INCOMING"] = str(self.td)
        self.assertFalse(app_module.start_upload_watcher())

    def test_the_summary_carries_uploads_and_the_finish_lands_in_activity(self):
        incoming = self.td / "incoming"
        folder = incoming / NIGHT
        now = datetime.now(timezone.utc)
        write_plan(folder, started_at=(now - timedelta(minutes=10)).isoformat(),
                   window_end=(now + timedelta(hours=5)).isoformat())
        add_frames(folder, 4)
        app_module._upload_watcher = app_module.make_upload_watcher(incoming)
        app_module._upload_watcher.tick()
        body = app.test_client().get("/api/activity/summary").get_json()
        [row] = body["uploads"]
        self.assertEqual(row["files_done"], 4)
        self.assertIn("now", body)

        (folder / up.MANIFEST_NAME).write_text("{}")
        app_module._upload_watcher.tick()
        app_module._upload_watcher = app_module.make_upload_watcher(incoming)  # a restart
        app_module._upload_watcher.tick()
        body = app.test_client().get("/api/activity/summary").get_json()
        self.assertEqual(body["uploads"], [])
        stored = activity.get_event("acp", f"upload_finished:{NIGHT}")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["type"], "upload_finished")
        self.assertEqual(stored["night"], NIGHT)
        nights = activity.list_nights(nights=5, before=None)["nights"]
        self.assertEqual(sum(1 for n in nights for e in n["events"] if e["type"] == "upload_finished"), 1)

    def test_the_folder_path_is_hidden_from_an_unauthorised_settings_view(self):
        view = app_module._settings_view(authorised=False)
        self.assertNotIn("value", view["keys"]["upload_incoming"])
        self.assertIn("value", app_module._settings_view(authorised=True)["keys"]["upload_incoming"])


if __name__ == "__main__":
    unittest.main()
