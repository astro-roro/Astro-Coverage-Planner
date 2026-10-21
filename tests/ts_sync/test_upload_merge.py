"""The merge an upload applies. The acceptance tests under "Extension: the
merge" in ACP's docs/specs/ts-upload-import.md.

Each test starts from a plans.json and a TS file built for it. "Unchanged"
means the plan's JSON is equal before and after, field for field.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import uuid

import pytest

from nina_ts_sync import config
from .host import (
    MACHINE, P, Q, FakeHost, connect, link, new_db, push, set_desired, set_project_state,
)
from .plans import plan, row


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch)


def ha(hours: float) -> dict:
    return {"Ha": {"target_hours": hours, "sub_exposure_s": 600}}


def _setup(host, tmp_path, plans, *, profile=P, linked=True):
    """Push *plans* to a fresh TS file, link them, and save plans.json."""
    db = new_db(tmp_path / f"ts-{uuid.uuid4().hex[:6]}.sqlite")
    push(db, plans, profile)
    if linked:
        link(db, plans, profile)
    host.write_plans(plans)
    return db


def _ids(entries):
    return sorted(e["plan_id"] for e in entries)


# ---------------------------------------------------------------------------


def test_add_only(host, tmp_path):
    a = plan("a", project_name="M31")
    b = plan("b", project_name="M33")
    db = _setup(host, tmp_path, [a, b])
    push(db, [plan("c", project_name="Rosette", ra=98.0, dec=5.0)])  # added on the rig
    before = {p["id"]: p for p in host.plans()}

    status = host.upload_ready(db)
    assert status["counts"] == {"new": 1, "updated": 0, "conflicts": 0}
    r = host.apply(status)
    assert r.status_code == 200, r.get_json()

    after = host.plans()
    assert len(after) == 3
    assert host.plan("a") == before["a"]
    assert host.plan("b") == before["b"]
    c = next(p for p in after if p["project_name"] == "Rosette")
    assert P in c["ts_links"]
    assert c["ts_links"][P]["machine"] == MACHINE


def test_nothing_deleted(host, tmp_path):
    plans = [plan("a", project_name="M31"), plan("b", project_name="M33"),
             plan("d", project_name="Horsehead")]
    _setup(host, tmp_path, plans)
    db2 = new_db(tmp_path / "rig-now.sqlite")
    push(db2, plans[:2])  # D was deleted on the rig
    # The rig's file is db2, so re-link A and B to it; D keeps its old link.
    snap_plans = host.plans()
    link(db2, [p for p in snap_plans if p["id"] in ("a", "b")])
    host.write_plans(snap_plans)
    before_d = host.plan("d")

    status = host.upload_ready(db2)
    assert _ids(status["preview"]["not_in_upload"]) == ["d"]
    assert host.apply(status).status_code == 200
    assert host.plan("d") == before_d
    assert len(host.plans()) == 3


def test_other_rig_untouched(host, tmp_path):
    x = plan("x", project_name="M31", filter_goals=ha(10))
    y = plan("y", project_name="M31", filter_goals=ha(10))
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [x], Q)
    push(db, [y], P)
    link(db, [x], Q)
    link(db, [y], P)
    host.write_plans([x, y])
    set_desired(db, "M31", "Ha", 72, P)  # 12 h on rig P
    before_x = host.plan("x")

    status = host.upload_ready(db)
    assert '"x"' not in json.dumps(status["preview"])
    assert _ids(status["preview"]["updated"]) == ["y"]
    assert host.apply(status).status_code == 200
    assert row(host.plan("y"), "Ha")["target_hours"] == pytest.approx(12.0)
    assert host.plan("x") == before_x


def test_acp_only_plans_untouched(host, tmp_path):
    a = plan("a", project_name="M31")
    db = _setup(host, tmp_path, [a])
    lonely = plan("lonely", project_name="Never pushed", target_name="IC 1396")
    host.write_plans(host.plans() + [lonely])
    before = host.plan("lonely")

    status = host.upload_ready(db)
    assert "lonely" not in json.dumps(status["preview"])
    assert host.apply(status).status_code == 200
    assert host.plan("lonely") == before


def test_match_by_link_beats_name(host, tmp_path):
    a = plan("a", project_name="M31", target_name="M31")
    db = _setup(host, tmp_path, [a])
    conn = connect(db)
    conn.execute("UPDATE project SET name = 'Andromeda' WHERE name = 'M31'")
    conn.commit()
    conn.close()
    # An unlinked ACP plan that has the new name must not win the match.
    decoy = plan("decoy", project_name="Andromeda", target_name="M31")
    host.write_plans(host.plans() + [decoy])

    status = host.upload_ready(db)
    preview = status["preview"]
    assert preview["proposals"] == []
    [upd] = preview["updated"]
    assert upd["plan_id"] == "a" and upd["matched_by"] == "link"
    assert [f["path"] for f in upd["fields"]] == ["project_name"]
    assert upd["fields"][0]["ts"] == "Andromeda"

    assert host.apply(status).status_code == 200
    assert host.plan("a")["project_name"] == "Andromeda"
    assert host.plan("decoy") == decoy


def test_match_by_guid(host, tmp_path):
    a = plan("a", project_name="M31")
    db = _setup(host, tmp_path, [a], linked=False)
    status = host.upload_ready(db)
    preview = status["preview"]
    assert preview["new"] == []
    [m] = preview["unchanged"]
    assert m["plan_id"] == "a" and m["matched_by"] == "guid"
    assert host.apply(status).status_code == 200
    got = host.plan("a")
    assert P in got["ts_links"]
    assert got["ts_refs"] == got["ts_links"][P]["refs"]


def _strip_acp_stamps(db):
    """Make every row look hand-made in TS: NINA's own random guids."""
    conn = connect(db)
    for table in ("project", "target", "exposureplan", "exposuretemplate"):
        for row in list(conn.execute(f"SELECT Id FROM {table}")):
            conn.execute(f"UPDATE {table} SET guid = ? WHERE Id = ?", (str(uuid.uuid4()), row["Id"]))
    conn.commit()
    conn.close()


