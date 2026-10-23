"""When a push replaces an override order in TS: only when the user changed
the order in ACP (goal-panel-and-ts-order.md section 5).

Mosaics decide per target, from the anchor panel's base order. Keeping ACP
on an import is a choice on the order itself. A row added anywhere but the
end counts as a reorder. A base built with no push outcome never takes rows
set in TS for rows the sync wrote.
"""
from __future__ import annotations

import sqlite3

import pytest

from nina_ts_sync import import_merge
from nina_ts_sync.order import as_rows, same_order
from nina_ts_sync.reader import read_all
from nina_ts_sync.state import build_base_snapshot
from . import row_scenarios as sc
from .make_db import make_db
from .plans import PROFILE_ID, plan

HAND = [(0, 2), (0, 0), (0, 1)]


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(make_db(28, tmp_path / "ts.sqlite"))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def rows3():
    return [sc.row("row-ha", "Ha", "Ha", 300, frames=4), sc.row("row-oiii", "OIII", "OIII", 300, frames=4),
            sc.row("row-sii", "SII", "SII", 300, frames=4)]


def ov(conn, tid):
    return [(r[0], r[1]) for r in conn.execute(
        'SELECT "action", referenceIdx FROM overrideexposureorderitem WHERE targetid=? ORDER BY "order"', (tid,))]


def blocks(rows):
    """The referenceIdx of each block of rows, in order."""
    out = []
    for action, ref in rows:
        if not out or out[-1] != (action, ref):
            out.append((action, ref))
    return out


def strip_base_order(p):
    """A base from before the order was recorded."""
    for e in p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"].values():
        e.pop("exposure_order", None)
        e.pop("override_rows", None)


def view(conn, plans):
    return import_merge.build_view(read_all(conn, PROFILE_ID), 28, plans, sc.ROW_GEAR,
                                   profile_id=PROFILE_ID, machine=None, strict=False)


def do_import(conn, plans, decisions=None):
    v = view(conn, plans)
    import_merge.apply_view(v, decisions=decisions or {}, add=[], new_plan_gear=None, require_decisions=False)
    return v


def mosaic():
    return plan("mos", project_name="Mos", target_name="Mos", filter_rows=rows3(), rows=1, cols=2)


def mosaic_targets(conn):
    return [r[0] for r in conn.execute("SELECT Id FROM target ORDER BY Id")]


def linked_m42(conn, rows):
    p = sc.m42(rows)
    sc.link(conn, [p], sc.push(conn, [p]))
    strip_base_order(p)
    tid = sc.m42_target_id(conn)
    return p, tid


# -- 1: a mosaic decides per target ---------------------------------------------


def test_mosaic_hand_order_on_panel_two_survives_push_import_push(conn):
    p = mosaic()
    sc.link(conn, [p], sc.push(conn, [p]))
    strip_base_order(p)
    t1, t2 = mosaic_targets(conn)
    sc.set_override(conn, t2, HAND)
    conn.commit()
    sc.link(conn, [p], sc.push(conn, [p]))
    assert ov(conn, t2) == HAND
    v = do_import(conn, [p])
    (p,) = v.plans
    base = p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"]
    assert base["1,2"]["exposure_order"] == base["1,1"]["exposure_order"]
    sc.push(conn, [p])
    assert ov(conn, t2) == HAND
    assert ov(conn, t1) == []


def test_mosaic_hand_order_on_panel_one_is_not_copied_to_panel_two(conn):
    p = mosaic()
    sc.link(conn, [p], sc.push(conn, [p]))
    strip_base_order(p)
    t1, t2 = mosaic_targets(conn)
    sc.set_override(conn, t1, HAND)
    conn.commit()
    sc.link(conn, [p], sc.push(conn, [p]))
    assert ov(conn, t1) == HAND and ov(conn, t2) == []
    v = do_import(conn, [p])
    (p,) = v.plans
    assert [r["id"] for r in p["filter_rows"]] == ["row-sii", "row-ha", "row-oiii"]
    sc.push(conn, [p])
    assert ov(conn, t1) == HAND
    assert ov(conn, t2) == []


