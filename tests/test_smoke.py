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

# Same for sites — never write to data/sites.json from the smoke test.
_tmp_sites = Path(tempfile.mkdtemp()) / "sites.json"
app_module.SITES_PATH = _tmp_sites
app_module._sites_cache = None
app_module._sites_cache_mtime = None

# Same for saved searches.
_tmp_saved = Path(tempfile.mkdtemp()) / "saved_searches.json"
app_module.SAVED_SEARCHES_PATH = _tmp_saved
app_module._saved_searches_cache = None
app_module._saved_searches_cache_mtime = None

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

# --- Gap finder ----------------------------------------------------------
r = c.get("/api/gaps")
print("GET /api/gaps (defaults)", r.status_code)
assert r.status_code in (200, 503)
if r.status_code == 200:
    body = r.get_json()
    for k in ("have_filter", "missing_filter", "have_sources", "missing_sources",
              "gap_sky_fraction", "candidates", "skipped"):
        assert k in body, f"missing {k} in gap response"
    assert body["have_filter"] == "Ha"
    assert body["missing_filter"] == "SII"

r = c.get("/api/gaps?have=Ha&missing=SII&sources=manifest&min_have_hours=0")
print("GET /api/gaps (sources=manifest, min_have=0)", r.status_code)
assert r.status_code in (200, 503)
if r.status_code == 200:
    body = r.get_json()
    if body["have_sources"]:
        assert body["gap_sky_fraction"] >= 0

r = c.get("/api/gaps?have=NotARealFilter&missing=SII")
print("GET /api/gaps (unknown filter)", r.status_code)
assert r.status_code in (200, 503)
if r.status_code == 200:
    body = r.get_json()
    assert body["gap_sky_fraction"] == 0.0
    assert body["candidates"] == []

r = c.get("/api/gaps/moc.fits")
print("GET /api/gaps/moc.fits", r.status_code)
assert r.status_code in (200, 404, 503), f"unexpected status {r.status_code}"
if r.status_code == 200:
    assert r.data[:8] == b"SIMPLE  ", r.data[:16]

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

# --- Catalog registry + CategorisedCatalogSource (Plan 3b/3c/3d) ---

# File-loaded entries should appear at /api/catalog-registry.
r = c.get("/api/catalog-registry")
print("GET /api/catalog-registry (file only)", r.status_code)
assert r.status_code == 200
reg = r.get_json()
file_ids = {e["id"] for e in reg["catalogues"]}
assert "green" in file_ids and "messier" in file_ids, file_ids

# Register a synthetic CategorisedCatalogSource and confirm it surfaces
# in both /api/catalog-registry and /api/catalogs without code changes.
class _FakeCatalogSource:
    def id(self): return "demo_pne"
    def metadata(self): return {
        "label": "Demo PNe",
        "color": "#ff66cc",
        "marker": "circle",
        "size": 11,
        "attribution": "Synthetic test catalogue",
        "enabled_default": False,
    }
    def categories(self): return ["PNe", "HII"]
    def objects(self):
        return [
            {"name": "PN_TEST_1", "ra_deg": 12.34, "dec_deg": -45.67, "category": "PNe"},
            {"name": "HII_TEST_1", "ra_deg": 56.78, "dec_deg": -12.34, "category": "HII"},
        ]

app.catalog_sources.append(_FakeCatalogSource())
try:
    r = c.get("/api/catalog-registry")
    print("GET /api/catalog-registry (with extension)", r.status_code)
    assert r.status_code == 200
    reg2 = r.get_json()
    demo = next((e for e in reg2["catalogues"] if e["id"] == "demo_pne"), None)
    assert demo is not None, reg2
    assert demo["label"] == "Demo PNe"
    assert demo["categories"] == ["PNe", "HII"]
    assert demo["data_key"] == "demo_pne"

    r = c.get("/api/catalogs")
    print("GET /api/catalogs (with extension)", r.status_code)
    assert r.status_code == 200
    cats = r.get_json()
    assert "demo_pne" in cats, list(cats.keys())[:5]
    assert len(cats["demo_pne"]) == 2
    assert cats["demo_pne"][0]["category"] == "PNe"
    print("CategorisedCatalogSource round-trip OK")
finally:
    app.catalog_sources.pop()

# --- Tile sources (Plan 4a) ---

# Baseline registry — may already contain user-installed extensions, so
# count what's there rather than asserting empty.
r = c.get("/api/tile-sources")
print("GET /api/tile-sources (baseline)", r.status_code)
assert r.status_code == 200
_baseline_tile_sources = len(r.get_json()["sources"])

