"""Upsert SyncPayload into a `schedulerdb.sqlite` at user_version 23 to 28.

Pattern per entity (Project / Target / ExposureTemplate / ExposurePlan):
    SELECT Id FROM {tbl} WHERE guid = ?
    if hit: UPDATE all data columns by Id, return existing Id.
    else:   INSERT (Id NULL → autoincrement), capture conn.lastrowid.

Order is fixed by FKs, even though the plugin doesn't enforce them:
    1. exposuretemplate    (no FK in)
    2. project             (no FK in) + ruleweight rows for the project
    3. target              (FK projectid → project.Id)
    4. exposureplan        (FK targetid → target.Id, exposureTemplateId → exposuretemplate.Id)

`SyncPayload.template_guid_by_ref` is the bridge between logical
(target_guid, filter_name) keys and the template guid that should hold the
ExposurePlan's `exposureTemplateId`. Both have to be looked up to resolve.

Exposes a single entry point, ``apply(conn, payload)``, plus a
``preview(conn, payload)`` that reports counts without writing.

Schema versions: the column list for each INSERT/UPDATE comes from the
entity's dataclass fields narrowed to what the open database actually has,
so one code path serves the whole supported range. `target.priority` arrived
at user_version 24, so against a 23 database it is simply left out of the
statement and Target Scheduler's own default applies everywhere else. See
docs/schema-history.md.
"""
from __future__ import annotations

import dataclasses
import math
import logging
import sqlite3
from dataclasses import dataclass, field

from .db import get_user_version
from .gear_templates import apply_gear_templates
from .migrate import migrate_legacy_guids
from .order import EXPOSURE, Rows, effective_plan_order, inject_dithers, same_order
from .schema import (
    PayloadError,
    PlanLookup,
    RuleWeight,
    SyncPayload,
    TargetOrder,
    columns_for_update,
    columns_for_version,
)

LOG = logging.getLogger(__name__)


@dataclass
class TableCounts:
    inserted: int = 0
    updated: int = 0
    claimed: int = 0  # row existed by name but had no guid; we set our guid on it


@dataclass
class SyncReport:
    exposuretemplate: TableCounts = field(default_factory=TableCounts)
    project: TableCounts = field(default_factory=TableCounts)
    target: TableCounts = field(default_factory=TableCounts)
    exposureplan: TableCounts = field(default_factory=TableCounts)
    ruleweight_seeded_projects: int = 0
    #: Rows restamped from the pre-length-prefix identity recipe onto the
    #: current one. Zero on a database that has already been migrated.
    migrated_guids: int = 0
    notes: list[str] = field(default_factory=list)
    #: Exposure plans of rows removed in ACP, set to enabled 0 by this push.
    disabled_plans: int = 0
    #: "M42 R" for each plan the user turned off in TS since the last sync,
    #: which this push left off.
    kept_disabled: list[str] = field(default_factory=list)
    #: "M42 R: TS shoots 300 s, template R says 180 s" for every written plan
    #: whose own exposure overrides its template's default.
    exposure_mismatches: list[str] = field(default_factory=list)
    #: Templates on the profile whose filter is on none of the cameras.
    #: The sync never deletes a template; these are for the user to remove.
    stray_templates: list[str] = field(default_factory=list)
    #: Targets whose override rows were remapped because a plan was disabled
    #: or enabled again.
    override_rebuilt: list[str] = field(default_factory=list)
    #: Projects whose switch frequency or smart order this push wrote, so
    #: every target's cadence rows were deleted.
    cadence_reset_projects: list[str] = field(default_factory=list)
    #: Targets whose override rows this push replaced with ACP's order.
    order_written: list[str] = field(default_factory=list)
    #: Targets whose override rows are ACP's after this push, so the base
    #: records them and the order TS now shoots. Every other target keeps
    #: the order its old base recorded (goal-panel-and-ts-order.md section 5,
    #: "As with state"). Not part of the push result.
    order_synced_target_ids: set[int] = field(default_factory=set)
    #: Guids of the projects this push inserted, whose base holds exactly
    #: ACP's values. Not part of the push result.
    inserted_project_guids: set[str] = field(default_factory=set)
    #: Projects where this push wrote AAC's accepted counts and turned the
    #: grader on (section 6).
    aac_written_projects: list[str] = field(default_factory=list)
    #: What the gear template writer did with each gear template of the
    #: pushed plans' cameras: `{template_id, ts_id, guid, base, action,
    #: fields_written, note?, conflicts?}`. Empty without gear templates.
    templates: list[dict] = field(default_factory=list)
    #: TS ids of the profile's templates no gear template stands for, when
    #: the push ran in gear mode (their names are in `stray_templates`).
    stray_template_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "exposuretemplate": dataclasses.asdict(self.exposuretemplate),
            "project": dataclasses.asdict(self.project),
            "target": dataclasses.asdict(self.target),
            "exposureplan": dataclasses.asdict(self.exposureplan),
            "ruleweight_seeded_projects": self.ruleweight_seeded_projects,
            "migrated_guids": self.migrated_guids,
            "notes": list(self.notes),
            "disabled_plans": self.disabled_plans,
            "kept_disabled": list(self.kept_disabled),
            "exposure_mismatches": list(self.exposure_mismatches),
            "stray_templates": list(self.stray_templates),
            "override_rebuilt": list(self.override_rebuilt),
            "cadence_reset_projects": list(self.cadence_reset_projects),
            "order_written": list(self.order_written),
            "aac_written_projects": list(self.aac_written_projects),
        }
        if self.templates:
            out["templates"] = [dict(t) for t in self.templates]
            out["stray_template_ids"] = list(self.stray_template_ids)
        return out


@dataclass
class _LiveSchema:
    """What the open database actually offers, so writes can adapt to it.

    `user_version` is the declared schema version; `columns_by_table` is what
    `PRAGMA table_info` reports, lowercased. The version is the source of
    truth for which columns *should* be there, and the PRAGMA is a backstop
    for a database that has drifted from the migration history (a partially
    applied migration, or a hand-edited install).
    """

    user_version: int
    columns_by_table: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def probe(cls, conn: sqlite3.Connection) -> "_LiveSchema":
        version = get_user_version(conn)
        by_table: dict[str, set[str]] = {}
        for table in ("exposuretemplate", "project", "target", "exposureplan"):
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if rows:
                by_table[table] = {str(r[1]).lower() for r in rows}
        return cls(user_version=version, columns_by_table=by_table)

    def writable(self, table: str, fields: list[str]) -> list[str]:
        """Narrow *fields* to the columns this database can take, in order."""
        allowed = columns_for_version(table, fields, self.user_version)
        actual = self.columns_by_table.get(table)
        if actual is None:
            return allowed
        return [c for c in allowed if c.lower() in actual]


