"""Build ts-snapshot-sample.sql: a made-up Target Scheduler profile.

Nothing here comes from a real database. The rows are invented to cover the
shapes a profile built by hand in Target Scheduler can take, which the import
tests in test_import_from_ts.py and test_live_import_routes.py rely on:

- a 2x2 mosaic whose panels TS named itself, so no gear rebuilds it
- parked projects (state 2) next to active ones
- exposure plans on their template's default length (exposure -1)
- star frames: "R Stars", "G Stars" and "B Stars" templates on the R, G and
  B filters, with frames already accepted
- a mosaic mixing narrowband and broadband panels at the same centres
- disabled exposure plans
- a few survey-style tiles with an explicit sub length
- a single deep target, SNR-A

Every row carries a version 4 guid, as rows made in the TS UI do, drawn from
a seeded generator so the output is the same on every run.

Run from the repo root::

    python tests/ts_sync/fixtures/make_sample_snapshot.py

It writes ts-snapshot-sample.sql next to this script. Apply the result to an
empty database built by make_db(23, ...).
"""
from __future__ import annotations

import random
import uuid
from pathlib import Path

PROFILE = "00000000-0000-4000-8000-000000000002"
OUT = Path(__file__).resolve().parent / "ts-snapshot-sample.sql"

_rng = random.Random(20260101)


def _guid() -> str:
    return str(uuid.UUID(int=_rng.getrandbits(128), version=4))


