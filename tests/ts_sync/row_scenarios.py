"""Filter row scenarios shared by the tests and the golden rows.

docs/specs/goal-panel-and-ts-order.md section 7, tests S1, S2, M2 to M4, R1
to R3, F1, F2 and the O tests of the order writer. Each scenario starts from an empty database at user_version 28 and
returns the last push's report. `tests/dump_golden_rows.py` records the rows
each one leaves, and the plugin's `TsPushGoldenTests` replays the same steps
in C#, so every step here is a push, a link, or a plain SQL edit that the C#
side can repeat word for word.

The spec's fixture: one plan "M42", rows whose templates take 300 s subs and
ask for 4 frames, Ha then OIII unless a scenario says otherwise.
"""
from __future__ import annotations

import copy
import sqlite3

from nina_ts_sync import convert, upsert
from nina_ts_sync.links import link_for, write_link
from nina_ts_sync.reader import read_all
from nina_ts_sync.rows import refresh_template_subs
from nina_ts_sync.schema import exposure_plan_guid, target_guid
from nina_ts_sync.state import build_base_snapshot, build_ts_refs
from .plans import GEAR, PROFILE_ID, plan

USER_VERSION = 28

#: The push tests' camera with every filter the scenarios use.
ROW_GEAR = copy.deepcopy(GEAR)
ROW_GEAR["cameras"][0]["filters"].update({
    "L": {"default_sub_s": 120, "gain": 100, "offset": 50, "bin": 1},
    "R": {"ts_template_name": "R", "default_sub_s": 180, "gain": 100, "offset": 50, "bin": 1},
    "G": {"default_sub_s": 180, "gain": 100, "offset": 50, "bin": 1},
    "B": {"default_sub_s": 180, "gain": 100, "offset": 50, "bin": 1},
    "SII": {"ts_template_name": "SII 3nm", "default_sub_s": 300, "gain": 100, "offset": 50, "bin": 1},
})

M42 = "M42"


def row(row_id: str, filt: str, template: str, sub: float, *,
        frames: int | None = None, hours: float | None = None) -> dict:
    out = {"id": row_id, "filter": filt, "template": template, "template_sub_s": sub}
    if frames is not None:
        out.update(unit="fr", target_frames=frames)
    else:
        out.update(unit="h", target_hours=hours or 0.0)
    return out


def m42(rows: list[dict]) -> dict:
    return plan("m42", project_name=M42, target_name=M42, filter_rows=rows)


def push(conn: sqlite3.Connection, plans: list[dict]):
    report = upsert.apply(conn, convert.build_payload(plans, ROW_GEAR, profile_id=PROFILE_ID))
    conn.commit()
    return report


def link(conn: sqlite3.Connection, plans: list[dict], report=None) -> None:
    """What a push leaves on each plan: its link for this profile, and each
    row's `template_sub_s` refreshed from the base. Given the push's
    *report*, a target whose override rows the push left alone keeps the
    order its old base recorded, as the /sync route does; without one, each
    target records the order TS shoots and its override rows only when TS
    has none."""
    snap = read_all(conn, PROFILE_ID)
    order_targets = None if report is None else report.order_synced_target_ids
    for p in plans:
        old = (link_for(p, PROFILE_ID) or {}).get("base_snapshot")
        refs = build_ts_refs(snap, p, profile_id=PROFILE_ID, user_version=USER_VERSION, operation="push",
                             camera=ROW_GEAR["cameras"][0])
        base = build_base_snapshot(snap, p, profile_id=PROFILE_ID, old_base=old, order_targets=order_targets,
                                   camera=ROW_GEAR["cameras"][0])
        write_link(p, PROFILE_ID, machine=None, refs=refs, base_snapshot=base)
        refresh_template_subs(p, base)


def grader_on(conn: sqlite3.Connection, project: str = "M42") -> None:
    """Turn the project's grader on by hand, as the spec's fixture has it.
    A push inserts a project with it off and never writes it again."""
    conn.execute("UPDATE project SET enablegrader = 1 WHERE name = ?", (project,))
    conn.commit()


def reorder(p: dict, ids: list[str]) -> None:
    """Put the plan's rows in the order of *ids*."""
    by_id = {r["id"]: r for r in p["filter_rows"]}
    p["filter_rows"] = [by_id[i] for i in ids]


def m42_target_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT Id FROM target WHERE name = ?", (M42,)).fetchone()[0])


def m42_target_guid() -> str:
    return target_guid(PROFILE_ID, M42, M42)


def add_template(conn, name: str, filtername: str, default: float, guid: str) -> int:
    cur = conn.execute(
        "INSERT INTO exposuretemplate (profileId, name, filtername, gain, offset, bin, readoutmode, "
        "twilightlevel, moonavoidanceenabled, moonavoidanceseparation, moonavoidancewidth, "
        "maximumhumidity, defaultexposure, guid) "
        "VALUES (?, ?, ?, 100, 50, 1, -1, 1, 0, 0.0, 0, 100.0, ?, ?)",
        (PROFILE_ID, name, filtername, float(default), guid),
    )
    return int(cur.lastrowid)


def add_old_plan(conn, target_id: int, template_id: int, key: str, exposure: float,
                 desired: int, acquired: int, accepted: int) -> int:
    """An exposure plan the writer before filter rows made, stamped with the
    goal-key recipe."""
    cur = conn.execute(
        "INSERT INTO exposureplan (profileId, exposure, desired, acquired, accepted, targetid, "
        "exposureTemplateId, enabled, guid) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
        (PROFILE_ID, float(exposure), desired, acquired, accepted, target_id, template_id,
         exposure_plan_guid(PROFILE_ID, m42_target_guid(), key)),
    )
    return int(cur.lastrowid)


def set_override(conn, target_id: int, items: list[tuple[int, int]]) -> None:
    """Override rows as (action, referenceIdx), numbered from 1."""
    conn.execute("DELETE FROM overrideexposureorderitem WHERE targetid = ?", (target_id,))
    conn.executemany(
        'INSERT INTO overrideexposureorderitem (targetid, "order", "action", referenceIdx) '
        "VALUES (?, ?, ?, ?)",
        [(target_id, n, a, r) for n, (a, r) in enumerate(items, start=1)],
    )


def add_cadence(conn, target_id: int, n: int) -> None:
    conn.executemany(
        'INSERT INTO filtercadenceitem (targetid, "order", "next", "action", referenceIdx) '
        "VALUES (?, ?, ?, 0, ?)",
        [(target_id, i, 1 if i == 1 else 0, i % 2) for i in range(1, n + 1)],
    )


