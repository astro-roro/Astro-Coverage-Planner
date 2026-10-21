"""The identity recipe, and the two payloads the push now refuses.

The old recipe joined free-text name parts with a slash, so a target name
holding a slash could reach the same UUID as a different project and target
pair. Length-prefixing every part removes that by construction, whatever
characters a name holds.
"""
from __future__ import annotations

import pytest

from nina_ts_sync import convert
from nina_ts_sync.schema import (
    ACP_NS,
    PayloadError,
    exposure_plan_guid,
    legacy_project_guid,
    legacy_target_guid,
    name_part,
    project_guid,
    row_plan_guid,
    row_template_guid,
    stable_guid,
    target_guid,
    template_guid,
)
from .plans import GEAR, PROFILE_ID, plan

PROFILE = "profile-1"


def test_the_namespace_uuid_is_unchanged():
    """It is part of the on-disk identity of every row either tool stamps."""
    assert str(ACP_NS) == "c4b6f1ee-1f9e-5e4b-9a7a-7e1d2c3a4b5c"


def test_a_slash_in_a_name_no_longer_collides():
    a = target_guid(PROFILE, "M42", "M43/NGC1977")
    b = target_guid(PROFILE, "M42/M43", "NGC1977")
    assert a != b


def test_the_old_recipe_did_collide():
    """The legacy functions are kept exactly as they were, for the migration."""
    a = legacy_target_guid(PROFILE, "M42", "M43/NGC1977")
    b = legacy_target_guid(PROFILE, "M42/M43", "NGC1977")
    assert a == b == "80e4a6ad-29a0-57ad-83b5-f0a07560b561"


def test_the_filter_row_recipes():
    """goal-panel-and-ts-order.md section 3: a row's exposure plan and the
    template the push inserts for it name both the filter and the template.
    The plugin's TsIdentityRecipeTests asserts the same literals."""
    assert row_plan_guid(PROFILE, "abc", "R", "R Stars") == \
        stable_guid("9:profile-1/plan/3:abc/1:R/7:R Stars") == "715ccc47-9d4a-58d4-b9ab-a8cc00190da9"
    assert row_template_guid(PROFILE, "R", "cam-1", "R Stars") == \
        stable_guid("9:profile-1/template/1:R/5:cam-1/7:R Stars") == "b6b57e89-affc-5f6a-8db1-c75c0804c73e"
    # A row whose template is named after its filter is not the goal-key plan.
    assert row_plan_guid(PROFILE, "abc", "R", "R") != exposure_plan_guid(PROFILE, "abc", "R")


def test_the_new_recipe_is_not_the_old_one():
    assert project_guid(PROFILE, "M42") != legacy_project_guid(PROFILE, "M42")


def test_name_parts_are_length_prefixed_in_utf8_bytes():
    assert name_part("M42") == "3:M42"
    assert name_part("") == "0:"
    assert name_part(None) == "0:"
    # Three bytes in UTF-8, one character. The C# side counts bytes too,
    # because .NET would otherwise count UTF-16 units and disagree.
    assert name_part("µm°") == "5:µm°"


def test_every_recipe_uses_the_prefixed_form():
    assert project_guid(PROFILE, "M42") == stable_guid("9:profile-1/project/3:M42")
    assert target_guid(PROFILE, "P", "T") == stable_guid("9:profile-1/target/1:P/1:T")
    assert template_guid(PROFILE, "Ha", "cam-1") == stable_guid(
        "9:profile-1/template/2:Ha/5:cam-1"
    )
    assert exposure_plan_guid(PROFILE, "abc", "Ha") == stable_guid(
        "9:profile-1/plan/3:abc/2:Ha"
    )


def test_ambiguity_is_gone_for_every_pair_of_split_points():
    """The one property the prefix buys: no two splits of a string agree."""
    whole = "A/B/C"
    seen = set()
    for cut in range(len(whole) + 1):
        seen.add(target_guid(PROFILE, whole[:cut], whole[cut:]))
    assert len(seen) == len(whole) + 1


# ---------------------------------------------------------------------------
# Payloads the push refuses rather than writing.
# ---------------------------------------------------------------------------


def test_a_target_with_no_name_is_refused():
    plans = [plan("blank", project_name="P", target_name="   ")]
    with pytest.raises(PayloadError) as excinfo:
        convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    assert "no name" in str(excinfo.value)
    assert "'blank'" in str(excinfo.value)


def test_two_plans_with_one_target_name_in_one_project_are_refused():
    plans = [
        plan("first", project_name="Winter", target_name="M31"),
        plan("second", project_name="Winter", target_name="M31"),
    ]
    with pytest.raises(PayloadError) as excinfo:
        convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    message = str(excinfo.value)
    assert "'first'" in message and "'second'" in message
    assert "'M31'" in message and "'Winter'" in message


def test_the_same_target_name_in_two_projects_is_fine():
    plans = [
        plan("first", project_name="Winter", target_name="M31"),
        plan("second", project_name="Summer", target_name="M31"),
    ]
    payload = convert.build_payload(plans, GEAR, profile_id=PROFILE_ID)
    guids = [t.guid for ts in payload.targets_by_project_guid.values() for t in ts]
    assert len(guids) == len(set(guids)) == 2
