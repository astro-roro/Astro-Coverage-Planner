"""The gear template writer: for a camera whose filters carry gear templates,
settle each template's Target Scheduler row before any exposure plan is
written (docs/specs/gear-templates.md section 8).

Each field is decided by three values: ACP's A (from the gear template; for
the filtername, the wheel slot), the base B (what TS held at the last push,
pull or adoption) and TS's value now C. A change made only in ACP is written,
a change made only in TS is left for the pull, and a change made in both is
held and reported, never overwritten. A row is found by TS id first, so a
rename updates the same row and never inserts a second one.

The plugin's `TsUpsert.GearTemplates.cs` is the twin of this module, and
`tests/row_scenarios.py` holds the scenarios both are checked with.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Callable

from . import rows as fr
from .schema import GearWork, SyncPayload

#: The fields a gear template syncs, in the order they are decided and written.
GEAR_FIELDS = ("name", "filtername", "defaultexposure", "gain", "offset", "bin")


@dataclass
class _Row:
    """A template row as it stands in TS during the gear pass."""
    id: int
    name: str
    filtername: str
    guid: str
    defaultexposure: float
    gain: int
    offset: int
    bin: int


def _read_rows(conn: sqlite3.Connection, profile_id: str) -> list[_Row]:
    out = []
    for r in conn.execute(
        "SELECT * FROM exposuretemplate WHERE profileId = ? ORDER BY Id", (profile_id,)
    ).fetchall():
        out.append(_Row(
            id=int(r["Id"]),
            name=r["name"] if r["name"] is not None else "",
            filtername=r["filtername"] if r["filtername"] is not None else "",
            guid=r["guid"] if r["guid"] is not None else "",
            defaultexposure=float(r["defaultexposure"]) if r["defaultexposure"] is not None else 60.0,
            gain=int(r["gain"]) if r["gain"] is not None else -1,
            offset=int(r["offset"]) if r["offset"] is not None else -1,
            bin=int(r["bin"]) if r["bin"] is not None else 1,
        ))
    return out


def _value_of_gear(work: GearWork, field: str):
    t = work.template
    if field == "name":
        return t.get("name")
    if field == "filtername":
        return work.slot
    if field == "defaultexposure":
        return fr._num(t.get("sub_s"))
    return int(t.get(field, -1 if field != "bin" else 1))


def _same(x, y) -> bool:
    if x is None or y is None:
        return x is None and y is None
    if isinstance(x, str) or isinstance(y, str):
        return str(x) == str(y)
    return math.isclose(float(x), float(y), rel_tol=0.0, abs_tol=1e-9)


def _show(value) -> str:
    """The value as the sync notes print it: "300" for 300.0, "2.5" for 2.5."""
    if isinstance(value, float):
        text = f"{value:.5f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"
    return str(value)


def _label(field: str) -> str:
    return "sub" if field == "defaultexposure" else field


def _in_group(row: _Row, filt: str, sub: float) -> bool:
    return fr.same_filter(row.filtername, filt) and (
        (row.defaultexposure < fr.FRAME_UNIT_BELOW_S) == (sub < fr.FRAME_UNIT_BELOW_S))


def _clash_note(work: GearWork, holder: GearWork, profile_label: str) -> str:
    return (
        f"Template '{work.template.get('name')}' of {work.camera_name} was not created in profile "
        f"{profile_label}, because {holder.camera_name}'s '{holder.template.get('name')}' already uses that name.")


def apply_gear_templates(
    conn: sqlite3.Connection,
    payload: SyncPayload,
    report,
    schema,
    insert_template: Callable,
    template_id_by_guid: dict[str, int],
    skipped: set[str],
    unknown_wheel_rows: dict[str, int],
) -> None:
    """Settle every gear template's TS row. *template_id_by_guid* gets the TS
    id for each template whose plans may be written, and *skipped* the gear
    guids whose rows must wait (not on the wheel, wheel unknown with no row,
    a name that waits for adoption). *unknown_wheel_rows* counts, per camera,
    the rows held back because the wheel is unknown."""
    profile = payload.profile_id
    label = (payload.profile_name or "").strip() or "this profile"
    rows = _read_rows(conn, profile)
    template_by_guid = {t.guid: t for t in payload.templates}
    claimed_by: dict[int, GearWork] = {}
    found_row: dict[GearWork, _Row] = {}
    via_link: set[GearWork] = set()
    pending: dict[GearWork, str] = {}

    # Pass 1: the rows ACP already links to, by TS id and then guid.
    for work in payload.gear_works:
        link = work.link_for(profile)
        if link is None:
            continue
        row = None
        link_id = link.get("id")
        if isinstance(link_id, int) and not isinstance(link_id, bool):
            row = next((r for r in rows if r.id == link_id), None)
        if row is None and link.get("guid"):
            row = next((r for r in rows if r.guid == link["guid"]), None)
        if row is None or row.id in claimed_by:
            continue
        claimed_by[row.id] = work
        found_row[work] = row
        via_link.add(work)

    # Pass 2: by name or an earlier name, for a template not linked.
    inserted_names: dict[str, GearWork] = {}
    for work in payload.gear_works:
        if work.off_wheel or work in found_row:
            continue
        t = work.template
        names = [t.get("name")] + [a for a in (t.get("aliases") or []) if a]
        candidates = [r for r in rows if r.name in names]
        if len(candidates) > 1:
            ids = ", ".join(str(r.id) for r in candidates)
            line = f"Template '{t.get('name')}' matches {len(candidates)} templates in {label} (ids {ids}), left for adoption."
            report.notes.append(line)
            pending[work] = line
            continue
        if len(candidates) == 1:
            row = candidates[0]
            by_name = row.name == t.get("name")
            other = claimed_by.get(row.id)
            if other is not None:
                line = _clash_note(work, other, label)
                report.notes.append(line)
                pending[work] = line
                continue
            if _in_group(row, work.filter, _sub_of(t)):
                claimed_by[row.id] = work
                found_row[work] = row
                continue
            if by_name:
                line = (f"Template '{t.get('name')}' exists in {label} (id {row.id}) for another "
                        "filter or sub class, so it was not touched or created.")
                report.notes.append(line)
                pending[work] = line
                continue
            # An alias on a row of another group claims nothing.
        # Nothing to find: an insert, unless another camera's template of the
        # same name is already taking that name in this push.
        holder = inserted_names.get(t.get("name"))
        if holder is not None and holder.camera_id != work.camera_id:
            line = _clash_note(work, holder, label)
            report.notes.append(line)
            pending[work] = line
            continue
        inserted_names[t.get("name")] = work

    # Pass 3: decide and write, in gear order.
    unknown_order: list[str] = []
    for work in payload.gear_works:
        t = work.template
        link = work.link_for(profile)
        result: dict = {"template_id": t.get("id")}
        report.templates.append(result)

        def hold(note: str) -> None:
            result.update(
                action="skipped", note=note, ts_id=(link or {}).get("id"),
                guid=(link or {}).get("guid"), base=(link or {}).get("base"), fields_written=[])
            skipped.add(work.guid)

        if work.off_wheel:
            hold(f"{work.filter} is not on the wheel of {label} yet")
            continue
        if work in pending:
            hold(pending[work])
            continue

        row = found_row.get(work)
        if row is None:
            if not work.slot:
                # The column is not null and a guess would be a wrong
                # filtername, so nothing is inserted.
                hold(f"the wheel of {label} is unknown")
                key = work.camera_name or "The camera"
                if key not in unknown_wheel_rows:
                    unknown_order.append(key)
                    unknown_wheel_rows[key] = 0
                unknown_wheel_rows[key] += len(work.row_labels)
                continue
            tpl = template_by_guid[work.guid]
            new_id = insert_template(conn, tpl, report.exposuretemplate, schema)
            inserted = _Row(
                id=new_id, name=tpl.name, filtername=tpl.filtername, guid=tpl.guid,
                defaultexposure=tpl.defaultexposure, gain=tpl.gain, offset=tpl.offset, bin=tpl.bin)
            rows.append(inserted)
            claimed_by[new_id] = work
            template_id_by_guid[work.guid] = new_id
            result.update(
                action="inserted", ts_id=new_id, guid=inserted.guid,
                base=_base_of(inserted), fields_written=list(GEAR_FIELDS))
            continue

        _decide_fields(
            conn, schema, report, work, row, link, work in via_link, rows, label, result)
        template_id_by_guid[work.guid] = row.id

    # Templates of the profile no gear template stands for.
    for row in rows:
        if row.id in claimed_by:
            continue
        report.stray_templates.append(row.name)
        report.stray_template_ids.append(row.id)


def _sub_of(template: dict) -> float:
    return fr._num(template.get("sub_s")) or 0.0


def _base_of(row: _Row) -> dict:
    return {
        "name": row.name, "filtername": row.filtername, "defaultexposure": row.defaultexposure,
        "gain": row.gain, "offset": row.offset, "bin": row.bin,
    }


def _decide_fields(
    conn: sqlite3.Connection, schema, report, work: GearWork, row: _Row, link: dict | None,
    linked: bool, all_rows: list[_Row], label: str, result: dict,
) -> None:
    """The three way comparison for an existing row."""
    t = work.template
    new_base: dict = {}
    written: list[str] = []
    conflicts: list[dict] = []
    writes: dict = {}
    existing_base = (link or {}).get("base") if linked else None
    if not isinstance(existing_base, dict):
        existing_base = None
    adopted = not linked or existing_base is None

    for field in GEAR_FIELDS:
        a = _value_of_gear(work, field)
        c = getattr(row, field)
        b = existing_base.get(field) if existing_base is not None else None
        if b is None:
            b = c

        if a is None:
            # A filtername with no known slot: left alone, not compared.
            new_base[field] = b
            continue
        if adopted and field != "name":
            # Found by name and never linked: take TS's values, write nothing.
            new_base[field] = c
            continue
        if _same(a, b):
            new_base[field] = b
            continue
        if _same(c, b):
            if field == "name":
                clash = next((r for r in all_rows if r.id != row.id and r.name == a), None)
                if clash is not None:
                    report.notes.append(
                        f"{t.get('name')}: the name was skipped, because {label} already has a "
                        f"template named '{t.get('name')}' (id {clash.id}).")
                    new_base[field] = b
                    continue
            writes[field] = a
            new_base[field] = a
            continue
        if _same(c, a):
            new_base[field] = c
            continue
        conflicts.append({"field": field, "acp": a, "ts": c})
        report.notes.append(
            f"{t.get('name')}: {_label(field)} is {_show(a)} in ACP and {_show(c)} in TS. "
            "Left alone until you choose in the gear editor.")
        new_base[field] = b

    if writes:
        cols = schema.writable("exposuretemplate", list(writes))
        if cols:
            set_clause = ", ".join(f'"{col}" = ?' for col in cols)
            conn.execute(
                f"UPDATE exposuretemplate SET {set_clause} WHERE Id = ?",
                (*[writes[col] for col in cols], row.id))
            for col in cols:
                written.append(col)
                value = writes[col]
                setattr(row, col, float(value) if col == "defaultexposure" else value)
            report.exposuretemplate.updated += 1

    if adopted and not written and row.name == t.get("name"):
        report.notes.append(f"{t.get('name')} already exists in profile {label}, so ACP took its values.")

    result.update(ts_id=row.id, guid=row.guid, base=new_base, fields_written=written)
    if conflicts:
        result["conflicts"] = conflicts
    if written:
        result["action"] = "updated"
    elif conflicts:
        result["action"] = "conflict"
    elif adopted:
        result["action"] = "adopted"
    else:
        result["action"] = "unchanged"