def test_name_proposal(host, tmp_path):
    a = plan("a", project_name="M31", target_name="M31")
    db = _setup(host, tmp_path, [a], linked=False)
    _strip_acp_stamps(db)
    before = host.plan("a")

    status = host.upload_ready(db)
    preview = status["preview"]
    [prop] = preview["proposals"]
    assert prop["acp"]["plan_id"] == "a"
    assert preview["new"] == []

    assert host.apply(status).status_code == 200
    assert host.plans() == [before]  # neither linked nor added

    status = host.upload_ready(db)
    key = status["preview"]["proposals"][0]["key"]
    r = host.apply(status, decisions={key: "link"})
    assert r.status_code == 200, r.get_json()
    after = host.plans()
    assert len(after) == 1
    assert P in after[0]["ts_links"]
    assert after[0]["filter_rows"] == before["filter_rows"]


def test_name_proposal_can_be_added_as_new(host, tmp_path):
    a = plan("a", project_name="M31", target_name="M31")
    db = _setup(host, tmp_path, [a], linked=False)
    _strip_acp_stamps(db)
    status = host.upload_ready(db)
    key = status["preview"]["proposals"][0]["key"]
    assert host.apply(status, add=[key]).status_code == 200
    assert len(host.plans()) == 2


def test_ts_only_change(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    set_desired(db, "M31", "Ha", 72)
    status = host.upload_ready(db)
    assert status["counts"]["updated"] == 1
    assert host.apply(status).status_code == 200
    assert row(host.plan("a"), "Ha")["target_hours"] == pytest.approx(12.0)


def test_ts_only_change_can_keep_acp(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    set_desired(db, "M31", "Ha", 72)
    status = host.upload_ready(db)
    assert host.apply(status, decisions={"a": "keep_acp"}).status_code == 200
    got = host.plan("a")
    assert row(got, "Ha")["target_hours"] == pytest.approx(10.0)
    # The base now says TS has 12, so the next push writes ACP's 10.
    base = got["ts_links"][P]["base_snapshot"]["exposure_plans_by_panel_filter"]["1,1_Ha"]
    assert base["desired"] == 72


def test_acp_only_change(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    plans = host.plans()
    row(plans[0], "Ha")["target_hours"] = 14
    host.write_plans(plans)
    status = host.upload_ready(db)
    assert status["counts"] == {"new": 0, "updated": 0, "conflicts": 0}
    assert host.apply(status).status_code == 200
    assert row(host.plan("a"), "Ha")["target_hours"] == 14


def test_pne_style_filter_name_mismatch_is_not_a_conflict(host, tmp_path):
    """The PNe Survey case: ACP has a plan keyed "OIII", and TS's own
    exposuretemplate for that same plan is named "OIII" but its filtername
    column (the wheel's own slot name) is "O". Before the canon fix the
    import kept "O" as a second, phantom goal, so every such plan came back
    "needs a choice" for a mismatch that was only a spelling difference."""
    a = plan("a", project_name="PNe Survey",
             filter_goals={"OIII": {"target_hours": 1.5, "sub_exposure_s": 300}})
    db = _setup(host, tmp_path, [a])
    conn = connect(db)
    conn.execute("UPDATE exposuretemplate SET filtername = 'O' WHERE filtername = 'OIII'")
    conn.commit()
    conn.close()

    status = host.upload_ready(db)
    assert status["counts"] == {"new": 0, "updated": 0, "conflicts": 0}
    assert "\"O\"" not in json.dumps(status["preview"]["conflicts"])
    assert host.apply(status).status_code == 200
    assert [r["filter"] for r in host.plan("a")["filter_rows"]] == ["OIII"]


def _conflict(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    plans = host.plans()
    row(plans[0], "Ha")["target_hours"] = 14
    host.write_plans(plans)
    set_desired(db, "M31", "Ha", 72)
    return db


def test_conflict_is_listed_and_needs_a_choice(host, tmp_path):
    db = _conflict(host, tmp_path)
    status = host.upload_ready(db)
    [c] = status["preview"]["conflicts"]
    assert c["plan_id"] == "a" and c["reason"] == "both_changed"
    [f] = [f for f in c["fields"] if f["path"] == "filter_rows.Ha.target_hours"]
    assert (f["base"], f["acp"], f["ts"]) == (pytest.approx(10), 14, pytest.approx(12))
    before = host.plans_path.read_bytes()
    r = host.apply(status)
    assert r.status_code == 400
    assert r.get_json()["missing_decisions"] == ["a"]
    assert host.plans_path.read_bytes() == before


@pytest.mark.parametrize("choice,hours", [("take_ts", 12.0), ("keep_acp", 14.0)])
def test_conflict_choices(host, tmp_path, choice, hours):
    db = _conflict(host, tmp_path)
    status = host.upload_ready(db)
    assert host.apply(status, decisions={"a": choice}).status_code == 200
    assert row(host.plan("a"), "Ha")["target_hours"] == pytest.approx(hours)


def test_conflict_leave_for_now_changes_nothing(host, tmp_path):
    db = _conflict(host, tmp_path)
    before = host.plan("a")
    status = host.upload_ready(db)
    assert host.apply(status, decisions={"a": "leave"}).status_code == 200
    assert host.plan("a") == before


def test_no_base_is_a_conflict(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a], linked=False)
    set_desired(db, "M31", "Ha", 72)
    status = host.upload_ready(db)
    [c] = status["preview"]["conflicts"]
    assert c["reason"] == "no_base"
    assert c["message"] == "no sync record for this rig"


def test_stale_preview(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    push(db, [plan("c", project_name="Rosette")])
    status = host.upload_ready(db)
    plans = host.plans()
    plans[0]["min_altitude_deg"] = 45  # an edit through ACP's API
    host.write_plans(plans)
    before = host.plans_path.read_bytes()
    r = host.apply(status)
    assert r.status_code == 409
    assert r.get_json()["error"] == "ACP changed since this preview"
    assert host.plans_path.read_bytes() == before


def test_progress_sent_back_does_not_make_the_preview_stale(host, tmp_path):
    """Actual hours move whenever the plugin reports progress; the merge
    never reads them."""
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    status = host.upload_ready(db)
    plans = host.plans()
    row(plans[0], "Ha")["actual_hours"] = 3.5
    host.write_plans(plans)
    assert host.apply(status).status_code == 200


def test_wrong_sha_is_409(host, tmp_path):
    db = _setup(host, tmp_path, [plan("a", project_name="M31")])
    status = host.upload_ready(db)
    before = host.plans_path.read_bytes()
    r = host.apply(status, sha256="0" * 64)
    assert r.status_code == 409
    assert host.plans_path.read_bytes() == before


def test_every_apply_writes_a_backup(host, tmp_path):
    db = _setup(host, tmp_path, [plan("a", project_name="M31")])
    push(db, [plan("c", project_name="Rosette")])
    status = host.upload_ready(db)
    before = host.plans_path.read_bytes()
    r = host.apply(status)
    assert r.status_code == 200
    backup = r.get_json()["plans_backup_path"]
    assert backup and "acpsync" in backup
    assert host.backups()
    assert open(backup, "rb").read() == before


def test_apply_deletes_the_upload(host, tmp_path):
    db = _setup(host, tmp_path, [plan("a", project_name="M31")])
    status = host.upload_ready(db)
    assert host.apply(status).status_code == 200
    assert host.status(status["upload_id"]).status_code == 404
    assert list(host.upload_dir.iterdir()) == []


def _gear_setup(host, tmp_path):
    linked = [plan(f"l{i}", project_name=f"Old {i}", telescope_id="tel-2", camera_id="cam-2")
              for i in range(2)]
    db = _setup(host, tmp_path, linked)
    push(db, [plan("n", project_name="Brand new", telescope_id="tel-1", camera_id="cam-1",
                   ra=200.0, dec=10.0)])
    return db


def test_new_plan_gets_the_gear_chosen_on_the_page(host, tmp_path):
    db = _gear_setup(host, tmp_path)
    status = host.upload_ready(db)
    choice = {"telescope_id": "tel-1", "camera_id": "cam-2"}
    assert host.apply(status, new_plan_gear=choice).status_code == 200
    new = next(p for p in host.plans() if p["project_name"] == "Brand new")
    assert (new["telescope_id"], new["camera_id"]) == ("tel-1", "cam-2")
    assert config.load()["new_plan_gear"][P] == choice


def test_new_plan_defaults_to_the_gear_most_used_on_this_rig(host, tmp_path):
    db = _gear_setup(host, tmp_path)
    status = host.upload_ready(db)
    default = status["preview"]["gear"]["default"]
    assert (default["telescope_id"], default["camera_id"], default["source"]) == \
        ("tel-2", "cam-2", "most_used")
    assert host.apply(status).status_code == 200
    new = next(p for p in host.plans() if p["project_name"] == "Brand new")
    assert (new["telescope_id"], new["camera_id"]) == ("tel-2", "cam-2")


def test_unknown_gear_is_refused(host, tmp_path):
    db = _gear_setup(host, tmp_path)
    status = host.upload_ready(db)
    r = host.apply(status, new_plan_gear={"telescope_id": "nope", "camera_id": "cam-1"})
    assert r.status_code == 400


def test_links_after_apply(host, tmp_path):
    shared = plan("shared", project_name="M31", filter_goals=ha(10))
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [shared], Q)
    link(db, [shared], Q, machine="HAYABUSA")
    push(db, [shared], P)
    link(db, [shared], P)
    host.write_plans([shared])
    q_link = copy.deepcopy(host.plan("shared")["ts_links"][Q])
    set_desired(db, "M31", "Ha", 72, P)
    push(db, [plan("c", project_name="Rosette")])

    status = host.upload_ready(db)
    assert host.apply(status).status_code == 200
    for p in host.plans():
        assert P in p["ts_links"], p["id"]
        assert p["ts_refs"] == p["ts_links"][P]["refs"]
    assert host.plan("shared")["ts_links"][Q] == q_link


def test_a_plan_linked_only_to_another_rig_is_not_added_again(host, tmp_path):
    """Pushed here by a plugin that did not report the push, so its rows
    carry ACP's stamp for this rig but the plan has no link for it."""
    a = plan("a", project_name="M31")
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a], Q)
    link(db, [a], Q)
    push(db, [a], P)
    host.write_plans([a])
    before = host.plan("a")
    status = host.upload_ready(db)
    preview = status["preview"]
    assert preview["new"] == []
    assert "a" not in json.dumps(preview["updated"] + preview["unchanged"] + preview["conflicts"])
    assert any("linked only to other rigs" in n for n in preview["link_notes"])
    assert host.apply(status).status_code == 200
    assert host.plans() == [before]


def test_a_legacy_link_for_this_rig_becomes_a_ts_links_entry(host, tmp_path):
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    plans = host.plans()
    plans[0].pop("ts_links")  # as written before ts_links existed
    host.write_plans(plans)
    set_desired(db, "M31", "Ha", 72)
    status = host.upload_ready(db)
    assert status["preview"]["updated"][0]["matched_by"] == "link"
    assert host.apply(status).status_code == 200
    got = host.plan("a")
    assert got["ts_refs"] == got["ts_links"][P]["refs"]


def test_writing_one_link_keeps_a_legacy_link_for_another_profile():
    from nina_ts_sync.links import links_of, write_link
    legacy_refs = {"profile_id": Q, "project_id": 1, "target_ids_by_panel": {"1,1": 1}}
    p = {"id": "a", "ts_refs": legacy_refs, "ts_base_snapshot": {"project": None}}
    write_link(p, P, machine=MACHINE, refs={"profile_id": P, "project_id": 5}, base_snapshot=None)
    assert set(links_of(p)) == {P, Q}
    assert p["ts_links"][Q]["refs"] == legacy_refs
    assert p["ts_refs"]["profile_id"] == P


def test_link_from_another_machine_falls_back_to_guid(host, tmp_path):
    a = plan("a", project_name="M31")
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a])
    link(db, [a], P, machine="HAYABUSA")
    host.write_plans([a])
    status = host.upload_ready(db)
    preview = status["preview"]
    assert preview["unchanged"][0]["matched_by"] == "guid"
    assert preview["link_notes"] == [
        "Profile Voyager main was last linked from HAYABUSA; matched by name stamp instead."]


