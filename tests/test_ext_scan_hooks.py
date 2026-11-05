"""H5: hooks called after each clean archive scan."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
from ext_dummy import load_dummy  # noqa: E402

app = app_module.app


@pytest.fixture
def scans(monkeypatch):
    td = Path(tempfile.mkdtemp())
    manifest = td / "manifest.json"
    manifest.write_text(json.dumps({"targets": [{"target_id": 1}, {"target_id": 2}]}), encoding="utf-8")
    monkeypatch.setattr(app_module, "MANIFEST_PATH", manifest)
    real = app_module._run_scan_subprocess
    saved_hooks = list(app.scan_hooks)
    dummy = load_dummy("scan_summary")
    dummy.received.clear()
    yield dummy
    app_module._run_scan_subprocess = real
    app.scan_hooks[:] = saved_hooks
    with app_module.SCAN_STATE_LOCK:
        app_module._scan_state.update({"running": False, "last_start": None, "last_finish": None,
                                       "last_exit_code": None, "last_trigger": None,
                                       "last_error": None})


def _host_with(dummy):
    host = Flask("host")
    host.acp_extension_api = 1
    host.acp_extension_hooks = {"scan_hooks"}
    host.scan_hooks = app.scan_hooks
    dummy.register(host)
    return host


def test_the_hook_is_offered():
    assert isinstance(app.scan_hooks, list) and "scan_hooks" in app.acp_extension_hooks


def test_a_clean_scan_calls_the_hook_with_a_summary(scans):
    host = _host_with(scans)
    app_module._run_scan_subprocess = lambda args: 0
    assert app_module.run_scan_now("test")
    assert len(scans.received) == 1
    s = scans.received[0]
    assert s["exit_code"] == 0 and s["trigger"] == "test" and s["targets"] == 2
    assert s["started_at"] and s["finished_at"]
    assert host.test_client().get("/api/ext/scan-summary/last").get_json()["targets"] == 2


def test_a_failed_scan_calls_no_hook(scans):
    _host_with(scans)
    app_module._run_scan_subprocess = lambda args: 1
    app_module.run_scan_now("test")
    assert scans.received == []


def test_a_hook_that_raises_does_not_stop_the_others_or_the_scan(scans):
    def boom(summary):
        summary["mutated"] = True
        raise RuntimeError("broken hook")
    app.scan_hooks.append(boom)
    _host_with(scans)
    app_module._run_scan_subprocess = lambda args: 0
    assert app_module.run_scan_now("test")
    assert len(scans.received) == 1 and "mutated" not in scans.received[0]
    with app_module.SCAN_STATE_LOCK:
        assert app_module._scan_state["last_exit_code"] == 0
        assert app_module._scan_state["running"] is False
