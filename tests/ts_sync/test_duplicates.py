"""Possible duplicates: a new TS plan whose footprint covers an ACP plan's.

The maths first (duplicates.py), then the upload flow: the preview flags the
plan, and the three choices (skip, add anyway, link) do what the review page
says they do. Name proposals keep working as before.
"""
from __future__ import annotations

import math

import pytest

from nina_ts_sync import duplicates as dup
from .host import P, Q, FakeHost, link, new_db, push
from .plans import GEAR, plan


@pytest.fixture
def host(tmp_path, monkeypatch):
    return FakeHost(tmp_path, monkeypatch)


# The test gear: 600 mm with an IMX571 gives about 134.6 x 89.9 arcmin.
FOV_W, FOV_H = dup.fov_arcmin(GEAR["telescopes"][0], GEAR["cameras"][0])


def fp(ra, dec, w=60.0, h=40.0, rot=0.0):
    return dup.Footprint(ra=ra, dec=dec, w=w, h=h, rot=rot)


def ra_shift(arcmin, dec):
    """The RA change that moves a point *arcmin* east at *dec*."""
    return arcmin / 60.0 / math.cos(math.radians(dec))


# ---------------------------------------------------------------------------
# Overlap maths
# ---------------------------------------------------------------------------


def test_fov_matches_acp_formula():
    aspp = 206.265 * 3.76 / 600.0
    assert FOV_W == pytest.approx(6248 * aspp / 60.0)
    assert FOV_H == pytest.approx(4176 * aspp / 60.0)


def test_mosaic_extent_uses_acp_stride():
    assert dup.mosaic_extent_arcmin(60, 40, {"rows": 2, "cols": 3, "overlap_pct": 20}) == \
        pytest.approx((60 * (1 + 2 * 0.8), 40 * (1 + 1 * 0.8)))


def test_identical():
    c = dup.compare(fp(100, -30), fp(100, -30))
    assert c.overlap == pytest.approx(1.0)
    assert c.flagged and c.strong


def test_partial_half():
    assert dup.overlap_fraction(fp(100, -30), fp(100 + ra_shift(30, -30), -30)) == \
        pytest.approx(0.5, abs=0.01)


def test_partial_rotated_square():
    # A 60 x 40 turned through 90 degrees on itself: 40 x 40 of 60 x 40.
    assert dup.overlap_fraction(fp(100, -30), fp(100, -30, rot=90)) == pytest.approx(2 / 3, abs=0.01)


def test_rotation_turns_east_of_north_as_acp_does():
    # app._mosaic_panel_centers: a camera-frame offset (x, y) lands at
    # east = x cos R + y sin R, north = -x sin R + y cos R.
    w, h, rot = 60.0, 40.0, 30.0
    r = math.radians(rot)
    ra_c, dec_c = 100.0, 0.0
    ra, dec = fp(ra_c, dec_c, w, h, rot).corners()[2]   # the (+x, +y) corner
    east = (w / 2) * math.cos(r) + (h / 2) * math.sin(r)
    north = -(w / 2) * math.sin(r) + (h / 2) * math.cos(r)
    assert (ra - ra_c) * 60.0 == pytest.approx(east, abs=0.05)
    assert (dec - dec_c) * 60.0 == pytest.approx(north, abs=0.05)
    # The sign matters: a frame turned the other way is not the same frame.
    assert dup.overlap_fraction(fp(100, 0, 60, 10, 30), fp(100, 0, 60, 10, -30)) < 0.5


def test_none():
    assert dup.overlap_fraction(fp(100, -30), fp(110, -30)) == 0.0
    assert not dup.compare(fp(100, -30), fp(110, -30)).flagged


def test_small_inside_large_is_full_overlap_of_the_smaller():
    assert dup.overlap_fraction(fp(100, -30, 20, 20), fp(100, -30, 200, 200)) == pytest.approx(1.0)


