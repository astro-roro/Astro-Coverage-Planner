"""Target numbers stay the same across rescans (scripts/target_ids.py)."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import target_ids as ti  # noqa: E402


def tgt(ra, dec, objects=(), hours=1.0, fov=(60.0, 40.0), tid=None, rot=0.0):
    t = {
        "center_ra_deg": ra % 360.0,
        "center_dec_deg": dec,
        "objects": list(objects),
        "footprint_arcmin": list(fov),
        "fov_arcmin": list(fov),
        "rotation_deg": rot,
        "corners_icrs": ti._box_corners(ra % 360.0, dec, fov[0], fov[1], rot),
        "filters": {"Ha": {"total_hours": hours}},
    }
    if tid is not None:
        t["target_id"] = tid
    return t


LIBRARY = [
    tgt(10.68, 41.27, ["M31"], 20, tid=1),
    tgt(83.82, -5.39, ["M42"], 8, tid=2),
    tgt(15.0, -39.0, ["Sculptor"], 5, tid=3),
    tgt(201.37, -43.02, ["NGC 5128"], 12, tid=4),
    tgt(274.7, -13.8, ["M16"], 3, tid=5),
]


def rescan(prev, new, ledger=None):
    """Scan order differs from last time, as it does in real rescans."""
    new = copy.deepcopy(new)
    for t in new:
        t.pop("target_id", None)
    led, rep = ti.assign_target_ids(new, copy.deepcopy(prev), ledger, now="2026-09-25T00:00:00")
    return new, led, rep


def ids_by_name(targets):
    return {t["objects"][0]: t["target_id"] for t in targets}


def test_unchanged_library_keeps_every_id():
    new, led, rep = rescan(LIBRARY, list(reversed(LIBRARY)))
    assert ids_by_name(new) == ids_by_name(LIBRARY)
    assert (rep["kept"], rep["new"], rep["retired"], rep["merged"]) == (5, 0, 0, 0)
    assert led["next_id"] == 6


def test_small_centre_drift_and_more_hours_keep_id():
    moved = [tgt(t["center_ra_deg"] + 0.05, t["center_dec_deg"] - 0.03, t["objects"],
                 hours=30) for t in LIBRARY]
    new, _, rep = rescan(LIBRARY, moved)
    assert ids_by_name(new) == ids_by_name(LIBRARY)
    assert rep["kept"] == 5


def test_new_target_gets_new_id_and_nobody_moves():
    extra = tgt(310.0, 45.0, ["NGC 7000"], 4)
    new, led, rep = rescan(LIBRARY, [extra] + LIBRARY)
    ids = ids_by_name(new)
    assert ids["NGC 7000"] == 6
    assert {k: v for k, v in ids.items() if k != "NGC 7000"} == ids_by_name(LIBRARY)
    assert (rep["kept"], rep["new"]) == (5, 1)
    assert led["next_id"] == 7


def test_removed_target_retires_id_and_it_is_not_reused():
    without = [t for t in LIBRARY if t["objects"] != ["Sculptor"]]
    new, led, rep = rescan(LIBRARY, without)
    assert rep["retired"] == 1 and rep["retired_ids"] == [3]
    assert 3 in led["retired"]
    # Next scan adds an unrelated target: it must not get 3.
    after = without + [tgt(310.0, 45.0, ["NGC 7000"], 4)]
    new2, led2, rep2 = rescan(new, after, led)
    assert ids_by_name(new2)["NGC 7000"] == 6
    assert 3 not in {t["target_id"] for t in new2}


def test_retired_id_comes_back_for_the_same_sky():
    without = [t for t in LIBRARY if t["objects"] != ["Sculptor"]]
    new, led, _ = rescan(LIBRARY, without)
    new2, led2, rep2 = rescan(new, LIBRARY, led)
    assert ids_by_name(new2)["Sculptor"] == 3
    assert rep2["revived"] == 1
    assert 3 not in led2["retired"]


def test_split_keeps_id_on_part_with_most_integration():
    prev = [tgt(100.0, 10.0, ["NGC 2264", "Cone"], 10, fov=(120, 80), tid=7)]
    halves = [
        tgt(99.8, 10.0, ["NGC 2264"], 2, fov=(60, 40)),
        tgt(100.2, 10.0, ["Cone"], 9, fov=(60, 40)),
    ]
    new, led, rep = rescan(prev, halves)
    ids = ids_by_name(new)
    assert ids["Cone"] == 7
    assert ids["NGC 2264"] == 8
    assert rep["split"] == 1 and rep["new"] == 1


def test_merge_carries_override_across(tmp_path):
    prev = [
        tgt(100.0, 10.0, ["A"], 3, fov=(60, 40), tid=4),
        tgt(100.3, 10.0, ["B"], 9, fov=(60, 40), tid=9),
    ]
    merged = [tgt(100.15, 10.0, ["A", "B"], 12, fov=(90, 40))]
    new, led, rep = rescan(prev, merged)
    assert new[0]["target_id"] == 9
    assert rep["merges"] == {"4": 9} and rep["merged"] == 1
    assert led["merges"] == {"4": 9} and 4 in led["retired"]

    overrides = tmp_path / "target_overrides.json"
    overrides.write_text(json.dumps({"version": 1, "overrides": {
        "4": {"finished": True, "updated_at": "2026-09-20T00:00:00+00:00"},
        "9": {"finished": False, "updated_at": "2026-09-01T00:00:00+00:00"},
        "12": {"finished": True},
    }}))
    store = ti.TargetStore("finished marks", lambda: overrides, "overrides",
                           ti._combine_override)
    moved = ti.carry_over_merges({4: 9}, [store], log=lambda s: None)
    data = json.loads(overrides.read_text())
    assert moved == {"finished marks": 1}
    assert "4" not in data["overrides"]
    assert data["overrides"]["9"]["finished"] is True
    assert data["overrides"]["9"]["updated_at"].startswith("2026-09-20")
    assert data["overrides"]["12"] == {"finished": True}
    assert data["version"] == 1
    assert not list(tmp_path.glob(".*.tmp"))


def test_merge_onto_target_without_override_moves_it(tmp_path):
    p = tmp_path / "hidden.json"
    p.write_text(json.dumps({"4": {"finished": True}}))
    store = ti.TargetStore("marks", lambda: p, None, ti._combine_override)
    ti.carry_over_merges({4: 9}, [store], log=lambda s: None)
    assert json.loads(p.read_text()) == {"9": {"finished": True}}


def test_carry_over_skips_missing_file(tmp_path):
    store = ti.TargetStore("marks", lambda: tmp_path / "nope.json", "overrides",
                           ti._combine_override)
    assert ti.carry_over_merges({4: 9}, [store], log=lambda s: None) == {}
    assert not (tmp_path / "nope.json").exists()


def test_target_near_ra_zero_matches_itself():
    prev = [tgt(359.9, 20.0, ["NGC 7814"], 5, tid=11)]
    new, _, rep = rescan(prev, [tgt(0.05, 20.02, ["NGC 7814"], 6)])
    assert new[0]["target_id"] == 11 and rep["kept"] == 1


def test_target_near_pole_matches_itself():
    prev = [tgt(40.0, 89.5, ["NCP"], 5, fov=(120, 80), tid=12)]
    # A big RA change near the pole is a small move on the sky.
    new, _, rep = rescan(prev, [tgt(70.0, 89.45, ["NCP"], 5, fov=(120, 80))])
    assert new[0]["target_id"] == 12 and rep["kept"] == 1


def test_missing_previous_manifest_falls_back_to_ledger():
    _, led, _ = rescan(LIBRARY, LIBRARY)
    new, _, rep = rescan(None, list(reversed(LIBRARY)), led)
    assert ids_by_name(new) == ids_by_name(LIBRARY)
    assert rep["previous_source"] == "ledger" and rep["kept"] == 5


def test_first_run_adopts_live_ids_as_they_stand():
    live = [dict(t, target_id=tid) for t, tid in zip(LIBRARY, (6, 49, 2, 17, 30))]
    new, led, rep = rescan(live, LIBRARY)
    assert ids_by_name(new) == ids_by_name(live)
    assert led["next_id"] == 50


def test_no_previous_anything_numbers_from_one():
    new, led, rep = rescan(None, LIBRARY)
    assert [t["target_id"] for t in new] == [1, 2, 3, 4, 5]
    assert rep["previous_source"] == "none"


def test_small_target_inside_old_wide_field_is_not_the_same_target():
    prev = [tgt(83.8, -5.4, ["Orion wide"], 10, fov=(600, 400), tid=1)]
    new, _, rep = rescan(prev, [tgt(83.82, -5.39, ["M42"], 2, fov=(30, 20)),
                                tgt(83.8, -5.4, ["Orion wide"], 11, fov=(600, 400))])
    ids = ids_by_name(new)
    assert ids["Orion wide"] == 1
    assert ids["M42"] == 2


def test_ledger_round_trip_is_atomic(tmp_path):
    _, led, _ = rescan(LIBRARY, LIBRARY)
    p = tmp_path / "target_ids.json"
    ti.atomic_write_json(p, led, indent=2)
    assert ti.load_ledger(p) == led
    assert ti.load_ledger(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert ti.load_ledger(tmp_path / "bad.json") is None


def test_merge_chain_resolves_to_latest_survivor():
    led = ti.empty_ledger()
    led["merges"] = {"3": 5}
    led["next_id"] = 10
    prev = [tgt(100.0, 10.0, ["A"], 3, tid=5), tgt(100.3, 10.0, ["B"], 9, tid=8)]
    _, led2, _ = rescan(prev, [tgt(100.15, 10.0, ["A", "B"], 12, fov=(90, 40))], led)
    assert led2["merges"]["5"] == 8
    assert led2["merges"]["3"] == 8


def test_large_shuffled_library_keeps_every_id():
    import random
    rng = random.Random(7)
    lib = []
    while len(lib) < 300:
        ra, dec = rng.uniform(0, 360), rng.uniform(-89, 89)
        # The scanner clusters at 30 arcmin, so real targets sit apart.
        if any(ti.separation_deg(ra, dec, t["center_ra_deg"], t["center_dec_deg"]) < 1.0
               for t in lib):
            continue
        fov = (rng.uniform(20, 200), rng.uniform(15, 150))
        lib.append(tgt(ra, dec, [f"T{len(lib)}"], rng.uniform(0.5, 40), fov=fov,
                       tid=len(lib) + 1, rot=rng.uniform(0, 180)))
    shuffled = copy.deepcopy(lib)
    rng.shuffle(shuffled)
    new, _, rep = rescan(lib, shuffled)
    assert ids_by_name(new) == ids_by_name(lib)
    assert rep["kept"] == 300 and rep["new"] == 0


def test_describe_report_counts():
    s = ti.describe_report({"kept": 40, "new": 2, "retired": 1, "merged": 1})
    assert s == "40 kept their number, 2 new, 1 retired, 1 merged"
