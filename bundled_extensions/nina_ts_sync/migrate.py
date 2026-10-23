"""Restamp rows that still carry the pre-length-prefix ACP identity.

The identity recipe used to join free-text name parts with a slash, so
"M42" plus "M43/NGC1977" hashed to the same UUID as "M42/M43" plus
"NGC1977". Length-prefixing each part fixes that, and changes every guid ACP
has ever written.

If the new recipe simply went live, the next push would find none of its own
rows, insert a fresh tree beside them, and leave the old tree active and
still being imaged. So the push migrates as it goes: for each row in the four
tables it writes, recompute the old recipe from that row's natural key, and
where the stored guid matches, rewrite it to the new recipe. A row whose guid
does not match the old recipe was stamped by Target Scheduler or by somebody
else and is left exactly as it is.

This runs inside the push's own BEGIN IMMEDIATE transaction, after the
backup, so a failure anywhere in the push takes the restamping back with it.
It is idempotent: on a database that has already been migrated nothing
matches the old recipe and nothing is written.

Projects, targets and exposure plans carry their whole natural key on the
row, or on the row plus a join to its parent, so their old guids are
recomputed from the database alone. Templates are not migrated: the push
finds a template by profile and name and uses it as it is
(docs/specs/goal-panel-and-ts-order.md section 3), so a template's guid is
never looked up and an old one does no harm.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .schema import (
    SyncPayload,
    exposure_plan_guid,
    legacy_exposure_plan_guid,
    legacy_project_guid,
    legacy_target_guid,
    project_guid,
    target_guid,
)


@dataclass
class MigrationResult:
    #: How many rows were restamped. Zero on an already-migrated database.
    rewritten: int = 0
    notes: list[str] = field(default_factory=list)


def migrate_legacy_guids(
    conn: sqlite3.Connection, payload: SyncPayload
) -> MigrationResult:
    """Rewrite every old-recipe guid onto the new one. Caller owns the tx."""
    result = MigrationResult()
    _migrate_projects(conn, result)
    _migrate_targets(conn, result)
    _migrate_exposure_plans(conn, result)
    return result


def _migrate_projects(conn: sqlite3.Connection, result: MigrationResult) -> None:
    rows = conn.execute("SELECT Id, profileId, name, guid FROM project").fetchall()
    for row_id, profile_id, name, guid in rows:
        profile_id, name = _text(profile_id), _text(name)
        if guid != legacy_project_guid(profile_id, name):
            continue
        _rewrite(conn, "project", int(row_id), project_guid(profile_id, name), result)


def _migrate_targets(conn: sqlite3.Connection, result: MigrationResult) -> None:
    rows = conn.execute(
        "SELECT t.Id, t.name, t.guid, p.profileId, p.name "
        "FROM target t JOIN project p ON p.Id = t.projectid"
    ).fetchall()
    for row_id, target_name, guid, profile_id, project_name in rows:
        profile_id = _text(profile_id)
        project_name = _text(project_name)
        target_name = _text(target_name)
        if guid != legacy_target_guid(profile_id, project_name, target_name):
            continue
        _rewrite(
            conn, "target", int(row_id),
            target_guid(profile_id, project_name, target_name), result,
        )


def _migrate_exposure_plans(conn: sqlite3.Connection, result: MigrationResult) -> None:
    """A plan's identity embeds its target's, so both recipes are recomputed.

    The target guid used here comes from the target's natural key rather than
    the guid stored on the target row, which means the order the four tables
    are migrated in makes no difference.
    """
    rows = conn.execute(
        "SELECT e.Id, e.guid, e.profileId, p.profileId, p.name, t.name, x.filtername "
        "FROM exposureplan e "
        "JOIN target t ON t.Id = e.targetid "
        "JOIN project p ON p.Id = t.projectid "
        "JOIN exposuretemplate x ON x.Id = e.exposureTemplateId"
    ).fetchall()
    for row_id, guid, plan_profile, profile_id, project_name, target_name, filter_name in rows:
        plan_profile = _text(plan_profile)
        profile_id = _text(profile_id)
        project_name = _text(project_name)
        target_name = _text(target_name)
        filter_name = _text(filter_name)

        old_target = legacy_target_guid(profile_id, project_name, target_name)
        if guid != legacy_exposure_plan_guid(plan_profile, old_target, filter_name):
            continue
        new_target = target_guid(profile_id, project_name, target_name)
        _rewrite(
            conn, "exposureplan", int(row_id),
            exposure_plan_guid(plan_profile, new_target, filter_name), result,
        )


def _rewrite(
    conn: sqlite3.Connection, table: str, row_id: int, new_guid: str,
    result: MigrationResult,
) -> None:
    """Stamp *new_guid* on one row, unless another row already holds it.

    The clash cannot happen on first contact, because the new recipe has
    never been written to this database. It could happen on a database that
    was half migrated by a run that died between the commit and the next
    push, and a duplicate guid is worse than an unmigrated row, so the row is
    left alone and the push says so.
    """
    clash = conn.execute(
        f"SELECT Id FROM {table} WHERE guid = ? AND Id <> ?", (new_guid, row_id)
    ).fetchone()
    if clash is not None:
        result.notes.append(
            f"{table} row {row_id} still carries the old ACP identity, but row "
            f"{int(clash[0])} already holds the new one, so it was left alone"
        )
        return
    conn.execute(f"UPDATE {table} SET guid = ? WHERE Id = ?", (new_guid, row_id))
    result.rewritten += 1


def _text(value) -> str:
    """A NULL column is an empty name, which is how the recipes read it."""
    return "" if value is None else str(value)