def _entity_columns(entity, table: str, schema: _LiveSchema) -> tuple[list[str], list]:
    """Return (column_names, values) for the columns *schema* actually has.

    `Id` is never a dataclass field, so autoincrement is left alone.
    """
    fields = [f.name for f in dataclasses.fields(entity)]
    cols = schema.writable(table, fields)
    values = [getattr(entity, n) for n in cols]
    return cols, values


def _upsert_by_guid(
    conn: sqlite3.Connection,
    table: str,
    entity,
    counts: TableCounts,
    schema: _LiveSchema,
    *,
    claim_keys: tuple[str, ...] = (),
    extra_skip_update: frozenset[str] = frozenset(),
) -> int:
    """Upsert *entity* into *table*. Returns the row's Id.

    Lookup order:
    1. By guid (the deterministic UUIDv5 we stamp). Hit → UPDATE.
    2. If `claim_keys` provided and (1) missed, look up by those columns'
       values on the entity. Hit with empty/null guid → CLAIM (UPDATE all
       columns, including stamping our guid). Hit with non-empty foreign
       guid → fall through to (3); we don't overwrite another tool's stamp.
    3. INSERT.

    The claim step is the bridge between "TS already has rows the user
    created manually" and "ACP wants to push without duplicating them."

    *extra_skip_update* leaves out further columns from just this one
    UPDATE, on top of the table's static insert-only columns (used for a
    project's `state`, `priority`, `minimumtime`, `activedate` and
    `inactivedate`, per `project_update_skip_cols_by_guid`). It never
    affects an INSERT or a CLAIM.
    """
    cols, vals = _entity_columns(entity, table, schema)

    # The UPDATE never touches the insert-only columns: the frame counts on an
    # exposure plan and a project's creation date belong to whoever wrote them
    # first. See INSERT_ONLY_COLUMNS in schema.py for why.
    update_cols = columns_for_update(table, cols, extra_skip_update)
    update_vals = [getattr(entity, c) for c in update_cols]
    set_clause = ", ".join(f"{c} = ?" for c in update_cols)

    # 1) By guid
    row = conn.execute(
        f"SELECT Id FROM {table} WHERE guid = ?", (entity.guid,)
    ).fetchone()
    if row is not None:
        existing_id = int(row["Id"])
        conn.execute(
            f"UPDATE {table} SET {set_clause} WHERE Id = ?",
            (*update_vals, existing_id),
        )
        counts.updated += 1
        return existing_id

    # 2) By claim keys (e.g. profileId+name)
    if claim_keys:
        keys_sql = " AND ".join(f"{k} = ?" for k in claim_keys)
        keys_vals = tuple(getattr(entity, k) for k in claim_keys)
        row = conn.execute(
            f"SELECT Id, guid FROM {table} WHERE {keys_sql}",
            keys_vals,
        ).fetchone()
        if row is not None:
            existing_guid = (row["guid"] or "").strip()
            if not existing_guid:
                # Empty guid → manual-setup row, safe to claim. A claim is an
                # update of a row that already existed, so the insert-only
                # columns are left where they are here too: the frames it has
                # already taken are not ours to reset.
                existing_id = int(row["Id"])
                conn.execute(
                    f"UPDATE {table} SET {set_clause} WHERE Id = ?",
                    (*update_vals, existing_id),
                )
                counts.claimed += 1
                return existing_id
            # Non-empty foreign guid: another stamping authority owns this
            # row. Don't fight; insert a fresh row and let the user resolve.

    # 3) INSERT
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    counts.inserted += 1
    return int(cur.lastrowid)


def _template_id_by_name(conn: sqlite3.Connection, tpl) -> int | None:
    """The Id of the profile's template named *tpl.name*, or None. Two
    templates of one name: the lowest Id."""
    row = conn.execute(
        "SELECT Id FROM exposuretemplate WHERE profileId = ? AND name = ? ORDER BY Id LIMIT 1",
        (tpl.profileId, tpl.name),
    ).fetchone()
    return int(row["Id"]) if row is not None else None


def _insert_template(conn: sqlite3.Connection, tpl, counts: TableCounts, schema: _LiveSchema) -> int:
    cols, vals = _entity_columns(tpl, "exposuretemplate", schema)
    cur = conn.execute(
        f"INSERT INTO exposuretemplate ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        vals,
    )
    counts.inserted += 1
    return int(cur.lastrowid)


#: The exposure plan columns an update always writes. `exposureTemplateId`
#: and `exposure` are written only when the row's template changed.
_PLAN_UPDATE_COLUMNS = ("profileId", "targetid", "guid", "desired", "enabled")


def _find_exposure_plan(
    conn: sqlite3.Connection, plan, lookup: PlanLookup, used: set[int]
) -> int | None:
    """The existing plan for a row: its own guid, the pinned id when that
    plan is on this target, then the goal-key recipes. A plan another row of
    this push already took is skipped."""
    def by_guid(guid: str) -> int | None:
        for r in conn.execute(
            "SELECT Id FROM exposureplan WHERE guid = ? ORDER BY Id", (guid,)
        ).fetchall():
            if int(r["Id"]) not in used:
                return int(r["Id"])
        return None

    hit = by_guid(plan.guid)
    if hit is not None:
        return hit
    if lookup.pinned_id is not None and lookup.pinned_id not in used:
        r = conn.execute(
            "SELECT targetid, profileId FROM exposureplan WHERE Id = ?", (lookup.pinned_id,)
        ).fetchone()
        if r is not None and r["targetid"] == plan.targetid and r["profileId"] == plan.profileId:
            return lookup.pinned_id
    for guid in lookup.old_guids:
        hit = by_guid(guid)
        if hit is not None:
            return hit
    return None


