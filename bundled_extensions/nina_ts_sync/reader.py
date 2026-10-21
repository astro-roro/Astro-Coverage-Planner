"""Read TS rows for a profile — the inverse of upsert.py.

Returns the same dataclass shapes upsert.py consumes, plus the on-disk Ids
since callers need them to navigate FK relationships.

Two read modes:
    `read_all(conn, profile_id)`  — pull every row in scope, used by the
                                    bootstrap-import flow.
    `read_acquired(conn, profile_id)` — just the (plan_guid → acquired)
                                        mapping, used by the lightweight
                                        ongoing actual-hours sync.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .schema import ExposurePlan, ExposureTemplate, Project, Target


@dataclass
class TSSnapshot:
    """Everything we read for a profile, with on-disk Ids preserved.

    `templates_by_id` / `projects_by_id` / `targets_by_id` map the integer
    primary keys to the dataclass; the `*_id_by_guid` indexes are convenience
    reverse-lookups for guid-based dedup callers.
    """
    profile_id: str
    templates_by_id: dict[int, ExposureTemplate] = field(default_factory=dict)
    projects_by_id: dict[int, Project] = field(default_factory=dict)
    targets_by_id: dict[int, Target] = field(default_factory=dict)
    plans_by_id: dict[int, ExposurePlan] = field(default_factory=dict)
    template_id_by_guid: dict[str, int] = field(default_factory=dict)
    project_id_by_guid: dict[str, int] = field(default_factory=dict)
    target_id_by_guid: dict[str, int] = field(default_factory=dict)
    plan_id_by_guid: dict[str, int] = field(default_factory=dict)
    #: Each target's override rows as (action, referenceIdx) pairs, in
    #: order. A target without any has no entry.
    override_rows_by_target: dict[int, list[tuple[int, int]]] = field(default_factory=dict)

    def enabled_plan_ids(self, target_id: int) -> list[int]:
        """The target's enabled plans in natural order, which is what an
        override row's referenceIdx counts over (SchedulerPlanLoader.cs:53)."""
        return sorted(
            pid for pid, pl in self.plans_by_id.items()
            if pl.targetid == target_id and int(pl.enabled if pl.enabled is not None else 1) != 0
        )


def _row_to_dataclass(row: sqlite3.Row, cls):
    """Build a dataclass from a Row by matching column names to field names.

    SQLite is case-insensitive on column matching; dataclass fields are not.
    We use the dataclass's own field names as the source of truth — anything
    else in the row is ignored, anything missing falls back to the default.

    That fallback is also how reads tolerate the supported schema range: a
    column a newer version added, such as `target.priority` at user_version
    24, is simply absent from the row on an older database and the dataclass
    default (the same value Target Scheduler would have defaulted it to)
    stands in. Columns a newer version added to tables we don't model are
    ignored the same way.
    """
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(cls)}
    kwargs = {}
    row_keys = set(row.keys())
    for name, f in fields.items():
        if name in row_keys:
            kwargs[name] = row[name]
        elif f.default is not dataclasses.MISSING:
            kwargs[name] = f.default
        else:
            kwargs[name] = None
    return cls(**kwargs)


def read_all(conn: sqlite3.Connection, profile_id: str) -> TSSnapshot:
    """Pull every row for a profile across the four core tables."""
    snap = TSSnapshot(profile_id=profile_id)

    for row in conn.execute(
        "SELECT * FROM exposuretemplate WHERE profileId = ?", (profile_id,)
    ):
        tpl = _row_to_dataclass(row, ExposureTemplate)
        tid = int(row["Id"])
        snap.templates_by_id[tid] = tpl
        if tpl.guid:
            snap.template_id_by_guid[tpl.guid] = tid

    # Projects don't have profileId on the column the way other entities do —
    # actually they do (see schema). Filter the same way.
    for row in conn.execute(
        "SELECT * FROM project WHERE profileId = ?", (profile_id,)
    ):
        proj = _row_to_dataclass(row, Project)
        pid = int(row["Id"])
        snap.projects_by_id[pid] = proj
        if proj.guid:
            snap.project_id_by_guid[proj.guid] = pid

    if snap.projects_by_id:
        # Targets have no profileId column — scope via parent project.
        placeholder = ",".join("?" for _ in snap.projects_by_id)
        for row in conn.execute(
            f"SELECT * FROM target WHERE projectid IN ({placeholder})",
            tuple(snap.projects_by_id),
        ):
            tgt = _row_to_dataclass(row, Target)
            tid = int(row["Id"])
            snap.targets_by_id[tid] = tgt
            if tgt.guid:
                snap.target_id_by_guid[tgt.guid] = tid

    for row in conn.execute(
        "SELECT * FROM exposureplan WHERE profileId = ?", (profile_id,)
    ):
        plan = _row_to_dataclass(row, ExposurePlan)
        pid = int(row["Id"])
        snap.plans_by_id[pid] = plan
        if plan.guid:
            snap.plan_id_by_guid[plan.guid] = pid

    if snap.targets_by_id:
        placeholder = ",".join("?" for _ in snap.targets_by_id)
        for row in conn.execute(
            'SELECT targetid, "action", referenceIdx FROM overrideexposureorderitem '
            f'WHERE targetid IN ({placeholder}) ORDER BY targetid, "order", Id',
            tuple(snap.targets_by_id),
        ):
            ref = -1 if row["referenceIdx"] is None else int(row["referenceIdx"])
            snap.override_rows_by_target.setdefault(int(row["targetid"]), []).append(
                (int(row["action"]), ref))

    return snap


def read_acquired(
    conn: sqlite3.Connection, profile_id: str
) -> dict[str, tuple[int, float]]:
    """Return ``{plan_guid: (good_subs, exposure_seconds)}`` per profile.

    "Good subs" prefers `accepted` over `acquired` when the parent project
    has `enablegrader=1` — so frames the user (or auto-grader) flagged as
    bad don't count toward ACP's "h left" rail. Falls back to `acquired`
    otherwise.

    Skips plans without a guid stamp (legacy rows from TS UI directly).
    """
    out: dict[str, tuple[int, float]] = {}
    for row in conn.execute(
        "SELECT ep.guid AS guid, ep.acquired AS acquired, "
        "       ep.accepted AS accepted, ep.exposure AS exposure, "
        "       x.defaultexposure AS defaultexposure, "
        "       p.enablegrader AS enablegrader "
        "FROM exposureplan ep "
        "JOIN target t ON ep.targetid = t.Id "
        "JOIN project p ON t.projectid = p.Id "
        "LEFT JOIN exposuretemplate x ON x.Id = ep.exposureTemplateId "
        "WHERE ep.profileId = ? AND ep.guid IS NOT NULL AND ep.guid != ''",
        (profile_id,),
    ):
        if int(row["enablegrader"] or 0) == 1:
            count = int(row["accepted"] or 0)
        else:
            count = int(row["acquired"] or 0)
        # A plan the push inserted carries exposure -1, "use the template's
        # default", so the template supplies the length.
        exposure = float(row["exposure"] or 0)
        if exposure <= 0:
            exposure = float(row["defaultexposure"] or 0)
        out[row["guid"]] = (count, exposure)
    return out
