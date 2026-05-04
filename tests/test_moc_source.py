"""End-to-end tests for the MOC coverage source + /api/moc/<id> route.

All offline — no real network. Builds synthetic MOCs with mocpy for cache-hit
fixtures and monkey-patches the fetch helper for the failure paths.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import app as app_module  # noqa: E402
from app import app  # noqa: E402

c = app.test_client()


def _redirect_cache_dir() -> Path:
    """Move MOC_CACHE_DIR to a fresh tempdir and rebuild every existing
    MocCoverageSource so its private cache_dir tracks the new location."""
    cache_dir = Path(tempfile.mkdtemp())
    app_module.MOC_CACHE_DIR = cache_dir
    for src in app.coverage_sources:
        if isinstance(src, app_module.MocCoverageSource):
            src._cache_dir = cache_dir
    return cache_dir


# === 1. Hostname-allowlist rejection at construction time ============
try:
    app_module.MocCoverageSource(
        source_id="evil",
        label="Evil",
        color="",
        attribution="x",
        enabled_default=False,
        moc_url="https://evil.example.com/moc",
    )
    raise AssertionError("expected ValueError for non-allowlisted host")
except ValueError as e:
    print("hostname allowlist OK:", e)
    assert "allowlist" in str(e)

# === 2. HTTP scheme rejection ========================================
try:
    app_module.MocCoverageSource(
        source_id="plain",
        label="Plain",
        color="",
        attribution="x",
        enabled_default=False,
        moc_url="http://alasky.cds.unistra.fr/foo",
    )
    raise AssertionError("expected ValueError for http://")
except ValueError as e:
    print("https-only OK:", e)
    assert "https" in str(e)

# === 3. Build a synthetic MOC for cache-hit fixture ==================
from mocpy import MOC  # noqa: E402
import numpy as np  # noqa: E402
import astropy.units as u  # noqa: E402

_synth = MOC.from_polygon(
    np.array([0.0, 1.0, 1.0, 0.0]) * u.deg,
    np.array([0.0, 0.0, 1.0, 1.0]) * u.deg,
    max_depth=8,
)
_tmp_fits = Path(tempfile.mkdtemp()) / "synth.fits"
_synth.save(str(_tmp_fits), format="fits", overwrite=True)
SYNTH_BYTES = _tmp_fits.read_bytes()
assert len(SYNTH_BYTES) > 0
print(f"built synthetic MOC: {len(SYNTH_BYTES)} bytes")

# === 4. Register a known-good test source against the allowlist ======
_test_src = app_module.MocCoverageSource(
    source_id="acp_test_moc",
    label="ACP Test MOC",
    color="#888",
    attribution="synthetic",
    enabled_default=False,
    moc_url="https://alasky.cds.unistra.fr/test/Moc.fits",
)
app.coverage_sources.append(_test_src)

cache_dir = _redirect_cache_dir()


def _cleanup_cache() -> None:
    """Wipe per-test cache files so subsequent tests start clean."""
    for p in cache_dir.glob("acp_test_moc.*"):
        p.unlink(missing_ok=True)


def _seed_cache(fresh: bool = True) -> None:
    """Drop a known-good FITS + sidecar into the cache dir."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "acp_test_moc.fits").write_bytes(SYNTH_BYTES)
    fetched_at = (
        datetime.now(timezone.utc).isoformat() if fresh
        else "1990-01-01T00:00:00+00:00"
    )
    (cache_dir / "acp_test_moc.meta.json").write_text(json.dumps({
        "fetched_at": fetched_at,
        "content_sha256": "n/a",
        "url": _test_src.moc_url,
    }), encoding="utf-8")


# === 5. Cache hit serves bytes without any network ===================
_orig_fetch = app_module._fetch_moc_bytes


def _no_network(_url):
    raise AssertionError("network must not be used on cache hit")


app_module._fetch_moc_bytes = _no_network
try:
    _cleanup_cache()
    _seed_cache(fresh=True)
    r = c.get("/api/moc/acp_test_moc")
    print("GET /api/moc/acp_test_moc (cache hit)", r.status_code, len(r.data), "bytes")
    assert r.status_code == 200
    assert r.data == SYNTH_BYTES
    assert r.headers.get("Content-Disposition", "").startswith("inline; filename=")
    # Round-trip via mocpy as a final sanity check that what we served parses.
    MOC.from_fits(io.BytesIO(r.data))
    print("cache-hit served bytes parse as MOC OK")
finally:
    app_module._fetch_moc_bytes = _orig_fetch
    _cleanup_cache()


# === 6. Size-cap rejection — fake fetch returns >10 MB ===============
def _fake_oversize(_url):
    raise app_module._MocFetchError("response body exceeded cap")


app_module._fetch_moc_bytes = _fake_oversize
try:
    r = c.get("/api/moc/acp_test_moc")
    print("GET /api/moc/acp_test_moc (oversize)", r.status_code)
    assert r.status_code == 502
    body = r.get_json()
    assert "fetch failed" in body["error"].lower() or "exceeded" in body["error"].lower()
    assert not (cache_dir / "acp_test_moc.fits").exists(), "cache must not be written on failure"
    print("size-cap rejection OK")
finally:
    app_module._fetch_moc_bytes = _orig_fetch
    _cleanup_cache()

# === 7. Malformed FITS rejection — fake fetch returns garbage =======
app_module._fetch_moc_bytes = lambda _url: b"not a fits file"
try:
    r = c.get("/api/moc/acp_test_moc")
    print("GET /api/moc/acp_test_moc (malformed)", r.status_code)
    assert r.status_code == 502
    body = r.get_json()
    assert "invalid moc" in body["error"].lower() or "fetch failed" in body["error"].lower()
    assert not (cache_dir / "acp_test_moc.fits").exists(), "cache must not be written on malformed FITS"
    assert not (cache_dir / "acp_test_moc.meta.json").exists()
    print("malformed-MOC rejection OK")
finally:
    app_module._fetch_moc_bytes = _orig_fetch
    _cleanup_cache()

# === 8. mocpy missing → 503 + source still in registry ==============
_orig_avail = app_module._MOCPY_AVAILABLE
app_module._MOCPY_AVAILABLE = False
try:
    r = c.get("/api/moc/acp_test_moc")
    print("GET /api/moc/acp_test_moc (mocpy missing)", r.status_code)
    assert r.status_code == 503
    body = r.get_json()
    assert "mocpy" in body["error"].lower()
    # Source must still appear in /api/sources — inert, not removed.
    r2 = c.get("/api/sources")
    ids = [s["id"] for s in r2.get_json()]
    assert "acp_test_moc" in ids, ids
    print("mocpy-missing fallback OK; source still listed")
finally:
    app_module._MOCPY_AVAILABLE = _orig_avail

# === 9. Unknown id → 404 ============================================
r = c.get("/api/moc/nonexistent_source")
print("GET /api/moc/nonexistent_source", r.status_code)
assert r.status_code == 404
print("unknown-id 404 OK")

# === Cleanup ========================================================
app.coverage_sources.remove(_test_src)
_cleanup_cache()
try:
    cache_dir.rmdir()
except OSError:
    pass

print("ALL OK")