def _upsert_exposure_plan(
    conn: sqlite3.Connection,
    plan,
    lookup: PlanLookup,
    template_id,
    counts: TableCounts,
    schema: _LiveSchema,
    used: set[int],
) -> tuple[int, bool]:
    """Write one row's exposure plan. Returns (Id, inserted).

    A plan found by any step of the chain is restamped with the row's guid
    and enabled, and its `desired` written. It keeps the template it has and
    its `exposure`, unless the row's template changed since the base: then
    it moves to the row's template with `exposure` -1. `acquired` and
    `accepted` always stay. Then the old claim of an unstamped plan on the
    same target and template, then an insert with `exposure` -1.

    *template_id* is a callable giving the row's template Id, by name with
    an insert on a miss. It is called only when a plan needs the row's
    template, so a plan that keeps its own never makes a template.
    """
    found = _find_exposure_plan(conn, plan, lookup, used)
    if found is not None and lookup.in_base:
        r = conn.execute("SELECT enabled FROM exposureplan WHERE Id = ?", (found,)).fetchone()
        if r is not None and r["enabled"] is not None and int(r["enabled"]) == 0:
            plan.enabled = 0
    move = found is None or lookup.template_changed
    write_exposure = False
    if found is not None and lookup.gear_mode:
        # The sub rules of gear-templates.md section 8. A row's sub is what
        # TS shoots for its plan, and a plan is only changed for a move to
        # another template or for a sub ACP changed since the base.
        current = conn.execute(
            "SELECT exposure FROM exposureplan WHERE Id = ?", (found,)).fetchone()
        current_exposure = float(current["exposure"]) if current is not None and current["exposure"] is not None else -1.0
        if move:
            # -1 when the row follows its template, else its own sub.
            plan.exposure = -1.0 if lookup.row_follows else lookup.row_sub_s
        elif lookup.row_sub_changed:
            if lookup.row_follows:
                if current_exposure > 0 and abs(current_exposure - lookup.row_sub_s) > 1e-9:
                    plan.exposure = -1.0
                    write_exposure = True
            elif abs(current_exposure - lookup.row_sub_s) > 1e-9:
                plan.exposure = lookup.row_sub_s
                write_exposure = True
    if move:
        plan.exposureTemplateId = template_id()
    wanted = list(_PLAN_UPDATE_COLUMNS) + (
        ["exposureTemplateId", "exposure"] if move else ["exposure"] if write_exposure else [])
    update_cols = schema.writable("exposureplan", wanted)
    set_clause = ", ".join(f"{c} = ?" for c in update_cols)
    update_vals = [getattr(plan, c) for c in update_cols]

    if found is not None:
        conn.execute(f"UPDATE exposureplan SET {set_clause} WHERE Id = ?", (*update_vals, found))
        counts.updated += 1
        return found, False

    row = conn.execute(
        "SELECT Id, guid FROM exposureplan WHERE targetid = ? AND exposureTemplateId = ? ORDER BY Id",
        (plan.targetid, plan.exposureTemplateId),
    ).fetchone()
    if row is not None and not (row["guid"] or "").strip() and int(row["Id"]) not in used:
        conn.execute(f"UPDATE exposureplan SET {set_clause} WHERE Id = ?", (*update_vals, int(row["Id"])))
        counts.claimed += 1
        return int(row["Id"]), False

    ins_cols, ins_vals = _entity_columns(plan, "exposureplan", schema)
    cur = conn.execute(
        f"INSERT INTO exposureplan ({', '.join(ins_cols)}) VALUES ({', '.join('?' for _ in ins_cols)})",
        ins_vals,
    )
    counts.inserted += 1
    return int(cur.lastrowid), True


def _enabled_plan_ids(conn: sqlite3.Connection, target_id: int) -> list[int]:
    """The target's enabled plans in natural order, which is what
    `referenceIdx` counts over (SchedulerPlanLoader.cs:53)."""
    return [
        int(r["Id"]) for r in conn.execute(
            "SELECT Id FROM exposureplan WHERE targetid = ? AND COALESCE(enabled, 1) <> 0 ORDER BY Id",
            (target_id,),
        ).fetchall()
    ]


def _remap_override_rows(
    conn: sqlite3.Connection, target_id: int, before: list[int], after: list[int]
) -> bool:
    """Keep a target's override rows pointing at the same plans after the
    enabled set changed. A row of a plan no longer enabled is dropped, and
    the rest are renumbered from 1. Returns whether the target had any.

    Used only for rows the order writer leaves alone, which are rows set
    in TS: rows the sync generated are built again from ACP's order.
    """
    rows = conn.execute(
        'SELECT "action", referenceIdx FROM overrideexposureorderitem '
        'WHERE targetid = ? ORDER BY "order", Id',
        (target_id,),
    ).fetchall()
    if not rows:
        return False
    index_after = {pid: i for i, pid in enumerate(after)}
    kept: list[tuple[int, int]] = []
    for r in rows:
        action, ref = int(r["action"]), r["referenceIdx"]
        if action != 0:
            kept.append((action, -1 if ref is None else int(ref)))
            continue
        if ref is None or not (0 <= int(ref) < len(before)):
            continue
        new_idx = index_after.get(before[int(ref)])
        if new_idx is not None:
            kept.append((0, new_idx))
    conn.execute("DELETE FROM overrideexposureorderitem WHERE targetid = ?", (target_id,))
    conn.executemany(
        'INSERT INTO overrideexposureorderitem (targetid, "order", "action", referenceIdx) '
        "VALUES (?, ?, ?, ?)",
        [(target_id, n, action, ref) for n, (action, ref) in enumerate(kept, start=1)],
    )
    return True


#: The project columns whose write deletes every target's cadence rows.
ORDER_COLUMNS = frozenset({"filterswitchfrequency", "smartexposureorder"})


def _reset_cadence_if_order_written(
    conn: sqlite3.Connection, proj, project_id: int, payload: SyncPayload, report: SyncReport
) -> None:
    """Delete the cadence rows of every target in an updated project whose
    switch frequency or smart order this push wrote. TS rebuilds a cadence
    from the stored rows before anything else (FilterCadenceFactory.cs:19-29),
    so without this a new setting does nothing until the old cadence runs
    out. TS's own editor does the same (Project.cs:257).
    goal-panel-and-ts-order.md section 4."""
    skip = {c.lower() for c in payload.project_update_skip_cols_by_guid.get(proj.guid, frozenset())}
    if ORDER_COLUMNS <= skip:
        return
    conn.execute(
        "DELETE FROM filtercadenceitem WHERE targetid IN "
        "(SELECT Id FROM target WHERE projectid = ?)",
        (project_id,),
    )
    report.cadence_reset_projects.append(proj.name)