def test_across_ra_zero():
    # 0.4 degrees of RA apart across 0/360 at Dec +10, fields 60 arcmin wide.
    gap = 0.4 * 60 * math.cos(math.radians(10))
    frac = dup.overlap_fraction(fp(359.8, 10, 60, 60), fp(0.2, 10, 60, 60))
    assert frac == pytest.approx((60 - gap) / 60, abs=0.01)
    assert dup.compare(fp(359.8, 10, 60, 60), fp(0.2, 10, 60, 60)).flagged


def test_near_the_pole():
    # Two fields 0.5 degrees from the pole on opposite sides: 60 arcmin apart,
    # 60 arcmin wide, so they touch and do not overlap.
    assert dup.overlap_fraction(fp(0, 89.5, 60, 60), fp(180, 89.5, 60, 60)) == pytest.approx(0.0, abs=0.01)
    # 0.1 degrees from the pole on opposite sides: 12 arcmin apart.
    frac = dup.overlap_fraction(fp(0, 89.9, 60, 60), fp(180, 89.9, 60, 60))
    assert frac == pytest.approx(48 / 60, abs=0.02)
    # The same field reached at a very different RA, as near a pole it can be.
    assert dup.overlap_fraction(fp(10, 89.99, 60, 60), fp(200, 89.99, 60, 60)) > 0.8


def test_project_round_trip():
    for ra0, dec0 in ((0, 0), (359.9, 45), (120, 89.9), (10, -89.5)):
        ra, dec = dup.deproject(0.3, -0.2, ra0, dec0)
        xi, eta = dup.project(ra, dec, ra0, dec0)
        assert (xi, eta) == pytest.approx((0.3, -0.2), abs=1e-9)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


def test_flag_at_thirty_percent_not_below():
    # Slide one 60-wide field east: overlap is (60 - d) / 60.
    at = dup.compare(fp(100, 0), fp(100 + ra_shift(41.5, 0), 0))   # about 31%
    below = dup.compare(fp(100, 0, 60, 4), fp(100 + ra_shift(43, 0), 0, 60, 4))  # about 28%
    assert at.flagged and at.overlap == pytest.approx(0.308, abs=0.01)
    assert not below.flagged and below.overlap == pytest.approx(0.283, abs=0.01)


def test_strong_above_seventy_percent():
    strong = dup.compare(fp(100, 0), fp(100 + ra_shift(15, 0), 0))   # 75%
    weak = dup.compare(fp(100, 0), fp(100 + ra_shift(21, 0), 0))     # 65%
    assert strong.flagged and strong.strong
    assert weak.flagged and not weak.strong


def test_centre_within_half_the_smaller_field_flags_without_a_size():
    # The TS plan has no field of view (gear unknown): the centre rule alone.
    near = dup.compare(fp(100, 0, 0, 0), fp(100 + ra_shift(19, 0), 0))
    far = dup.compare(fp(100, 0, 0, 0), fp(100 + ra_shift(21, 0), 0))
    assert near.overlap is None and near.flagged and not near.strong
    assert not far.flagged


def test_best_match_picks_the_most_overlap():
    candidates = [("far", fp(100 + ra_shift(25, 0), 0)), ("close", fp(100 + ra_shift(5, 0), 0)),
                  ("away", fp(150, 0))]
    item, c = dup.best_match(fp(100, 0), candidates)
    assert item == "close"
    assert dup.best_match(fp(10, 0), candidates) is None


def test_few_hundred_by_few_hundred_is_quick():
    import random
    import time
    rng = random.Random(1)
    candidates = [(i, fp(rng.uniform(0, 360), rng.uniform(-89, 89))) for i in range(400)]
    t = time.perf_counter()
    for _ in range(400):
        dup.best_match(fp(rng.uniform(0, 360), rng.uniform(-89, 89)), candidates)
    assert time.perf_counter() - t < 2.0