def test_mosaic_acp_reorder_writes_the_same_order_to_both_panels(conn):
    p = mosaic()
    sc.link(conn, [p], sc.push(conn, [p]))
    strip_base_order(p)
    t1, t2 = mosaic_targets(conn)
    sc.set_override(conn, t2, [(0, 1), (0, 0), (0, 2)])
    conn.commit()
    sc.link(conn, [p], sc.push(conn, [p]))
    v = do_import(conn, [p])
    (p,) = v.plans
    sc.reorder(p, ["row-sii", "row-ha", "row-oiii"])
    report = sc.push(conn, [p])
    assert ov(conn, t1) == ov(conn, t2) != []
    assert {t1, t2} <= report.order_synced_target_ids
    assert any("replaced an order set in Target Scheduler" in n for n in report.notes)


# -- 2: keep ACP on an import ---------------------------------------------------


def hand_order_pulled(conn):
    p, tid = linked_m42(conn, rows3())
    sc.set_override(conn, tid, HAND)
    conn.commit()
    sc.link(conn, [p], sc.push(conn, [p]))
    return p, tid


def test_keep_acp_on_the_plan_still_pulls_the_order(conn):
    p, tid = hand_order_pulled(conn)
    v = view(conn, [p])
    (m,) = v.matches
    assert m.diff.auto_pull == ["exposure_order"]
    # A TS-only change to another field, with the plan kept as ACP's.
    import_merge.apply_view(v, decisions={str(p["id"]): "keep_acp"}, add=[], new_plan_gear=None,
                            require_decisions=False)
    (p,) = v.plans
    assert [r["id"] for r in p["filter_rows"]] == ["row-sii", "row-ha", "row-oiii"]
    sc.push(conn, [p])
    assert ov(conn, tid) == HAND


def test_keep_acp_on_the_order_field_replaces_ts_order_on_the_next_push(conn):
    p, tid = hand_order_pulled(conn)
    v = view(conn, [p])
    applied = import_merge.apply_view(
        v, decisions={import_merge.order_key(str(p["id"])): "keep_acp"}, add=[], new_plan_gear=None,
        require_decisions=False)
    assert applied["order_kept_acp"] == [str(p["id"])]
    (p,) = v.plans
    assert [r["id"] for r in p["filter_rows"]] == ["row-ha", "row-oiii", "row-sii"]
    report = sc.push(conn, [p])
    assert ov(conn, tid) == []
    assert any("replaced an order set in Target Scheduler" in n for n in report.notes)


def test_the_preview_says_what_keeping_acps_order_does(conn):
    p, tid = hand_order_pulled(conn)
    preview = import_merge.preview_dict(view(conn, [p]), sc.ROW_GEAR)
    (u,) = preview["profiles"][0]["updated"] if "profiles" in preview else preview["updated"]
    (field,) = [f for f in u["fields"] if f["path"] == "exposure_order"]
    assert field["keep_acp_note"] == "Your next push will replace the order set in Target Scheduler."


# -- 3: a row added anywhere but the end ----------------------------------------


def test_same_order_counts_a_row_added_before_a_base_row_as_a_reorder():
    assert same_order(["a", "b", "c"], ["a", "b"])
    assert not same_order(["c", "a", "b"], ["a", "b"])
    assert not same_order(["a", "c", "b"], ["a", "b"])
    assert same_order(["a", "c"], ["a", "b", "c"])
    assert same_order(["a", "b"], [])


def c_state(conn):
    p, tid = linked_m42(conn, rows3()[:2])
    sc.set_override(conn, tid, [(0, 1), (0, 0)])
    conn.commit()
    sc.link(conn, [p], sc.push(conn, [p]))
    v = do_import(conn, [p])
    (p,) = v.plans
    return p, tid


def test_added_row_dragged_to_the_top_is_a_reorder(conn):
    p, tid = c_state(conn)
    p["filter_rows"].insert(0, sc.row("row-sii", "SII", "SII", 300, frames=4))
    report = sc.push(conn, [p])
    assert blocks(ov(conn, tid)) == [(0, 2), (0, 1), (0, 0)]
    assert any("replaced an order set in Target Scheduler" in n for n in report.notes)


