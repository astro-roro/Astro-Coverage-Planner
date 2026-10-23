"""Entity dataclasses + deterministic GUID stamping.

The Target Scheduler tables don't have a unique index on the `guid` column —
uniqueness is purely by convention. To make re-syncs idempotent we stamp our
own GUIDs deterministically so the same logical entity always hashes to the
same UUID, and the upsert layer can `SELECT Id WHERE guid = ?` to dedupe.

Identity recipe (UUIDv5 over the ACP_NS namespace, name = stable key). Every
free-text part is length-prefixed as "<utf-8 byte length>:<value>", so two
parts cannot run into each other whatever characters a name holds:
    ExposureTemplate:  "{profileId}/template/{filter}/{cameraId}/{template}"
    Project:           "{profileId}/project/{projectName}"
    Target:            "{profileId}/target/{projectName}/{targetName}"
    ExposurePlan:      "{profileId}/plan/{targetGuid}/{filter}/{template}"

The template and exposure plan recipes name a filter row's filter and TS
template (docs/specs/goal-panel-and-ts-order.md section 3). The recipes they
replaced, keyed by one goal key, stay below as `template_guid` and
`exposure_plan_guid`: the push still looks for rows stamped with them.

The length prefix is what stops "M42" plus "M43/NGC1977" hashing to the same
UUID as "M42/M43" plus "NGC1977". Slashes in catalogue names are ordinary,
so that collision was reachable with real input.

Anything that changes those name parts is a logical re-identification and
will create a fresh row in TS — that's intentional. Renames are out of scope
for v0.1; if needed, we can add a manual remapping table later.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

# Stable namespace UUID for ACP. Any UUID literal would do; this one was
# generated once with uuid.uuid4() and is now part of the on-disk identity
# of every row this extension stamps. **Never change this.**
ACP_NS = uuid.UUID("c4b6f1ee-1f9e-5e4b-9a7a-7e1d2c3a4b5c")


class PayloadError(ValueError):
    """The payload cannot be written, and the message says what to fix.

    Raised for the two things that make a row impossible to find again: a
    target with no name at all, and two entities that would land on one
    identity. Both refuse the whole push rather than write something the
    next push cannot match.
    """


def stable_guid(name: str) -> str:
    """UUIDv5 in the ACP namespace, returned as a string (matches TS storage)."""
    return str(uuid.uuid5(ACP_NS, name))


def name_part(value: str | None) -> str:
    """One free-text component of an identity name, length-prefixed.

    "<utf-8 byte length>:<value>". Byte length rather than character count,
    because the C# plugin has to produce the same string and .NET counts
    UTF-16 units. Byte length is the one measure both languages agree on for
    every input.
    """
    text = value or ""
    return f"{len(text.encode('utf-8'))}:{text}"


def template_guid(profile_id: str, filter_name: str, camera_id: str) -> str:
    return stable_guid(
        f"{name_part(profile_id)}/template/{name_part(filter_name)}/{name_part(camera_id)}"
    )


def project_guid(profile_id: str, project_name: str) -> str:
    return stable_guid(f"{name_part(profile_id)}/project/{name_part(project_name)}")


def target_guid(profile_id: str, project_name: str, target_name: str) -> str:
    return stable_guid(
        f"{name_part(profile_id)}/target/{name_part(project_name)}/{name_part(target_name)}"
    )


def exposure_plan_guid(profile_id: str, target_guid_str: str, filter_name: str) -> str:
    """The recipe keyed by one goal key ("R", or "R Stars" for a star goal).

    Pushes before filter rows stamped every exposure plan with it. The push
    now uses it only to find those plans and restamp them with
    `row_plan_guid`.
    """
    return stable_guid(
        f"{name_part(profile_id)}/plan/{name_part(target_guid_str)}/{name_part(filter_name)}"
    )


def row_plan_guid(
    profile_id: str, target_guid_str: str, filter_name: str, template_name: str
) -> str:
    """The exposure plan of one filter row on one target."""
    return stable_guid(
        f"{name_part(profile_id)}/plan/{name_part(target_guid_str)}/"
        f"{name_part(filter_name)}/{name_part(template_name)}"
    )


def row_template_guid(
    profile_id: str, filter_name: str, camera_id: str, template_name: str
) -> str:
    """The guid stamped on a template the push inserts for a filter row.

    Only ever written on insert. A template is found by profile and name,
    never by this guid, because Target Scheduler owns the templates it has.
    """
    return stable_guid(
        f"{name_part(profile_id)}/template/{name_part(filter_name)}/"
        f"{name_part(camera_id)}/{name_part(template_name)}"
    )


def gear_template_guid(profile_id: str, template_id: str) -> str:
    """The guid stamped on a template the push inserts for a gear template:
    the profile and the gear template's stable id. Only written on insert
    (docs/specs/gear-templates.md section 8); a template is found by its TS
    id, never by this guid alone. Matches `TsGuid.GearTemplate` in the plugin.
    """
    return stable_guid(f"{name_part(profile_id)}/gear-template/{name_part(template_id)}")


# ---------------------------------------------------------------------------
# The recipe as it stood before the length prefix. These are legacy: the
# migration in migrate.py is the only caller, and it uses them to recognise a
# row this extension stamped under the old recipe so it can restamp it. Never
# use them to identify a row being written.
# ---------------------------------------------------------------------------


def legacy_template_guid(profile_id: str, filter_name: str, camera_id: str) -> str:
    return stable_guid(f"{profile_id}/template/{filter_name}/{camera_id}")


def legacy_project_guid(profile_id: str, project_name: str) -> str:
    return stable_guid(f"{profile_id}/project/{project_name}")


def legacy_target_guid(profile_id: str, project_name: str, target_name: str) -> str:
    return stable_guid(f"{profile_id}/target/{project_name}/{target_name}")


def legacy_exposure_plan_guid(
    profile_id: str, target_guid_str: str, filter_name: str
) -> str:
    return stable_guid(f"{profile_id}/plan/{target_guid_str}/{filter_name}")


# ---------------------------------------------------------------------------
# Entity rows. Field names match column names in `schedulerdb.sqlite` v23 so
# that mapping to `INSERT INTO {tbl} ({fields}) VALUES (...)` is mechanical.
# Defaults match the plugin's C# constructor defaults where they exist.
# ---------------------------------------------------------------------------


@dataclass
class ExposureTemplate:
    profileId: str
    name: str
    filtername: str
    guid: str
    defaultexposure: float = 60.0
    gain: int = -1
    offset: int = -1
    bin: int = 1
    readoutmode: int = -1
    twilightlevel: int = 1  # 1 = Astronomical (NINA enum)
    minutesOffset: int = 0
    maximumhumidity: float = 100.0
    moonavoidanceenabled: int = 0
    moonavoidanceseparation: float = 0.0
    moonavoidancewidth: int = 0
    moonrelaxscale: float = 0.0
    moonrelaxmaxaltitude: float = 5.0
    moonrelaxminaltitude: float = -15.0
    moondownenabled: int = 0
    ditherevery: int = -1


@dataclass
class Project:
    profileId: str
    name: str
    guid: str
    description: str = ""
    state: int = 1  # 1 = Active
    priority: int = 1  # 1 = Normal
    createdate: int = 0  # unix seconds; convert.py fills in
    activedate: int | None = None
    inactivedate: int | None = None
    isMosaic: int = 0
    flatsHandling: int = 1  # 1 = flats on; the usual setting for imaging projects
    minimumtime: int = 0
    minimumaltitude: float = 0.0
    maximumAltitude: float = 0.0  # column name uses capital A in v23 schema
    usecustomhorizon: int = 0
    horizonoffset: float = 0.0
    meridianwindow: int = 0
    filterswitchfrequency: int = 0
    ditherevery: int = 0
    smartexposureorder: int = 0
    enablegrader: int = 0


@dataclass
class Target:
    projectid: int  # filled in after the parent Project upsert
    name: str
    guid: str
    active: int = 1
    ra: float = 0.0  # hours (NINA convention)
    dec: float = 0.0  # degrees
    epochcode: int = 2  # J2000 (NINA Epoch enum: JNOW 0, B1950 1, J2000 2, J2050 3)
    rotation: float = 0.0
    roi: float = 100.0
    # Legacy column from a v17 rename (formerly `overrideExposureOrder`).
    # Plugin no longer reads it; we leave NULL.
    unusedOEO: str | None = None
    # Added by Migrate/24.sql, so absent at user_version 23. Written only
    # when the DB has the column; see COLUMN_MIN_VERSION below.
    priority: int = -1


@dataclass
class ExposurePlan:
    profileId: str
    targetid: int  # filled in after the parent Target upsert
    exposureTemplateId: int  # filled in after the ExposureTemplate upsert
    guid: str
    exposure: float = -1.0  # -1 = "use template default"
    desired: int = 0
    acquired: int = 0
    accepted: int = 0
    enabled: int = 1


@dataclass
class RuleWeight:
    """One row per scoring rule on a project. Plugin's RepairAndUpdate fills
    in any missing rules on next NINA start, so this is a soft seed."""
    projectid: int
    name: str
    weight: float


@dataclass
class AacProject:
    """What convert.py knows about one project's AAC figures, so the push can
    decide whether to write `accepted` and turn the grader on
    (goal-panel-and-ts-order.md section 6).

    `blocked` is a reason ACP's own data rules the project out (a mosaic
    panel, or a pushed row with no AAC block); None means the push still has
    to check TS (the profile, the plans TS holds, imaging since the
    watermark). `accepted_by_plan_guid` is the count to write for every plan
    this push writes in the project. `filed_through_night` is the oldest
    watermark among the project's plans.
    """
    name: str
    filed_through_night: str | None = None
    #: Whether any plan or row in the project carries an `aac` block. A
    #: project AAC has never reported on is skipped without a push note.
    linked: bool = False
    blocked: str | None = None
    accepted_by_plan_guid: dict[str, int] = field(default_factory=dict)


@dataclass(eq=False)
class GearWork:
    """One gear template the push has to settle in TS
    (docs/specs/gear-templates.md section 8). *template* is the gear
    template dict as ACP sends it, with its `ts` links by profile id.

    `slot` is the wheel slot for the filter, None when the wheel is unknown
    or the filter is not on it; `off_wheel` is True only when the slots are
    known and none is the filter's. `guid` is stamped on an insert. Compared
    by identity, so a work can key a dict.
    """
    camera_id: str
    camera_name: str | None
    filter: str
    template: dict
    slot: str | None
    off_wheel: bool
    guid: str
    #: Rows of pushed plans on this template, named for the skip notes.
    row_labels: list[str] = field(default_factory=list)

    def link_for(self, profile_id: str) -> dict | None:
        """The template's `ts` entry for *profile_id*, matched ignoring case."""
        ts = self.template.get("ts")
        if not isinstance(ts, dict) or not profile_id:
            return None
        for pid, link in ts.items():
            if isinstance(pid, str) and pid.lower() == profile_id.lower() and isinstance(link, dict):
                return link
        return None