def _has_override_rows(conn: sqlite3.Connection, target_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM overrideexposureorderitem WHERE targetid = ? LIMIT 1", (target_id,)
    ).fetchone() is not None


#: Target Scheduler's default exposure throttle, a percentage
#: (ProfilePreference.cs:67).
DEFAULT_THROTTLE = 125.0


def _block_size(conn: sqlite3.Connection, target_id: int, plan_id: int) -> int:
    """How many override rows one plan's block holds (goal-panel-and-ts-
    order.md "What the sync writes so TS shoots in ACP's order"): N at a
    switch frequency of N; at 0, the frames TS needs to call the plan done,
    `desired` with the grader on, else ceil(throttle / 100 x desired)."""
    row = conn.execute(
        "SELECT p.filterswitchfrequency AS freq, p.enablegrader AS grader, p.profileId AS profile, "
        "e.desired AS desired FROM exposureplan e JOIN target t ON t.Id = e.targetid "
        "JOIN project p ON p.Id = t.projectid WHERE e.Id = ? AND t.Id = ?",
        (plan_id, target_id),
    ).fetchone()
    if row is None:
        return 1
    freq = int(row["freq"] or 0)
    if freq > 0:
        return freq
    desired = max(1, int(row["desired"] or 0))
    if int(row["grader"] or 0) != 0:
        return desired
    pref = conn.execute(
        "SELECT exposurethrottle FROM profilepreference WHERE profileId = ? ORDER BY Id LIMIT 1",
        (row["profile"],),
    ).fetchone()
    throttle = float(pref[0]) if pref is not None and pref[0] is not None else DEFAULT_THROTTLE
    return max(1, math.ceil(round(throttle / 100.0 * desired, 9)))