# -- the scenarios ----------------------------------------------------------


def s1(conn):
    """A new row R with template "R Stars", 15 s, 50 frames; no such template."""
    return push(conn, [m42([row("row-rs", "R", "R Stars", 15, frames=50)])])


def s2(conn):
    """As S1, with a TS template "R Stars" already there under NINA's own
    guid and a 10 s default."""
    add_template(conn, "R Stars", "R", 10, "nina-guid-r-stars")
    conn.commit()
    return s1(conn)


def m2_old_writer_state(conn) -> dict[str, int]:
    """M1's plan as the writer before filter rows left it: one exposure plan
    per goal key, the star goal's template using "R Stars" as a filter name,
    and a hand-made "R Stars" template on filter R beside it."""
    push(conn, [m42([])])
    tid = m42_target_id(conn)
    tpl = {
        "L": add_template(conn, "L (Test IMX571)", "L", 120, "old-tpl-l"),
        "R": add_template(conn, "R", "R", 180, "old-tpl-r"),
        "R Stars": add_template(conn, "R Stars (Test IMX571)", "R Stars", 15, "old-tpl-r-stars"),
        "Ha": add_template(conn, "Ha 3nm", "Ha", 300, "old-tpl-ha"),
        "mine": add_template(conn, "R Stars", "R", 10, "nina-guid-r-stars"),
    }
    add_old_plan(conn, tid, tpl["L"], "L", 120, 60, 11, 10)
    add_old_plan(conn, tid, tpl["R"], "R", 180, 60, 12, 11)
    add_old_plan(conn, tid, tpl["R Stars"], "R Stars", 15, 20, 18, 18)
    add_old_plan(conn, tid, tpl["Ha"], "Ha", 300, 48, 13, 9)
    conn.commit()
    return tpl


def m1_rows() -> list[dict]:
    """M1's plan after ACP's migration."""
    return [
        row("row-l", "L", "L (Test IMX571)", 120, hours=2.0),
        row("row-r", "R", "R", 180, hours=3.0),
        row("row-rs", "R", "R Stars", 15, frames=20),
        row("row-ha", "Ha", "Ha 3nm", 300, hours=4.0),
    ]


def m2(conn):
    m2_old_writer_state(conn)
    return push(conn, [m42(m1_rows())])


def m3_state(conn) -> tuple[dict, int]:
    """An exposure plan with exposure 300 whose template "R" says 180, and
    the migrated row for it. Returns the plan and the plan's Id."""
    push(conn, [m42([])])
    tid = m42_target_id(conn)
    tpl = add_template(conn, "R", "R", 180, "old-tpl-r")
    ep = add_old_plan(conn, tid, tpl, "R", 300, 12, 4, 4)
    conn.commit()
    return m42([row("row-r", "R", "R", 300, hours=1.0)]), ep


def m3(conn):
    p, _ = m3_state(conn)
    return push(conn, [p])


def m4(conn):
    """M3, linked, then the row's template changed in ACP to "R 300", a TS
    template whose default is 300."""
    p, _ = m3_state(conn)
    push(conn, [p])
    link(conn, [p])
    add_template(conn, "R 300", "R", 300, "nina-guid-r-300")
    conn.commit()
    p["filter_rows"][0]["template"] = "R 300"
    return push(conn, [p])


def r1_state(conn) -> dict:
    """Ha, OIII and SII pushed in that order (natural order Ha, OIII, SII),
    the grader turned on, linked, then the ACP list reordered to SII, Ha,
    OIII and pushed, so the order writer wrote SII x4, Ha x4, OIII x4, and
    linked again. Three cadence rows from a night of imaging."""
    p = m42([
        row("row-ha", "Ha", "Ha", 300, frames=4),
        row("row-oiii", "OIII", "OIII", 300, frames=4),
        row("row-sii", "SII", "SII", 300, frames=4),
    ])
    link(conn, [p], push(conn, [p]))
    grader_on(conn)
    tid = m42_target_id(conn)
    conn.execute("UPDATE exposureplan SET acquired = 3, accepted = 2 WHERE Id = 1")
    conn.commit()
    reorder(p, ["row-sii", "row-ha", "row-oiii"])
    link(conn, [p], push(conn, [p]))
    add_cadence(conn, tid, 3)
    conn.commit()
    return p


def r1(conn):
    """Remove the Ha row and push."""
    p = r1_state(conn)
    p["filter_rows"] = [r for r in p["filter_rows"] if r["id"] != "row-ha"]
    return push(conn, [p])


def r2(conn):
    """R2: as R1, but the override rows were set by hand in TS to SII, Ha,
    OIII (one each) before the removal. The push keeps them, remapped."""
    p = r1_state(conn)
    set_override(conn, m42_target_id(conn), [(0, 2), (0, 0), (0, 1)])
    conn.commit()
    p["filter_rows"] = [r for r in p["filter_rows"] if r["id"] != "row-ha"]
    return push(conn, [p])


def r3(conn):
    """After R1, linked, add a row with filter Ha and template Ha again,
    second in the list, and push."""
    p = r1_state(conn)
    p["filter_rows"] = [r for r in p["filter_rows"] if r["id"] != "row-ha"]
    link(conn, [p], push(conn, [p]))
    p["filter_rows"].insert(1, row("row-ha-again", "Ha", "Ha", 300, frames=4))
    return push(conn, [p])


# -- the order writer (section 5) ------------------------------------------


def o_state(conn, *, freq: int = 0) -> dict:
    """The spec's fixture: M42 with rows Ha then OIII at 300 s and 4 frames,
    pushed in that order so the natural order is Ha, OIII, the grader on,
    switch frequency *freq*, smart order off, ditherevery 0, linked."""
    p = m42([row("row-ha", "Ha", "Ha", 300, frames=4), row("row-oiii", "OIII", "OIII", 300, frames=4)])
    p["filter_switch_frequency"] = freq
    report = push(conn, [p])
    grader_on(conn)
    link(conn, [p], report)
    return p


def o1_state(conn) -> tuple[dict, object]:
    """O1: ACP's order OIII, Ha pushed and linked. Returns the plan and the
    push's report."""
    p = o_state(conn)
    reorder(p, ["row-oiii", "row-ha"])
    report = push(conn, [p])
    link(conn, [p], report)
    return p, report


def o1(conn):
    """O1: ACP order OIII, Ha. Push."""
    p = o_state(conn)
    reorder(p, ["row-oiii", "row-ha"])
    return push(conn, [p])