# ---------------------------------------------------------------------------
# State, priority and minimum time (the four TS values, synced both ways)
# ---------------------------------------------------------------------------


def test_ts_only_state_change_is_an_update(host, tmp_path):
    """A plan pushed as active, whose TS project goes inactive on the rig
    since the last sync, shows state as an ordinary update, not a conflict.
    Applying it sets the plan inactive."""
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    set_project_state(db, "M31", 2)  # inactive, an edit on the rig

    status = host.upload_ready(db)
    preview = status["preview"]
    assert (preview["counts"]["new"], preview["counts"]["updated"], preview["counts"]["conflicts"]) == (0, 1, 0)
    [upd] = preview["updated"]
    assert upd["plan_id"] == "a"
    [f] = [f for f in upd["fields"] if f["path"] == "state"]
    assert (f["acp"], f["ts"]) == ("active", "inactive")

    assert host.apply(status).status_code == 200
    assert host.plan("a")["state"] == "inactive"


def test_conflict_when_both_sides_change_state(host, tmp_path):
    """A plan paused in ACP whose TS project is independently closed on the
    rig is a conflict: both sides changed state since the last sync."""
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    plans = host.plans()
    plans[0]["state"] = "inactive"
    host.write_plans(plans)
    set_project_state(db, "M31", 3)  # closed, on the rig

    status = host.upload_ready(db)
    [c] = status["preview"]["conflicts"]
    assert c["plan_id"] == "a" and c["reason"] == "both_changed"
    [f] = [f for f in c["fields"] if f["path"] == "state"]
    assert (f["base"], f["acp"], f["ts"]) == ("active", "inactive", "closed")

    assert host.apply(status, decisions={"a": "take_ts"}).status_code == 200
    assert host.plan("a")["state"] == "closed"


