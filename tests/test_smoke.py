"""Quick sanity check — exercises every endpoint via Flask's test client."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as app_module  # noqa: E402
from app import app  # noqa: E402

# Redirect plans persistence to a temp file so the smoke test doesn't clobber data/plans.json
_tmp_plans = Path(tempfile.mkdtemp()) / "plans.json"
app_module.PLANS_PATH = _tmp_plans
app_module._plans_cache = None
app_module._plans_cache_mtime = None

# Same redirection for the new target-overrides file.
_tmp_overrides = Path(tempfile.mkdtemp()) / "target_overrides.json"
app_module.TARGET_OVERRIDES_PATH = _tmp_overrides
app_module._target_overrides_cache = None
app_module._target_overrides_cache_mtime = None

c = app.test_client()

r = c.get("/")
print("GET /           ", r.status_code, len(r.data), "bytes")
assert r.status_code == 200

r = c.get("/api/manifest")
print("GET /api/manifest", r.status_code, len(r.data), "bytes")
assert r.status_code == 200
j = r.get_json()
assert "targets" in j and len(j["targets"]) >= 1

tid = j["targets"][0]["target_id"]
r = c.get(f"/api/target/{tid}")
print(f"GET /api/target/{tid}", r.status_code)
assert r.status_code == 200

r = c.get("/api/catalogs")
print("GET /api/catalogs", r.status_code)
assert r.status_code == 200

r = c.get("/api/sources")
print("GET /api/sources", r.status_code)
assert r.status_code == 200
sources = r.get_json()
assert isinstance(sources, list) and sources, "expected at least one registered source"
first = sources[0]
assert first["id"] == "manifest"
assert first["label"] == "Your archive"
for k in ("label", "color", "kind", "attribution", "enabled_default"):
    assert k in first, f"missing {k} in source metadata"
_baseline_source_count = len(sources)  # may include built-in MOC sources from data/surveys.json

# --- Friend manifest source (Option B: hand-construct + append to registry) ---
# Build a synthetic sanitised manifest, write it to a tempfile, register a
# JsonManifestSource pointing at it, and confirm it surfaces in /api/sources
# and yields polygons through coverage(). Pop it after to keep other tests
# (and the registry) untouched.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from sanitise_manifest import sanitise_dict  # noqa: E402

_friend_raw = {
    "scan_date": "2026-04-19T00:00:00",
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
_friend_data = sanitise_dict(_friend_raw, label="Dave")
_friend_dir = Path(tempfile.mkdtemp())
_friend_path = _friend_dir / "dave.json"
_friend_path.write_text(json.dumps(_friend_data), encoding="utf-8")

_friend_src = app_module.FriendManifestSource(
    source_id="friend_dave", label="Dave", color="", path=_friend_path,
)
app.coverage_sources.append(_friend_src)
try:
    r = c.get("/api/sources")
    print("GET /api/sources (with friend)", r.status_code)
    assert r.status_code == 200
    src_list = r.get_json()
    assert len(src_list) == _baseline_source_count + 1, src_list
    friend = next((s for s in src_list if s["id"] == "friend_dave"), None)
    assert friend is not None, src_list
    assert friend["label"] == "Dave"
    assert friend["kind"] == "friend"
    assert friend["color"] == ""  # frontend palette assigns
    assert friend["attribution"] == "Shared by Dave"
    # Coverage round-trip — polygons should reach the consumer.
    regions = list(_friend_src.coverage())
    assert len(regions) == 1, regions
    assert regions[0]["kind"] == "polygon"
    assert len(regions[0]["vertices"]) == 4
    assert regions[0]["filters"]["Ha"]["hours"] == 5.0
    print("friend manifest source OK")
finally:
    app.coverage_sources.pop()
    _friend_path.unlink(missing_ok=True)

r = c.get("/api/observability?lat=-33.87&lon=151.21")
print("GET /api/observability (valid)", r.status_code)
assert r.status_code == 200

r = c.get("/api/observability?lat=999&lon=151.21")
print("GET /api/observability (out-of-range lat ->clamped to default)", r.status_code)
assert r.status_code == 200

r = c.get("/api/observability?lat=abc&lon=151.21")
print("GET /api/observability (non-numeric lat ->clamped)", r.status_code)
assert r.status_code == 200

r = c.get("/api/observability?time=not-a-date")
print("GET /api/observability (bad time)", r.status_code)
assert r.status_code == 400

r = c.get("/api/export/priority")
print("GET /api/export/priority", r.status_code)
# 404 when data/catalogs.json hasn't been generated yet, 200 once it has.
# Either is fine; a 500 means a real bug (missing dep, etc).
assert r.status_code in (200, 404), f"unexpected status {r.status_code}"

# --- Planner endpoints ---

r = c.get("/api/gear")
print("GET /api/gear", r.status_code)
assert r.status_code == 200
gear = r.get_json()
assert gear["version"] == 2
assert gear["telescopes"] and gear["cameras"], "expected demo telescope and camera in data/gear.json"
tel0 = gear["telescopes"][0]
cam0 = gear["cameras"][0]
assert tel0["focal_length_mm"] > 0 and cam0["sensor_px"][0] > 0

# Redirect gear persistence to a tempfile before any POST /api/gear calls.
_tmp_gear = Path(tempfile.mkdtemp()) / "gear.json"
_tmp_gear.write_text(json.dumps({"version": 2, "telescopes": gear["telescopes"], "cameras": gear["cameras"]}))
app_module.GEAR_PATH = _tmp_gear
app_module._gear_cache = None
app_module._gear_cache_mtime = None

# POST /api/gear should persist a template mapping round-trip.
updated_cameras = json.loads(json.dumps(gear["cameras"]))
updated_cameras[0]["filters"]["Ha"]["ts_template_name"] = "Ha 300s"
r = c.post("/api/gear", json={"telescopes": gear["telescopes"], "cameras": updated_cameras})
print("POST /api/gear", r.status_code)
assert r.status_code == 200 and r.get_json()["ok"] is True
# Reload — mapping should survive.
r = c.get("/api/gear")
assert r.get_json()["cameras"][0]["filters"]["Ha"]["ts_template_name"] == "Ha 300s"

r = c.post("/api/gear/seed")
print("POST /api/gear/seed", r.status_code)
assert r.status_code == 200
seed = r.get_json()
assert seed["ok"] is True
assert isinstance(seed["added_telescopes"], list)
assert isinstance(seed["added_cameras"], list)
# Second call must be a no-op — everything the manifest offers is now present.
r2 = c.post("/api/gear/seed")
assert r2.status_code == 200 and r2.get_json()["added_telescopes"] == []

r = c.get("/api/plans")
print("GET /api/plans (empty)", r.status_code)
assert r.status_code == 200
assert r.get_json()["plans"] == []

new_plan = {
    "id": "test-plan-1",
    "project_name": "Smoke Test",
    "target": {"name": "Test", "target_id": None,
               "center_ra_deg": 161.26, "center_dec_deg": -59.68, "rotation_deg": 0,
               "mosaic": {"rows": 1, "cols": 1, "overlap_pct": 0}},
    "telescope_id": "redcat51",
    "camera_id": "zwo-asi2600mm-pro",
    "filter_goals": {"Ha": {"target_hours": 5, "sub_exposure_s": 300}},
    "priority": "normal",
    "min_altitude_deg": 30,
    "meridian_window_min": 0,
    "state": "draft",
}
r = c.post("/api/plans", json=new_plan)
print("POST /api/plans", r.status_code)
assert r.status_code == 201
created = r.get_json()
assert created["guid"] and created["created_at"] and created["updated_at"]

r = c.get(f"/api/plans/{new_plan['id']}")
print(f"GET /api/plans/{new_plan['id']}", r.status_code)
assert r.status_code == 200

updated = {**new_plan, "priority": "high"}
r = c.put(f"/api/plans/{new_plan['id']}", json=updated)
print(f"PUT /api/plans/{new_plan['id']}", r.status_code)
assert r.status_code == 200
assert r.get_json()["priority"] == "high"

r = c.delete(f"/api/plans/{new_plan['id']}")
print(f"DELETE /api/plans/{new_plan['id']}", r.status_code)
assert r.status_code == 204

r = c.get(f"/api/plans/{new_plan['id']}")
print(f"GET /api/plans/{new_plan['id']} (after delete)", r.status_code)
assert r.status_code == 404

# --- Target overrides ---

r = c.get("/api/target-overrides")
print("GET /api/target-overrides (empty)", r.status_code)
assert r.status_code == 200 and r.get_json()["overrides"] == {}

r = c.post("/api/target-overrides", json={"target_id": 7, "finished": True})
print("POST /api/target-overrides (mark finished)", r.status_code)
assert r.status_code == 200
assert r.get_json()["overrides"]["7"]["finished"] is True

r = c.post("/api/target-overrides", json={"target_id": 7, "finished": None})
print("POST /api/target-overrides (clear)", r.status_code)
assert r.status_code == 200 and "7" not in r.get_json()["overrides"]

r = c.post("/api/target-overrides", json={})
print("POST /api/target-overrides (missing target_id)", r.status_code)
assert r.status_code == 400

r = c.get("/api/ts-templates")
print("GET /api/ts-templates", r.status_code)
assert r.status_code == 200
# available may be True or False depending on whether the user has TS installed — both are valid
j = r.get_json()
assert "available" in j and "templates" in j

# --- Sync (zip export) ---
# No plans yet → expect 400
r = c.post("/api/sync")
print("POST /api/sync (empty)", r.status_code)
assert r.status_code == 400

# Create two plans in the same project with different min-altitudes → warning
# One of them is a 2x2 mosaic to exercise the per-panel expansion.
for pl_id, min_alt, mosaic in [
    ("sync-plan-a", 25, {"rows": 1, "cols": 1, "overlap_pct": 0}),
    ("sync-plan-b", 35, {"rows": 2, "cols": 2, "overlap_pct": 20}),
]:
    c.post("/api/plans", json={
        "id": pl_id,
        "project_name": "Smoke Sync",
        "target": {"name": pl_id, "target_id": None,
                   "center_ra_deg": 161.26, "center_dec_deg": -59.68, "rotation_deg": 0,
                   "mosaic": mosaic},
        "telescope_id": "redcat51",
        "camera_id": "zwo-asi2600mm-pro",
        "filter_goals": {"Ha": {"target_hours": 4, "sub_exposure_s": 300}},
        "priority": "normal",
        "min_altitude_deg": min_alt,
        "meridian_window_min": 0,
        "state": "draft",
    })

# Redirect ZIP_OUTPUT_DIR to tempdir so we don't pollute data/exports
app_module.ZIP_OUTPUT_DIR = Path(tempfile.mkdtemp())

r = c.post("/api/sync")
print("POST /api/sync (2 plans incl. 2×2 mosaic, 1 project)", r.status_code)
assert r.status_code == 200
body = r.get_json()
assert body["ok"] is True
assert body["plan_count"] == 2
assert body["project_count"] == 1
assert body["template_count"] == 1
assert Path(body["zip_path"]).exists(), f"zip not written at {body['zip_path']}"
assert any(w["kind"] == "min_altitude" for w in body["warnings"]), body["warnings"]

# Verify zip contents structurally — the mosaic plan should expand to 4 panels.
import zipfile as _zf
with _zf.ZipFile(body["zip_path"]) as zf:
    names = set(zf.namelist())
    assert names == {"metadata.json", "profilePreference.json", "exposureTemplates.json", "projects.json"}, names
    projects = json.loads(zf.read("projects.json"))
    assert len(projects) == 1
    proj = projects[0]
    assert proj["name"] == "Smoke Sync"
    assert proj["minimumAltitude"] == 35  # strictest of 25/35
    # sync-plan-a (1 panel) + sync-plan-b (2×2 = 4 panels) = 5 TS targets
    assert len(proj["targets"]) == 5, [t["name"] for t in proj["targets"]]
    panel_names = [t["name"] for t in proj["targets"] if t["name"].startswith("sync-plan-b")]
    assert set(panel_names) == {"sync-plan-b r1c1", "sync-plan-b r1c2", "sync-plan-b r2c1", "sync-plan-b r2c2"}, panel_names
    # Target Scheduler stores RA in HOURS
    assert abs(proj["targets"][0]["ra"] - 161.26 / 15.0) < 1e-3
    # desired = ceil(4 * 3600 / 300) = 48
    assert proj["targets"][0]["exposurePlans"][0]["desired"] == 48

print("ALL OK")