def _sql(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _insert(table: str, row: dict) -> str:
    cols = ", ".join(f'"{c}"' for c in row)
    vals = ", ".join(_sql(v) for v in row.values())
    return f"INSERT INTO {table} ({cols}) VALUES ({vals});"


# -- templates -----------------------------------------------------------------

# (name, filter, default sub, gain, offset, star frames)
_TEMPLATES = [
    ("L", "L", 60.0, 0, 25, False),
    ("R", "R", 60.0, 0, 25, False),
    ("G", "G", 60.0, 0, 25, False),
    ("B", "B", 60.0, 0, 25, False),
    ("R Stars", "R", 10.0, 0, 25, True),
    ("G Stars", "G", 10.0, 0, 25, True),
    ("B Stars", "B", 10.0, 0, 25, True),
    ("H", "H", 300.0, 0, 25, False),
    ("O", "O", 300.0, 0, 25, False),
    ("S", "S", 300.0, 0, 25, False),
    ("OIII", "O", 300.0, -1, -1, False),
    ("Ha", "H", 300.0, -1, -1, False),
    ("SII", "S", 300.0, -1, -1, False),
]
TPL = {name: i for i, (name, *_rest) in enumerate(_TEMPLATES, start=1)}


def _template_rows() -> list[dict]:
    rows = []
    for i, (name, flt, sub, gain, offset, stars) in enumerate(_TEMPLATES, start=1):
        narrow = flt in ("H", "O", "S")
        rows.append({
            "Id": i, "profileId": PROFILE, "name": name, "filtername": flt,
            "gain": gain, "offset": offset, "bin": 1, "readoutmode": -1,
            "twilightlevel": 1 if narrow else 0,
            "moonavoidanceenabled": 0 if stars or gain == -1 else 1,
            "moonavoidanceseparation": 0.0 if gain == -1 else (60.0 if narrow else 120.0),
            "moonavoidancewidth": 0 if gain == -1 else (7 if narrow else 14),
            "maximumhumidity": 100.0 if gain == -1 else 0.0,
            "defaultexposure": sub,
            "moonrelaxscale": 0.0 if stars or gain == -1 else 2.0,
            "moonrelaxmaxaltitude": 5.0, "moonrelaxminaltitude": -15.0,
            "moondownenabled": 0, "ditherevery": -1 if stars or gain == -1 else 2,
            "minutesOffset": 0, "guid": _guid(),
        })
    return rows


# -- projects, targets and exposure plans --------------------------------------

_BB = ["L", "R", "G", "B"]
_STARS = ["R Stars", "G Stars", "B Stars"]


def _project(pid, name, *, state, priority=1, mosaic=False, minimumtime=60,
             minalt=25.0, grader=1, smart=1, created=1767225600) -> dict:
    return {
        "Id": pid, "profileId": PROFILE, "name": name, "description": "",
        "state": state, "priority": priority, "createdate": created + pid * 600,
        "activedate": None, "inactivedate": None, "minimumtime": minimumtime,
        "minimumaltitude": minalt, "usecustomhorizon": 0, "horizonoffset": 0.0,
        "meridianwindow": 0, "filterswitchfrequency": 0, "ditherevery": 0,
        "enablegrader": grader, "isMosaic": 1 if mosaic else 0, "flatsHandling": 1 if grader else 0,
        "maximumAltitude": 0.0, "smartexposureorder": smart, "guid": _guid(),
    }


# Each project: its row, then (target name, ra hours, dec, rotation, epoch,
# [(template, exposure, desired, acquired, accepted, enabled), ...]).
def _plans(templates, *, desired=60, exposure=-1.0, done=None, disabled=()):
    done = done or {}
    return [
        (t, exposure, desired, done.get(t, 0), done.get(t, 0), 0 if t in disabled else 1)
        for t in templates
    ]


def _projects() -> list[tuple[dict, list]]:
    out = []

    # 1: a 2x2 mosaic made in TS, parked. The panel names are TS's own and the
    # centres are slightly off a regular grid, so no gear rebuilds it.
    mosaic = [
        ("Example Nebula Panel 1", 3.590277777777778, -12.978333333333333),
        ("Example Nebula Panel 2", 3.59, -11.020833333333334),
        ("Example Nebula Panel 3", 3.501944444444445, -12.978333333333333),
        ("Example Nebula Panel 4", 3.502222222222222, -11.020833333333334),
    ]
    out.append((
        _project(1, "Example Nebula Mosaic", state=2, mosaic=True),
        [(n, ra, dec, 90.0, 2, _plans(_STARS + _BB, desired=60)) for n, ra, dec in mosaic],
    ))

    # 2: an active single target, star frames plus narrowband.
    out.append((
        _project(2, "Example Emission Nebula", state=1),
        [("Example Emission Nebula", 7.25, -20.5, 0.0, 2,
          _plans(_STARS + ["H", "O"], desired=40, done={"H": 6}))],
    ))

    # 3: a mosaic mixing narrowband and broadband panels at the same centres.
    mixed = [
        ("Panel 1", 9.142777777777777, 10.709444444444445),
        ("Panel 2", 9.208055555555556, 12.405),
        ("Panel 3", 9.067222222222222, 11.363611111111112),
        ("Panel 4", 9.1325, 13.059444444444445),
    ]
    targets = []
    for n, ra, dec in mixed:
        targets.append((f"{n} NB", ra, dec, 120.0, 2, _plans(["O", "H"], desired=30)))
        targets.append((f"{n} BB", ra, dec, 120.0, 2,
                        _plans(_STARS + ["R", "G", "B"], desired=20,
                               disabled=("B",) if n == "Panel 4" else ())))
    out.append((_project(3, "Example Mixed Mosaic", state=1, mosaic=True), targets))

    # 4: an active planetary nebula, priority high, narrowband on template defaults.
    out.append((
        _project(4, "Example Planetary", state=1, priority=2),
        [("Example Planetary", 22.5, -21.0, 15.0, 2,
          _plans(_STARS + ["O", "H", "S"], desired=50))],
    ))

    # 5: a parked cluster with one disabled plan.
    out.append((
        _project(5, "Example Cluster", state=2),
        [("Example Cluster", 11.0, -58.5, 0.0, 2,
          _plans(_BB, desired=30, disabled=("L",)))],
    ))

    # 6: the galaxy with star frames, parked. Its star rows already hold
    # accepted frames.
    out.append((
        _project(6, "Example Galaxy", state=2),
        [("Example Galaxy", 1.25, -33.5, 145.0, 2,
          _plans(_STARS, desired=15, done={t: 18 for t in _STARS})
          + _plans(_BB + ["H"], desired=120))],
    ))

    # 7: survey-style tiles, an explicit 300 s sub, no minimum time.
    tiles = [
        ("Survey Tile 01", 20.0, -40.0),
        ("Survey Tile 02", 20.25, -40.0),
        ("Survey Tile 03", 20.125, -41.25),
        ("Survey Tile 04", 20.125, -38.75),
    ]
    out.append((
        _project(7, "Sample Survey", state=1, priority=2, minimumtime=0, minalt=20.0,
                 grader=0, smart=0),
        [(n, ra, dec, 0.0, 1, _plans(["OIII", "Ha"], desired=18, exposure=300.0))
         for n, ra, dec in tiles],
    ))

    # 8: a single deep target.
    out.append((
        _project(8, "Science SNR", state=1, priority=2, minimumtime=0, minalt=20.0,
                 grader=0, smart=0),
        [("SNR-A", 16.5, -35.0, 0.0, 1,
          _plans(["Ha", "OIII", "SII"], desired=24, exposure=300.0))],
    ))
    return out


def build() -> str:
    lines = [
        "-- A made-up Target Scheduler profile at schema 23, for the import tests.",
        f"-- One profile ({PROFILE}): templates, projects, targets and exposure plans.",
        "-- Generated by make_sample_snapshot.py; do not edit by hand. Apply to make_db(23, ...).",
    ]
    lines += [_insert("exposuretemplate", r) for r in _template_rows()]
    projects = _projects()
    target_rows, plan_rows = [], []
    tid = ep_id = 0
    for proj, targets in projects:
        lines.append(_insert("project", proj))
        for name, ra, dec, rot, epoch, plans in targets:
            tid += 1
            target_rows.append({
                "Id": tid, "name": name, "active": 1, "ra": ra, "dec": dec,
                "epochcode": epoch, "rotation": rot, "roi": 100.0,
                "projectid": proj["Id"], "unusedOEO": None, "guid": _guid(),
            })
            for tpl, exposure, desired, acquired, accepted, enabled in plans:
                ep_id += 1
                plan_rows.append({
                    "Id": ep_id, "profileId": PROFILE, "exposure": exposure,
                    "desired": desired, "acquired": acquired, "accepted": accepted,
                    "targetid": tid, "exposureTemplateId": TPL[tpl], "enabled": enabled,
                    "guid": _guid(),
                })
    lines += [_insert("target", r) for r in target_rows]
    lines += [_insert("exposureplan", r) for r in plan_rows]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.name}")