def o3(conn):
    """O2 then O3: after O1, TS's override rows edited to Ha, OIII (one
    each) and a push with no ACP change, then ACP reordered to Ha, OIII."""
    p, _ = o1_state(conn)
    set_override(conn, m42_target_id(conn), [(0, 0), (0, 1)])
    conn.commit()
    link(conn, [p], push(conn, [p]))
    reorder(p, ["row-ha", "row-oiii"])
    return push(conn, [p])


def o4_rotate(conn):
    """O4 at switch frequency 3: ACP order OIII, Ha."""
    p = o_state(conn, freq=3)
    reorder(p, ["row-oiii", "row-ha"])
    return push(conn, [p])


def o4c(conn):
    """O4c: after O1, a night shot OIII x4 and one Ha (the cadence left
    with row 6 next), then AAC lowered OIII's accepted to 3. Push."""
    p, _ = o1_state(conn)
    tid = m42_target_id(conn)
    conn.execute("UPDATE exposureplan SET acquired = 4, accepted = 3 WHERE Id = 2")
    conn.execute("UPDATE exposureplan SET acquired = 1, accepted = 1 WHERE Id = 1")
    conn.executemany(
        'INSERT INTO filtercadenceitem (targetid, "order", "next", "action", referenceIdx) '
        "VALUES (?, ?, ?, 0, ?)",
        [(tid, n, 1 if n == 6 else 0, 1 if n <= 4 else 0) for n in range(1, 9)],
    )
    conn.commit()
    return push(conn, [p])


def o4d(conn):
    """O4d: after O1, OIII's goal raised to 6 frames, pushed with no order
    change."""
    p, _ = o1_state(conn)
    next(r for r in p["filter_rows"] if r["id"] == "row-oiii")["target_frames"] = 6
    return push(conn, [p])


def o7(conn):
    """O7: ditherevery 2 set in TS, order OIII, Ha, switch frequency 0."""
    p = o_state(conn)
    conn.execute("UPDATE project SET ditherevery = 2 WHERE name = ?", (M42,))
    conn.commit()
    reorder(p, ["row-oiii", "row-ha"])
    return push(conn, [p])


def o8(conn):
    """O8: hand-made override rows Ha, OIII (one each), then ACP adds a row
    for SII and pushes."""
    p = o_state(conn)
    tid = m42_target_id(conn)
    set_override(conn, tid, [(0, 0), (0, 1)])
    add_cadence(conn, tid, 2)
    conn.commit()
    p["filter_rows"].append(row("row-sii", "SII", "SII", 300, frames=4))
    return push(conn, [p])


def o11(conn):
    """O11: a base from before the order was recorded, and TS in natural
    order with OIII first, while ACP lists Ha, OIII."""
    p = m42([row("row-oiii", "OIII", "OIII", 300, frames=4), row("row-ha", "Ha", "Ha", 300, frames=4)])
    link(conn, [p], push(conn, [p]))
    grader_on(conn)
    for entry in p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"].values():
        entry.pop("exposure_order", None)
        entry.pop("override_rows", None)
    reorder(p, ["row-ha", "row-oiii"])
    return push(conn, [p])


# -- one order decision per target (section 5) -----------------------------

MOS = "Mos"
HAND_ORDER = [(0, 2), (0, 0), (0, 1)]


def mosaic_state(conn) -> tuple[dict, int, int]:
    """A 1 by 2 mosaic with rows Ha, OIII and SII, pushed and linked, the
    grader on. Returns the plan and its two target Ids."""
    p = plan("mos", project_name=MOS, target_name=MOS, rows=1, cols=2, filter_rows=[
        row("row-ha", "Ha", "Ha", 300, frames=4),
        row("row-oiii", "OIII", "OIII", 300, frames=4),
        row("row-sii", "SII", "SII", 300, frames=4)])
    link(conn, [p], push(conn, [p]))
    grader_on(conn, MOS)
    t1, t2 = (int(r[0]) for r in conn.execute("SELECT Id FROM target ORDER BY Id"))
    return p, t1, t2


def x1(conn):
    """A hand order on panel 2 only, SII, Ha, OIII. Two pushes with no ACP
    change leave it, and panel 1 stays in natural order."""
    p, _, t2 = mosaic_state(conn)
    set_override(conn, t2, HAND_ORDER)
    conn.commit()
    link(conn, [p], push(conn, [p]))
    return push(conn, [p])


def x2(conn):
    """As X1, then ACP reordered to SII, Ha, OIII. Both panels get ACP's
    order."""
    p, _, t2 = mosaic_state(conn)
    set_override(conn, t2, [(0, 1), (0, 0), (0, 2)])
    conn.commit()
    link(conn, [p], push(conn, [p]))
    reorder(p, ["row-sii", "row-ha", "row-oiii"])
    return push(conn, [p])


def added_row_state(conn) -> dict:
    """O8's start: hand-made override rows OIII, Ha (one each) on the spec's
    fixture, pushed and linked, so the base order stays Ha, OIII."""
    p = o_state(conn)
    set_override(conn, m42_target_id(conn), [(0, 1), (0, 0)])
    conn.commit()
    link(conn, [p], push(conn, [p]))
    return p


def x3(conn):
    """A row for SII added in ACP and dragged to the top. ACP's order is
    written over the hand order."""
    p = added_row_state(conn)
    p["filter_rows"].insert(0, row("row-sii", "SII", "SII", 300, frames=4))
    return push(conn, [p])


def x3b(conn):
    """A row for SII added at the end. The hand order stays and SII's block
    is appended."""
    p = added_row_state(conn)
    p["filter_rows"].append(row("row-sii", "SII", "SII", 300, frames=4))
    return push(conn, [p])


def x4(conn):
    """A goal-0 row in the middle (OIII), pushed and linked, then a hand
    order SII, Ha in TS, then OIII's goal raised to 4 frames and pushed.
    OIII was known to the base, so this is no reorder: the hand order stays
    and OIII's block is appended."""
    p = m42([row("row-ha", "Ha", "Ha", 300, frames=4),
             row("row-oiii", "OIII", "OIII", 300, frames=0),
             row("row-sii", "SII", "SII", 300, frames=4)])
    link(conn, [p], push(conn, [p]))
    grader_on(conn)
    set_override(conn, m42_target_id(conn), [(0, 1), (0, 0)])
    conn.commit()
    link(conn, [p], push(conn, [p]))
    next(r for r in p["filter_rows"] if r["id"] == "row-oiii")["target_frames"] = 4
    return push(conn, [p])