def test_added_row_at_the_end_leaves_the_hand_order(conn):
    p, tid = c_state(conn)
    p["filter_rows"].append(sc.row("row-sii", "SII", "SII", 300, frames=4))
    sc.push(conn, [p])
    assert blocks(ov(conn, tid)) == [(0, 1), (0, 0), (0, 2)]
    assert ov(conn, tid)[:2] == [(0, 1), (0, 0)]


def test_diff_counts_an_added_row_placed_early_as_acps_change(conn):
    p, tid = c_state(conn)
    sc.link(conn, [p], sc.push(conn, [p]))
    p["filter_rows"].insert(0, sc.row("row-sii", "SII", "SII", 300, frames=4))
    # SII is not in TS yet, so it is not in the remote plan.
    (m,) = view(conn, [p]).matches
    assert "exposure_order" in m.diff.auto_keep
    q = p
    q["filter_rows"] = [r for r in q["filter_rows"] if r["id"] != "row-sii"] + [sc.row("row-sii", "SII", "SII", 300, frames=4)]
    (m,) = view(conn, [q]).matches
    assert "exposure_order" not in m.diff.auto_keep + m.diff.auto_pull + m.diff.conflict


# -- 4: no push outcome ---------------------------------------------------------


def test_a_base_with_no_push_outcome_does_not_take_hand_rows_for_the_syncs(conn):
    p, tid = linked_m42(conn, rows3())
    sc.set_override(conn, tid, HAND)
    conn.commit()
    snap = read_all(conn, PROFILE_ID)
    entry = build_base_snapshot(snap, p, profile_id=PROFILE_ID)["targets_by_panel"]["1,1"]
    assert entry["exposure_order"] == ["row-sii", "row-ha", "row-oiii"]
    assert "override_rows" not in entry
    sc.set_override(conn, tid, [])
    conn.commit()
    entry = build_base_snapshot(read_all(conn, PROFILE_ID), p, profile_id=PROFILE_ID)["targets_by_panel"]["1,1"]
    assert entry["override_rows"] == []


# -- 6: malformed stored rows ---------------------------------------------------


@pytest.mark.parametrize("raw", [
    "x", [[0, 1, 2]], [[0]], [["0", 1]], [[0, 1.0]], [[True, 1]], [None], [[0, 2**31]], {"a": 1},
])
def test_malformed_stored_rows_are_absent(raw):
    assert as_rows(raw) is None


def test_well_formed_stored_rows_are_read():
    assert as_rows([[0, 1], [1, -1]]) == [(0, 1), (1, -1)]
    assert as_rows([]) == []


# -- 6: the push base names derived rows' templates with the camera ------------


def test_the_push_base_reads_derived_rows_with_the_camera(conn):
    from nina_ts_sync.state import stamp_plans
    p = sc.old_acp({"Ha": {"target_hours": 1.0, "sub_exposure_s": 300},
                    "SII": {"target_hours": 1.0, "sub_exposure_s": 300}})
    report = sc.push(conn, [p])
    snap = read_all(conn, PROFILE_ID)
    with_camera = [dict(p)]
    stamp_plans(snap, with_camera, profile_id=PROFILE_ID, user_version=28, operation="push",
                order_targets=report.order_synced_target_ids, gear_data=sc.ROW_GEAR)
    entry = with_camera[0]["ts_base_snapshot"]["targets_by_panel"]["1,1"]
    assert len(entry["exposure_order"]) == 2


# -- 7: rows the base knew, a keep on a conflict, stale sync rows ----------------


def push_link(conn, p):
    report = sc.push(conn, [p])
    sc.link(conn, [p], report)
    return report


def test_same_order_does_not_count_a_known_row_gaining_a_plan_as_a_reorder():
    assert not same_order(["a", "c", "b"], ["a", "b"])
    assert same_order(["a", "c", "b"], ["a", "b"], ["a", "b", "c"])
    assert not same_order(["c", "a", "b"], ["a", "b"], ["a", "b", "d"])
    assert not same_order(["b", "a", "c"], ["a", "b"], ["a", "b", "c"])