@dataclass
class SyncPayload:
    """Everything we want to write in a single sync run.

    Order matters at insert time: templates first (so we have IDs to point
    ExposurePlans at), then projects (with their ruleweights), then targets,
    then exposure plans.
    """
    profile_id: str
    templates: list[ExposureTemplate] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    rule_weights_by_project_guid: dict[str, list[RuleWeight]] = field(default_factory=dict)
    targets_by_project_guid: dict[str, list[Target]] = field(default_factory=dict)
    plans_by_target_guid: dict[str, list[ExposurePlan]] = field(default_factory=dict)
    # Maps each ExposurePlan (by its own guid) to the ExposureTemplate guid
    # it should reference. convert.py populates this; upsert.py reads it to
    # resolve `exposureTemplateId` after both rows are in the DB.
    template_guid_by_plan_guid: dict[str, str] = field(default_factory=dict)
    # Project guid -> extra column names (lowercase) to leave out of THIS
    # project's UPDATE, beyond the static INSERT_ONLY_COLUMNS below. Covers
    # `state`, `priority`, `minimumtime`, `filterswitchfrequency` and
    # `smartexposureorder` when ACP hasn't changed them
    # since the last sync on this rig, and `activedate`/`inactivedate` when
    # the push isn't writing the matching state. convert.py computes it per
    # the rules in docs/specs/ts-project-settings.md section 5; always
    # empty for a fresh (never-before-synced) project, since there's
    # nothing yet to compare against. INSERT is never affected: a new row
    # always gets every column, ACP's values included.
    project_update_skip_cols_by_guid: dict[str, frozenset[str]] = field(default_factory=dict)
    # How to find each exposure plan when its own guid misses, and whether
    # this push may write its `exposure`. Keyed by the plan's row guid.
    plan_lookup_by_guid: dict[str, "PlanLookup"] = field(default_factory=dict)
    # Exposure plans of rows removed in ACP, per target guid. The push sets
    # `enabled` to 0 on each, never deleting it.
    removed_by_target_guid: dict[str, list["RemovedPlan"]] = field(default_factory=dict)
    # Canonical filter names on the cameras this push's plans use. A template
    # on the profile whose filter is none of these is listed in the result.
    camera_filters: set[str] = field(default_factory=set)
    # Per project guid, the AAC figures the push may write
    # (goal-panel-and-ts-order.md section 6). A project not listed has none.
    aac_by_project_guid: dict[str, "AacProject"] = field(default_factory=dict)
    # The time the AAC freshness rule is judged at, in the site's zone. Set
    # by convert.build_payload; None means no AAC write is made.
    aac_now: "datetime | None" = None
    # The IANA name of the site's time zone, for the same rule.
    aac_tz: str = "Australia/Melbourne"
    # Lines for the push result that convert.py works out, such as plans in
    # one project disagreeing on the filter switch frequency.
    notes: list[str] = field(default_factory=list)
    # What the base recorded about each target's shooting order, per target
    # guid (goal-panel-and-ts-order.md section 5). A target without an entry
    # has no base for it.
    order_by_target_guid: dict[str, "TargetOrder"] = field(default_factory=dict)
    # The profile's name, for the notes. None reads as "this profile".
    profile_name: str | None = None
    # The gear templates of the cameras the pushed plans use, in gear order,
    # for the gear template writer. Empty when no camera carries gear
    # templates, which is today's behaviour. Each has an ExposureTemplate in
    # `templates` under the same guid.
    gear_works: list[GearWork] = field(default_factory=list)


