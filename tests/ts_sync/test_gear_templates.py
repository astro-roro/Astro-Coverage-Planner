"""The gear template writer, scenario by scenario, against the 20 template
fixture (docs/specs/gear-templates.md section 8, S1 to S31). The routes that
carry its inputs and results (S21, S22, S29) are in
test_gear_template_routes.py.

The recipes are in tests/row_scenarios.py and are the same ones the plugin's
`TsRowScenarios.Gear.cs` holds; `dump_golden_rows.py` writes the rows each
leaves to the golden file the plugin compares itself to (S27).

Not here, because they live in ACP's `app.py` and its tests: the pull (the
second half of S4, and S28) and adoption (M3 to M5, M10 to M12).
"""
from __future__ import annotations

import copy
import sqlite3

import pytest

from nina_ts_sync import convert, upsert
from nina_ts_sync.reader import read_all
from nina_ts_sync.rows import refresh_template_subs
from nina_ts_sync.schema import gear_template_guid, stable_guid, name_part
from nina_ts_sync.state import build_base_snapshot
from . import row_scenarios as sc
from .row_scenarios import GEAR_PROFILE


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(sc.make_template_db(tmp_path / "ts.sqlite"))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture
def fresh_conn(tmp_path):
    """A second template database, for a scenario run twice."""
    c = sqlite3.connect(sc.make_template_db(tmp_path / "ts2.sqlite"))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def row_of(conn, template_id: int) -> dict:
    return next(r for r in sc.template_rows(conn) if r["Id"] == template_id)


def fixture_plan_max(conn) -> int:
    return sc.cell(conn, "SELECT MAX(Id) FROM exposureplan")


def exposure_of(conn, p: dict, row_id: str):
    return sc.cell(conn, "SELECT exposure FROM exposureplan WHERE guid = ?".replace("?", f"'{sc.gear_plan_guid(p, row_id)}'"))


def template_of(conn, p: dict, row_id: str):
    return sc.cell(conn, f"SELECT exposureTemplateId FROM exposureplan WHERE guid = '{sc.gear_plan_guid(p, row_id)}'")


# -- S1 to S31 -----------------------------------------------------------------


def test_s1_a_push_with_no_gear_edits_writes_nothing_and_reports_ten_unchanged(conn):
    before = sc.template_rows(conn)
    run = sc.gear_s1(conn)
    assert len(run.report.templates) == 10
    assert {t["action"] for t in run.report.templates} == {"unchanged"}
    assert (run.report.exposuretemplate.inserted, run.report.exposuretemplate.updated) == (0, 0)
    assert sc.template_rows(conn) == before


def test_s2_an_offset_changed_in_acp_is_written_to_that_template_only(conn):
    before = sc.template_rows(conn)
    run = sc.gear_s2(conn)
    ha = sc.gear_template(run.gear, "Ha")
    result = run.result_for(ha["id"])
    assert (result["action"], result["fields_written"], result["ts_id"]) == ("updated", ["offset"], 12)
    assert result["base"]["offset"] == 25
    for was, now in zip(before, sc.template_rows(conn)):
        if was["Id"] == 12:
            assert now["offset"] == 25
            now = {**now, "offset": was["offset"]}
        assert now == was


def test_s3_a_field_changed_in_both_places_is_held_and_the_ts_value_survives(conn):
    run = sc.gear_s3(conn)
    assert sc.cell(conn, "SELECT offset FROM exposuretemplate WHERE Id = 12") == 10
    assert "Ha: offset is 25 in ACP and 10 in TS. Left alone until you choose in the gear editor." in run.notes
    result = run.result_for(sc.gear_template(run.gear, "Ha")["id"])
    assert (result["action"], result["fields_written"]) == ("conflict", [])
    assert [c["field"] for c in result["conflicts"]] == ["offset"]
    assert result["conflicts"][0] == {"field": "offset", "acp": 25, "ts": 10}
    # The base keeps the old value, so the hold lasts until the user chooses.
    assert result["base"]["offset"] == -1


def test_s4_a_field_changed_only_in_ts_is_left_for_the_pull(conn):
    run = sc.gear_s4(conn)
    assert sc.cell(conn, "SELECT offset FROM exposuretemplate WHERE Id = 12") == 10
    result = run.result_for(sc.gear_template(run.gear, "Ha")["id"])
    assert (result["action"], result["base"]["offset"]) == ("unchanged", -1)
    assert not any("Left alone" in n for n in run.notes)