def x6(conn):
    """Natural order Ha, OIII so no rows exist. Ha is removed and pushed
    (its plan is disabled, base and TS rows both empty), then added back at
    the end. The plan keeps its lower Id, so TS would shoot Ha first; ACP's
    order matched the natural order before the push, so the rows are
    written."""
    p = m42([row("row-ha", "Ha", "Ha", 300, frames=4), row("row-oiii", "OIII", "OIII", 300, frames=4)])
    link(conn, [p], push(conn, [p]))
    p["filter_rows"] = [r for r in p["filter_rows"] if r["id"] != "row-ha"]
    link(conn, [p], push(conn, [p]))
    p["filter_rows"].append(row("row-ha2", "Ha", "Ha", 300, frames=4))
    return push(conn, [p])


def x5(conn):
    """A mosaic whose panels hold the sync's rows for OIII, Ha, SII. Panel 1
    is then set by hand to SII, Ha, OIII, and the import takes that order
    into ACP (the base records it as the order, and leaves panel 1's rows
    out). The push refreshes panel 2's stale sync rows to ACP's order and
    leaves panel 1's hand rows."""
    p, t1, _ = mosaic_state(conn)
    reorder(p, ["row-oiii", "row-ha", "row-sii"])
    link(conn, [p], push(conn, [p]))
    set_override(conn, t1, HAND_ORDER)
    conn.commit()
    reorder(p, ["row-sii", "row-ha", "row-oiii"])
    targets = p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"]
    for entry in targets.values():
        entry["exposure_order"] = ["row-sii", "row-ha", "row-oiii"]
    targets["1,1"].pop("override_rows", None)
    return push(conn, [p])


def f1_override(conn):
    """F1 on O1's database: base switch frequency 0, ACP 3, TS 0. The
    override rows are rebuilt in blocks of 3."""
    p, _ = o1_state(conn)
    p["filter_switch_frequency"] = 3
    return push(conn, [p])


def imported_plan(conn):
    """A plan made in TS on the user's own template "Ha", linked, whose ACP
    row names its template "Ha 3nm" as ACP's migration would. The push keeps
    the plan on "Ha" and inserts no template."""
    push(conn, [m42([])])
    tid = m42_target_id(conn)
    mine = add_template(conn, "Ha", "Ha", 600, "nina-guid-ha")
    ep = conn.execute(
        "INSERT INTO exposureplan (profileId, exposure, desired, acquired, accepted, targetid, "
        "exposureTemplateId, enabled, guid) VALUES (?, -1, 24, 5, 5, ?, ?, 1, 'nina-guid-plan')",
        (PROFILE_ID, tid, mine)).lastrowid
    conn.commit()
    p = m42([row("row-ha", "Ha", "Ha 3nm", 600, hours=4.0)])
    p["ts_links"] = {PROFILE_ID: {
        "machine": None,
        "refs": {"profile_id": PROFILE_ID, "project_id": 1, "target_ids_by_panel": {"1,1": tid},
                 "template_ids_by_filter": {"row-ha": mine},
                 "exposure_plan_ids": {f"{tid}_row-ha": ep}},
        "base_snapshot": {"exposure_plans_by_panel_filter": {
            "1,1_row-ha": {"desired": 24, "filter": "Ha", "template": "Ha 3nm"}}},
    }}
    return push(conn, [p])


def old_acp(goals: dict) -> dict:
    """A plan as an ACP from before filter rows hands it over: goals only."""
    p = m42([])
    del p["filter_rows"]
    p["filter_goals"] = goals
    return p


def derived_row_keeps_existing_template(conn):
    """An existing plan on the user's template "G", a link that pins the
    template "G Stars" for its goal, and rows derived from filter_goals that
    name the template "G (Test IMX571)". The plan stays on "G" and no
    template is made: only an edit in ACP moves a plan."""
    push(conn, [m42([])])
    tid = m42_target_id(conn)
    mine = add_template(conn, "G", "G", 60, "nina-guid-g")
    stars = add_template(conn, "G Stars", "G", 10, "nina-guid-g-stars")
    ep = conn.execute(
        "INSERT INTO exposureplan (profileId, exposure, desired, acquired, accepted, targetid, "
        "exposureTemplateId, enabled, guid) VALUES (?, 60, 120, 7, 7, ?, ?, 1, 'nina-guid-plan-g')",
        (PROFILE_ID, tid, mine)).lastrowid
    conn.commit()
    p = old_acp({"G": {"target_hours": 2.0, "sub_exposure_s": 60}})
    p["ts_links"] = {PROFILE_ID: {
        "machine": None,
        "refs": {"profile_id": PROFILE_ID, "project_id": 1, "target_ids_by_panel": {"1,1": tid},
                 "template_ids_by_filter": {"G": stars},
                 "exposure_plan_ids": {f"{tid}_G": ep}},
        "base_snapshot": {"exposure_plans_by_panel_filter": {"1,1_G": {"desired": 120}}},
    }}
    return push(conn, [p])


SEQ_GOALS = {
    "L": {"target_hours": 2.0, "sub_exposure_s": 120, "actual_hours": 0.5},
    "R Stars": {"target_hours": 0.0833, "sub_exposure_s": 15},
    "Ha": {"target_hours": 4.0, "sub_exposure_s": 300, "actual_hours": 2.0},
    "Antlia Ha": {"target_hours": 1.0, "sub_exposure_s": 120},
}


def seq_old_writer_state(conn) -> None:
    """What the writer before filter rows left for SEQ_GOALS: one plan per
    goal key on templates it named, each with an explicit exposure."""
    push(conn, [m42([])])
    tid = m42_target_id(conn)
    for key, name, filt, sub, desired in (
        ("L", "L (Test IMX571)", "L", 120, 60),
        ("R Stars", "R Stars (Test IMX571)", "R Stars", 15, 20),
        ("Ha", "Ha 3nm", "Ha", 300, 48),
        ("Antlia Ha", "Antlia Ha (Test IMX571)", "Antlia Ha", 120, 30),
    ):
        tpl = add_template(conn, name, filt, sub, f"old-tpl-{key}")
        add_old_plan(conn, tid, tpl, key, sub, desired, 3, 3)
    conn.commit()