@dataclass
class PlanLookup:
    """Where else a row's exposure plan may be, in the order section 3's
    "Exposure plan identity" gives, after its own guid.

    `pinned_id` is the id `ts_links` records for this row and panel.
    `old_guids` are the goal-key recipes: the template's name, then the
    filter's name for the first row of that filter only.

    `template_changed` is True when the base records a template for this
    row and the row's filter or template differs from it: the user changed
    it in ACP. Only then does an existing plan move to the row's template,
    with `exposure` written as -1. Otherwise an existing plan keeps the
    template it has, because TS owns that choice and ACP's name for it may
    be one an older writer made up ("R (camera)" for TS's own "R"). A row
    derived from `filter_goals` is never changed this way.
    """
    label: str
    pinned_id: int | None = None
    old_guids: list[str] = field(default_factory=list)
    template_changed: bool = False
    #: The ACP row this plan is written for, which the order writer needs to
    #: compare ACP's order with the base's.
    row_id: str | None = None
    #: True when the base records this row's plan as enabled after the last
    #: sync. A plan TS now has disabled was then turned off in TS by the
    #: user, and the push leaves it off. A base from before `enabled` was
    #: recorded gives False, so such a plan is enabled as before.
    in_base: bool = False
    #: The row is written for a gear template. Its `exposure` then follows
    #: the sub rules of gear-templates.md section 8.
    gear_mode: bool = False
    #: The row's `template_sub_s`, and whether it equals the gear template's
    #: `sub_s` (the row follows its template).
    row_sub_s: float = 0.0
    row_follows: bool = False
    #: The base recorded a different sub for the row: ACP changed it since
    #: the last push.
    row_sub_changed: bool = False


