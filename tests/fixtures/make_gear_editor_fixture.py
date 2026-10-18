"""Writes tests/fixtures/gear-editor-api.json, the fixture tests/frontend/gear-editor.test.mjs reads.

It is what the gear editor receives: `GET /api/gear` for the gear after
migration (gear-templates-v3.json) with the IMAGING-PC profile's fingerprint
(wheel slots L R G B H O S) and a plan set that uses the templates as often as
the live data does (docs/specs/gear-templates.md section 6). Re-run it after
the gear API changes shape:

    .venv/bin/python tests/fixtures/make_gear_editor_fixture.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import test_gear_templates as T  # noqa: E402

USES = {
    "L": 11, "R (ZWO ASI6200MM Pro)": 26, "R Stars": 23, "G (ZWO ASI6200MM Pro)": 26, "G Stars": 23,
    "B": 26, "B Stars": 23, "Ha": 23, "OIII": 55, "SII": 3,
}


def build() -> dict:
    T.redirect_state()
    doc = T.v3()
    doc["links_capable"] = {T.P1: {"templates": True, "repoint": True, "at": "2026-10-13T00:00:00Z"}}
    T.write_gear(doc)
    cam = next(c for c in doc["cameras"] if c["id"] == T.CAM)
    ids = {t["name"]: (f, t) for f, cfg in cam["filters"].items() for t in cfg["templates"]}
    plans = []
    for i in range(max(USES.values())):
        rows = []
        for name, n in USES.items():
            if i < n:
                f, t = ids[name]
                sub = t["sub_s"]
                row = {"id": f"r{i}-{f}-{len(rows)}", "filter": f, "template": name, "template_id": t["id"],
                       "template_sub_s": sub, "unit": "fr" if sub < 60 else "h", "actual_hours": 0}
                row["target_frames" if sub < 60 else "target_hours"] = 20 if sub < 60 else 3
                rows.append(row)
        plans.append(T.plan(f"plan-{i}", rows))
    T.write_plans(plans)
    app = T.app.test_client()
    app.post("/api/plans/match", json=T.fingerprint())
    gear = app.get("/api/gear").get_json()
    return {"gear": gear, "plans": plans}


if __name__ == "__main__":
    out = HERE / "gear-editor-api.json"
    out.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8")
    print("wrote", out)