def test_s5_a_rename_is_written_to_the_same_row_and_inserts_nothing(conn):
    fixture_max = fixture_plan_max(conn)
    run = sc.gear_s5(conn)
    assert sc.cell(conn, "SELECT name FROM exposuretemplate WHERE Id = 12") == "Ha 300"
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate") == 20
    result = run.result_for(sc.gear_template(run.gear, "Ha 300")["id"])
    assert (result["action"], result["fields_written"], result["base"]["name"]) == ("updated", ["name"], "Ha 300")
    # The 23 plans already on the template are still on it, and the pushed
    # row's plan took the new name's identity without a second plan.
    assert sc.cell(conn, f"SELECT COUNT(*) FROM exposureplan WHERE exposureTemplateId = 12 AND Id <= {fixture_max}") == 23
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan") == 245


def test_s5_a_renamed_templates_plan_keeps_its_counts_and_takes_the_new_guid(conn):
    gear = sc.gear_v3()
    p = sc.gear_plan("gear-ha", "M42", sc.CAMERA_6200, sc.gear_row(gear, "row-ha", "Ha", 3.0))
    first = sc.gear_push(conn, gear, sc.WHEEL_SLOTS, p)
    sc.gear_link(conn, gear, first, p)
    sc.record_results(first)
    conn.execute("UPDATE exposureplan SET acquired = 9, accepted = 7 WHERE guid = ?", (sc.gear_plan_guid(p, "row-ha"),))
    old_plan_id = sc.cell(conn, f"SELECT Id FROM exposureplan WHERE guid = '{sc.gear_plan_guid(p, 'row-ha')}'")
    ha = sc.gear_template(gear, "Ha")
    ha["name"] = "Ha 300"
    ha["aliases"].insert(0, "Ha")
    p["filter_rows"][0]["template"] = "Ha 300"
    sc.gear_push(conn, gear, sc.WHEEL_SLOTS, p)
    row = conn.execute("SELECT * FROM exposureplan WHERE guid = ?", (sc.gear_plan_guid(p, "row-ha"),)).fetchone()
    assert (row["Id"], row["acquired"], row["accepted"], row["exposureTemplateId"]) == (old_plan_id, 9, 7, 12)
    assert ha["aliases"][0] == "Ha"


def test_s6_a_rename_to_a_name_another_template_holds_is_skipped_with_a_note(conn):
    run = sc.gear_s6(conn)
    assert sc.cell(conn, "SELECT name FROM exposuretemplate WHERE Id = 12") == "Ha"
    assert any("the name was skipped" in n and "(id 11)" in n for n in run.notes)


def test_s7_a_new_gear_template_is_inserted_under_the_slot_with_a_guid_from_its_id(conn):
    run = sc.gear_s7(conn)
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate") == 21
    r = conn.execute("SELECT * FROM exposuretemplate WHERE name = 'Ha 600'").fetchone()
    assert (r["filtername"], r["defaultexposure"], r["offset"]) == ("H", 600.0, 25)
    assert r["guid"] == gear_template_guid(GEAR_PROFILE, sc.EXTRA_HA_600)
    assert sc.cell(conn, f"SELECT COUNT(*) FROM exposureplan WHERE exposureTemplateId = {r['Id']}") == 0
    assert run.result_for(sc.EXTRA_HA_600)["action"] == "inserted"


def test_the_inserted_template_guid_is_the_length_prefixed_gear_template_recipe():
    tid = "11111111-1111-4111-8111-111111111111"
    assert gear_template_guid("p", tid) == stable_guid(f"{name_part('p')}/gear-template/{name_part(tid)}")
    assert gear_template_guid("p", tid) == stable_guid(f"1:p/gear-template/36:{tid}")


def test_s8_a_filter_not_on_the_wheel_is_skipped_until_the_slot_appears(conn):
    sii_before = row_of(conn, 13)
    run = sc.gear_s8(conn)
    assert row_of(conn, 13) == sii_before
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan") == 244
    assert any("SII is not on the wheel of Foo yet, so 1 row was skipped" in n for n in run.notes)
    assert run.result_for(sc.gear_template(run.gear, "SII")["id"])["action"] == "skipped"
    sc.gear_s8(conn, run.gear, sc.WHEEL_SLOTS)
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan") == 245
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan WHERE exposureTemplateId = 13") == 4


