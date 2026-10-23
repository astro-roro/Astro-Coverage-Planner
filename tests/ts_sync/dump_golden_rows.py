"""Produce the golden rows the C# plugin's test suite compares itself to.

The plugin and this extension write to the same Target Scheduler database, so
a user can push from one tonight and the other tomorrow. They have to produce
the same identities and the same rows. `ACP.NINA.Plugin.Tests/Fixtures/
golden-rows.json` is this module's output, and `TsPushGoldenTests.cs` reads
it and asserts the plugin matches, field by field.

Run it whenever the payload, the identity recipe, or the write path changes,
and copy the result into the plugin repo::

    python -m tests.ts_sync.dump_golden_rows > \\
        ../ACP.NINA.Plugin/ACP.NINA.Plugin.Tests/Fixtures/golden-rows.json

Two kinds of slice come out:

`versions` is a plain push into an empty database at 23 and at 28, which is
what the suite started with.

`scenarios` covers the states a second push has to get right, and each one
is a fixed recipe the C# side re-runs step for step:

    counts_survive_a_second_push
        push, then write the frame counts a night of imaging and a grader
        would have left, and an older createdate, then push the same payload
        again. Nothing in that hand-written state may move.

    migrated_from_the_old_recipe
        push, then wind every guid back to the pre-length-prefix recipe so
        the database looks like one an older ACP wrote, then push again. The
        migration inside the push has to restamp all of it in place.

    ts_owned_columns_survive_a_second_push
        push, then hand-edit dither, the grader and custom horizon the way a
        rig-side setting would, then push the same payload again. TS-owned
        columns are never in the UPDATE, so they keep the hand-edited
        values (docs/specs/ts-project-settings.md section 4).

    ts_pause_survives_a_push
        push and link (base: active), then pause the project by hand in TS
        (an edit on the rig), then push again with ACP still saying active.
        ACP hasn't changed state since the base, so the push writes nothing
        and TS keeps its pause.

    acp_pause_reaches_ts
        push and link (base: active, TS still active), then pause the plan
        in ACP, then push again. ACP changed state since the base, so the
        push writes inactive and sets inactivedate.

    s1_new_star_row ... r3_readded_row_enabled
        the filter row scenarios in tests/row_scenarios.py, which say step
        by step what each one does (goal-panel-and-ts-order.md tests S1, S2,
        M2 to M4, R1 and R3).

    f1_switch_frequency_written ... smart_order_written
        the order setting scenarios in the same file (tests F1 and F2): a
        switch frequency or smart order written only when ACP changed it,
        and every target's cadence rows deleted when it is.

    r2_ts_rows_kept_and_remapped, o1_acp_order_written ... f1_override_rows_rebuilt
        the order writer's scenarios in the same file (tests R2, O1, O3,
        O4, O4c, O4d, O7, O8, O11 and the override half of F1): ACP's order
        written as override rows, rows set in TS kept, and every target's
        cadence rows deleted on every push.

    gear_s1_no_edits ... gear_s31_alias_finds_row
        the gear template scenarios in the same file (docs/specs/gear-
        templates.md tests S1 to S31, golden test S27). Each starts from
        the 20 template fixture (`ts-templates-20.sql`) and the gear in
        `gear-templates-v3.json`, and the plugin's `TsRowScenarios.Gear.cs`
        replays the same recipes.

Every slice dumps the override and cadence tables too, which the row
scenarios, the order setting scenarios and the order writer write to.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import types
import uuid
from pathlib import Path

from nina_ts_sync import convert, upsert  # noqa: E402
from nina_ts_sync.reader import read_all  # noqa: E402
from nina_ts_sync.schema import ACP_NS  # noqa: E402
from nina_ts_sync.state import build_base_snapshot  # noqa: E402
from .make_db import make_db  # noqa: E402
from . import row_scenarios  # noqa: E402
from .plans import GEAR, PROFILE_ID, plan, three_plans  # noqa: E402

TABLES = (
    "exposuretemplate", "project", "target", "exposureplan", "ruleweight",
    "overrideexposureorderitem", "filtercadenceitem",
)

FROZEN_NOW = 1_700_000_000
#: The createdate a project is given by hand before the second push, standing
#: in for a project created long before ACP ever pushed to it.
OLDER_CREATEDATE = 1_600_000_000
#: The camera every template in `three_plans()` belongs to. The old identity
#: recipe needs it and the row does not carry it.
CAMERA_ID = "cam-1"


def _payload():
    return convert.build_payload(three_plans(), GEAR, profile_id=PROFILE_ID)


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _push(conn):
    report = upsert.apply(conn, _payload())
    conn.commit()
    return report


def _dump(conn):
    return {
        table: [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY Id")]
        for table in TABLES
    }


def _old(name: str) -> str:
    """The identity recipe as it stood before the length prefix arrived."""
    return str(uuid.uuid5(ACP_NS, name))


def _wind_back_to_the_old_recipe(conn, profile_id=PROFILE_ID):
    """Restamp every ACP row the way the pre-fix code would have stamped it."""
    for row in conn.execute("SELECT Id, filtername FROM exposuretemplate").fetchall():
        conn.execute(
            "UPDATE exposuretemplate SET guid = ? WHERE Id = ?",
            (_old(f"{profile_id}/template/{row['filtername']}/{CAMERA_ID}"), row["Id"]),
        )
    for row in conn.execute("SELECT Id, name FROM project").fetchall():
        conn.execute(
            "UPDATE project SET guid = ? WHERE Id = ?",
            (_old(f"{profile_id}/project/{row['name']}"), row["Id"]),
        )
    old_target_guid_by_id = {}
    for row in conn.execute(
        "SELECT t.Id, t.name, p.name AS pname FROM target t "
        "JOIN project p ON p.Id = t.projectid"
    ).fetchall():
        guid = _old(f"{profile_id}/target/{row['pname']}/{row['name']}")
        old_target_guid_by_id[row["Id"]] = guid
        conn.execute("UPDATE target SET guid = ? WHERE Id = ?", (guid, row["Id"]))
    for row in conn.execute(
        "SELECT e.Id, e.targetid, x.filtername FROM exposureplan e "
        "JOIN exposuretemplate x ON x.Id = e.exposureTemplateId"
    ).fetchall():
        parent = old_target_guid_by_id[row["targetid"]]
        conn.execute(
            "UPDATE exposureplan SET guid = ? WHERE Id = ?",
            (_old(f"{profile_id}/plan/{parent}/{row['filtername']}"), row["Id"]),
        )
    conn.commit()


def _slice(version, tmp, name, prepare=None):
    """One push into a fresh database, optionally with a second push after
    *prepare* has edited what the first one wrote."""
    conn = _connect(make_db(version, tmp / f"{name}.sqlite"))
    try:
        report = _push(conn)
        if prepare is not None:
            prepare(conn)
            report = _push(conn)
        return {"report": report.to_dict(), "tables": _dump(conn)}
    finally:
        conn.close()


def _a_night_of_imaging(conn):
    conn.execute("UPDATE exposureplan SET acquired = 137, accepted = 120 WHERE Id = 1")
    conn.execute("UPDATE exposureplan SET acquired = 40, accepted = 0 WHERE Id = 2")
    conn.execute(f"UPDATE project SET createdate = {OLDER_CREATEDATE} WHERE Id = 1")
    conn.commit()


def _ts_owned_settings_by_hand(conn):
    conn.execute(
        "UPDATE project SET ditherevery = 3, enablegrader = 1, usecustomhorizon = 1"
    )
    conn.commit()


def _push_plans(conn, plans_list):
    report = upsert.apply(
        conn, convert.build_payload(plans_list, GEAR, profile_id=PROFILE_ID)
    )
    conn.commit()
    return report


def _link(conn, plans_list):
    """Stand in for a real sync: read TS back and stamp `ts_base_snapshot`
    onto each plan, the state a push+link leaves behind."""
    snap = read_all(conn, PROFILE_ID)
    for p in plans_list:
        p["ts_base_snapshot"] = build_base_snapshot(snap, p, profile_id=PROFILE_ID)


def _ts_pause_survives_a_push(version, tmp):
    conn = _connect(make_db(version, tmp / "ts_pause_survives.sqlite"))
    try:
        plans_list = [plan("pause-survives", project_name="Pause Survives")]
        _push_plans(conn, plans_list)
        _link(conn, plans_list)
        conn.execute("UPDATE project SET state = 2 WHERE name = 'Pause Survives'")
        conn.commit()
        report = _push_plans(conn, plans_list)  # ACP still says active
        return {"report": report.to_dict(), "tables": _dump(conn)}
    finally:
        conn.close()


def _acp_pause_reaches_ts(version, tmp):
    conn = _connect(make_db(version, tmp / "acp_pause_reaches.sqlite"))
    try:
        plans_list = [plan("acp-pause-reaches", project_name="ACP Pause Reaches")]
        _push_plans(conn, plans_list)
        _link(conn, plans_list)
        plans_list[0]["state"] = "inactive"  # paused in ACP since the base
        report = _push_plans(conn, plans_list)
        return {"report": report.to_dict(), "tables": _dump(conn)}
    finally:
        conn.close()


def _row_slice(scenario, tmp, name):
    """One filter row scenario from tests/row_scenarios.py."""
    conn = _connect(make_db(28, tmp / f"{name}.sqlite"))
    try:
        report = scenario(conn)
        return {"report": report.to_dict(), "tables": _dump(conn)}
    finally:
        conn.close()


def _gear_slice(scenario, tmp, name):
    """One gear template scenario from tests/row_scenarios.py, over the 20
    template fixture."""
    conn = _connect(row_scenarios.make_template_db(tmp / f"{name}.sqlite"))
    try:
        report = scenario(conn)
        return {"report": report.to_dict(), "tables": _dump(conn)}
    finally:
        conn.close()


def build() -> dict:
    convert.time = types.SimpleNamespace(time=lambda: FROZEN_NOW)
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        return {
            "frozen_now": FROZEN_NOW,
            "profile_id": PROFILE_ID,
            "older_createdate": OLDER_CREATEDATE,
            "versions": {
                "23": _slice(23, tmp, "v23"),
                "28": _slice(28, tmp, "v28"),
            },
            "scenarios": {
                "counts_survive_a_second_push": _slice(
                    28, tmp, "counts", _a_night_of_imaging),
                "migrated_from_the_old_recipe": _slice(
                    28, tmp, "migrated", _wind_back_to_the_old_recipe),
                "ts_owned_columns_survive_a_second_push": _slice(
                    28, tmp, "ts_owned_columns", _ts_owned_settings_by_hand),
                "ts_pause_survives_a_push": _ts_pause_survives_a_push(28, tmp),
                "acp_pause_reaches_ts": _acp_pause_reaches_ts(28, tmp),
                **{name: _row_slice(fn, tmp, name) for name, fn in row_scenarios.SCENARIOS.items()},
                **{name: _gear_slice(fn, tmp, name) for name, fn in row_scenarios.GEAR_SCENARIOS.items()},
            },
        }


def main() -> None:
    json.dump(build(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