@dataclass
class TargetOrder:
    """One target's order as the base recorded it after the last sync.

    `base_order` is `exposure_order`, the row ids in the order TS shot them;
    None means the base has none, so the push leaves the override rows
    alone. `base_rows` is `override_rows`, as (action, referenceIdx) pairs;
    None means the base did not record them, which never equals TS's rows.
    `known_rows` is every ACP row id the base knew, including rows with no
    plan; None means an older base, where the ids of `base_order` and of the
    base's exposure plans stand for it.
    """
    base_order: list[str] | None = None
    base_rows: list[tuple[int, int]] | None = None
    known_rows: list[str] | None = None


@dataclass
class RemovedPlan:
    """An exposure plan `ts_links` pins to a row ACP no longer has.

    `guids` are the identities ACP could have stamped on it, from the
    filter and template the base recorded for that row. A plan carrying
    none of them was not ACP's, and is left alone.
    """
    plan_id: int
    guids: frozenset[str]


# ---------------------------------------------------------------------------
# Version-dependent columns.
#
# Target Scheduler applies `Database/Migrate/N.sql` for every N greater than
# the DB's `PRAGMA user_version`, so a DB at version N is the initial schema
# plus scripts 1..N. Between 23 and 28 every step is an additive ALTER TABLE
# ADD COLUMN, and only one of them touches a table this extension writes.
# See docs/schema-history.md for the full step-by-step diff and the citations.
#
# Nothing was renamed or dropped in that span, so there is no rename map here.
# If a future version renames a column, add the mapping alongside this table
# rather than forking the module per version.
# ---------------------------------------------------------------------------