def migrate_like_acp(p: dict) -> None:
    """ACP's one-shot migration, as it treats a plan the writers derived rows
    for: the same rows under new ids, links rekeyed, and each base entry
    given its row's filter and template."""
    from nina_ts_sync.rows import rows_of
    rows = copy.deepcopy(rows_of(p, ROW_GEAR["cameras"][0]))
    new_id = {r["id"]: f"m-{r['id']}" for r in rows}
    for r in rows:
        r["id"] = new_id[r["id"]]
    by_id = {r["id"]: r for r in rows}
    p["filter_rows"] = rows
    p.pop("filter_goals", None)
    for link in (p.get("ts_links") or {}).values():
        refs = link.get("refs") or {}
        refs["exposure_plan_ids"] = {
            f"{k.partition('_')[0]}_{new_id.get(k.partition('_')[2], k.partition('_')[2])}": v
            for k, v in (refs.get("exposure_plan_ids") or {}).items()}
        refs["template_ids_by_filter"] = {
            new_id.get(k, k): v for k, v in (refs.get("template_ids_by_filter") or {}).items()}
        base = link.get("base_snapshot") or {}
        plans = {}
        for k, v in (base.get("exposure_plans_by_panel_filter") or {}).items():
            panel, _, key = k.partition("_")
            rid = new_id.get(key, key)
            v = dict(v)
            if rid in by_id:
                v["filter"], v["template"] = by_id[rid]["filter"], by_id[rid]["template"]
            plans[f"{panel}_{rid}"] = v
        base["exposure_plans_by_panel_filter"] = plans
        base["templates_by_filter"] = {
            new_id.get(k, k): v for k, v in (base.get("templates_by_filter") or {}).items()}


def acp_migration_keeps_plans(conn):
    """The probe seq.py: plans an older writer made, pushed by the new plugin
    against an older ACP (rows derived from goals, alias and brand keys
    included), linked, then ACP migrates and the push runs again. Every plan
    keeps its template and exposure and no template is made."""
    seq_old_writer_state(conn)
    p = old_acp(copy.deepcopy(SEQ_GOALS))
    push(conn, [p])
    link(conn, [p])
    migrate_like_acp(p)
    return push(conn, [p])


def older_acp_goals(conn):
    """A plan from an ACP before filter rows, with an alias key ("O"), a
    star key ("R Stars"), a brand key ("Antlia Ha") and a long name ("Lum"),
    into an empty database."""
    return push(conn, [old_acp({
        "Ha": {"target_hours": 2.0, "sub_exposure_s": 300},
        "O": {"target_hours": 1.0, "sub_exposure_s": 300},
        "R Stars": {"target_hours": 0.05, "sub_exposure_s": 10},
        "Antlia Ha": {"target_hours": 1.0, "sub_exposure_s": 120},
        "Lum": {"target_hours": 1.0, "sub_exposure_s": 60},
    })])


M43 = "M43"


def f_state(conn, *, freq: int) -> list[dict]:
    """Two plans in project M42, M42 and M43, each with rows OIII then Ha at
    300 s and 4 frames, pushed with switch frequency *freq*, linked, and
    three cadence rows left on each target by a night of imaging."""
    plans = [
        m42([row("row-oiii", "OIII", "OIII", 300, frames=4), row("row-ha", "Ha", "Ha", 300, frames=4)]),
        plan("m43", project_name=M42, target_name=M43, ra=101.0, filter_rows=[
            row("row-oiii-43", "OIII", "OIII", 300, frames=4),
            row("row-ha-43", "Ha", "Ha", 300, frames=4)]),
    ]
    for p in plans:
        p["filter_switch_frequency"] = freq
    link(conn, plans, push(conn, plans))
    for name in (M42, M43):
        tid = int(conn.execute("SELECT Id FROM target WHERE name = ?", (name,)).fetchone()[0])
        add_cadence(conn, tid, 3)
    conn.commit()
    return plans


def f1(conn):
    """F1: base switch frequency 0, ACP 3, TS 0. The push writes 3 and
    deletes the cadence rows of both targets in the project."""
    plans = f_state(conn, freq=0)
    for p in plans:
        p["filter_switch_frequency"] = 3
    return push(conn, plans)


def f2(conn):
    """F2: base 0, ACP 0, TS 3 (set in TS since the base). The push leaves
    3 and the cadence rows."""
    plans = f_state(conn, freq=0)
    conn.execute("UPDATE project SET filterswitchfrequency = 3 WHERE name = ?", (M42,))
    conn.commit()
    return push(conn, plans)


def smart_order_on(conn):
    """Smart order turned on in ACP since the base. The push writes it and
    deletes the cadence rows, and leaves the switch frequency alone."""
    plans = f_state(conn, freq=0)
    for p in plans:
        p["smart_exposure_order"] = True
    return push(conn, plans)


#: name -> scenario, in the order the golden file lists them.
SCENARIOS = {
    "s1_new_star_row": s1,
    "s2_existing_template": s2,
    "m2_old_writer_rows_restamped": m2,
    "m3_explicit_exposure_kept": m3,
    "m4_template_change_writes_default": m4,
    "r1_removed_row_disabled": r1,
    "r3_readded_row_enabled": r3,
    "imported_plan_keeps_its_template": imported_plan,
    "derived_row_keeps_existing_template": derived_row_keeps_existing_template,
    "acp_migration_keeps_plans": acp_migration_keeps_plans,
    "older_acp_goals": older_acp_goals,
    "f1_switch_frequency_written": f1,
    "f2_switch_frequency_set_in_ts_kept": f2,
    "smart_order_written": smart_order_on,
    "r2_ts_rows_kept_and_remapped": r2,
    "o1_acp_order_written": o1,
    "o3_acp_order_replaces_ts_order": o3,
    "o4_rotate_every_3": o4_rotate,
    "o4c_cadence_reset_every_push": o4c,
    "o4d_goal_change_refreshes_rows": o4d,
    "o7_dither_rows": o7,
    "o8_new_plan_appended_to_ts_order": o8,
    "o11_no_base_order_writes_nothing": o11,
    "f1_override_rows_rebuilt": f1_override,
    "x1_mosaic_hand_order_on_panel_two_kept": x1,
    "x2_mosaic_acp_reorder_writes_both_panels": x2,
    "x3_added_row_dragged_to_top_is_a_reorder": x3,
    "x3b_added_row_at_end_keeps_hand_order": x3b,
    "x4_goal_raised_from_zero_keeps_hand_order": x4,
    "x5_stale_sync_rows_refreshed_hand_rows_kept": x5,
    "x6_readded_row_with_no_rows_writes_acps_order": x6,
}