def test_sky_view_draws_both_and_a_grid():
    v = dup.sky_view(fp(100.2, -30.1, rot=20), fp(100, -30))
    assert len(v["ts"]["polygon"]) == 4 and len(v["acp"]["polygon"]) == 4
    kinds = {g["kind"] for g in v["grid"]}
    assert kinds == {"ra", "dec"}
    box = v["box"]
    for x, y in v["ts"]["polygon"] + v["acp"]["polygon"]:
        assert box[0] <= x <= box[2] and box[1] <= y <= box[3]
    # East is on the left: the plan further east sits further left.
    assert v["ts"]["centre"][0] < v["acp"]["centre"][0]


def test_sky_view_near_the_pole():
    v = dup.sky_view(fp(0, 89.8, 60, 60), fp(180, 89.8, 60, 60))
    labels = [g["label"] for g in v["grid"] if g["kind"] == "ra"]
    assert len(labels) == len(set(labels)) >= 2


# ---------------------------------------------------------------------------
# The upload flow
# ---------------------------------------------------------------------------


def _other_rig(host, tmp_path, acp_plans, ts_plans):
    """ACP plans linked only to rig Q, and TS plans on rig P that ACP never
    stamped: a profile cloned from another, or one profile on two rigs."""
    db = new_db(tmp_path / "ts.sqlite")
    push(db, acp_plans, Q)
    link(db, acp_plans, Q)
    host.write_plans(acp_plans)
    push(db, ts_plans, P)
    return db


def test_strong_match_is_flagged_and_defaults_to_skip(host, tmp_path):
    sculptor = plan("a", project_name="Sculptor", target_name="NGC 253", ra=11.89, dec=-25.29)
    clone = plan("x", project_name="NGC 253 wide", target_name="Sculptor Galaxy", ra=11.89, dec=-25.29)
    db = _other_rig(host, tmp_path, [sculptor], [clone])
    before = host.plans()

    status = host.upload_ready(db)
    preview = status["preview"]
    assert preview["new"] == []
    [d] = preview["duplicates"]
    assert d["acp"]["plan_id"] == "a" and d["acp"]["profiles"] == [Q]
    assert d["overlap_pct"] == 100 and d["strong"] and d["default"] == "skip"
    assert d["linkable"]
    assert preview["counts"]["duplicates"] == 1
    assert "1 possible duplicate" in preview["summary"]
    assert d["legend"]["acp"]["telescope"] == "Test 600mm"
    assert d["legend"]["acp"]["camera"] == "Test IMX571"
    assert d["legend"]["ts"]["filters"] == ["OIII"] and d["legend"]["ts"]["hours"] == pytest.approx(1.0)
    assert d["sky"]["acp"]["polygon"] and d["sky"]["ts"]["polygon"]

    # Skip: the page sends no add and no link. Nothing is created or linked.
    r = host.apply(status, add=[])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["applied"]["skipped"] == [d["key"]]
    assert host.plans() == before


def test_default_apply_skips_a_strong_match(host, tmp_path):
    a = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29)
    db = _other_rig(host, tmp_path, [a], [plan("x", project_name="Other name", ra=11.89, dec=-25.29)])
    before = host.plans()
    status = host.upload_ready(db)
    assert host.apply(status).status_code == 200  # add omitted: the defaults
    assert host.plans() == before


def test_partial_match_defaults_to_add(host, tmp_path):
    a = plan("a", project_name="Helix", ra=337.41, dec=-20.84)
    moved = plan("x", project_name="Helix wide", ra=337.41 + ra_shift(FOV_W / 2, -20.84), dec=-20.84)
    db = _other_rig(host, tmp_path, [a], [moved])
    status = host.upload_ready(db)
    [d] = status["preview"]["duplicates"]
    assert d["overlap_pct"] == pytest.approx(50, abs=2)
    assert not d["strong"] and d["default"] == "add"
    assert host.apply(status).status_code == 200
    assert sorted(p["project_name"] for p in host.plans()) == ["Helix", "Helix wide"]