def test_s9_an_unknown_wheel_updates_existing_rows_without_their_filtername(conn, fresh_conn):
    run = sc.gear_s9(conn, slots=[])
    for tid in (12, 11, 13):
        assert sc.cell(conn, f"SELECT offset FROM exposuretemplate WHERE Id = {tid}") == 25
    assert [sc.cell(conn, f"SELECT filtername FROM exposuretemplate WHERE Id = {t}") for t in (12, 11, 13)] == ["H", "O", "S"]
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate") == 20
    assert any("the wheel of Foo is unknown, so 1 row was skipped" in n for n in run.notes)
    skipped = run.result_for(sc.EXTRA_HA_600_S9)
    assert skipped["action"] == "skipped"
    # No wheel list at all gives the same rows.
    sc.gear_s9(fresh_conn, slots=None)
    assert sc.template_rows(conn) == sc.template_rows(fresh_conn)


def test_s10_templates_no_gear_template_stands_for_are_listed_as_stray(conn):
    run = sc.gear_s10(conn)
    assert sorted(run.report.stray_template_ids) == [1, 2, 3, 4, 8, 9, 10, 15, 17, 20]
    assert run.report.to_dict()["stray_template_ids"] == run.report.stray_template_ids


def test_s11_a_sub_change_writes_the_default_and_puts_following_plans_back_on_it(conn):
    fixture_max = fixture_plan_max(conn)
    run = sc.gear_s11(conn)
    assert sc.cell(conn, "SELECT defaultexposure FROM exposuretemplate WHERE Id = 12") == 600.0
    p = run.plans[0]
    assert exposure_of(conn, p, "row-ha") == -1.0
    # Plans of other projects, not in the push, keep their explicit exposure.
    assert sc.cell(
        conn, f"SELECT COUNT(*) FROM exposureplan WHERE exposureTemplateId = 12 AND Id <= {fixture_max} AND exposure = 300") == 23


def test_s12_a_gain_of_zero_stays_zero_in_the_base(conn):
    run = sc.gear_s12(conn)
    assert run.result_for(sc.gear_template(run.gear, "Ha")["id"])["base"]["gain"] == 0


def test_s12_the_snapshot_keeps_a_gain_of_zero_too(conn):
    conn.execute("UPDATE exposuretemplate SET gain = 0, \"offset\" = 0 WHERE Id = 12")
    gear = sc.gear_v3()
    p = sc.gear_plan("gear-ha", "M42", sc.CAMERA_6200, sc.gear_row(gear, "row-ha", "Ha"))
    gear_template = sc.gear_template(gear, "Ha")
    gear_template["gain"] = gear_template["offset"] = 0
    gear_template["ts"][GEAR_PROFILE]["base"].update(gain=0, offset=0)
    sc.gear_push(conn, gear, sc.WHEEL_SLOTS, p)
    base = build_base_snapshot(read_all(conn, GEAR_PROFILE), p, profile_id=GEAR_PROFILE,
                               camera=sc.gear_camera(gear))
    entry = base["templates_by_filter"]["row-ha"]
    assert (entry["gain"], entry["offset"]) == (0, 0)


def test_s13_a_row_whose_plan_sits_on_another_template_by_hand_is_not_moved(conn):
    run = sc.gear_s13(conn)
    assert template_of(conn, run.plans[0], "row-ha") == 4


def test_s14_a_row_changed_to_another_template_moves_its_plan_and_writes_the_default(conn):
    run = sc.gear_s14(conn)
    p = run.plans[0]
    assert template_of(conn, p, "row-ha") == 11
    assert exposure_of(conn, p, "row-ha") == -1.0
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan") == 245


def test_s15_a_base_without_template_id_counts_as_unchanged_when_its_name_is_known(conn, fresh_conn):
    run = sc.gear_s15(conn)
    assert template_of(conn, run.plans[0], "row-rs") == 15
    # The control: a name the template is not known by is a move.
    run = sc.gear_s15(fresh_conn, base_name="Something else")
    assert template_of(fresh_conn, run.plans[0], "row-rs") == 5


def test_s16_two_cameras_wanting_one_name_insert_the_first_and_say_why_for_the_second(conn):
    run = sc.gear_s16(conn)
    assert ("Template 'Ha' of ZWO ASI2600MM Pro was not created in profile Foo, because "
            "ZWO ASI6200MM Pro's 'Ha' already uses that name.") in run.notes
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate WHERE Id > 20 AND filtername = 'H'") == 0
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate WHERE Id <= 20") == 20