# ---------------------------------------------------------------------------
# Gear template scenarios (docs/specs/gear-templates.md section 8, S1 to S31)
#
# Each starts from the 20 template fixture (`ts-templates-20.sql`, built on an
# empty 28 database) and the gear after migration and adoption
# (`gear-templates-v3.json`), and is a named sequence of steps: edit gear,
# edit TS, set the slot list, push. The plugin's `TsRowScenarios.Gear.cs`
# holds the same recipes in C#, and `dump_golden_rows.py` writes the rows each
# one leaves to the golden file the plugin compares itself to.
# ---------------------------------------------------------------------------

import json  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

from nina_ts_sync.schema import row_plan_guid  # noqa: E402
from .make_db import make_db  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GEAR_PROFILE = "9d3a61fd-9f35-42ae-a911-bd6e4b0f76a7"
CAMERA_6200 = "zwo-asi6200mm-pro"
CAMERA_2600 = "zwo-asi2600mm-pro"
WHEEL_SLOTS = ["L", "R", "G", "B", "H", "O", "S"]
NO_SII = ["L", "R", "G", "B", "H", "O"]
EXTRA_HA_600 = "11111111-1111-4111-8111-111111111111"
EXTRA_HA_600_S9 = "22222222-2222-4222-8222-222222222222"


@dataclass
class GearRun:
    """What one gear scenario step left behind."""
    report: object
    gear: dict
    plans: list = field(default_factory=list)

    @property
    def notes(self) -> list[str]:
        return self.report.notes

    def result_for(self, template_id: str) -> dict:
        (hit,) = [r for r in self.report.templates if r["template_id"] == template_id]
        return hit