def test_far_away_plan_is_plain_new(host, tmp_path):
    db = _other_rig(host, tmp_path, [plan("a", project_name="Helix", ra=337.41, dec=-20.84)],
                    [plan("x", project_name="Rosette", ra=98.0, dec=5.0)])
    preview = host.upload_ready(db)["preview"]
    assert preview["duplicates"] == []
    assert [n["project_name"] for n in preview["new"]] == ["Rosette"]
    assert preview["summary"].endswith("1 new, 0 updated, 0 need a choice")


def test_add_anyway(host, tmp_path):
    a = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29)
    db = _other_rig(host, tmp_path, [a], [plan("x", project_name="Other name", ra=11.89, dec=-25.29)])
    status = host.upload_ready(db)
    key = status["preview"]["duplicates"][0]["key"]
    r = host.apply(status, add=[key])
    assert r.status_code == 200, r.get_json()
    added = next(p for p in host.plans() if p["project_name"] == "Other name")
    assert P in added["ts_links"]
    assert P not in host.plan("a")["ts_links"]


def test_add_wins_over_link(host, tmp_path):
    a = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29)
    db = _other_rig(host, tmp_path, [a], [plan("x", project_name="Other name", ra=11.89, dec=-25.29)])
    status = host.upload_ready(db)
    key = status["preview"]["duplicates"][0]["key"]
    r = host.apply(status, add=[key], decisions={key: "link"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["applied"]["linked"] == []
    assert len(host.plans()) == 2
    assert P not in host.plan("a")["ts_links"]


def test_single_target_is_measured_with_the_preselected_gear(host, tmp_path):
    # This rig's plans use the 250 mm and the IMX585, so that pair is the
    # preselected gear for a new single target, not the importer's first pair.
    mine = [plan(f"m{i}", project_name=f"Mine {i}", ra=200.0 + 20 * i, dec=10.0,
                 telescope_id="tel-2", camera_id="cam-2") for i in range(2)]
    other = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29)
    db = new_db(tmp_path / "ts.sqlite")
    push(db, mine, P)
    link(db, mine, P)
    push(db, [other], Q)
    link(db, [other], Q)
    host.write_plans(mine + [other])
    push(db, [plan("x", project_name="Other name", ra=11.89, dec=-25.29)], P)
    [d] = host.upload_ready(db)["preview"]["duplicates"]
    assert (d["legend"]["ts"]["telescope"], d["legend"]["ts"]["camera"]) == ("Test 250mm", "Test IMX585")
    assert (d["legend"]["acp"]["telescope"], d["legend"]["acp"]["camera"]) == ("Test 600mm", "Test IMX571")


def test_link_keeps_acp_values_and_records_the_ts_rows(host, tmp_path):
    a = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29,
             filter_goals={"Ha": {"target_hours": 8.0, "sub_exposure_s": 600}})
    clone = plan("x", project_name="Other name", ra=11.89, dec=-25.29)
    db = _other_rig(host, tmp_path, [a], [clone])
    before = host.plan("a")
    status = host.upload_ready(db)
    key = status["preview"]["duplicates"][0]["key"]
    r = host.apply(status, add=[], decisions={key: "link"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["applied"]["linked"] == [{"plan_id": "a", "key": key}]
    after = host.plans()
    assert len(after) == 1
    got = after[0]
    assert set(got["ts_links"]) == {P, Q}
    assert got["ts_links"][Q] == before["ts_links"][Q]
    assert got["filter_rows"] == before["filter_rows"]
    assert got["project_name"] == "Sculptor"
    assert got["ts_links"][P]["refs"]["project_id"] == int(key.split(":")[0])

    # The next upload finds the plan by its link, not as a duplicate.
    again = host.upload_ready(db)["preview"]
    assert again["duplicates"] == [] and again["new"] == []
    assert [m["plan_id"] for m in again["unchanged"] + again["updated"] + again["conflicts"]] == ["a"]


def test_link_refused_when_the_acp_plan_is_matched_in_this_upload(host, tmp_path):
    a = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29)
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a], P)
    link(db, [a], P)
    host.write_plans([a])
    push(db, [plan("x", project_name="Second copy", ra=11.89, dec=-25.29)], P)
    before = host.plans()

    status = host.upload_ready(db)
    [d] = status["preview"]["duplicates"]
    assert not d["linkable"]
    r = host.apply(status, add=[], decisions={d["key"]: "link"})
    assert r.status_code == 400
    assert host.plans() == before


