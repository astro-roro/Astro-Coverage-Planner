"""H3: scheduled jobs extensions register, run by one core thread."""
from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import scheduled_jobs  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402

app = app_module.app


@pytest.fixture
def registered(monkeypatch):
    monkeypatch.delenv("ACP_DISABLE_JOBS", raising=False)
    saved = list(app.scheduled_jobs)
    dummy = load_dummy("nightly_tidy")
    dummy.counts.clear()
    dummy.register(app)
    yield dummy
    app_module.stop_jobs_scheduler()
    app.scheduled_jobs[:] = saved


def test_the_hook_is_offered_and_no_jobs_means_no_thread():
    assert "scheduled_jobs" in app.acp_extension_hooks
    assert app_module.scan_status()["jobs"] == []


def test_a_job_runs_when_due_and_not_before(registered):
    runner = scheduled_jobs.Runner(scheduled_jobs.valid_jobs(app.scheduled_jobs),
                                   datetime(2026, 1, 1, 9, 0))
    with mock.patch.object(app.plans_api, "read", return_value={"plans": [{"id": "a"}, {"id": "b", "filter_rows": [1]}]}):
        assert runner.run_due(datetime(2026, 1, 1, 11, 59)) == []
        assert runner.run_due(datetime(2026, 1, 1, 12, 0)) == ["nightly_tidy"]
        assert runner.run_due(datetime(2026, 1, 1, 12, 1)) == []
        assert runner.run_due(datetime(2026, 1, 2, 12, 0)) == ["nightly_tidy"]
    assert registered.counts == [1, 1]
    st = runner.status()[0]
    assert st["id"] == "nightly_tidy" and st["last_ok"] is True
    assert st["next_at"] == "2026-01-03T12:00:00"


def test_a_job_that_raises_is_logged_and_the_others_still_run():
    hits = []
    jobs = scheduled_jobs.valid_jobs([
        {"id": "bad", "cron": "* * * * *", "run": lambda: 1 / 0},
        {"id": "good", "cron": "* * * * *", "run": lambda: hits.append(1)},
    ])
    runner = scheduled_jobs.Runner(jobs, datetime(2026, 1, 1, 0, 0))
    assert runner.run_due(datetime(2026, 1, 1, 0, 5)) == ["bad", "good"]
    assert hits == [1]
    assert {s["id"]: s["last_ok"] for s in runner.status()} == {"bad": False, "good": True}


def test_bad_entries_are_skipped():
    jobs = scheduled_jobs.valid_jobs([
        {"id": "no cron", "cron": "whenever", "run": print},
        {"id": "ok", "cron": "0 * * * *", "run": print},
        {"id": "ok", "cron": "0 * * * *", "run": print},
        {"id": "not callable", "cron": "0 * * * *", "run": "print"},
        "not a dict",
    ])
    assert [j["id"] for j in jobs] == ["ok"]


def test_the_thread_starts_and_shows_in_scan_status(registered):
    assert app_module.start_jobs_scheduler()
    jobs = app.test_client().get("/api/scan/status").get_json()["jobs"]
    assert [j["id"] for j in jobs] == ["nightly_tidy"]
    assert set(jobs[0]) == {"id", "cron", "next_at", "last_run", "last_ok"}


def test_acp_disable_jobs_keeps_the_thread_off(registered, monkeypatch):
    monkeypatch.setenv("ACP_DISABLE_JOBS", "1")
    assert not app_module.start_jobs_scheduler()
    assert app_module.scan_status()["jobs"] == []


def test_the_loop_stops_when_asked():
    runner = scheduled_jobs.Runner([], datetime(2026, 1, 1))
    stop = threading.Event()
    t = threading.Thread(target=scheduled_jobs.loop, args=(runner, stop), kwargs={"poll_seconds": 0.01})
    t.start()
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()