def test_no_state_or_minimum_time_against_the_defaults_is_noise(host, tmp_path):
    """A plan with no `state` key reads as active, and no `minimum_time_min`
    key reads as 0. Neither is a difference against an active TS project
    with no minimum time, so the first upload shows the plan unchanged."""
    a = plan("a", project_name="M31", filter_goals=ha(10))
    assert "state" not in a and "minimum_time_min" not in a
    db = _setup(host, tmp_path, [a], linked=False)  # first upload, matched by guid

    status = host.upload_ready(db)
    preview = status["preview"]
    assert (preview["counts"]["updated"], preview["counts"]["conflicts"]) == (0, 0)
    [m] = preview["unchanged"]
    assert m["plan_id"] == "a"


def test_old_base_with_no_state_reads_a_ts_only_change(host, tmp_path):
    """A base snapshot taken before this feature has no `state` or
    `minimumtime`. It reads as active and 0 - ACP couldn't have changed
    either since that base, so a TS project gone inactive is a TS-only
    change, applied without a conflict."""
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    plans = host.plans()
    del plans[0]["ts_links"][P]["base_snapshot"]["project"]["state"]
    del plans[0]["ts_links"][P]["base_snapshot"]["project"]["minimumtime"]
    host.write_plans(plans)
    set_project_state(db, "M31", 2)

    status = host.upload_ready(db)
    assert status["preview"]["counts"]["conflicts"] == 0
    [upd] = status["preview"]["updated"]
    [f] = [f for f in upd["fields"] if f["path"] == "state"]
    assert (f["acp"], f["ts"]) == ("active", "inactive")

    assert host.apply(status).status_code == 200
    assert host.plan("a")["state"] == "inactive"