def make_template_db(path) -> Path:
    """An empty 28 database holding the fixture's 20 templates and 244
    exposure plans. The fixture's plans name targets that have no row here."""
    path = make_db(USER_VERSION, Path(path))
    conn = sqlite3.connect(path)
    try:
        conn.executescript((FIXTURES / "ts-templates-20.sql").read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return path


def gear_v3() -> dict:
    """The gear after the migration and adoption (gear-templates-v3.json)."""
    return json.loads((FIXTURES / "gear-templates-v3.json").read_text(encoding="utf-8"))


def gear_camera(gear: dict, camera_id: str = CAMERA_6200) -> dict:
    return next(c for c in gear["cameras"] if c["id"] == camera_id)


def gear_template(gear: dict, name: str, camera_id: str = CAMERA_6200) -> dict:
    (hit,) = [t for cfg in gear_camera(gear, camera_id)["filters"].values()
              for t in cfg.get("templates", []) if t["name"] == name]
    return hit


def gear_filter_name(gear: dict, template: dict, camera_id: str = CAMERA_6200) -> str:
    return next(f for f, cfg in gear_camera(gear, camera_id)["filters"].items()
                if any(t is template for t in cfg.get("templates", [])))


def gear_templates_of(gear: dict, template: dict, camera_id: str = CAMERA_6200) -> list[dict]:
    return gear_camera(gear, camera_id)["filters"][gear_filter_name(gear, template, camera_id)]["templates"]


def gear_row_for(gear: dict, row_id: str, template: dict, hours: float = 1.0, sub: float | None = None,
                 camera_id: str = CAMERA_6200) -> dict:
    """A row on a gear template: it carries the template's id and name,
    follows its sub unless *sub* says otherwise, and asks for *hours*."""
    return {
        "id": row_id, "filter": gear_filter_name(gear, template, camera_id),
        "template": template["name"], "template_id": template["id"],
        "template_sub_s": sub if sub is not None else template["sub_s"],
        "unit": "h", "target_hours": hours,
    }


def gear_row(gear: dict, row_id: str, template_name: str, hours: float = 1.0, sub: float | None = None,
             camera_id: str = CAMERA_6200) -> dict:
    return gear_row_for(gear, row_id, gear_template(gear, template_name, camera_id), hours, sub, camera_id)


def gear_plan(plan_id: str, target: str, camera_id: str, *rows: dict) -> dict:
    return plan(plan_id, project_name=target, target_name=target, telescope_id="telescope-2",
                camera_id=camera_id, filter_rows=list(rows))


def gear_push(conn, gear: dict, slots, *plans: dict, profile_name: str = "Foo") -> GearRun:
    report = upsert.apply(conn, convert.build_payload(
        list(plans), gear, profile_id=GEAR_PROFILE, wheel_slots=slots, profile_name=profile_name))
    conn.commit()
    return GearRun(report=report, gear=gear, plans=list(plans))


def gear_link(conn, gear: dict, run: GearRun, *plans: dict) -> None:
    """What a push leaves on each plan: its link for the gear profile, with
    the base the next push compares with."""
    snap = read_all(conn, GEAR_PROFILE)
    for p in plans:
        camera = gear_camera(gear, p["camera_id"])
        old = (link_for(p, GEAR_PROFILE) or {}).get("base_snapshot")
        refs = build_ts_refs(snap, p, profile_id=GEAR_PROFILE, user_version=USER_VERSION,
                             operation="push", camera=camera)
        base = build_base_snapshot(snap, p, profile_id=GEAR_PROFILE, old_base=old,
                                   order_targets=run.report.order_synced_target_ids, camera=camera)
        write_link(p, GEAR_PROFILE, machine=None, refs=refs, base_snapshot=base)


def record_results(run: GearRun) -> None:
    """Record the results of a push on the gear templates the way ACP's
    record_template_results does, so the next push has the new base."""
    every = {t["id"]: t for cam in run.gear["cameras"] for cfg in cam.get("filters", {}).values()
             for t in cfg.get("templates", [])}
    for result in run.report.templates:
        if result["ts_id"] is None:
            continue
        every[result["template_id"]].setdefault("ts", {})[GEAR_PROFILE] = {
            "id": result["ts_id"], "guid": result["guid"], "base": copy.deepcopy(result["base"])}


def gear_plan_guid(p: dict, row_id: str) -> str:
    r = next(r for r in p["filter_rows"] if r["id"] == row_id)
    t_guid = target_guid(GEAR_PROFILE, p["project_name"], p["target"]["name"])
    return row_plan_guid(GEAR_PROFILE, t_guid, r["filter"], r["template"])


def template_rows(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM exposuretemplate ORDER BY Id")]


def cell(conn, sql: str):
    row = conn.execute(sql).fetchone()
    return None if row is None else row[0]


def _ha(gear: dict, hours: float = 3.0) -> dict:
    return gear_plan("gear-ha", "M42", CAMERA_6200, gear_row(gear, "row-ha", "Ha", hours))


def gear_s1(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s2(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    gear_template(gear, "Ha")["offset"] = 25
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s3(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    gear_template(gear, "Ha")["offset"] = 25
    conn.execute("UPDATE exposuretemplate SET offset = 10 WHERE Id = 12")
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s4(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    conn.execute("UPDATE exposuretemplate SET offset = 10 WHERE Id = 12")
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s5(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    ha = gear_template(gear, "Ha")
    ha["name"] = "Ha 300"
    ha["aliases"].insert(0, "Ha")
    return gear_push(conn, gear, WHEEL_SLOTS,
                     gear_plan("gear-ha", "M42", CAMERA_6200, gear_row(gear, "row-ha", "Ha 300")))


def gear_s6(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    ha = gear_template(gear, "Ha")
    ha["name"] = "OIII"
    ha["aliases"].insert(0, "Ha")
    return gear_push(conn, gear, WHEEL_SLOTS,
                     gear_plan("gear-ha", "M42", CAMERA_6200, gear_row_for(gear, "row-ha", ha)))


def gear_s7(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    ha = gear_template(gear, "Ha")
    gear_templates_of(gear, ha).append({
        "id": EXTRA_HA_600, "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": 25, "bin": 1,
        "starred": False, "aliases": [], "ts_names": [], "ts": {}})
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s8(conn, gear=None, slots=None) -> GearRun:
    gear = gear or gear_v3()
    p = gear_plan("gear-sii", "M42", CAMERA_6200, gear_row(gear, "row-sii", "SII"))
    return gear_push(conn, gear, NO_SII if slots is None else slots, p)


def gear_s8_then_slot(conn, gear=None) -> GearRun:
    """S8's second step: the slot appears and the next push writes SII."""
    gear = gear or gear_v3()
    gear_s8(conn, gear, NO_SII)
    return gear_s8(conn, gear, WHEEL_SLOTS)


def gear_s9(conn, gear=None, slots=()) -> GearRun:
    gear = gear or gear_v3()
    for name in ("Ha", "OIII", "SII"):
        gear_template(gear, name)["offset"] = 25
    ha = gear_template(gear, "Ha")
    extra = {"id": EXTRA_HA_600_S9, "name": "Ha 600", "sub_s": 600, "gain": -1, "offset": 25, "bin": 1,
             "starred": False, "aliases": [], "ts_names": [], "ts": {}}
    gear_templates_of(gear, ha).append(extra)
    row_600 = {"id": "row-600", "filter": "Ha", "template": "Ha 600", "template_id": extra["id"],
               "template_sub_s": 600, "unit": "h", "target_hours": 1.0}
    return gear_push(conn, gear, None if slots is None else list(slots),
                     gear_plan("gear-ha", "M42", CAMERA_6200, row_600))


def gear_s10(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s11(conn, gear=None) -> GearRun:
    """The plan sits on id 12 at an explicit 300; the template and the row
    move to 600."""
    gear = gear or gear_v3()
    p = _ha(gear)
    first = gear_push(conn, gear, WHEEL_SLOTS, p)
    conn.execute("UPDATE exposureplan SET exposure = 300 WHERE guid = ?", (gear_plan_guid(p, "row-ha"),))
    gear_link(conn, gear, first, p)
    record_results(first)
    gear_template(gear, "Ha")["sub_s"] = 600
    p["filter_rows"][0]["template_sub_s"] = 600
    return gear_push(conn, gear, WHEEL_SLOTS, p)


def gear_s12(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    ha = gear_template(gear, "Ha")
    ha["gain"] = 0
    ha["ts"][GEAR_PROFILE]["base"]["gain"] = 0
    conn.execute("UPDATE exposuretemplate SET gain = 0 WHERE Id = 12")
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s13(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    p = _ha(gear)
    first = gear_push(conn, gear, WHEEL_SLOTS, p)
    gear_link(conn, gear, first, p)
    record_results(first)
    conn.execute("UPDATE exposureplan SET exposureTemplateId = 4 WHERE guid = ?", (gear_plan_guid(p, "row-ha"),))
    return gear_push(conn, gear, WHEEL_SLOTS, p)


def gear_s14(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    p = _ha(gear)
    first = gear_push(conn, gear, WHEEL_SLOTS, p)
    gear_link(conn, gear, first, p)
    record_results(first)
    oiii = gear_template(gear, "OIII")
    r = p["filter_rows"][0]
    r.update(filter="OIII", template=oiii["name"], template_id=oiii["id"], template_sub_s=oiii["sub_s"])
    # The plan's guid names the template, so it is found again through the
    # id pinned in ts_refs, as after any template change.
    return gear_push(conn, gear, WHEEL_SLOTS, p)


def gear_s15(conn, gear=None, base_name="R Stars (ZWO ASI6200MM Pro)") -> GearRun:
    """A base entry from before gear templates (no `template_id`, the old
    TS name) on a row of "R Stars", whose plan sits on TS id 15 by hand."""
    gear = gear or gear_v3()
    p = gear_plan("gear-rs", "M42", CAMERA_6200, gear_row(gear, "row-rs", "R Stars", 1.0))
    first = gear_push(conn, gear, WHEEL_SLOTS, p)
    gear_link(conn, gear, first, p)
    record_results(first)
    conn.execute("UPDATE exposureplan SET exposureTemplateId = 15 WHERE guid = ?", (gear_plan_guid(p, "row-rs"),))
    entry = p["ts_links"][GEAR_PROFILE]["base_snapshot"]["exposure_plans_by_panel_filter"]["1,1_row-rs"]
    entry.pop("template_id", None)
    entry["template"] = base_name
    return gear_push(conn, gear, WHEEL_SLOTS, p)


def gear_s16(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    other = gear_template(gear, "Ha (ZWO ASI2600MM Pro)", CAMERA_2600)
    other["name"] = "Ha"
    return gear_push(
        conn, gear, WHEEL_SLOTS,
        gear_plan("gear-ha", "M42", CAMERA_6200, gear_row(gear, "row-ha", "Ha")),
        gear_plan("gear-ha2", "M43", CAMERA_2600, gear_row(gear, "row-ha2", "Ha", 1.0, None, CAMERA_2600)))


def gear_s17(conn, gear=None, linked=True) -> GearRun:
    gear = gear or gear_v3()
    l = gear_template(gear, "L")
    if linked:
        l["offset"] = 25
    else:
        l["ts"] = {}
    return gear_push(conn, gear, WHEEL_SLOTS,
                     gear_plan("gear-l", "M42", CAMERA_6200, gear_row(gear, "row-l", "L")))


def gear_s17_unlinked(conn, gear=None) -> GearRun:
    return gear_s17(conn, gear, linked=False)


def gear_s18(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    other = gear_template(gear, "B (ZWO ASI2600MM Pro)", CAMERA_2600)
    other["name"] = "B"
    return gear_push(conn, gear, WHEEL_SLOTS,
                     gear_plan("gear-b", "M43", CAMERA_2600, gear_row(gear, "row-b", "B", 1.0, None, CAMERA_2600)))


def gear_s19(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    ha = gear_template(gear, "Ha")
    ha["ts"] = {}
    ha["offset"] = 25
    return gear_push(conn, gear, WHEEL_SLOTS, _ha(gear))


def gear_s20(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    stars = gear_template(gear, "R Stars")
    stars["ts"] = {}
    stars["name"] = "R Stars 2"
    stars["aliases"].insert(0, "R")
    return gear_push(conn, gear, WHEEL_SLOTS,
                     gear_plan("gear-rs", "M42", CAMERA_6200, gear_row(gear, "row-rs", "R Stars 2", 0.0)))


def gear_s23(conn, gear=None) -> GearRun:
    """A row whose pinned exposure plan ids are not plans of its target."""
    gear = gear or gear_v3()
    p = _ha(gear)
    first = gear_push(conn, gear, WHEEL_SLOTS, p)
    gear_link(conn, gear, first, p)
    record_results(first)
    refs = p["ts_links"][GEAR_PROFILE]["refs"]
    (key,) = list(refs["exposure_plan_ids"])
    tid = key.split("_")[0]
    refs["exposure_plan_ids"] = {f"{tid}_row-ha": 21, f"{tid}_row-x": 22, f"{tid}_row-y": 23}
    return gear_push(conn, gear, WHEEL_SLOTS, p)


def gear_s24(conn, gear=None) -> GearRun:
    """Ha 300 s, the row deviating at an explicit 400, then ACP moves the
    row to 450."""
    gear = gear or gear_v3()
    p = gear_plan("gear-ha", "M42", CAMERA_6200, gear_row(gear, "row-ha", "Ha", 3.0, 400.0))
    first = gear_push(conn, gear, WHEEL_SLOTS, p)
    gear_link(conn, gear, first, p)
    record_results(first)
    p["filter_rows"][0]["template_sub_s"] = 450
    return gear_push(conn, gear, WHEEL_SLOTS, p)


def gear_s25(conn, gear=None) -> GearRun:
    """A row at 120 s on template 19 follows it; ACP moves the row and the
    template to 150 s together."""
    gear = gear or gear_v3()
    r = gear_template(gear, "R (ZWO ASI6200MM Pro)")
    p = gear_plan("gear-r", "M42", CAMERA_6200, gear_row_for(gear, "row-r", r, 3.0))
    first = gear_push(conn, gear, WHEEL_SLOTS, p)
    conn.execute("UPDATE exposureplan SET exposure = 120 WHERE guid = ?", (gear_plan_guid(p, "row-r"),))
    gear_link(conn, gear, first, p)
    record_results(first)
    r["sub_s"] = 150
    p["filter_rows"][0]["template_sub_s"] = 150
    return gear_push(conn, gear, WHEEL_SLOTS, p)


def gear_s30(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    r = gear_template(gear, "R (ZWO ASI6200MM Pro)")
    r["ts"] = {}
    # Its plans stay behind in the fixture; nothing enforces the keys here.
    conn.execute("DELETE FROM exposuretemplate WHERE Id = 19")
    return gear_push(conn, gear, WHEEL_SLOTS,
                     gear_plan("gear-r", "M42", CAMERA_6200, gear_row(gear, "row-r", "R (ZWO ASI6200MM Pro)", 0.0)))


def gear_s31(conn, gear=None) -> GearRun:
    gear = gear or gear_v3()
    ha = gear_template(gear, "Ha")
    ha["name"] = "Ha 300"
    ha["aliases"].insert(0, "Ha")
    ha["ts"] = {}
    return gear_push(conn, gear, WHEEL_SLOTS,
                     gear_plan("gear-ha", "M42", CAMERA_6200, gear_row(gear, "row-ha", "Ha 300")))


def _golden(fn, **kw):
    return lambda conn: fn(conn, **kw).report


#: name -> scenario over a database holding the 20 template fixture, in the
#: order the golden file lists them. Each takes the connection and returns the
#: last push's report.
GEAR_SCENARIOS = {
    "gear_s1_no_edits": _golden(gear_s1),
    "gear_s2_offset_written": _golden(gear_s2),
    "gear_s3_conflict_held": _golden(gear_s3),
    "gear_s4_ts_edit_left_for_pull": _golden(gear_s4),
    "gear_s5_rename_in_place": _golden(gear_s5),
    "gear_s6_rename_name_taken": _golden(gear_s6),
    "gear_s7_new_template_inserted": _golden(gear_s7),
    "gear_s8_off_wheel_skipped": _golden(gear_s8),
    "gear_s8_slot_appears": _golden(gear_s8_then_slot),
    "gear_s9_unknown_wheel": _golden(gear_s9),
    "gear_s9_wheel_not_given": _golden(gear_s9, slots=None),
    "gear_s10_strays": _golden(gear_s10),
    "gear_s11_sub_change": _golden(gear_s11),
    "gear_s12_zero_kept": _golden(gear_s12),
    "gear_s13_plan_not_moved": _golden(gear_s13),
    "gear_s14_plan_moved": _golden(gear_s14),
    "gear_s15_old_base_name_known": _golden(gear_s15),
    "gear_s15_old_base_name_unknown": _golden(gear_s15, base_name="Something else"),
    "gear_s16_name_clash": _golden(gear_s16),
    "gear_s17_duplicate_names": _golden(gear_s17),
    "gear_s17_duplicate_names_unlinked": _golden(gear_s17_unlinked),
    "gear_s18_duplicate_not_linked": _golden(gear_s18),
    "gear_s19_adopted_by_name": _golden(gear_s19),
    "gear_s20_alias_other_group": _golden(gear_s20),
    "gear_s23_pins_not_in_ts": _golden(gear_s23),
    "gear_s24_row_sub_deviates": _golden(gear_s24),
    "gear_s25_row_follows_template": _golden(gear_s25),
    "gear_s30_ts_names_never_find": _golden(gear_s30),
    "gear_s31_alias_finds_row": _golden(gear_s31),
}