def _append_override_blocks(
    conn: sqlite3.Connection, target_id: int, after: list[int], arrived: list[int]
) -> None:
    """Append a block for each plan this push inserted or enabled again, in
    the order written. A target with override rows shoots only the plans
    those rows name (PlanningTarget.cs:80-97), so without this TS would never
    shoot it. This is section 5's "new plan rule", for a target whose rows
    the order writer left alone."""
    if not arrived:
        return
    last = conn.execute(
        'SELECT COALESCE(MAX("order"), 0) FROM overrideexposureorderitem WHERE targetid = ?',
        (target_id,),
    ).fetchone()[0]
    rows = []
    for plan_id in arrived:
        idx = after.index(plan_id)
        for _ in range(_block_size(conn, target_id, plan_id)):
            last += 1
            rows.append((target_id, last, 0, idx))
    conn.executemany(
        'INSERT INTO overrideexposureorderitem (targetid, "order", "action", referenceIdx) '
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def _override_rows(conn: sqlite3.Connection, target_id: int) -> Rows:
    """The target's override rows as (action, referenceIdx) pairs, in order."""
    return [
        (int(r["action"]), -1 if r["referenceIdx"] is None else int(r["referenceIdx"]))
        for r in conn.execute(
            'SELECT "action", referenceIdx FROM overrideexposureorderitem '
            'WHERE targetid = ? ORDER BY "order", Id',
            (target_id,),
        ).fetchall()
    ]


def _replace_override_rows(conn: sqlite3.Connection, target_id: int, rows: Rows) -> None:
    conn.execute("DELETE FROM overrideexposureorderitem WHERE targetid = ?", (target_id,))
    conn.executemany(
        'INSERT INTO overrideexposureorderitem (targetid, "order", "action", referenceIdx) '
        "VALUES (?, ?, ?, ?)",
        [(target_id, n, action, ref) for n, (action, ref) in enumerate(rows, start=1)],
    )


def _generated_rows(
    conn: sqlite3.Connection, target_id: int, acp_plan_ids: list[int], after: list[int]
) -> Rows:
    """The override rows that make TS shoot ACP's order
    (goal-panel-and-ts-order.md "What the sync writes so TS shoots in ACP's
    order"): a block per plan in ACP's order, then dithers by TS's own rule
    when the project dithers. None when that order is the natural order.

    An enabled plan no ACP row wrote (one made in TS, or a row whose goal
    is 0) keeps its place after ACP's rows, in natural order, so the order
    never stops TS shooting it."""
    order = [p for p in dict.fromkeys(acp_plan_ids) if p in after]
    order += [p for p in after if p not in order]
    if order == after:
        return []
    index = {p: i for i, p in enumerate(after)}
    items: Rows = []
    for plan_id in order:
        items.extend([(EXPOSURE, index[plan_id])] * _block_size(conn, target_id, plan_id))
    row = conn.execute(
        "SELECT p.ditherevery FROM target t JOIN project p ON p.Id = t.projectid WHERE t.Id = ?",
        (target_id,),
    ).fetchone()
    dither_every = int(row[0] or 0) if row is not None else 0
    if dither_every <= 0:
        return items
    filters = {
        int(r["Id"]): str(r["filtername"] or "")
        for r in conn.execute(
            "SELECT e.Id AS Id, x.filtername AS filtername FROM exposureplan e "
            "LEFT JOIN exposuretemplate x ON x.Id = e.exposureTemplateId WHERE e.targetid = ?",
            (target_id,),
        ).fetchall()
    }
    return inject_dithers(items, [filters.get(p, "") for p in after], dither_every)


def _smart_order_on(conn: sqlite3.Connection, target_id: int) -> bool:
    row = conn.execute(
        "SELECT p.smartexposureorder FROM target t JOIN project p ON p.Id = t.projectid WHERE t.Id = ?",
        (target_id,),
    ).fetchone()
    return row is not None and int(row[0] or 0) != 0


#: What the order writer does with one target (section 5's decision table).
ORDER_LEAVE, ORDER_WRITE, ORDER_REFRESH = "leave", "write", "refresh"


def _order_decision(
    conn: sqlite3.Connection,
    target_id: int,
    order: TargetOrder | None,
    acp_row_ids: list[str],
    ts_rows: Rows,
    ts_order: list[str],
    *,
    inserted: bool,
) -> tuple[str, bool]:
    """Which case of goal-panel-and-ts-order.md section 5 applies, and
    whether writing replaces an order set in TS.

    *ts_order* is the row ids in the order TS shot the target's plans before
    this push (natural order, as *ts_rows* is empty when it is used).

    A target this push inserted has no rows of anyone's yet, so ACP's order
    goes in as a new project's state does. Smart order on: leave. No base
    order: leave. ACP's order changed since the target's base order (the
    anchor panel's): write, ACP wins. The same order and TS's rows still the
    base's: refresh them. A panel's base rows are only ever rows the sync
    wrote (an import leaves out rows set in TS), so rows equal to them are
    the sync's own, even if stale. A base with no rows has none of the
    sync's to refresh, so when TS has none either, refresh only if ACP's order
    matched TS's natural order before this push (a plan re-enabled with a
    lower Id would otherwise shoot first). A panel whose natural order is not
    ACP's is left alone, so another panel's order that ACP holds is not
    copied to it. Otherwise leave: the rows were set in TS."""
    if _smart_order_on(conn, target_id):
        return ORDER_LEAVE, False
    if inserted:
        return ORDER_WRITE, False
    if order is None or order.base_order is None:
        return ORDER_LEAVE, False
    base_rows = order.base_rows
    if not same_order(acp_row_ids, order.base_order, order.known_rows):
        return ORDER_WRITE, ts_rows != (base_rows if base_rows is not None else [])
    if base_rows and ts_rows == base_rows:
        return ORDER_REFRESH, False
    if not base_rows and not ts_rows and same_order(acp_row_ids, ts_order):
        return ORDER_REFRESH, False
    return ORDER_LEAVE, False


def _target_name(conn: sqlite3.Connection, target_id: int) -> str:
    row = conn.execute("SELECT name FROM target WHERE Id = ?", (target_id,)).fetchone()
    return str(row["name"]) if row is not None else str(target_id)


def _fmt_s(value: float) -> str:
    return f"{value:g}"


def _note_exposure_mismatch(
    conn: sqlite3.Connection, plan_id: int, label: str, report: SyncReport
) -> None:
    row = conn.execute(
        "SELECT e.exposure AS exposure, x.defaultexposure AS dflt, x.name AS tname "
        "FROM exposureplan e JOIN exposuretemplate x ON x.Id = e.exposureTemplateId "
        "WHERE e.Id = ?",
        (plan_id,),
    ).fetchone()
    if row is None:
        return
    exposure = float(row["exposure"] or 0)
    default = float(row["dflt"] or 0)
    if exposure > 0 and exposure != default:
        report.exposure_mismatches.append(
            f"{label}: TS shoots {_fmt_s(exposure)} s, template {row['tname']} says {_fmt_s(default)} s"
        )


def _note_stray_templates(conn: sqlite3.Connection, payload: SyncPayload, report: SyncReport) -> None:
    """List the profile's templates whose filter is on none of the cameras
    this push used and that no enabled exposure plan uses. An older writer
    made some by using a goal key such as "R Stars" as a filter name; the
    sync never deletes them."""
    from .filter_canon import canonical_filter_name
    if not payload.camera_filters:
        return
    for row in conn.execute(
        "SELECT name, filtername FROM exposuretemplate WHERE profileId = ? "
        "AND Id NOT IN (SELECT exposureTemplateId FROM exposureplan "
        "WHERE COALESCE(enabled, 1) <> 0 AND exposureTemplateId IS NOT NULL) ORDER BY Id",
        (payload.profile_id,),
    ).fetchall():
        if canonical_filter_name(row["filtername"]) not in payload.camera_filters:
            report.stray_templates.append(str(row["name"]))


def _seed_rule_weights(
    conn: sqlite3.Connection, project_id: int, weights: list[RuleWeight]
) -> bool:
    """Insert ruleweight rows iff the project has none yet.

    The plugin's RepairAndUpdate() heals missing rules on next NINA start,
    so this is purely a "make the project usable immediately" courtesy. Once
    rows exist, we never modify them — that's user-tunable territory.
    """
    existing = conn.execute(
        "SELECT COUNT(*) AS n FROM ruleweight WHERE projectid = ?",
        (project_id,),
    ).fetchone()
    if int(existing["n"]) > 0:
        return False
    for w in weights:
        conn.execute(
            "INSERT INTO ruleweight (projectid, name, weight) VALUES (?, ?, ?)",
            (project_id, w.name, w.weight),
        )
    return True


def _heal_legacy_rule_weights(
    conn: sqlite3.Connection, project_id: int, weights: list[RuleWeight]
) -> bool:
    """Replace rule weights an earlier version seeded under the wrong names.

    Only acts on a project that carries one of LEGACY_RULE_NAMES, so a project
    made by hand in TS is never touched. The legacy rows go, and each real
    rule name the project lacks is added at its default. Rows already under a
    real name keep their weight. Returns True when anything changed.
    """
    from .convert import LEGACY_RULE_NAMES

    names = {
        str(r["name"])
        for r in conn.execute(
            "SELECT name FROM ruleweight WHERE projectid = ?", (project_id,)
        ).fetchall()
    }
    legacy = names & LEGACY_RULE_NAMES
    if not legacy:
        return False
    conn.executemany(
        "DELETE FROM ruleweight WHERE projectid = ? AND name = ?",
        [(project_id, n) for n in sorted(legacy)],
    )
    for w in weights:
        if w.name not in names:
            conn.execute(
                "INSERT INTO ruleweight (projectid, name, weight) VALUES (?, ?, ?)",
                (project_id, w.name, w.weight),
            )
    return True


def validate(payload: SyncPayload) -> None:
    """Refuse a payload that cannot be written back to safely.

    Two rules, both about being able to find a row again on the next push:

    - A target with no name cannot be identified by a human or by us, so it
      is refused rather than written under a blank name.
    - Two entities that hash to one identity would collapse into a single
      row while the log claims both were loaded. Refused, naming what
      collided, rather than silently merged.

    `convert.build_payload` catches the target case earlier and can name the
    ACP plans involved. This is the backstop for any other caller, and the
    message is deliberately about the rows rather than the plans.
    """
    for targets in payload.targets_by_project_guid.values():
        for tgt in targets:
            if not (tgt.name or "").strip():
                raise PayloadError(
                    "A target has no name, so Target Scheduler would have no way to "
                    "show it and the next push no way to find it. Give every target "
                    "a name and push again. Nothing was written."
                )

    _no_duplicate_guids("exposure template", [t.guid for t in payload.templates])
    _no_duplicate_guids("project", [p.guid for p in payload.projects])
    _no_duplicate_guids(
        "target",
        [t.guid for ts in payload.targets_by_project_guid.values() for t in ts],
        labels={
            t.guid: t.name
            for ts in payload.targets_by_project_guid.values()
            for t in ts
        },
    )
    _no_duplicate_guids(
        "exposure plan",
        [p.guid for ps in payload.plans_by_target_guid.values() for p in ps],
    )


def _no_duplicate_guids(
    what: str, guids: list[str], labels: dict[str, str] | None = None
) -> None:
    seen: set[str] = set()
    for guid in guids:
        if guid in seen:
            named = f" ({labels[guid]})" if labels and guid in labels else ""
            raise PayloadError(
                f"Two {what} rows in this push share the identity {guid}{named}, so "
                "one would overwrite the other and the log would report both as "
                "loaded. Nothing was written."
            )
        seen.add(guid)


#: The profile grading settings AAC's write needs to be 0
#: (goal-panel-and-ts-order.md section 6). They are set by hand in TS;
#: the sync only reads them. A column the database lacks cannot be on.
_AAC_PROFILE_COLUMNS = (
    "delayGrading", "enableGradeRMS", "enableGradeStars", "enableGradeHFR",
    "enableGradeFWHM", "enableGradeEccentricity",
)


def _aac_profile_problem(conn: sqlite3.Connection, profile_id: str) -> str | None:
    """Why the profile's grading settings rule out AAC's write, or None."""
    have = {str(r[1]).lower() for r in conn.execute("PRAGMA table_info(profilepreference)").fetchall()}
    cols = [c for c in _AAC_PROFILE_COLUMNS if c.lower() in have]
    if not cols:
        return "the profile has no grading settings to read"
    row = conn.execute(
        f"SELECT {', '.join(cols)} FROM profilepreference WHERE profileId = ? ORDER BY Id LIMIT 1",
        (profile_id,),
    ).fetchone()
    if row is None:
        return "the profile has no preference row, so Target Scheduler's defaults (delay grading 80) apply"
    bad = []
    for col in cols:
        value = row[col]
        if value is None or float(value) != 0:
            bad.append(f"{col} is {'unset' if value is None else f'{value:g}'}")
    if bad:
        return ("set these to 0 in Target Scheduler's profile settings: " + ", ".join(bad))
    return None


def _aac_cutoff_utc(night: str, zone) -> int:
    """Noon local on the day after the watermark night, as UTC unix seconds.
    A frame saved after it belongs to a night AAC has not seen."""
    from datetime import date, datetime, timedelta
    day = date.fromisoformat(night) + timedelta(days=1)
    return int(datetime(day.year, day.month, day.day, 12, tzinfo=zone).timestamp())


def _write_aac(
    conn: sqlite3.Connection,
    payload: SyncPayload,
    project_id_by_guid: dict[str, int],
    plan_id_by_guid: dict[str, int],
    report: SyncReport,
) -> None:
    """Write AAC's accepted counts and turn the project's grader on, in this
    transaction, for each eligible project (goal-panel-and-ts-order.md
    section 6, and AAC's incremental-master-builds.md section 7).

    A project is eligible when ACP's data allows it (`AacProject.blocked` is
    None), the profile's grading settings are all 0, the watermark is on or
    after the night that ended this morning, every enabled exposure plan in
    the project is one this push wrote with an AAC count, and TS saved no frame
    for the project after noon on the day after the watermark night (a rerun
    during imaging would otherwise reset `accepted` and have TS reshoot
    tonight's frames). `accepted` and `enablegrader` stay out of the generic
    UPDATE (INSERT_ONLY_COLUMNS) and are written here, together or not at
    all. `acquired` is never written.
    """
    # A project AAC has reported on says why it is skipped; one AAC has never
    # touched stays quiet.
    for g, a in payload.aac_by_project_guid.items():
        if a.blocked is not None and a.linked and g in project_id_by_guid:
            _aac_warn(report, f"{a.name}: AAC counts not written, {a.blocked}.")
    candidates = [(g, a) for g, a in payload.aac_by_project_guid.items()
                  if a.blocked is None and g in project_id_by_guid]
    if not candidates:
        return
    now = payload.aac_now
    zone = None
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(payload.aac_tz)
    except Exception:  # noqa: BLE001 - a missing zone database is a skip, not a crash
        zone = None
    if now is None or zone is None:
        _aac_warn(report, f"AAC figures not written: no time zone data for {payload.aac_tz}.")
        return
    local = now.astimezone(zone)
    from datetime import timedelta
    newest_night = (local.date() - timedelta(days=1)).isoformat()

    profile_problem = None
    profile_checked = False
    profile_warned = False
    for guid, aac in candidates:
        pid = project_id_by_guid[guid]
        wm = aac.filed_through_night
        if wm is None or wm < newest_night:
            _aac_warn(report, f"{aac.name}: AAC figures not written: they are filed through "
                              f"{wm}, older than the night that ended this morning ({newest_night}).")
            continue
        enabled = {
            int(r["Id"]) for r in conn.execute(
                "SELECT e.Id FROM exposureplan e JOIN target t ON t.Id = e.targetid "
                "WHERE t.projectid = ? AND COALESCE(e.enabled, 1) <> 0", (pid,)).fetchall()
        }
        counts = {plan_id_by_guid[g]: n for g, n in aac.accepted_by_plan_guid.items() if g in plan_id_by_guid}
        if not enabled or not enabled <= set(counts):
            if not enabled:
                why = "no exposure plan is enabled"
            else:
                names = [
                    str((conn.execute(
                        "SELECT x.name FROM exposureplan e JOIN exposuretemplate x "
                        "ON x.Id = e.exposureTemplateId WHERE e.Id = ?", (i,)).fetchone() or [i])[0])
                    for i in sorted(enabled - set(counts))
                ]
                why = f"{', '.join(names)} is enabled in Target Scheduler but is not a row in ACP"
            _aac_warn(report, f"{aac.name}: AAC counts not written, {why}.")
            continue
        if not profile_checked:
            profile_problem, profile_checked = _aac_profile_problem(conn, payload.profile_id), True
        if profile_problem:
            if not profile_warned:
                _aac_warn(report, f"AAC figures not written for profile {payload.profile_id}: "
                                  f"{profile_problem}.")
                profile_warned = True
            continue
        cutoff = _aac_cutoff_utc(wm, zone)
        if conn.execute(
            "SELECT 1 FROM acquiredimage WHERE projectId = ? AND acquireddate > ? LIMIT 1", (pid, cutoff)
        ).fetchone() is not None:
            report.notes.append(
                f"{aac.name}: AAC figures not written: Target Scheduler has saved frames since the "
                "night they cover, so its own counts stand until tomorrow's sync.")
            continue
        for plan_id in enabled:
            conn.execute("UPDATE exposureplan SET accepted = ? WHERE Id = ?", (counts[plan_id], plan_id))
        conn.execute("UPDATE project SET enablegrader = 1 WHERE Id = ?", (pid,))
        report.aac_written_projects.append(aac.name)


def _aac_warn(report: SyncReport, message: str) -> None:
    LOG.warning("%s", message)
    report.notes.append(message)


def apply(conn: sqlite3.Connection, payload: SyncPayload) -> SyncReport:
    """Upsert the whole payload. Caller owns the transaction (see db.py).

    Inserts/updates in the correct FK order, captures real DB Ids as it goes,
    and stitches them back into dependent rows before insert.
    """
    report = SyncReport()
    schema = _LiveSchema.probe(conn)

    validate(payload)
    report.notes.extend(payload.notes)

    # 0) Bring any rows still carrying the pre-length-prefix identity onto the
    # current recipe, so the writes below find them instead of inserting a
    # second tree beside them. Same transaction as the push, so a failure
    # anywhere below takes the restamping with it.
    migration = migrate_legacy_guids(conn, payload)
    report.migrated_guids = migration.rewritten
    report.notes.extend(migration.notes)
    if migration.rewritten:
        LOG.info(
            "Migrated %d Target Scheduler row(s) onto the new ACP identity recipe.",
            migration.rewritten,
        )

    # 1) ExposureTemplates, looked up by profile and name. A hit is used as
    # it is, because TS owns the templates it has; a miss is inserted with
    # the row's filter and sub (goal-panel-and-ts-order.md section 3).
    # A template TS lacks is inserted when the first plan that needs it is
    # written. An existing plan keeps its own template unless the row's
    # template changed in ACP, so it may never need one.
    template_id_by_guid: dict[str, int] = {}
    template_by_guid = {tpl.guid: tpl for tpl in payload.templates}
    # A camera whose filters carry gear templates has its rows settled first
    # by the gear template writer: found by TS id, compared three ways,
    # renamed in place, or inserted (gear-templates.md section 8). Plans then
    # point at the Id it settled on, and a template that waits holds its
    # rows back.
    gear_guids = {w.guid for w in payload.gear_works}
    skipped_templates: set[str] = set()
    if payload.gear_works:
        unknown_wheel_rows: dict[str, int] = {}
        apply_gear_templates(
            conn, payload, report, schema, _insert_template,
            template_id_by_guid, skipped_templates, unknown_wheel_rows)
        label = (payload.profile_name or "").strip() or "this profile"
        for camera, n in unknown_wheel_rows.items():
            if n:
                report.notes.append(
                    f"{camera}: the wheel of {label} is unknown, so {n} "
                    f"{'row was' if n == 1 else 'rows were'} skipped.")
    for tpl in payload.templates:
        if tpl.guid in gear_guids:
            continue
        found = _template_id_by_name(conn, tpl)
        if found is not None:
            template_id_by_guid[tpl.guid] = found

    # 2) Projects, claimed by (profileId, name). Seed ruleweight when the
    # project was actually new (inserted). On a project the sync already had,
    # only replace rule names an earlier version seeded wrongly.
    project_id_by_guid: dict[str, int] = {}
    for proj in payload.projects:
        before_inserted = report.project.inserted
        pid = _upsert_by_guid(
            conn, "project", proj, report.project, schema,
            claim_keys=("profileId", "name"),
            extra_skip_update=payload.project_update_skip_cols_by_guid.get(
                proj.guid, frozenset()
            ),
        )
        project_id_by_guid[proj.guid] = pid
        if report.project.inserted == before_inserted:
            _reset_cadence_if_order_written(conn, proj, pid, payload, report)
        weights = payload.rule_weights_by_project_guid.get(proj.guid, [])
        if report.project.inserted > before_inserted:
            report.inserted_project_guids.add(proj.guid)
            if weights and _seed_rule_weights(conn, pid, weights):
                report.ruleweight_seeded_projects += 1
        elif weights and _heal_legacy_rule_weights(conn, pid, weights):
            report.notes.append(f"project {proj.name}: replaced legacy rule weight names")

    # 3) Targets — claim by (projectid, name). Targets have no profileId
    # column; uniqueness is scoped to the parent project.
    target_id_by_guid: dict[str, int] = {}
    inserted_targets: set[str] = set()
    for proj_guid, targets in payload.targets_by_project_guid.items():
        if proj_guid not in project_id_by_guid:
            report.notes.append(
                f"target group references unknown project guid {proj_guid}; skipped"
            )
            continue
        pid = project_id_by_guid[proj_guid]
        for tgt in targets:
            tgt.projectid = pid
            before_inserted = report.target.inserted
            target_id_by_guid[tgt.guid] = _upsert_by_guid(
                conn, "target", tgt, report.target, schema,
                claim_keys=("projectid", "name"),
            )
            if report.target.inserted > before_inserted:
                inserted_targets.add(tgt.guid)

    # 4) ExposurePlans, found by the chain in section 3's "Exposure plan
    # identity", then disabled for rows removed in ACP. Then each target's
    # override rows, by section 5: ACP's order written, or the rows left as
    # they are. Left rows still follow the plans: a target whose set of
    # enabled plans changed other than by an insert has them remapped, a
    # plan that arrived gets its block on the end, and its cadence rows go.
    used_plan_ids: set[int] = set()
    plan_id_by_guid: dict[str, int] = {}
    passes: list[tuple] = []
    for tgt_guid, plans in payload.plans_by_target_guid.items():
        if tgt_guid not in target_id_by_guid:
            report.notes.append(
                f"plan group references unknown target guid {tgt_guid}; skipped"
            )
            continue
        tid = target_id_by_guid[tgt_guid]
        before = _enabled_plan_ids(conn, tid)
        ts_rows = _override_rows(conn, tid)
        had_override = bool(ts_rows)
        inserted: set[int] = set()
        written: list[int] = []
        written_rows: list[str] = []
        for plan in plans:
            tpl_guid = payload.template_guid_by_plan_guid.get(plan.guid)
            if tpl_guid is None or tpl_guid not in template_by_guid:
                report.notes.append(
                    f"plan {plan.guid} for target {tgt_guid} has no template; skipped"
                )
                continue
            # A template that waits (filter not on the wheel, wheel unknown,
            # name left for adoption) holds its rows back; their plans stay
            # as they are in TS.
            if tpl_guid in skipped_templates:
                continue
            if tpl_guid in gear_guids and tpl_guid not in template_id_by_guid:
                continue
            lookup = payload.plan_lookup_by_guid.get(plan.guid) or PlanLookup(label=plan.guid)

            def row_template(tpl_guid=tpl_guid) -> int:
                if tpl_guid not in template_id_by_guid:
                    template_id_by_guid[tpl_guid] = _insert_template(
                        conn, template_by_guid[tpl_guid], report.exposuretemplate, schema)
                return template_id_by_guid[tpl_guid]

            plan.targetid = tid
            plan_id, was_insert = _upsert_exposure_plan(
                conn, plan, lookup, row_template, report.exposureplan, schema, used_plan_ids)
            used_plan_ids.add(plan_id)
            plan_id_by_guid[plan.guid] = plan_id
            written.append(plan_id)
            written_rows.append(lookup.row_id or plan.guid)
            if was_insert:
                inserted.add(plan_id)
            if plan.enabled == 0:
                report.kept_disabled.append(lookup.label)
                report.notes.append(f"{lookup.label}: turned off in TS, so the sync left it off.")
            _note_exposure_mismatch(conn, plan_id, lookup.label, report)
        for removed in payload.removed_by_target_guid.get(tgt_guid, []):
            if removed.plan_id in used_plan_ids:
                continue
            row = conn.execute(
                "SELECT targetid, guid, enabled FROM exposureplan WHERE Id = ?",
                (removed.plan_id,),
            ).fetchone()
            if row is None or row["targetid"] != tid or row["guid"] not in removed.guids:
                continue
            if int(row["enabled"] if row["enabled"] is not None else 1) != 0:
                conn.execute("UPDATE exposureplan SET enabled = 0 WHERE Id = ?", (removed.plan_id,))
                report.disabled_plans += 1
        passes.append((tgt_guid, tid, before, ts_rows, had_override, inserted, written, written_rows))

    # 4b) AAC's accepted counts and the grader, for the projects that qualify
    # (section 6). Before the order pass, because a block's size follows the
    # project's grader (`_block_size`).
    _write_aac(conn, payload, project_id_by_guid, plan_id_by_guid, report)

    for tgt_guid, tid, before, ts_rows, had_override, inserted, written, written_rows in passes:
        after = _enabled_plan_ids(conn, tid)
        # Plans this push inserted or enabled again, in the order written.
        arrived = [i for i in written if i in after and i not in before]
        plans_moved = [i for i in after if i not in inserted] != before or (had_override and arrived)
        row_of_plan = dict(zip(written, written_rows))
        ts_order = [row_of_plan[p] for p in effective_plan_order(before, ts_rows)[0] if p in row_of_plan]
        decision, set_in_ts = _order_decision(
            conn, tid, payload.order_by_target_guid.get(tgt_guid), written_rows, ts_rows, ts_order,
            inserted=tgt_guid in inserted_targets)
        if decision == ORDER_LEAVE:
            if plans_moved:
                if had_override:
                    _remap_override_rows(conn, tid, before, after)
                    _append_override_blocks(conn, tid, after, arrived)
                    report.override_rebuilt.append(_target_name(conn, tid))
                conn.execute("DELETE FROM filtercadenceitem WHERE targetid = ?", (tid,))
        else:
            generated = _generated_rows(conn, tid, written, after)
            if generated != ts_rows:
                _replace_override_rows(conn, tid, generated)
                conn.execute("DELETE FROM filtercadenceitem WHERE targetid = ?", (tid,))
                name = _target_name(conn, tid)
                report.order_written.append(name)
                if set_in_ts:
                    report.notes.append(
                        f"{name}: ACP's filter order replaced an order set in Target Scheduler.")
            elif plans_moved:
                conn.execute("DELETE FROM filtercadenceitem WHERE targetid = ?", (tid,))
            report.order_synced_target_ids.add(tid)
        # Restart the walk every night (section 5): TS reuses a stored
        # cadence before anything else (FilterCadenceFactory.cs:19-29), so
        # without this a shortfall found in the morning waits for the whole
        # circle. TS rebuilds the cadence from row 1 of the override rows.
        if _has_override_rows(conn, tid):
            conn.execute("DELETE FROM filtercadenceitem WHERE targetid = ?", (tid,))

    if not payload.gear_works:
        _note_stray_templates(conn, payload, report)
    return report


def preview(conn: sqlite3.Connection, payload: SyncPayload) -> SyncReport:
    """Same shape as apply() but read-only — counts what *would* happen.

    Doesn't simulate ID resolution for plans (that needs writes to capture
    new ones), so plan counts are best-effort: it counts hits/misses against
    what's already in the DB, treating "no match by guid" as an insert.
    """
    report = SyncReport()
    gear_guids = {w.guid for w in payload.gear_works}
    for table, entities in (
        ("exposuretemplate", [t for t in payload.templates if t.guid not in gear_guids]),
        ("project", payload.projects),
    ):
        for ent in entities:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE guid = ?", (ent.guid,)
            ).fetchone()
            counts = getattr(report, table)
            if row is None:
                counts.inserted += 1
            else:
                counts.updated += 1

    for targets in payload.targets_by_project_guid.values():
        for tgt in targets:
            row = conn.execute(
                "SELECT 1 FROM target WHERE guid = ?", (tgt.guid,)
            ).fetchone()
            if row:
                report.target.updated += 1
            else:
                report.target.inserted += 1

    for plans in payload.plans_by_target_guid.values():
        for plan in plans:
            row = conn.execute(
                "SELECT 1 FROM exposureplan WHERE guid = ?", (plan.guid,)
            ).fetchone()
            if row:
                report.exposureplan.updated += 1
            else:
                report.exposureplan.inserted += 1

    return report
