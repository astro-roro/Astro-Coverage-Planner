"""End-to-end tests for the ACP_FRIEND_MANIFESTS env-var loader path.

Complements tests/test_smoke.py's friend-source check, which hand-constructs a
JsonManifestSource and appends it directly to the registry. This file goes
through the real loader: write a sanitised JSON file to disk, set the env var,
reload the app module, and assert the source surfaces with the right metadata.
Also exercises the tripwire validator and the JSON-decode/path-not-found
fall-through, both of which the smoke test bypasses.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from sanitise_manifest import sanitise_dict  # noqa: E402


class _LogCapture(logging.Handler):
    """Tiny stand-in for pytest's caplog — collects records on the root logger."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def warnings(self) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= logging.WARNING]


def _raw_manifest() -> dict:
    return {
        "scan_date": "2026-04-19T00:00:00",
        "total_targets": 1,
        "total_integration_hours": 5.0,
        "targets": [{
            "target_id": 99,
            "objects": ["Synthetic Nebula"],
            "center_ra_deg": 161.26, "center_dec_deg": -59.68,
            "center_l_deg": 287.6, "center_b_deg": -0.63,
            "fov_arcmin": [60, 45], "pix_arcsec": 1.0,
            "corners_icrs": [[160, -60], [160, -59], [162, -59], [162, -60]],
            "corners_galactic": [],
            "telescopes": ["AP110 GTX"],
            "filters": {"Ha": {"total_hours": 5.0, "files": 20}},
        }],
    }


def _reload_app_with_env(env_value: str | None) -> tuple[object, _LogCapture]:
    """Set ACP_FRIEND_MANIFESTS, reload the app module, return (module, capture)."""
    if env_value is None:
        os.environ.pop("ACP_FRIEND_MANIFESTS", None)
    else:
        os.environ["ACP_FRIEND_MANIFESTS"] = env_value

    cap = _LogCapture()
    root = logging.getLogger()
    root.addHandler(cap)
    prev_level = root.level
    root.setLevel(logging.WARNING)
    try:
        if "app" in sys.modules:
            mod = importlib.reload(sys.modules["app"])
        else:
            import app as mod  # noqa: F401
            mod = sys.modules["app"]
    finally:
        root.removeHandler(cap)
        root.setLevel(prev_level)
    return mod, cap


def _friend_sources(mod) -> list:
    return [s for s in mod.app.coverage_sources if s.metadata()["kind"] == "friend"]


def test_env_var_round_trip() -> None:
    sanitised = sanitise_dict(_raw_manifest(), label="Dave")
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "dave.json"
    path.write_text(json.dumps(sanitised), encoding="utf-8")

    mod, cap = _reload_app_with_env(str(path))
    friends = _friend_sources(mod)
    assert len(friends) == 1, [s.id() for s in mod.app.coverage_sources]
    friend = friends[0]
    meta = friend.metadata()
    assert meta["kind"] == "friend"
    assert meta["attribution"] == "Shared by Dave"
    assert friend.id() == "friend_dave"

    regions = list(friend.coverage())
    assert len(regions) == 1
    assert regions[0]["kind"] == "polygon"
    assert len(regions[0]["vertices"]) == 4
    assert regions[0]["filters"]["Ha"]["hours"] == 5.0
    assert not cap.warnings, cap.warnings
    print("test_env_var_round_trip OK")


def test_tripwire_rejects_unsanitised() -> None:
    """Manifest without 'sanitised: true' must be rejected and logged."""
    payload = _raw_manifest()  # raw, no sanitised flag
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "raw.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    mod, cap = _reload_app_with_env(str(path))
    assert _friend_sources(mod) == [], [s.id() for s in mod.app.coverage_sources]
    assert any("sanitised" in w for w in cap.warnings), cap.warnings
    print("test_tripwire_rejects_unsanitised OK")


def test_malformed_json_is_skipped() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")

    mod, cap = _reload_app_with_env(str(path))
    assert _friend_sources(mod) == [], [s.id() for s in mod.app.coverage_sources]
    assert any(str(path) in w for w in cap.warnings), cap.warnings
    print("test_malformed_json_is_skipped OK")


def test_missing_path_is_skipped() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "does_not_exist.json"  # never created
    mod, cap = _reload_app_with_env(str(path))
    assert _friend_sources(mod) == []
    assert any("path not found" in w for w in cap.warnings), cap.warnings
    print("test_missing_path_is_skipped OK")


def test_source_id_sanitiser() -> None:
    """Filename with characters outside [a-zA-Z0-9_-] gets coerced to underscores."""
    sanitised = sanitise_dict(_raw_manifest(), label="Friend 2026")
    tmp = Path(tempfile.mkdtemp())
    # Pick a filename with a character that survives Windows but isn't alnum/_/-.
    # A space and a dot in the stem both qualify; use a space which is safe everywhere.
    path = tmp / "friend share 2026.json"
    path.write_text(json.dumps(sanitised), encoding="utf-8")

    mod, _cap = _reload_app_with_env(str(path))
    friends = _friend_sources(mod)
    assert len(friends) == 1
    sid = friends[0].id()
    # All characters must be in the canonical safe set; the space in the stem
    # should have been replaced.
    assert all(c.isalnum() or c in "_-" for c in sid), sid
    assert sid.startswith("friend_"), sid
    assert " " not in sid, sid
    print(f"test_source_id_sanitiser OK (id={sid!r})")


def test_two_friends_via_semicolon() -> None:
    """Both load when env var lists two valid sanitised manifests."""
    s1 = sanitise_dict(_raw_manifest(), label="Dave")
    s2 = sanitise_dict(_raw_manifest(), label="Sara")
    tmp = Path(tempfile.mkdtemp())
    p1 = tmp / "dave.json"
    p2 = tmp / "sara.json"
    p1.write_text(json.dumps(s1), encoding="utf-8")
    p2.write_text(json.dumps(s2), encoding="utf-8")

    mod, cap = _reload_app_with_env(f"{p1};{p2}")
    friends = _friend_sources(mod)
    assert len(friends) == 2, [s.id() for s in mod.app.coverage_sources]
    labels = [s.metadata()["label"] for s in friends]
    assert labels == ["Dave", "Sara"], labels
    assert not cap.warnings, cap.warnings
    print("test_two_friends_via_semicolon OK")


if __name__ == "__main__":
    try:
        test_env_var_round_trip()
        test_tripwire_rejects_unsanitised()
        test_malformed_json_is_skipped()
        test_missing_path_is_skipped()
        test_source_id_sanitiser()
        test_two_friends_via_semicolon()
    finally:
        os.environ.pop("ACP_FRIEND_MANIFESTS", None)
    print("ALL OK")