# Register a synthetic PrioritisedTilesSource and round-trip both endpoints.
class _FakeTilesSource:
    def id(self): return "test_synthetic_tiles"  # unique to avoid colliding with any installed extension
    def metadata(self): return {
        "label": "Demo tile queue",
        "color": "#a070ff",
        "attribution": "Synthetic test source",
        "enabled_default": True,
    }
    def tiles(self):
        return [
            {
                "id": "t1", "ra_deg": 100.0, "dec_deg": -30.0,
                "footprint": [[99.5,-29.5],[100.5,-29.5],[100.5,-30.5],[99.5,-30.5]],
                "priority_level": 1, "score": 99.0,
                "per_band": {"Ha": {"covered": True, "source": "external"},
                             "SII": {"covered": False},
                             "OIII": {"covered": False}},
                "category_counts": {"PNe": 4, "SNR": 1},
            },
            {
                "id": "t2", "ra_deg": 200.0, "dec_deg": -40.0,
                "footprint": [[199.5,-39.5],[200.5,-39.5],[200.5,-40.5],[199.5,-40.5]],
                "priority_level": 3, "score": 12.0,
                "per_band": {"Ha": {"covered": True}, "SII": {"covered": True},
                             "OIII": {"covered": True}},
                "category_counts": {"HII": 2},
            },
        ]

app.tile_sources.append(_FakeTilesSource())
try:
    r = c.get("/api/tile-sources")
    print("GET /api/tile-sources (with extension)", r.status_code)
    assert r.status_code == 200
    summary = r.get_json()["sources"]
    assert len(summary) == _baseline_tile_sources + 1
    s = next((x for x in summary if x["id"] == "test_synthetic_tiles"), None)
    assert s is not None and s["n_tiles"] == 2
    assert s["max_priority_level"] == 3
    assert set(s["categories"]) == {"PNe", "SNR", "HII"}
    assert set(s["bands"]) == {"Ha", "SII", "OIII"}

    r = c.get("/api/tiles/test_synthetic_tiles")
    print("GET /api/tiles/test_synthetic_tiles", r.status_code)
    assert r.status_code == 200
    body = r.get_json()
    assert body["id"] == "test_synthetic_tiles" and len(body["tiles"]) == 2
    assert body["tiles"][0]["priority_level"] == 1

    # Completion helper sanity (used to drive opacity downstream).
    assert app_module._tile_completion(body["tiles"][0]) == 1/3
    assert app_module._tile_completion(body["tiles"][1]) == 1.0

    r = c.get("/api/tiles/does-not-exist")
    print("GET /api/tiles/does-not-exist", r.status_code)
    assert r.status_code == 404
    print("PrioritisedTilesSource round-trip OK")
finally:
    app.tile_sources.pop()

# --- Visibility (Plan A.3) ---

# Bin rules (sanity-check the helper directly)
assert app_module._bin_visibility(-5.0, 0.0, 30.0) == "not_visible"
assert app_module._bin_visibility(28.0, 5.0, 30.0) == "not_visible"  # peak < min
assert app_module._bin_visibility(35.0, 0.5, 30.0) == "partial"      # hours < 1
assert app_module._bin_visibility(40.0, 1.5, 30.0) == "fair"         # hours ≥ 1, peak < 45
assert app_module._bin_visibility(50.0, 1.5, 30.0) == "fair"         # peak ≥ 45 but hours < 2 (not good)
assert app_module._bin_visibility(50.0, 2.5, 30.0) == "good"         # peak ≥ 45, hours ≥ 2
assert app_module._bin_visibility(70.0, 4.0, 30.0) == "great"        # peak ≥ 60, hours ≥ 3
assert app_module._bin_visibility(70.0, 2.5, 30.0) == "good"         # great rule needs hours ≥ 3
print("visibility bin rules OK")

# Force-clear the cache so the smoke run actually computes against the demo
# manifest rather than reading a stale entry from a previous import.
app_module._visibility_cache.clear()
r = c.get("/api/visibility?lat=-33.87&lon=151.21&min_alt_deg=30")
print("GET /api/visibility (lat/lon)", r.status_code)
assert r.status_code == 200
vis = r.get_json()
assert vis["labels"] == ["not_visible", "partial", "fair", "good", "great"]
assert vis["site"]["lat"] == -33.87
assert isinstance(vis["targets"], dict) and vis["targets"], vis["targets"]
first_bins = next(iter(vis["targets"].values()))
assert len(first_bins) == 12
for b in first_bins:
    assert b["label"] in vis["labels"]
    assert "peak_alt_deg" in b and "hours_above_min" in b and "month" in b
    assert 1 <= b["month"] <= 12

# site_id resolution — first save a known site, then reference it.
r = c.post("/api/sites", json={"sites": [
    {"id": "sydney", "name": "Sydney", "lat": -33.87, "lon": 151.21, "elev_m": 20, "min_alt_deg": 30},
]})
assert r.status_code == 200
r = c.get("/api/visibility?site_id=sydney")
print("GET /api/visibility (site_id)", r.status_code)
assert r.status_code == 200
assert r.get_json()["site"]["id"] == "sydney"

r = c.get("/api/visibility?site_id=does-not-exist")
print("GET /api/visibility (bad site_id)", r.status_code)
assert r.status_code == 404

# /api/visibility/point — single arbitrary point
r = c.get("/api/visibility/point?lat=-33.87&lon=151.21&min_alt_deg=30&ra=210.5&dec=-60.0")
print("GET /api/visibility/point (lat/lon)", r.status_code)
assert r.status_code == 200
pt = r.get_json()
assert len(pt["months"]) == 12
assert pt["ra_deg"] == 210.5
for m in pt["months"]:
    assert m["label"] in pt["labels"]

