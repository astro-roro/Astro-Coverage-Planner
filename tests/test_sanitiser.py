"""Imperative tests for scripts/sanitise_manifest.py — matches test_smoke.py style."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from sanitise_manifest import (  # noqa: E402
    sanitise_dict,
    validate_no_paths,
    _aperture_class,
)


def _make_input() -> dict:
    """Synthetic manifest with a bunch of leak-shaped fields to strip."""
    return {
        "scan_date": "2026-04-19T22:12:02.123456",
        "scan_duration_sec": 87.5,
        "scan_roots": ["Z:/Astro/Images", "/mnt/nas/archive"],
        "total_files_scanned": 12345,
        "total_targets": 2,
        "total_integration_hours": 9.7,
        "integrity_flags": {"masters_missing_wcs": ["Z:/some/master.fit"]},
        "targets": [
            {
                "target_id": 7,
                "objects": ["M 42", "Orion Nebula"],
                "center_ra_deg": 83.82,
                "center_dec_deg": -5.39,
                "center_l_deg": 209.01,
                "center_b_deg": -19.38,
                "fov_arcmin": [120.0, 90.0],
                "pix_arcsec": 1.5,
                "corners_icrs": [[83, -6], [83, -5], [84, -5], [84, -6]],
                "corners_galactic": [[209, -20], [209, -19], [210, -19], [210, -20]],
                "telescopes": ["AP110 GTX (sn:1234)"],
                "date_range": ["2025-01-01", "2025-12-31"],
                "filters": {
                    "Ha": {"total_hours": 3.27, "files": 12,
                           "paths": ["Z:/Astro/M42_Ha.fit"],
                           "sub_folders": ["Z:/Astro/M42/lights/Ha"]},
                    "OIII": {"total_hours": 1.42, "files": 5,
                             "paths": ["Z:/Astro/M42_OIII.xisf"]},
                },
                "master_files": ["Z:/Astro/masters/M42_Ha_master.fit"],
            },
            {
                "target_id": 13,
                "objects": ["Eta Carinae"],
                "center_ra_deg": 161.26,
                "center_dec_deg": -59.68,
                "center_l_deg": 287.6,
                "center_b_deg": -0.63,
                "fov_arcmin": [60.0, 45.0],
                "pix_arcsec": 0.9,
                "corners_icrs": [[160, -60], [160, -59], [162, -59], [162, -60]],
                "corners_galactic": [[287, -1], [287, 0], [288, 0], [288, -1]],
                "telescopes": ["EdgeHD 9.25\"", "190 MakNewt"],
                "filters": {"Ha": {"total_hours": 5.0, "files": 20}},
            },
        ],
    }


def test_round_trip_essentials():
    src = _make_input()
    out = sanitise_dict(src, label="Dave")

    assert out["sanitised"] is True, "missing sanitised flag"
    assert out["friend_label"] == "Dave"
    assert out["scan_date"] == "2026-04-01", out["scan_date"]
    assert out["total_targets"] == 2
    assert out["total_integration_hours"] == 9.7
    assert len(out["targets"]) == 2

    t0 = out["targets"][0]
    # Essentials preserved
    assert t0["center_ra_deg"] == 83.82
    assert t0["center_dec_deg"] == -5.39
    assert t0["objects"] == ["M 42", "Orion Nebula"]
    assert len(t0["corners_icrs"]) == 4
    # Stripped fields gone
    assert "master_files" not in t0
    assert "date_range" not in t0
    assert "paths" not in t0["filters"]["Ha"]
    assert "sub_folders" not in t0["filters"]["Ha"]
    # Filter rebuilt with only the two allowed keys
    assert set(t0["filters"]["Ha"].keys()) == {"total_hours", "files"}
    # target_id regenerated and prefixed
    assert isinstance(t0["target_id"], str) and t0["target_id"].startswith("f_")
    assert t0["target_id"] != 7
    # Top-level junk dropped
    for dropped in ("scan_roots", "scan_duration_sec", "integrity_flags",
                    "total_files_scanned"):
        assert dropped not in out, f"{dropped} should have been dropped"
    print("test_round_trip_essentials OK")


def test_path_leak_detection():
    bad = {"scan_date": "2026-04-19T00:00:00", "targets": []}
    out = sanitise_dict(bad)
    # Inject a leak directly into the output and verify the validator catches it.
    out["description"] = "C:\\Users\\Foo\\file.fits"
    raised = False
    try:
        validate_no_paths(out)
    except RuntimeError as e:
        raised = True
        msg = str(e)
        assert "description" in msg, msg
    assert raised, "validate_no_paths should have raised on a Windows path"

    # Also: nested leak inside a list
    out2 = {"targets": [{"objects": ["M 42"], "corners_icrs": ["/home/me/img.fit"]}]}
    raised = False
    try:
        validate_no_paths(out2)
    except RuntimeError as e:
        raised = True
        assert "corners_icrs" in str(e)
    assert raised, "nested leak not caught"
    print("test_path_leak_detection OK")


def test_telescope_downgrade():
    cases = {
        "AP110 GTX": "110mm refractor",
        "EdgeHD 9.25\"": "235mm telescope",
        "190 MakNewt": "190mm reflector",
        "no aperture rig name": "telescope",
    }
    for inp, expected in cases.items():
        got = _aperture_class(inp)
        assert got == expected, f"{inp!r} → {got!r}, expected {expected!r}"
    print("test_telescope_downgrade OK")


def test_hours_rounding():
    src = {
        "scan_date": "2026-04-19T00:00:00",
        "targets": [{
            "target_id": 1,
            "objects": ["M 42"],
            "center_ra_deg": 83.0, "center_dec_deg": -5.0,
            "center_l_deg": 209.0, "center_b_deg": -19.0,
            "corners_icrs": [], "corners_galactic": [],
            "fov_arcmin": [60, 45], "pix_arcsec": 1.0,
            "telescopes": ["RedCat 51"],
            "filters": {"Ha": {"total_hours": 3.27, "files": 12}},
        }],
    }
    out = sanitise_dict(src)
    assert out["targets"][0]["filters"]["Ha"]["total_hours"] == 3.3
    print("test_hours_rounding OK")


def test_object_name_suspicious_value():
    src = {
        "scan_date": "2026-04-19T00:00:00",
        "targets": [{
            "target_id": 1,
            "objects": ["M 42", "C:\\image.fits", "/home/me/x.xisf", "Eta Carinae"],
            "center_ra_deg": 83.0, "center_dec_deg": -5.0,
            "center_l_deg": 209.0, "center_b_deg": -19.0,
            "corners_icrs": [], "corners_galactic": [],
            "fov_arcmin": [60, 45], "pix_arcsec": 1.0,
            "telescopes": ["RedCat 51"],
            "filters": {},
        }],
    }
    out = sanitise_dict(src)
    objs = out["targets"][0]["objects"]
    assert objs[0] == "M 42"
    assert objs[1] == "unknown", objs
    assert objs[2] == "unknown", objs
    assert objs[3] == "Eta Carinae"
    # And the validator should still pass (we replaced the leaks)
    validate_no_paths(out)
    print("test_object_name_suspicious_value OK")


def test_sanitised_flag_and_label():
    out_unlabelled = sanitise_dict({"scan_date": "2026-04-19T00:00:00", "targets": []})
    assert out_unlabelled["sanitised"] is True
    assert out_unlabelled["friend_label"] == ""

    out_labelled = sanitise_dict({"scan_date": "2026-04-19T00:00:00", "targets": []}, label="Dave")
    assert out_labelled["friend_label"] == "Dave"
    print("test_sanitised_flag_and_label OK")


def test_full_synthetic_validates():
    """End-to-end: full input passes the validator after sanitise_dict."""
    out = sanitise_dict(_make_input(), label="Friend")
    validate_no_paths(out)  # should not raise
    print("test_full_synthetic_validates OK")


if __name__ == "__main__":
    test_round_trip_essentials()
    test_path_leak_detection()
    test_telescope_downgrade()
    test_hours_rounding()
    test_object_name_suspicious_value()
    test_sanitised_flag_and_label()
    test_full_synthetic_validates()
    print("ALL OK")