#: (table, column) -> first `user_version` that has the column. Anything not
#: listed has existed since 23, the oldest version we support.
COLUMN_MIN_VERSION: dict[tuple[str, str], int] = {
    ("target", "priority"): 24,  # Migrate/24.sql, INTEGER DEFAULT -1
}


def column_exists_at(table: str, column: str, user_version: int) -> bool:
    """True if *column* is part of *table* at the given `user_version`."""
    return user_version >= COLUMN_MIN_VERSION.get((table, column), 0)


def columns_for_version(table: str, columns: list[str], user_version: int) -> list[str]:
    """Filter *columns* down to those the schema at *user_version* has.

    Order is preserved so callers can zip the result against their values.
    """
    return [c for c in columns if column_exists_at(table, c, user_version)]


# ---------------------------------------------------------------------------
# Columns written when a row is created and never again.
#
# `exposureplan.acquired` is how many subs the camera actually took and
# `accepted` is the grader's verdict on them. Neither is ACP's to state: ACP
# recomputes both from its own `actual_hours`, which at best round-trips the
# acquired count and at worst marks every frame the grader rejected as good.
# On a project with the grader on, Target Scheduler reads completeness from
# `accepted`, so overwriting it makes TS believe a target is finished.
#
# `project.createdate` is the date the project was created. Rewriting it on
# every push makes it the date of the last push instead.
#
# `project.flatsHandling` is a Target Scheduler setting: whether the project
# takes flats at all. TsConvert never sets it, so the dataclass default was
# going out on every push, silently turning flats off for every ACP project.
# Insert-only with the default flipped to 1 (flats on) means a new project
# gets flats on, and a value changed in Target Scheduler stays changed.
#
# The rest of `project`'s TS-owned columns joined it for the same reason,
# per docs/specs/ts-project-settings.md section 4: rig tuning (custom
# horizon, dither, the grader, maximum altitude and the description) is
# TS's to set, and the guid update used to rewrite every one of them back
# to its dataclass default on every push.
#
# `state`, `priority`, `minimumtime`, `filterswitchfrequency` and
# `smartexposureorder` are NOT here: they're ACP-owned but synced both
# ways, so they're written on update only when ACP changed them since the
# last sync (goal-panel-and-ts-order.md section 4 added the last two).
# That's a per-push, per-project decision, handled by
# `project_update_skip_cols_by_guid` on the payload rather than by this
# static table. `name`, `minimumaltitude`, `meridianwindow`, `isMosaic` and
# `profileId` are ACP-owned outright and always written on update.
# ---------------------------------------------------------------------------

#: table -> columns that go into the INSERT and never into an UPDATE.
INSERT_ONLY_COLUMNS: dict[str, frozenset[str]] = {
    # `exposure` is written as -1 on insert, and on update only when the
    # row's filter or template changed (PlanLookup.template_changed). Every
    # plan an older push made carries an explicit sub, and rewriting it
    # would change what TS shoots (goal-panel-and-ts-order.md section 3).
    "exposureplan": frozenset({"acquired", "accepted", "exposure"}),
    "project": frozenset({
        "createdate", "flatshandling", "usecustomhorizon", "horizonoffset",
        "ditherevery", "enablegrader", "maximumaltitude", "description",
    }),
}


def columns_for_update(
    table: str, columns: list[str], extra_skip: frozenset[str] = frozenset()
) -> list[str]:
    """Drop the insert-only columns from *columns*, order preserved.

    *extra_skip* adds columns to leave out of this one UPDATE beyond the
    static table above; see `project_update_skip_cols_by_guid`.
    """
    skip = INSERT_ONLY_COLUMNS.get(table, frozenset()) | {c.lower() for c in extra_skip}
    return [c for c in columns if c.lower() not in skip]