r = c.get("/api/visibility/point?lat=-33.87&lon=151.21&min_alt_deg=30")
print("GET /api/visibility/point (no ra/dec)", r.status_code)
assert r.status_code == 400

# --- Sites ---

r = c.get("/api/sites")
print("GET /api/sites (defaults)", r.status_code)
assert r.status_code == 200
sites = r.get_json()["sites"]
assert any(s["id"] == "sydney" for s in sites), sites
assert all({"id", "name", "lat", "lon"}.issubset(s.keys()) for s in sites)

r = c.post("/api/sites", json={"sites": [
    {"id": "sydney", "name": "Sydney", "lat": -33.87, "lon": 151.21, "elev_m": 20, "min_alt_deg": 30},
    {"id": "remote", "name": "Remote rig", "lat": -34.5, "lon": 142.0, "min_alt_deg": 25},
]})
print("POST /api/sites", r.status_code)
assert r.status_code == 200
saved = r.get_json()["sites"]
assert len(saved) == 2 and saved[1]["id"] == "remote"

r = c.post("/api/sites", json={"sites": []})
print("POST /api/sites (empty)", r.status_code)
assert r.status_code == 400

r = c.post("/api/sites", json={"sites": [{"id": "bad", "name": "x", "lat": 999, "lon": 0}]})
print("POST /api/sites (bad lat)", r.status_code)
assert r.status_code == 400

r = c.post("/api/sites", json={"sites": [
    {"id": "dup", "name": "A", "lat": 0, "lon": 0},
    {"id": "dup", "name": "B", "lat": 1, "lon": 1},
]})
print("POST /api/sites (duplicate id)", r.status_code)
assert r.status_code == 400

# --- Saved Inventory searches (Plan 6) ---
r = c.get("/api/saved-searches")
print("GET /api/saved-searches (empty)", r.status_code)
assert r.status_code == 200 and r.get_json()["searches"] == []

r = c.post("/api/saved-searches", json={
    "name": "PNe needing OIII",
    "source_id": "demo_tiles",
    "filters": {"priorities": [1, 2], "missing": ["OIII"], "categories": ["PNe"], "hidePlanned": True},
})
print("POST /api/saved-searches", r.status_code)
assert r.status_code == 201
saved = r.get_json()
assert saved["id"] and saved["name"] == "PNe needing OIII"
assert saved["filters"]["hidePlanned"] is True
saved_id = saved["id"]

r = c.get("/api/saved-searches")
assert len(r.get_json()["searches"]) == 1

r = c.post("/api/saved-searches", json={"source_id": "x", "filters": {}})
print("POST /api/saved-searches (no name)", r.status_code)
assert r.status_code == 400

r = c.delete(f"/api/saved-searches/{saved_id}")
print("DELETE /api/saved-searches/<id>", r.status_code)
assert r.status_code == 204

r = c.delete(f"/api/saved-searches/{saved_id}")
print("DELETE /api/saved-searches/<id> (already gone)", r.status_code)
assert r.status_code == 404

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
    # profilePreference.json deliberately omitted — TS skips that import
    # step when the file is absent so user's existing prefs aren't clobbered.
    assert names == {"metadata.json", "exposureTemplates.json", "projects.json"}, names
    metadata = json.loads(zf.read("metadata.json"))
    for k in ("ExportDate", "TargetSchedulerVersion", "DatabaseVersion", "ExportedProfileName", "ExportedProfileId"):
        assert k in metadata, (k, metadata)
    projects = json.loads(zf.read("projects.json"))
    assert len(projects) == 1
    proj = projects[0]
    assert proj["Name"] == "Smoke Sync"
    assert proj["State"] == "Active"
    assert proj["Priority"] in ("Low", "Normal", "High")
    assert proj["MinimumAltitude"] == 35  # strictest of 25/35
    # sync-plan-a (1 panel) + sync-plan-b (2×2 = 4 panels) = 5 TS targets
    assert len(proj["Targets"]) == 5, [t["Name"] for t in proj["Targets"]]
    panel_names = [t["Name"] for t in proj["Targets"] if t["Name"].startswith("sync-plan-b")]
    assert set(panel_names) == {"sync-plan-b r1c1", "sync-plan-b r1c2", "sync-plan-b r2c1", "sync-plan-b r2c2"}, panel_names
    # Target Scheduler stores RA in HOURS
    assert abs(proj["Targets"][0]["RA"] - 161.26 / 15.0) < 1e-3
    assert proj["Targets"][0]["Epoch"] == 0   # J2000
    assert proj["Targets"][0]["Enabled"] is True
    # desired = ceil(4 * 3600 / 300) = 48
    ep0 = proj["Targets"][0]["ExposurePlans"][0]
    assert ep0["Desired"] == 48
    assert "ExposureTemplateId" in ep0 and isinstance(ep0["ExposureTemplateId"], int)
    templates = json.loads(zf.read("exposureTemplates.json"))
    assert templates and templates[0]["FilterName"]
    template_ids = {t["Id"] for t in templates}
    assert ep0["ExposureTemplateId"] in template_ids

print("ALL OK")