def test_s17_two_templates_of_one_name_are_written_by_id_and_never_linked_by_name(conn, fresh_conn):
    id1 = row_of(conn, 1)
    sc.gear_s17(conn, linked=True)
    assert sc.cell(conn, "SELECT offset FROM exposuretemplate WHERE Id = 18") == 25
    assert row_of(conn, 1) == id1

    before = sc.template_rows(fresh_conn)
    run = sc.gear_s17(fresh_conn, linked=False)
    assert sc.template_rows(fresh_conn) == before
    assert "Template 'L' matches 2 templates in Foo (ids 1, 18), left for adoption." in run.notes
    result = run.result_for(sc.gear_template(run.gear, "L")["id"])
    assert result["action"] == "skipped"
    # The status a skipped template reads as is `new`: no id came back.
    assert result["ts_id"] is None


def test_s18_a_new_template_whose_name_two_rows_share_waits_for_adoption(conn):
    before = sc.template_rows(conn)
    run = sc.gear_s18(conn)
    assert [r for r in sc.template_rows(conn) if r["Id"] <= 20] == before
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate WHERE name = 'B'") == 2
    assert "Template 'B' matches 2 templates in Foo (ids 4, 14), left for adoption." in run.notes


def test_s19_a_template_found_by_name_with_no_base_takes_ts_values_and_writes_nothing(conn):
    before = sc.template_rows(conn)
    run = sc.gear_s19(conn)
    assert sc.template_rows(conn) == before
    assert "Ha already exists in profile Foo, so ACP took its values." in run.notes
    result = run.result_for(sc.gear_template(run.gear, "Ha")["id"])
    assert (result["action"], result["ts_id"], result["base"]["offset"]) == ("adopted", 12, -1)
    assert result["fields_written"] == []


def test_s20_an_alias_naming_a_row_of_another_group_claims_nothing(conn):
    id2 = row_of(conn, 2)
    sc.gear_s20(conn)
    assert row_of(conn, 2) == id2
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate") == 21
    r = conn.execute("SELECT * FROM exposuretemplate WHERE name = 'R Stars 2'").fetchone()
    assert (r["filtername"], r["defaultexposure"]) == ("R", 15.0)


def test_s23_pinned_exposure_plan_ids_that_are_not_in_ts_are_ignored(conn):
    run = sc.gear_s23(conn)
    p = run.plans[0]
    # The plan is found by its guid, not by a pin on someone else's plan.
    assert sc.cell(conn, f"SELECT COUNT(*) FROM exposureplan WHERE guid = '{sc.gear_plan_guid(p, 'row-ha')}'") == 1
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan") == 245
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan WHERE Id IN (21, 22, 23)") == 0


def test_s24_a_row_sub_changed_in_acp_on_a_deviating_row_writes_an_explicit_exposure(conn):
    run = sc.gear_s24(conn)
    p = run.plans[0]
    assert exposure_of(conn, p, "row-ha") == 450.0
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan") == 245


def test_s24_on_template_19_only_the_changed_plan_moves(conn):
    """Template 19 is 120 s; the row deviates at 180 and ACP moves it to 200."""
    gear = sc.gear_v3()
    r19 = sc.gear_template(gear, "R (ZWO ASI6200MM Pro)")
    p = sc.gear_plan("gear-r", "M42", sc.CAMERA_6200, sc.gear_row_for(gear, "row-r", r19, 3.0, 180.0))
    first = sc.gear_push(conn, gear, sc.WHEEL_SLOTS, p)
    assert exposure_of(conn, p, "row-r") == 180.0
    sc.gear_link(conn, gear, first, p)
    sc.record_results(first)
    others = conn.execute("SELECT Id, exposure FROM exposureplan WHERE exposureTemplateId = 19 AND Id <= 244").fetchall()
    p["filter_rows"][0]["template_sub_s"] = 200
    sc.gear_push(conn, gear, sc.WHEEL_SLOTS, p)
    assert exposure_of(conn, p, "row-r") == 200.0
    assert r19["sub_s"] == 120
    assert conn.execute("SELECT Id, exposure FROM exposureplan WHERE exposureTemplateId = 19 AND Id <= 244").fetchall() \
        == others


def test_s25_a_row_that_follows_its_template_gets_exposure_minus_one(conn):
    run = sc.gear_s25(conn)
    assert exposure_of(conn, run.plans[0], "row-r") == -1.0
    assert sc.cell(conn, "SELECT defaultexposure FROM exposuretemplate WHERE Id = 19") == 150.0