def test_two_links_to_one_plan_refused(host, tmp_path):
    a = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29)
    db = _other_rig(host, tmp_path, [a], [
        plan("x", project_name="Copy one", ra=11.89, dec=-25.29),
        plan("y", project_name="Copy two", ra=11.89 + ra_shift(5, -25.29), dec=-25.29),
    ])
    before = host.plans()
    status = host.upload_ready(db)
    keys = [d["key"] for d in status["preview"]["duplicates"]]
    assert len(keys) == 2
    r = host.apply(status, add=[], decisions={k: "link" for k in keys})
    assert r.status_code == 400
    assert host.plans() == before


def test_duplicate_moved_by_another_rigs_plan_makes_the_preview_stale(host, tmp_path):
    a = plan("a", project_name="Sculptor", ra=11.89, dec=-25.29)
    db = _other_rig(host, tmp_path, [a], [plan("x", project_name="Other name", ra=11.89, dec=-25.29)])
    status = host.upload_ready(db)
    key = status["preview"]["duplicates"][0]["key"]
    # Plan "a" belongs to rig Q only, so this rig's scope hash cannot see
    # the change. It moves far away, and the TS plan is no longer a duplicate.
    moved = host.plans()
    moved[0]["target"]["center_ra_deg"] = 200.0
    host.write_plans(moved)
    r = host.apply(status, add=[], decisions={key: "link"})
    assert r.status_code == 409 and r.get_json()["reason"] == "stale_preview"
    assert host.plans() == moved


def test_name_proposal_is_not_also_a_duplicate(host, tmp_path):
    """A same-name TS plan stays a name proposal, as before."""
    from .test_upload_merge import _strip_acp_stamps
    a = plan("a", project_name="M31", target_name="M31")
    db = new_db(tmp_path / "ts.sqlite")
    push(db, [a], P)
    host.write_plans([a])
    _strip_acp_stamps(db)
    preview = host.upload_ready(db)["preview"]
    assert [p["acp"]["plan_id"] for p in preview["proposals"]] == ["a"]
    assert preview["duplicates"] == [] and preview["new"] == []
    assert "possible duplicate" not in preview["summary"]


def test_mosaic_footprint_is_the_whole_mosaic():
    # A 2x2 mosaic in ACP, and one TS frame on its top-left panel, placed the
    # way app._mosaic_panel_centers places it. The frame sits wholly inside
    # the mosaic, so it is all of the smaller footprint.
    m = plan("m", project_name="M31 mosaic", rows=2, cols=2, overlap_pct=15, ra=10.68, dec=41.27)
    mfp = dup.footprint_of(m, GEAR)
    assert (mfp.w, mfp.h) == pytest.approx(dup.mosaic_extent_arcmin(FOV_W, FOV_H, m["target"]["mosaic"]))
    east, north = -FOV_W * 0.85 / 2, FOV_H * 0.85 / 2
    panel = dup.Footprint(ra=10.68 + ra_shift(east, 41.27), dec=41.27 + north / 60.0,
                          w=FOV_W, h=FOV_H, rot=0)
    assert dup.overlap_fraction(panel, mfp) == pytest.approx(1.0, abs=0.01)
    # And the frame covers a bit over a quarter of the whole mosaic.
    assert panel.area / mfp.area == pytest.approx(1 / 1.85 ** 2, abs=0.01)