def test_no_base_state_conflict_names_the_rig(host, tmp_path):
    """An ACP plan that's active and a TS project that's inactive, with no
    sync record for this rig at all, is a conflict marked "no sync record
    for this rig", the same as any other field on a first upload."""
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a], linked=False)
    set_project_state(db, "M31", 2)

    status = host.upload_ready(db)
    [c] = status["preview"]["conflicts"]
    assert c["plan_id"] == "a" and c["reason"] == "no_base"
    assert c["message"] == "no sync record for this rig"
    [f] = [f for f in c["fields"] if f["path"] == "state"]
    assert (f["acp"], f["ts"]) == ("active", "inactive")

    assert host.apply(status, decisions={"a": "keep_acp"}).status_code == 200
    assert host.plan("a").get("state", "active") == "active"


# ---------------------------------------------------------------------------
# POST /links
# ---------------------------------------------------------------------------


def test_post_links_writes_only_that_profiles_link(host, tmp_path):
    a = plan("a", project_name="M31")
    b = plan("b", project_name="M33")
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a, b], Q)
    link(db, [a, b], Q)
    host.write_plans([a, b])
    before = {p["id"]: p for p in host.plans()}

    refs = {"profile_id": P, "project_id": 7, "target_ids_by_panel": {"1,1": 9},
            "template_ids_by_filter": {}, "exposure_plan_ids": {}}
    base = {"project": {"name": "M31"}, "targets_by_panel": {}}
    r = host.client.post("/api/ext/nina-ts-sync/links", json={
        "profile_id": P, "machine": MACHINE,
        "states": [{"plan_id": "a", "refs": refs, "base_snapshot": base},
                   {"plan_id": "ghost", "refs": refs, "base_snapshot": base}],
    })
    assert r.status_code == 200
    assert r.get_json() == {"updated": ["a"], "unknown": ["ghost"]}

    after = {p["id"]: p for p in host.plans()}
    assert after["b"] == before["b"]
    got = after["a"]
    link_p = got["ts_links"].pop(P)
    assert link_p["refs"] == refs and link_p["base_snapshot"] == base
    assert link_p["machine"] == MACHINE
    assert got == before["a"]
    assert [p["id"] for p in host.plans()] == ["a", "b"]


def test_post_links_rejects_a_bad_body(host):
    host.write_plans([])
    assert host.client.post("/api/ext/nina-ts-sync/links", json={"states": []}).status_code == 400
    assert host.client.post("/api/ext/nina-ts-sync/links",
                            json={"profile_id": P, "states": "no"}).status_code == 400


def test_upload_apply_preserves_unknown_top_level_keys(host, tmp_path):
    """ACP core keeps a top-level `settings_migrated` flag in plans.json
    alongside `version` and `plans`. Every write path has to load the
    document and keep whatever it doesn't own, or ACP re-runs whatever that
    flag gates (the one-shot draft-to-active migration)."""
    a = plan("a", project_name="M31", filter_goals=ha(10))
    db = _setup(host, tmp_path, [a])
    host.plans_path.write_text(
        json.dumps({"version": 1, "settings_migrated": 1, "plans": host.plans()}),
        encoding="utf-8")
    set_desired(db, "M31", "Ha", 72)

    status = host.upload_ready(db)
    assert host.apply(status).status_code == 200
    doc = json.loads(host.plans_path.read_text(encoding="utf-8"))
    assert doc["settings_migrated"] == 1