def test_the_base_records_rows_with_no_plan(conn):
    rows = rows3()
    rows[1] = sc.row("row-oiii", "OIII", "OIII", 300, frames=0)
    p, tid = linked_m42(conn, rows)
    entry = p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"]["1,1"]
    assert entry["known_rows"] == ["row-ha", "row-oiii", "row-sii"]
    strip_base_order(p)


def d_state(conn):
    rows = rows3()
    rows[1] = sc.row("row-oiii", "OIII", "OIII", 300, frames=0)
    p, tid = linked_m42(conn, rows)
    sc.set_override(conn, tid, [(0, 1), (0, 0)])
    conn.commit()
    push_link(conn, p)
    v = do_import(conn, [p])
    (p,) = v.plans
    return p, tid


def raise_oiii(p):
    for r in p["filter_rows"]:
        if r["id"] == "row-oiii":
            r["target_frames"] = 4


def test_raising_a_goal_from_zero_keeps_the_hand_order(conn):
    p, tid = d_state(conn)
    raise_oiii(p)
    report = push_link(conn, p)
    rows = ov(conn, tid)
    assert rows[:2] == [(0, 1), (0, 0)]
    assert not any("replaced an order set in Target Scheduler" in n for n in report.notes)


def test_a_base_with_no_known_rows_falls_back_to_the_rows_that_had_plans(conn):
    p, tid = d_state(conn)
    for e in p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"].values():
        e.pop("known_rows", None)
    raise_oiii(p)
    report = sc.push(conn, [p])
    assert any("replaced an order set in Target Scheduler" in n for n in report.notes)


def test_diff_does_not_count_a_known_row_gaining_a_plan_as_acps_change(conn):
    p, tid = d_state(conn)
    raise_oiii(p)
    push_link(conn, p)
    (m,) = view(conn, [p]).matches
    assert "exposure_order" not in m.diff.auto_keep + m.diff.auto_pull + m.diff.conflict


def e_state(conn):
    p, tid = linked_m42(conn, rows3())
    sc.link(conn, [p], sc.push(conn, [p]))
    sc.set_override(conn, tid, HAND)
    conn.execute("UPDATE exposureplan SET desired = 9 WHERE Id = (SELECT MIN(Id) FROM exposureplan)")
    conn.commit()
    p["filter_rows"][0]["target_frames"] = 7
    return p, tid


def test_keep_acp_on_a_conflict_still_pulls_an_order_that_is_no_part_of_it(conn):
    p, tid = e_state(conn)
    v = view(conn, [p])
    (m,) = v.matches
    assert m.diff.has_conflict and "exposure_order" in m.diff.auto_pull
    applied = import_merge.apply_view(v, decisions={str(p["id"]): "keep_acp"}, add=[], new_plan_gear=None,
                                      require_decisions=False)
    assert applied["order_pulled"] == [str(p["id"])]
    (p,) = v.plans
    assert [r["id"] for r in p["filter_rows"]] == ["row-sii", "row-ha", "row-oiii"]
    push_link(conn, p)
    assert ov(conn, tid) == HAND


def test_keep_acp_on_the_order_as_well_replaces_the_order_after_a_conflict(conn):
    p, tid = e_state(conn)
    v = view(conn, [p])
    import_merge.apply_view(
        v, decisions={str(p["id"]): "keep_acp", import_merge.order_key(str(p["id"])): "keep_acp"},
        add=[], new_plan_gear=None, require_decisions=False)
    (p,) = v.plans
    assert [r["id"] for r in p["filter_rows"]] == ["row-ha", "row-oiii", "row-sii"]


def test_the_conflict_card_marks_the_order_only_when_it_is_in_the_conflict(conn):
    p, tid = e_state(conn)
    preview = import_merge.preview_dict(view(conn, [p]), sc.ROW_GEAR)
    (c,) = preview["conflicts"]
    (f,) = [f for f in c["fields"] if f["path"] == "exposure_order"]
    assert f["in_conflict"] is False