def test_s26_the_refresh_never_undoes_a_sub_acp_changed_and_takes_one_edited_in_ts(conn):
    gear = sc.gear_v3()
    ha_row = sc.gear_row(gear, "row-ha", "Ha", 3.0, 400.0)
    oiii_row = sc.gear_row(gear, "row-oiii", "OIII", 3.0)
    p = sc.gear_plan("gear-ha", "M42", sc.CAMERA_6200, ha_row, oiii_row)
    first = sc.gear_push(conn, gear, sc.WHEEL_SLOTS, p)
    sc.gear_link(conn, gear, first, p)
    sc.record_results(first)
    previous = copy.deepcopy(p["ts_links"][GEAR_PROFILE]["base_snapshot"])
    # ACP moves the Ha row to 450. TS has the OIII plan edited to 240 by hand.
    p["filter_rows"][0]["template_sub_s"] = 450
    conn.execute("UPDATE exposureplan SET exposure = 240 WHERE guid = ?", (sc.gear_plan_guid(p, "row-oiii"),))
    run = sc.gear_push(conn, gear, sc.WHEEL_SLOTS, p)
    base = build_base_snapshot(read_all(conn, GEAR_PROFILE), p, profile_id=GEAR_PROFILE,
                               camera=sc.gear_camera(gear), order_targets=run.report.order_synced_target_ids)
    refresh_template_subs(p, base, previous)
    assert [r["template_sub_s"] for r in p["filter_rows"]] == [450, 240]

    # A push that did not write the changed row (its filter is off the wheel)
    # leaves the base at 400, and the refresh must not pull the row back to it.
    p2 = sc.gear_plan("gear-ha", "M42", sc.CAMERA_6200, copy.deepcopy(p["filter_rows"][0]))
    p2["filter_rows"][0]["template_sub_s"] = 500
    stale = copy.deepcopy(base)
    stale["exposure_plans_by_panel_filter"]["1,1_row-ha"]["template_sub_s"] = 450
    previous2 = copy.deepcopy(stale)
    stale["exposure_plans_by_panel_filter"]["1,1_row-ha"]["template_sub_s"] = 450.0
    assert refresh_template_subs(p2, stale, previous2) is False
    assert p2["filter_rows"][0]["template_sub_s"] == 500
    # With no previous base, the refresh takes TS's sub as it always did.
    assert refresh_template_subs(p2, stale) is True
    assert p2["filter_rows"][0]["template_sub_s"] == 450


def test_s30_names_in_ts_names_never_find_a_row(conn):
    id2 = row_of(conn, 2)
    sc.gear_s30(conn)
    assert row_of(conn, 2) == id2
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate") == 20
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate WHERE name = 'R (ZWO ASI6200MM Pro)' AND Id > 20") == 1


def test_s31_an_alias_finds_the_row_and_the_rename_is_written_to_it(conn):
    run = sc.gear_s31(conn)
    assert sc.cell(conn, "SELECT name FROM exposuretemplate WHERE Id = 12") == "Ha 300"
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposuretemplate") == 20
    result = run.result_for(sc.gear_template(run.gear, "Ha 300")["id"])
    assert (result["ts_id"], result["fields_written"]) == (12, ["name"])


def test_a_row_naming_no_gear_template_is_skipped_with_a_note_and_never_starred(conn):
    gear = sc.gear_v3()
    row = {"id": "row-q", "filter": "R", "template": "Q Stars", "template_sub_s": 15,
           "unit": "h", "target_hours": 1.0}
    run = sc.gear_push(conn, gear, sc.WHEEL_SLOTS, sc.gear_plan("gear-q", "M42", sc.CAMERA_6200, row))
    assert "M42 R: template 'Q Stars' is not in gear, row skipped" in run.notes
    assert sc.cell(conn, "SELECT COUNT(*) FROM exposureplan") == 244


def test_a_filter_without_gear_templates_is_written_as_before(tmp_path):
    from .plans import GEAR, PROFILE_ID, plan
    from .make_db import make_db
    c = sqlite3.connect(make_db(28, tmp_path / "legacy.sqlite"))
    c.row_factory = sqlite3.Row
    payload = convert.build_payload([plan("a")], GEAR, profile_id=PROFILE_ID, wheel_slots=["L"])
    assert payload.gear_works == []
    report = upsert.apply(c, payload)
    assert report.templates == [] and "templates" not in report.to_dict()
    assert c.execute("SELECT filtername FROM exposuretemplate").fetchone()[0] == "OIII"
