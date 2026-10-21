"""In-place merge helpers used during /import/resolve.

Pure functions over plan dicts — kept here (not in api.py) so tests can
exercise them without pulling in Flask.
"""
from __future__ import annotations


def merge_remote_into_local(plan: dict, remote: dict) -> None:
    """Overwrite the diffable fields on *plan* with *remote*'s.

    Preserves ACP-only fields (id, guid, telescope_id, camera_id, ts_refs,
    ts_base_snapshot — refs/snapshot get rewritten by the caller after
    this).
    """
    if remote.get("project_name"):
        plan["project_name"] = remote["project_name"]
    plan["priority"] = remote.get("priority", plan.get("priority"))
    plan["min_altitude_deg"] = remote.get("min_altitude_deg", plan.get("min_altitude_deg"))
    plan["meridian_window_min"] = remote.get("meridian_window_min", plan.get("meridian_window_min"))
    plan["minimum_time_min"] = remote.get("minimum_time_min", plan.get("minimum_time_min"))
    for key in ("filter_switch_frequency", "smart_exposure_order"):
        if key in remote:
            plan[key] = remote[key]
    rt = remote.get("target") or {}
    pt = plan.setdefault("target", {})
    for k in ("name", "center_ra_deg", "center_dec_deg", "rotation_deg", "mosaic"):
        if k in rt:
            pt[k] = rt[k]
    plan["filter_rows"] = take_rows(plan, remote)
    # "state" follows ACP's own absent-means-active convention: a remote
    # with no opinion (no project snapshot to build it from) drops the key
    # from ACP too, rather than writing a literal null that would never
    # again equal "active" on the next diff.
    if "state" in remote:
        plan["state"] = remote["state"]
    else:
        plan.pop("state", None)


def same_row_identity(a: dict, b: dict) -> bool:
    """Whether two rows name the same filter and template, which is what an
    `aac` block was reported against."""
    from .rows import same_filter
    return same_filter(a.get("filter"), b.get("filter")) and a.get("template") == b.get("template")


def take_rows(plan: dict, remote: dict) -> list[dict]:
    """The remote's rows, keeping each local row's `aac` block, which only
    AAC writes and TS knows nothing of. A block is kept only while the row
    still has the filter and template AAC reported it for. The remote's
    `desired` is read back as the row's goal where it is only AAC's floor
    (goal-panel-and-ts-order.md section 6)."""
    import copy
    from .diff import rows_by_id
    from .rows import unfloored
    local = rows_by_id(plan)
    remote = unfloored(plan, remote) or remote
    out = []
    for row in copy.deepcopy(remote.get("filter_rows") or []):
        if not isinstance(row, dict):
            continue
        mine = local.get(str(row.get("id"))) or {}
        if isinstance(mine.get("aac"), dict) and same_row_identity(mine, row):
            row["aac"] = copy.deepcopy(mine["aac"])
        out.append(row)
    return out


def _pull_row_field(plan: dict, remote: dict, row_id: str, field: str | None) -> None:
    """Apply one filter_rows path. A row the plan lacks comes over whole,
    at the end of the list; a field the remote row lacks is dropped."""
    import copy
    from .diff import rows_by_id
    remote_row = rows_by_id(remote).get(row_id)
    rows = plan.setdefault("filter_rows", [])
    mine = next((r for r in rows if isinstance(r, dict) and str(r.get("id")) == row_id), None)
    if remote_row is None:
        if mine is not None and field is not None:
            mine.pop(field, None)
        return
    if mine is None:
        rows.append(copy.deepcopy(remote_row))
        return
    if field is None:
        return
    before = (mine.get("filter"), mine.get("template"))
    if field in remote_row:
        mine[field] = copy.deepcopy(remote_row[field])
        if field in ("target_hours", "target_frames"):
            mine["unit"] = remote_row.get("unit", mine.get("unit"))
    else:
        mine.pop(field, None)
    # AAC's figures were reported for the filter and template the row had.
    if field in ("filter", "template") and (mine.get("filter"), mine.get("template")) != before:
        mine.pop("aac", None)


def pull_order(plan: dict, remote: dict) -> None:
    """Put the plan's rows that the remote also has into the remote's order,
    in the places those rows already hold. Rows the remote lacks stay where
    they are (goal-panel-and-ts-order.md section 5)."""
    remote_ids = [str(r.get("id")) for r in (remote.get("filter_rows") or []) if isinstance(r, dict)]
    rows = plan.get("filter_rows")
    if not isinstance(rows, list):
        return
    slots = [i for i, r in enumerate(rows) if isinstance(r, dict) and str(r.get("id")) in remote_ids]
    ordered = sorted((rows[i] for i in slots), key=lambda r: remote_ids.index(str(r.get("id"))))
    for i, r in zip(slots, ordered):
        rows[i] = r


def apply_auto_pull(plan: dict, remote: dict, fields: list[str]) -> None:
    """Apply only the TS-only-changed fields onto the local plan.

    *fields* is a list of dotted paths (e.g. ``"filter_rows.<row id>.target_hours"``);
    the value at each path is read from *remote* and written into *plan*,
    creating intermediate dicts as needed.
    """
    from .diff import ORDER, ROWS, align_units, split_path
    from .rows import unfloored
    # The diff compared TS's rows with AAC's floor read back as the goal and
    # in each local row's unit; pull them so.
    remote = unfloored(plan, remote) or remote
    remote = align_units(plan, remote) or remote
    # The order last, so a row pulled whole takes its place in it too.
    for path in sorted(fields, key=lambda f: f == ORDER):
        if path == ORDER:
            pull_order(plan, remote)
            continue
        parts = split_path(path)
        if parts[0] == ROWS:
            _pull_row_field(plan, remote, parts[1], parts[2] if len(parts) > 2 else None)
            continue
        rv = remote
        present = True
        for p in parts:
            if not isinstance(rv, dict) or p not in rv:
                present = False
                break
            rv = rv[p]
        cur = plan
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        if present:
            cur[parts[-1]] = rv
        else:
            # The remote side has no opinion on this field at all (as
            # opposed to an explicit null): drop it from ACP too, rather
            # than writing a literal null a field like "state" would never
            # again match "absent" against.
            cur.pop(parts[-1], None)