def test_stale_sync_rows_on_panel_two_are_refreshed_and_panel_one_keeps_its_hand_order(conn):
    p = mosaic()
    push_link(conn, p)
    sc.reorder(p, ["row-oiii", "row-ha", "row-sii"])
    push_link(conn, p)
    t1, t2 = mosaic_targets(conn)
    sc.set_override(conn, t1, HAND)
    conn.commit()
    (p,) = do_import(conn, [p]).plans
    assert [r["id"] for r in p["filter_rows"]] == ["row-sii", "row-ha", "row-oiii"]
    push_link(conn, p)
    assert ov(conn, t1) == HAND
    rows2 = ov(conn, t2)
    assert blocks(rows2)[:3] == [(0, 2), (0, 0), (0, 1)]


# -- 8: re-enabled plan with no rows, known rows on import, lasting mismatch ---


def h_state(conn):
    """Natural order Ha, OIII so no rows were written. Ha is removed and
    pushed (its plan is disabled), then added back at the end: the plan is
    enabled again with its lower Id."""
    p = sc.m42(rows3()[:2])
    push_link(conn, p)
    tid = sc.m42_target_id(conn)
    p["filter_rows"] = [r for r in p["filter_rows"] if r["id"] != "row-ha"]
    push_link(conn, p)
    p["filter_rows"].append(sc.row("row-ha2", "Ha", "Ha", 300, frames=4))
    return p, tid


def test_a_plan_added_back_at_the_end_with_no_rows_still_shoots_in_acps_order(conn):
    p, tid = h_state(conn)
    push_link(conn, p)
    # OIII is second in natural order now (Ha has the lower Id), ACP lists it first.
    assert blocks(ov(conn, tid)) == [(0, 1), (0, 0)]


def test_an_empty_base_and_empty_ts_rows_leave_a_target_whose_natural_order_is_not_acps(conn):
    p = mosaic()
    push_link(conn, p)
    t1, t2 = mosaic_targets(conn)
    sc.reorder(p, ["row-sii", "row-ha", "row-oiii"])
    sc.set_override(conn, t1, HAND)
    conn.commit()
    (p,) = do_import(conn, [p]).plans
    push_link(conn, p)
    assert ov(conn, t1) == HAND
    assert ov(conn, t2) == []


def j_state(conn):
    """A hand order OIII, Ha; the user adds SII mid-list in ACP (a reorder),
    then imports before pushing."""
    p, tid = linked_m42(conn, rows3()[:2])
    sc.set_override(conn, tid, [(0, 1), (0, 0)])
    conn.commit()
    push_link(conn, p)
    (p,) = do_import(conn, [p]).plans
    p["filter_rows"].insert(1, sc.row("row-sii", "SII", "SII", 300, frames=4))
    (p,) = do_import(conn, [p]).plans
    return p, tid


def test_an_import_does_not_make_a_row_added_in_acp_known(conn):
    p, tid = j_state(conn)
    entry = p["ts_links"][PROFILE_ID]["base_snapshot"]["targets_by_panel"]["1,1"]
    assert "row-sii" not in entry["known_rows"]
    assert [r["id"] for r in p["filter_rows"]] == ["row-oiii", "row-sii", "row-ha"]


def test_the_next_push_writes_acps_order_for_a_row_added_before_an_import(conn):
    p, tid = j_state(conn)
    report = push_link(conn, p)
    assert blocks(ov(conn, tid)) == [(0, 1), (0, 2), (0, 0)]
    assert report.order_written


def test_an_import_notes_an_order_ts_will_not_shoot(conn):
    p, tid = d_state(conn)
    raise_oiii(p)
    push_link(conn, p)
    v = do_import(conn, [p])
    (note,) = [n for n in v.notes if n.level == "warn" and "Target Scheduler will shoot" in n.message]
    assert note.target == "M42"
    assert note.message == ("M42: ACP lists SII, OIII, Ha but Target Scheduler will shoot SII, Ha, OIII. "
                            "Push after reordering in ACP to change it.")


def test_an_import_that_leaves_acp_in_ts_order_has_no_order_note(conn):
    p, tid = d_state(conn)
    v = do_import(conn, [p])
    assert not [n for n in v.notes if "Target Scheduler will shoot" in n.message]
