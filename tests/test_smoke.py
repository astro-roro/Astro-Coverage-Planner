"""Quick sanity check — exercises every endpoint via Flask's test client."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import app  # noqa: E402

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
print("GET /api/export/priority (no catalogs)", r.status_code)
assert r.status_code == 404  # no catalogs.json yet

print("ALL OK")
