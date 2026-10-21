"""Merge a Target Scheduler database into ACP's plans, one rig at a time.

An upload is a merge. It adds plans that are new to ACP and updates plans it
matches. It never deletes anything, and it never touches a plan that belongs
to another rig. The live routes `/import/diff` and `/import/resolve` use the
same engine with the database on the ACP host.

A rig is a NINA profile, and the merge is always for one profile P. It only
reads or changes plans linked to P and plans linked to no profile (see
links.py). TS plans are matched to those in this order:

1. By link: the plan's link for P names the TS project and target rows the
   TS plan came from. A link made on another machine is not trusted, because
   its row ids belong to another database file.
2. By ACP's guid: ACP's deterministic target guid for the plan is on one of
   the TS plan's target rows. Every row ACP or the plugin pushed carries one.
3. By name, as a proposal only. It is never linked without a tick.

A TS plan left unmatched is new, unless its footprint on the sky covers an
ACP plan's (any rig's, see duplicates.py). Then it is a possible duplicate:
skipped, added anyway, or linked to that plan, as the review page chooses. An ACP plan left unmatched is left alone, so
a row deleted in TS never deletes anything in ACP.

`diff_from` builds the view without writing anything; `resolve_from` applies
it. Both take the database path, so the upload routes pass the stored file
and the live routes pass the resolved TS path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .db import assert_supported_version, get_user_version, open_connection
from .diff import ORDER, PlanDiff, base_snapshot_to_plan_shape, diff_plan
from . import duplicates as dup
from .from_ts import ImportNote, _split_panel_suffix, import_to_acp_plans
from .links import in_scope, link_for, links_of, same_machine, write_link
from .merge import apply_auto_pull, merge_remote_into_local
from .order import effective_plan_order
from .reader import TSSnapshot, read_all
from . import rows as fr
from .rows import unfloored
from .schema import legacy_target_guid, target_guid
from .state import build_base_snapshot_from_ids, build_ts_refs_from_ids

# Every read-modify-write of plans.json by this extension holds this lock.
# ACP runs as one process under waitress, so a thread lock is enough.
PLANS_LOCK = threading.RLock()

DECISION_ALIASES = {
    "take_ts": "take_ts",
    "keep_acp": "keep_acp",
    "take_acp": "keep_acp",
    "leave": "leave",
    "skip": "leave",
    "link": "link",
}


class MergeError(Exception):
    """A refusal with the HTTP status the route should answer with."""

    def __init__(self, status: int, message: str, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict:
        return {"error": self.message, **self.extra}


# ---------------------------------------------------------------------------
# Reading the TS side
# ---------------------------------------------------------------------------


def open_readonly(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    """Open a TS database read-only.

    ``immutable`` is for an uploaded copy, which nothing else ever writes:
    SQLite then takes no locks and reads no WAL. Such a file came from
    outside, so ``trusted_schema`` is off, which stops a crafted view or
    trigger from calling functions with side effects. Python's sqlite3 never
    loads extensions unless asked, and this code never asks.
    """
    if not immutable:
        return open_connection(path, read_only=True)
    uri = Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA trusted_schema = OFF")
    return conn


def read_ts(path: Path, profile_id: str, *, immutable: bool = False) -> tuple[TSSnapshot, int]:
    conn = open_readonly(path, immutable=immutable)
    try:
        version = assert_supported_version(conn)
        return read_all(conn, profile_id), version
    finally:
        conn.close()


def load_acp_state() -> tuple[list[dict], dict]:
    """Plans and gear from ACP. Imported lazily so the package loads outside
    the ACP host, where tests stand in their own `app` module."""
    from app import load_gear, load_plans  # type: ignore[import-not-found]
    plans_data = load_plans()
    plans = plans_data.get("plans", []) if isinstance(plans_data, dict) else []
    return plans, load_gear()


# ---------------------------------------------------------------------------
# The view: what matched what
# ---------------------------------------------------------------------------


@dataclass
class Remote:
    """One ACP-shaped plan the importer built from the TS rows."""
    plan: dict
    id_map: dict
    key: str
    project_id: int
    target_ids: set[int]
    target_guids: set[str]
    base_name: str
    claimed: bool = False

    @property
    def is_single(self) -> bool:
        mos = (self.plan.get("target") or {}).get("mosaic") or {}
        return int(mos.get("rows") or 1) == 1 and int(mos.get("cols") or 1) == 1

    def summary(self) -> dict:
        tg = self.plan.get("target") or {}
        return {
            "key": self.key,
            "ts_project_id": self.project_id,
            "project_name": self.plan.get("project_name"),
            "target_name": tg.get("name"),
            "mosaic": tg.get("mosaic", {"rows": 1, "cols": 1}),
            "filters": list(dict.fromkeys(
                r.get("filter") for r in (self.plan.get("filter_rows") or []) if isinstance(r, dict))),
            "state": self.plan.get("state") or "active",
        }


@dataclass
class Match:
    plan: dict
    remote: Remote
    matched_by: str  # "link" | "guid"
    siblings: list[Remote] = field(default_factory=list)
    diff: PlanDiff | None = None
    split_mosaic: bool = False


@dataclass
class Duplicate:
    """A new TS plan whose footprint covers an existing ACP plan's."""
    remote: Remote
    plan: dict                  # the ACP plan it most overlaps
    comparison: dup.Comparison
    ts_footprint: dup.Footprint
    acp_footprint: dup.Footprint
    ts_gear: dict               # the telescope and camera the TS footprint used
    linkable: bool              # False when the ACP plan is matched in this view

    @property
    def default(self) -> str:
        return "skip" if self.comparison.strong else "add"


@dataclass
class MergeView:
    profile_id: str
    machine: str | None
    strict: bool
    snap: TSSnapshot
    user_version: int
    plans: list[dict]           # a deep copy of every ACP plan, in order
    in_scope: list[dict]        # the members of `plans` this merge may touch
    remotes: list[Remote]
    notes: list[ImportNote]
    matches: list[Match] = field(default_factory=list)
    proposals: list[tuple[dict, Remote]] = field(default_factory=list)
    new: list[Remote] = field(default_factory=list)
    not_in_upload: list[dict] = field(default_factory=list)
    unlinked_unmatched: list[dict] = field(default_factory=list)
    duplicates: list["Duplicate"] = field(default_factory=list)
    link_notes: list[str] = field(default_factory=list)
    preview_hash: str = ""

    def remote_by_key(self, key: str) -> Remote | None:
        for r in self.remotes:
            if r.key == key:
                return r
        return None


def project_name_of(plan: dict) -> str:
    explicit = (plan.get("project_name") or "").strip()
    if explicit:
        return explicit
    tg = plan.get("target") or {}
    return (tg.get("name") or "").strip() or plan.get("id") or "Untitled"


def _base_target_name(plan: dict) -> str:
    return _split_panel_suffix(((plan.get("target") or {}).get("name") or "").strip())[0]


def _panels(plan: dict) -> tuple[int, int, str]:
    tg = plan.get("target") or {}
    mos = tg.get("mosaic") or {}
    rows = max(1, int(mos.get("rows") or 1))
    cols = max(1, int(mos.get("cols") or 1))
    return rows, cols, tg.get("name") or plan.get("id") or "Untitled"


def acp_target_guids(plan: dict, profile_id: str) -> set[str]:
    """Every target guid ACP or the plugin could have stamped for *plan*.

    The bare name covers a single target and a mosaic pushed without gear
    (one row); the panel names cover a mosaic. Both recipes, because a row
    stamped before the length prefix keeps its old guid until the next push
    migrates it.
    """
    pname = project_name_of(plan)
    rows, cols, base = _panels(plan)
    names = {base}
    for r in range(rows):
        for c in range(cols):
            names.add(f"{base} Panel {r * cols + c + 1} (R{r + 1}C{c + 1})")
    out: set[str] = set()
    for n in names:
        out.add(target_guid(profile_id, pname, n))
        out.add(legacy_target_guid(profile_id, pname, n))
    return out


def _build_remotes(snap: TSSnapshot, gear: dict) -> tuple[list[Remote], list[ImportNote]]:
    plans, notes, id_maps = import_to_acp_plans(snap, gear)
    remotes: list[Remote] = []
    for plan, idm in zip(plans, id_maps):
        tids = {int(t) for t in (idm.get("target_ids_by_panel") or {}).values()}
        anchor = (idm.get("target_ids_by_panel") or {}).get("1,1")
        if anchor is None and tids:
            anchor = min(tids)
        guids = {snap.targets_by_id[t].guid for t in tids if t in snap.targets_by_id and snap.targets_by_id[t].guid}
        remotes.append(Remote(
            plan=plan,
            id_map=idm,
            key=f"{idm.get('project_id')}:{anchor}",
            project_id=int(idm.get("project_id") or 0),
            target_ids=tids,
            target_guids=guids,
            base_name=_base_target_name(plan),
        ))
    return remotes, notes


def _hashable(plan: dict) -> dict:
    """A plan as the stale-preview check sees it. Actual hours move whenever
    the plugin sends progress back, and the merge never reads them, so they
    are left out; anything else that changes makes the preview stale."""
    p = copy.deepcopy(plan)
    for row in (p.get("filter_rows") or []):
        if isinstance(row, dict):
            row.pop("actual_hours", None)
    return p


def scope_hash(plans: list[dict], profile_id: str) -> str:
    scoped = [_hashable(p) for p in plans if isinstance(p, dict) and in_scope(p, profile_id)]
    scoped.sort(key=lambda p: str(p.get("id") or ""))
    blob = json.dumps(scoped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _group_constraints(in_scope_plans: list[dict]) -> dict[str, tuple[float, int, str, str, int]]:
    """The project-level values the push would write for each project name.

    Several ACP plans can share one TS project, and the push writes the
    group-resolved value onto it (strictest for the numeric constraints,
    the group-consistency rule from section 1 of ts-project-settings.md
    for state, priority and minimum time). Comparing one plan's own value
    with the project's would report a difference that nobody made.
    """
    from .convert import _project_constraints
    groups: dict[str, list[dict]] = {}
    for p in in_scope_plans:
        groups.setdefault(project_name_of(p), []).append(p)
    return {name: _project_constraints(g) for name, g in groups.items()}


def _group_order(in_scope_plans: list[dict]) -> dict[str, dict[str, int | None]]:
    """The switch frequency and smart order the push would write for each
    project name, by TS column (goal-panel-and-ts-order.md section 4)."""
    from .convert import _group_order_settings
    groups: dict[str, list[dict]] = {}
    for p in in_scope_plans:
        groups.setdefault(project_name_of(p), []).append(p)
    return {name: _group_order_settings(name, g)[0] for name, g in groups.items()}


def _local_view(
    plan: dict,
    constraints: dict[str, tuple[float, int, str, str, int]],
    order_settings: dict[str, dict[str, int | None]] | None = None,
) -> dict:
    view = copy.deepcopy(plan)
    name = project_name_of(plan)
    view["project_name"] = name
    if name in constraints:
        min_alt, meridian, pri, state_name, min_time = constraints[name]
        view["min_altitude_deg"] = min_alt
        view["meridian_window_min"] = meridian
        view["priority"] = pri
        view["state"] = state_name
        view["minimum_time_min"] = min_time
    # The order settings, as the push resolves them for the project: the
    # first plan with a value. None stays absent, which means unknown.
    from .convert import ORDER_SETTINGS
    order = order_settings.get(name) if order_settings else None
    for field, col in ORDER_SETTINGS:
        val = (order or {}).get(col)
        if val is None:
            view.pop(field, None)
        else:
            view[field] = bool(val) if field == "smart_exposure_order" else val
    return view


def build_view(
    snap: TSSnapshot,
    user_version: int,
    plans_list: list[dict],
    gear: dict,
    *,
    profile_id: str,
    machine: str | None,
    strict: bool,
    profile_label: str | None = None,
) -> MergeView:
    plans = copy.deepcopy([p for p in plans_list if isinstance(p, dict)])
    scoped = [p for p in plans if in_scope(p, profile_id)]
    remotes, notes = _build_remotes(snap, gear)
    view = MergeView(
        profile_id=profile_id, machine=machine, strict=strict, snap=snap,
        user_version=user_version, plans=plans, in_scope=scoped,
        remotes=remotes, notes=notes,
        preview_hash=scope_hash(plans_list, profile_id),
    )
    matched_plans: set[int] = set()

    def claim_siblings(plan: dict, main: Remote, link_tids: set[int], guids: set[str]) -> list[Remote]:
        """Panels of the plan's mosaic that the importer emitted as separate
        plans, so they are not offered as new."""
        base = _base_target_name(plan)
        out = []
        for r in remotes:
            if r.claimed or r.project_id != main.project_id:
                continue
            by_link = bool(link_tids) and r.target_ids <= link_tids
            by_guid = bool(r.target_guids & guids)
            name = (r.plan.get("target") or {}).get("name") or ""
            by_name = _split_panel_suffix(name)[1] is not None and r.base_name == base
            if by_link or by_guid or by_name:
                r.claimed = True
                out.append(r)
        return out

    def record(plan: dict, remote: Remote, how: str, link_tids: set[int], guids: set[str]) -> None:
        remote.claimed = True
        siblings = claim_siblings(plan, remote, link_tids, guids)
        rows, cols, _ = _panels(plan)
        split = (rows > 1 or cols > 1) and remote.is_single
        view.matches.append(Match(plan=plan, remote=remote, matched_by=how,
                                  siblings=siblings, split_mosaic=split))
        matched_plans.add(id(plan))

    # 1. By link.
    other_machines: set[str] = set()
    for plan in scoped:
        link = link_for(plan, profile_id)
        if not link:
            continue
        if not same_machine(link.get("machine"), machine):
            other_machines.add(str(link.get("machine")))
            continue
        refs = link.get("refs") or {}
        proj_id = refs.get("project_id")
        link_tids = {int(t) for t in (refs.get("target_ids_by_panel") or {}).values() if t is not None}
        if proj_id is None or not link_tids:
            continue
        best, best_overlap = None, 0
        for r in remotes:
            if r.claimed or r.project_id != int(proj_id):
                continue
            overlap = len(r.target_ids & link_tids)
            if overlap > best_overlap:
                best, best_overlap = r, overlap
        if best is not None:
            record(plan, best, "link", link_tids, acp_target_guids(plan, profile_id))
    for m in sorted(other_machines):
        who = profile_label or profile_id
        view.link_notes.append(
            f"Profile {who} was last linked from {m}; matched by name stamp instead."
        )

    # 2. By ACP's guid.
    for plan in scoped:
        if id(plan) in matched_plans:
            continue
        guids = acp_target_guids(plan, profile_id)
        rows, cols, base = _panels(plan)
        anchor_names = {base, f"{base} Panel 1 (R1C1)"}
        pname = project_name_of(plan)
        anchor_guids = {target_guid(profile_id, pname, n) for n in anchor_names} | {
            legacy_target_guid(profile_id, pname, n) for n in anchor_names}
        candidates = [r for r in remotes if not r.claimed and r.target_guids & guids]
        if not candidates:
            continue
        candidates.sort(key=lambda r: (not (r.target_guids & anchor_guids), -len(r.target_guids & guids)))
        record(plan, candidates[0], "guid", set(), guids)

    # 3. By name, as a proposal.
    proposed_plans: set[int] = set()
    for r in remotes:
        if r.claimed:
            continue
        for plan in scoped:
            if id(plan) in matched_plans or id(plan) in proposed_plans:
                continue
            if project_name_of(plan) == (r.plan.get("project_name") or "").strip() and \
                    _base_target_name(plan) == r.base_name:
                view.proposals.append((plan, r))
                proposed_plans.add(id(plan))
                r.claimed = True
                break

    # A TS plan stamped with the guid of an ACP plan that belongs only to
    # other rigs is that plan, pushed here by a plugin that did not post its
    # links back. Offering it as new would duplicate the plan, so it is held
    # back, and only counted.
    out_of_scope = [p for p in plans if not in_scope(p, profile_id)]
    foreign_guids: set[str] = set()
    for p in out_of_scope:
        foreign_guids |= acp_target_guids(p, profile_id)
    held = 0
    for r in remotes:
        if not r.claimed and r.target_guids & foreign_guids:
            r.claimed = True
            held += 1
    if held:
        view.link_notes.append(
            f"{held} TS plan{'s' if held != 1 else ''} carry the stamp of ACP plans "
            "linked only to other rigs, so they are left alone rather than added again. "
            "They link to this rig when the plugin next reports its sync to ACP."
        )

    unclaimed = [r for r in remotes if not r.claimed]
    view.duplicates = find_duplicates(unclaimed, plans, gear, profile_id,
                                      busy={*matched_plans, *proposed_plans})
    flagged = {id(d.remote) for d in view.duplicates}
    view.new = [r for r in unclaimed if id(r) not in flagged]
    if view.duplicates:
        # Duplicates are found against every rig's plans, which the scope
        # hash does not cover. Folding them in means a change elsewhere that
        # moves a duplicate makes the preview stale, rather than a "link"
        # the page sent quietly doing nothing.
        sig = sorted((d.remote.key, str(d.plan.get("id")), d.linkable, d.default) for d in view.duplicates)
        view.preview_hash = hashlib.sha256(
            (view.preview_hash + json.dumps(sig)).encode("utf-8")).hexdigest()
    for plan in scoped:
        if id(plan) in matched_plans or id(plan) in proposed_plans:
            continue
        if link_for(plan, profile_id):
            view.not_in_upload.append(plan)
        else:
            view.unlinked_unmatched.append(plan)

    # A TS row takes the id of the ACP row it is, so the diff compares rows
    # by id and the link keys the plans by the rows ACP has.
    for m in view.matches:
        for r in [m.remote, *m.siblings]:
            adopt_row_ids(m.plan, r, profile_id)

    # The three-way diff for each match.
    constraints = _group_constraints(scoped)
    order_settings = _group_order(scoped)
    for m in view.matches:
        link = link_for(m.plan, profile_id) or {}
        base = base_snapshot_to_plan_shape(link.get("base_snapshot"))
        m.diff = diff_plan(
            str(m.plan.get("id") or "<unknown>"),
            base=base,
            local=_local_view(m.plan, constraints, order_settings),
            remote=unfloored(m.plan, m.remote.plan),
            no_base_is_conflict=strict,
            skip_prefixes=("target.",) if m.split_mosaic else (),
        )
    return view


def adopt_row_ids(plan: dict, remote: Remote, profile_id: str) -> None:
    """Give each row the importer built for *remote* the id of the ACP row
    it stands for, in place, and rekey the remote's id map to match.

    The ACP row with the same filter and template wins
    (goal-panel-and-ts-order.md section 3, "What the importer does"). A row
    with none, whose exposure plan the plan's link pins to an ACP row,
    takes that row's id, which is a template changed in TS. Anything else
    keeps the fresh id the importer gave it.
    """
    local_rows = [r for r in (plan.get("filter_rows") or []) if isinstance(r, dict) and r.get("id")]
    by_pair = {(r.get("filter"), r.get("template")): str(r["id"]) for r in local_rows}
    refs = (link_for(plan, profile_id) or {}).get("refs") or {}
    row_by_plan_id: dict[int, str] = {}
    for key, ep_id in (refs.get("exposure_plan_ids") or {}).items():
        _, _, row_id = str(key).partition("_")
        if isinstance(ep_id, int) and row_id:
            row_by_plan_id.setdefault(ep_id, row_id)
    remote_plan_ids: dict[str, int] = {}
    for key, ep_id in (remote.id_map.get("exposure_plan_ids") or {}).items():
        _, _, row_id = str(key).partition("_")
        remote_plan_ids.setdefault(row_id, ep_id)

    taken: set[str] = set()
    new_id: dict[str, str] = {}
    rows = [r for r in (remote.plan.get("filter_rows") or []) if isinstance(r, dict)]
    for r in rows:
        mine = by_pair.get((r.get("filter"), r.get("template")))
        if mine is not None and mine not in taken:
            new_id[str(r["id"])] = mine
            taken.add(mine)
    local_ids = {str(r["id"]) for r in local_rows}
    for r in rows:
        if str(r["id"]) in new_id:
            continue
        mine = row_by_plan_id.get(remote_plan_ids.get(str(r["id"]), -1))
        if mine is not None and mine in local_ids and mine not in taken:
            new_id[str(r["id"])] = mine
            taken.add(mine)
    if not new_id:
        return
    for r in rows:
        r["id"] = new_id.get(str(r["id"]), r["id"])
    epi = remote.id_map.get("exposure_plan_ids") or {}
    rekeyed = {}
    for key, ep_id in epi.items():
        tid, _, row_id = str(key).partition("_")
        rekeyed[f"{tid}_{new_id.get(row_id, row_id)}"] = ep_id
    remote.id_map["exposure_plan_ids"] = rekeyed
    remote.id_map["template_ids_by_filter"] = {
        new_id.get(k, k): v for k, v in (remote.id_map.get("template_ids_by_filter") or {}).items()
    }


def find_duplicates(new: list[Remote], plans: list[dict], gear: dict, profile_id: str,
                    *, busy: set[int]) -> list[Duplicate]:
    """Each new TS plan whose footprint covers an ACP plan's, against every
    ACP plan of every rig. A new single target is measured with the gear the
    page preselects for it. *busy* holds the ids of ACP plans this view has
    already matched or proposed, which cannot take a second link."""
    if not new:
        return []
    candidates = []
    for p in plans:
        fp = dup.footprint_of(p, gear)
        if fp is not None:
            candidates.append((p, fp))
    if not candidates:
        return []
    default = default_new_plan_gear(plans, gear, profile_id)
    out = []
    for r in new:
        if r.is_single:
            ts_gear = {"telescope_id": default.get("telescope_id"), "camera_id": default.get("camera_id")}
        else:
            ts_gear = {"telescope_id": r.plan.get("telescope_id"), "camera_id": r.plan.get("camera_id")}
        fp = dup.footprint_of(r.plan, gear, **ts_gear)
        if fp is None:
            continue
        hit = dup.best_match(fp, candidates)
        if hit is None:
            continue
        plan, comparison = hit
        out.append(Duplicate(
            remote=r, plan=plan, comparison=comparison, ts_footprint=fp,
            acp_footprint=next(f for p, f in candidates if p is plan),
            ts_gear=ts_gear, linkable=id(plan) not in busy,
        ))
    return out


# ---------------------------------------------------------------------------
# Gear for new single-target plans
# ---------------------------------------------------------------------------


def _gear_ids(gear: dict) -> tuple[set[str], set[str]]:
    return (
        {t.get("id") for t in gear.get("telescopes", []) or [] if t.get("id")},
        {c.get("id") for c in gear.get("cameras", []) or [] if c.get("id")},
    )


def valid_gear_choice(choice, gear: dict) -> dict | None:
    if not isinstance(choice, dict):
        return None
    tels, cams = _gear_ids(gear)
    tel, cam = choice.get("telescope_id"), choice.get("camera_id")
    if tel in tels and cam in cams:
        return {"telescope_id": tel, "camera_id": cam}
    return None


def default_new_plan_gear(plans: list[dict], gear: dict, profile_id: str) -> dict:
    """The gear the page preselects for new single-target plans on this rig.

    The choice saved for the profile on an earlier apply, else the pair most
    used by plans already linked to the profile, else the first telescope and
    camera in ACP's list. ``source`` says which.
    """
    from . import config
    saved = (config.load().get("new_plan_gear") or {}).get(profile_id)
    ok = valid_gear_choice(saved, gear)
    if ok:
        return {**ok, "source": "saved"}
    tels, cams = _gear_ids(gear)
    counts: Counter = Counter()
    for p in plans:
        if not isinstance(p, dict) or profile_id not in links_of(p):
            continue
        pair = (p.get("telescope_id"), p.get("camera_id"))
        if pair[0] in tels and pair[1] in cams:
            counts[pair] += 1
    if counts:
        (tel, cam), _ = counts.most_common(1)[0]
        return {"telescope_id": tel, "camera_id": cam, "source": "most_used"}
    first_tel = next(iter(gear.get("telescopes", []) or []), {}).get("id")
    first_cam = next(iter(gear.get("cameras", []) or []), {}).get("id")
    return {"telescope_id": first_tel, "camera_id": first_cam, "source": "first"}


def save_new_plan_gear(profile_id: str, choice: dict) -> None:
    from . import config
    current = dict(config.load().get("new_plan_gear") or {})
    current[profile_id] = {"telescope_id": choice["telescope_id"], "camera_id": choice["camera_id"]}
    config.save({"new_plan_gear": current})


# ---------------------------------------------------------------------------
# Rendering the view
# ---------------------------------------------------------------------------


def _plan_ref(plan: dict) -> dict:
    return {
        "plan_id": plan.get("id"),
        "project_name": project_name_of(plan),
        "target_name": (plan.get("target") or {}).get("name"),
    }


def _fields(d: PlanDiff, paths: list[str], labels: dict[str, str] | None = None) -> list[dict]:
    """The changed fields for the review page. The order's values are lists
    of row ids, shown by their rows' names."""
    def shown(path, value):
        if path == ORDER and isinstance(value, list) and labels:
            return [labels.get(str(i), str(i)) for i in value]
        return value
    out = [{
        "path": p,
        "base": shown(p, d.base_values.get(p)),
        "acp": shown(p, d.local_values.get(p)),
        "ts": shown(p, d.remote_values.get(p)),
    } for p in paths]
    for f in out:
        if f["path"] == ORDER:
            f["keep_acp_note"] = KEEP_ORDER_NOTE
            f["in_conflict"] = ORDER in d.conflict
    return out


def _row_labels(*plans: dict) -> dict[str, str]:
    from .rows import row_label
    out: dict[str, str] = {}
    for plan in plans:
        for r in (plan.get("filter_rows") or []):
            if isinstance(r, dict) and r.get("id"):
                out.setdefault(str(r["id"]), row_label(r))
    return out


_LEVEL_ORDER = {"loss": 0, "warn": 1, "info": 2}


def grouped_notes(notes: list[ImportNote]) -> list[dict]:
    """Loss notes grouped by project, projects with losses first."""
    by_project: dict[str, list[ImportNote]] = {}
    for n in notes:
        by_project.setdefault(n.project or "", []).append(n)
    groups = []
    for project, items in by_project.items():
        items = sorted(items, key=lambda n: _LEVEL_ORDER.get(n.level, 3))
        groups.append({
            "project": project,
            "losses": sum(1 for n in items if n.level == "loss"),
            "notes": [n.to_dict() for n in items],
        })
    groups.sort(key=lambda g: (g["losses"] == 0, g["project"].lower()))
    return groups


def _gear_name(items, wanted) -> str | None:
    for it in items or []:
        if isinstance(it, dict) and wanted and it.get("id") == wanted:
            return it.get("name") or it.get("id")
    return None


def _legend(plan: dict, gear: dict, telescope_id, camera_id) -> dict:
    """What the sky view's legend says about one plan."""
    from .diff import in_unit
    rows = [r for r in (plan.get("filter_rows") or []) if isinstance(r, dict)]
    hours = 0.0
    for r in rows:
        try:
            hours += float(in_unit(r, "h").get("target_hours") or 0)
        except (TypeError, ValueError, AttributeError):
            pass
    return {
        "project_name": project_name_of(plan),
        "target_name": (plan.get("target") or {}).get("name"),
        "telescope": _gear_name(gear.get("telescopes"), telescope_id),
        "camera": _gear_name(gear.get("cameras"), camera_id),
        "filters": list(dict.fromkeys(r.get("filter") for r in rows)),
        "hours": round(hours, 2),
    }


def _overlap_pct(c: dup.Comparison) -> int | None:
    return None if c.overlap is None else int(round(c.overlap * 100))


def duplicate_dict(d: Duplicate, gear: dict) -> dict:
    c = d.comparison
    tf, af = d.ts_footprint, d.acp_footprint
    return {
        **d.remote.summary(),
        "gear": "new_plan_gear" if d.remote.is_single else "mosaic",
        "acp": {**_plan_ref(d.plan), "profiles": sorted(links_of(d.plan))},
        "overlap_pct": _overlap_pct(c),
        "separation_arcmin": round(c.separation_arcmin, 2),
        "strong": c.strong,
        "default": d.default,
        "linkable": d.linkable,
        "sky": dup.sky_view(tf, af),
        "legend": {
            "ts": {**_legend(d.remote.plan, gear, d.ts_gear.get("telescope_id"), d.ts_gear.get("camera_id")),
                   "size_arcmin": [round(tf.w, 1), round(tf.h, 1)]},
            "acp": {**_legend(d.plan, gear, d.plan.get("telescope_id"), d.plan.get("camera_id")),
                    "size_arcmin": [round(af.w, 1), round(af.h, 1)]},
        },
    }


def preview_dict(view: MergeView, gear: dict, *, profile_name: str | None = None) -> dict:
    """The upload preview, as the status endpoint returns it and the review
    page draws it."""
    updated, unchanged, conflicts = [], [], []
    for m in view.matches:
        d = m.diff
        entry = {**_plan_ref(m.plan), "key": m.remote.key, "matched_by": m.matched_by,
                 "split_mosaic": m.split_mosaic}
        labels = _row_labels(m.plan, m.remote.plan)
        if d.has_conflict:
            conflicts.append({
                **entry,
                "reason": "no_base" if d.no_base else "both_changed",
                "message": ("no sync record for this rig" if d.no_base
                            else "changed in both ACP and TS since the last sync"),
                "fields": _fields(d, d.conflict + d.auto_pull + d.auto_keep, labels),
            })
        elif d.auto_pull:
            updated.append({**entry, "fields": _fields(d, d.auto_pull, labels),
                            "acp_only": _fields(d, d.auto_keep, labels)})
        else:
            unchanged.append({**entry, "acp_only": _fields(d, d.auto_keep, labels)})

    new_default = default_new_plan_gear(view.plans, gear, view.profile_id)
    new = []
    for r in view.new:
        s = r.summary()
        s["gear"] = ("new_plan_gear" if r.is_single else "mosaic")
        if not r.is_single:
            s["telescope_id"] = r.plan.get("telescope_id")
            s["camera_id"] = r.plan.get("camera_id")
        new.append(s)
    proposals = [{**r.summary(), "acp": _plan_ref(p)} for p, r in view.proposals]
    duplicates = [duplicate_dict(d, gear) for d in view.duplicates]

    counts = {
        "new": len(new),
        "updated": len(updated),
        "conflicts": len(conflicts),
        "proposals": len(proposals),
        "duplicates": len(duplicates),
        "unchanged": len(unchanged),
        "not_in_upload": len(view.not_in_upload),
    }
    who = profile_name or view.profile_id
    choice = "needs a choice" if counts["conflicts"] == 1 else "need a choice"
    dups = ""
    if counts["duplicates"]:
        dups = f"{counts['duplicates']} possible duplicate{'s' if counts['duplicates'] != 1 else ''}, "
    return {
        "profile_id": view.profile_id,
        "profile_name": profile_name,
        "machine": view.machine,
        "user_version": view.user_version,
        "preview_hash": view.preview_hash,
        "summary": f"{who}: {counts['new']} new, {dups}{counts['updated']} updated, "
                   f"{counts['conflicts']} {choice}",
        "counts": counts,
        "new": new,
        "updated": updated,
        "conflicts": conflicts,
        "proposals": proposals,
        "duplicates": duplicates,
        "unchanged": unchanged,
        "not_in_upload": [_plan_ref(p) for p in view.not_in_upload],
        "link_notes": list(view.link_notes),
        "notes": grouped_notes(view.notes),
        "gear": {
            "telescopes": [{"id": t.get("id"), "name": t.get("name") or t.get("id")}
                           for t in gear.get("telescopes", []) or []],
            "cameras": [{"id": c.get("id"), "name": c.get("name") or c.get("id")}
                        for c in gear.get("cameras", []) or []],
            "default": new_default,
        },
        "empty": not view.snap.projects_by_id,
    }


def legacy_diff_dict(view: MergeView) -> dict:
    """The response shape `/import/diff` has always had, which ACP's sync
    modal reads. `ts_only_new` entries gain a `key`; the modal still keys its
    ticks by project name, which `/import/resolve` keeps accepting."""
    auto_pull, auto_keep, conflicts, notices = [], [], [], []
    for m in view.matches:
        d = m.diff
        if d.has_conflict:
            conflicts.append(d.to_dict())
        elif d.auto_pull:
            auto_pull.append(d.to_dict())
        elif d.auto_keep:
            auto_keep.append(d.to_dict())
    for plan in view.not_in_upload + view.unlinked_unmatched + [p for p, _ in view.proposals]:
        plan_id = plan.get("id") or plan.get("guid") or "<unknown>"
        refs = (link_for(plan, view.profile_id) or {}).get("refs") or {}
        if refs.get("project_id"):
            notices.append({
                "kind": "ts_row_missing",
                "plan_id": plan_id,
                "ts_project_id": refs["project_id"],
                "message": (
                    f"ACP plan '{plan_id}' was last synced to TS project Id "
                    f"{refs['project_id']}, but that row no longer exists. "
                    "Re-push to recreate, or delete from ACP."
                ),
            })
        else:
            notices.append({
                "kind": "acp_only_unpushed",
                "plan_id": plan_id,
                "message": f"ACP plan '{plan_id}' has never been pushed to TS.",
            })
    for note in view.link_notes:
        notices.append({"kind": "link_other_machine", "message": note})
    return {
        "auto_pull": auto_pull,
        "auto_keep": auto_keep,
        "conflicts": conflicts,
        "ts_only_new": [r.summary() for r in view.new] + [r.summary() for _, r in view.proposals]
        + [d.remote.summary() for d in view.duplicates],
        "proposals": [{**r.summary(), "acp": _plan_ref(p)} for p, r in view.proposals],
        "possible_duplicates": [{**d.remote.summary(), "acp": _plan_ref(d.plan),
                                 "overlap_pct": _overlap_pct(d.comparison)} for d in view.duplicates],
        "notices": notices,
    }


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _match_id_map(m: Match) -> dict:
    """The TS row ids to link *m*'s plan to.

    For a mosaic whose panels came back as separate plans, the panels are put
    back at their grid positions from their "(RxCy)" names when every one has
    one; otherwise only the matched panel is linked.
    """
    idm = copy.deepcopy(m.remote.id_map)
    if not m.split_mosaic or not m.siblings:
        return idm
    panels: dict[str, int] = {}
    parts = [m.remote] + m.siblings
    for r in parts:
        name = (r.plan.get("target") or {}).get("name") or ""
        _, row, col = _split_panel_suffix(name)
        if row is None or len(r.target_ids) != 1:
            return idm
        panels[f"{row},{col}"] = next(iter(r.target_ids))
    idm["target_ids_by_panel"] = panels
    for r in m.siblings:
        for k, v in (r.id_map.get("template_ids_by_filter") or {}).items():
            idm.setdefault("template_ids_by_filter", {}).setdefault(k, v)
        idm.setdefault("exposure_plan_ids", {}).update(r.id_map.get("exposure_plan_ids") or {})
    return idm


_REF_TIMES = ("last_pushed_iso", "last_pushed_user_version", "last_imported_iso")


def _same_link(old: dict | None, machine: str | None, refs: dict, base: dict | None) -> bool:
    """Whether a stored link already says what a new one would, ignoring
    the timestamps a rewrite would move."""
    if not isinstance(old, dict):
        return False
    if (old.get("machine") or None) != (machine or None):
        return False
    old_refs = {k: v for k, v in (old.get("refs") or {}).items() if k not in _REF_TIMES}
    new_refs = {k: v for k, v in refs.items() if k not in _REF_TIMES}
    old_base = {k: v for k, v in (old.get("base_snapshot") or {}).items() if k != "captured_iso"}
    new_base = {k: v for k, v in (base or {}).items() if k != "captured_iso"}
    # JSON round trip so int dict keys and tuples compare as stored.
    norm = lambda v: json.loads(json.dumps(v, sort_keys=True, default=str))  # noqa: E731
    return norm(old_refs) == norm(new_refs) and norm(old_base) == norm(new_base)


def _order_mismatch_notes(view: MergeView, plan: dict, idm: dict) -> list[ImportNote]:
    """A warn note for each target of *plan* whose ACP order, over the
    enabled rows both ACP and TS have, is not the order TS will shoot them in
    (goal-panel-and-ts-order.md section 5). The next push leaves such an
    order alone until ACP's order changes. Checked after the import has
    changed the plan, so an order pulled from TS never shows."""
    snap = view.snap
    rows = {str(r["id"]): r for r in fr.rows_of(plan)}
    acp_ids = list(rows)
    project = snap.projects_by_id.get(idm.get("project_id"))
    out: list[ImportNote] = []
    for tid in (idm.get("target_ids_by_panel") or {}).values():
        tgt = snap.targets_by_id.get(tid)
        if tgt is None:
            continue
        row_of_plan: dict[int, str] = {}
        for key, pid in (idm.get("exposure_plan_ids") or {}).items():
            tid_str, _, row_id = str(key).partition("_")
            if tid_str == str(tid) and row_id in rows:
                row_of_plan.setdefault(pid, row_id)
        enabled = snap.enabled_plan_ids(tid)
        ts_ids = [row_of_plan[p] for p in effective_plan_order(
            enabled, snap.override_rows_by_target.get(tid, []))[0] if p in row_of_plan]
        acp_common = [r for r in acp_ids if r in set(ts_ids)]
        if acp_common == ts_ids:
            continue
        label = lambda ids: ", ".join(str(rows[r].get("filter") or r) for r in ids)  # noqa: E731
        out.append(ImportNote(
            "warn",
            f"{tgt.name}: ACP lists {label(acp_common)} but Target Scheduler will shoot "
            f"{label(ts_ids)}. Push after reordering in ACP to change it.",
            project=project.name if project is not None else "", target=tgt.name))
    return out


def _link(view: MergeView, plan: dict, idm: dict, *, skip_if_same: bool = False) -> bool:
    """Write the plan's link for the view's profile. With *skip_if_same*, a
    link already stored in ts_links that says the same is left alone, so an
    apply that changes nothing on a plan leaves its JSON as it was. Returns
    whether anything was written."""
    prior = (link_for(plan, view.profile_id) or {}).get("refs")
    refs = build_ts_refs_from_ids(
        profile_id=view.profile_id,
        project_id=idm.get("project_id"),
        target_ids_by_panel=idm.get("target_ids_by_panel") or {},
        template_ids_by_filter=idm.get("template_ids_by_filter") or {},
        exposure_plan_ids=idm.get("exposure_plan_ids") or {},
        user_version=view.user_version,
        operation="import",
        prior_refs=prior if isinstance(prior, dict) else None,
    )
    old_base = (link_for(plan, view.profile_id) or {}).get("base_snapshot")
    base = build_base_snapshot_from_ids(
        view.snap,
        project_id=idm.get("project_id"),
        target_ids_by_panel=idm.get("target_ids_by_panel") or {},
        template_ids_by_filter=idm.get("template_ids_by_filter") or {},
        exposure_plan_ids=idm.get("exposure_plan_ids") or {},
        plan=plan,
        old_base=old_base if isinstance(old_base, dict) else None,
    )
    if skip_if_same:
        stored = (plan.get("ts_links") or {}).get(view.profile_id) if isinstance(plan.get("ts_links"), dict) else None
        if _same_link(stored, view.machine, refs, base):
            return False
    write_link(plan, view.profile_id, machine=view.machine, refs=refs, base_snapshot=base)
    return True


def _take_ts(m: Match) -> None:
    if not m.split_mosaic:
        merge_remote_into_local(m.plan, m.remote.plan)
        return
    # One panel of a split mosaic: its project and exposure fields stand for
    # the mosaic, its position and name do not.
    rp = m.remote.plan
    if rp.get("project_name"):
        m.plan["project_name"] = rp["project_name"]
    for k in ("priority", "min_altitude_deg", "meridian_window_min"):
        if k in rp:
            m.plan[k] = rp[k]
    from .merge import take_rows
    m.plan["filter_rows"] = take_rows(m.plan, rp)


def order_key(plan_id: str) -> str:
    """The key of a plan's order in the import choices: the order is its own
    field, chosen apart from the plan's Keep ACP."""
    return f"{plan_id}#{ORDER}"


#: Said on the order's row of the import preview, for the choice to keep
#: ACP's order over TS's.
KEEP_ORDER_NOTE = "Your next push will replace the order set in Target Scheduler."


def normalise_decisions(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        d = DECISION_ALIASES.get(str(v))
        if d:
            out[str(k)] = d
    return out


def _note_dicts(view: MergeView, notes: list[ImportNote]) -> list[dict]:
    """Add *notes* to the view's import notes and return them as dicts."""
    view.notes.extend(notes)
    return [n.to_dict() for n in notes]


def apply_view(
    view: MergeView,
    *,
    decisions: dict[str, str],
    add: list[str] | None,
    new_plan_gear: dict | None,
    require_decisions: bool,
) -> dict:
    """Apply the merge to ``view.plans`` in place. Returns what was done.

    ``decisions`` is keyed by ACP plan id for matched plans ("take_ts",
    "keep_acp", "leave") and by TS key for proposals and possible duplicates
    ("link"). ``add`` lists the TS keys to add as new plans; a proposal's or
    a duplicate's key in it adds that TS plan as new. A duplicate neither
    linked nor added is skipped: no plan, no link. Plans out of scope are
    never looked at, except the ACP plan a duplicate is linked to.
    """
    applied: dict[str, list] = {
        "take_ts": [], "keep_acp": [], "auto_pull": [], "kept_acp_on_pull": [],
        "unchanged": [], "left": [], "linked": [], "added": [], "skipped": [],
        "order_pulled": [], "order_kept_acp": [], "notes": [],
    }
    if require_decisions:
        missing = [str(m.plan.get("id")) for m in view.matches
                   if m.diff.has_conflict and decisions.get(str(m.plan.get("id"))) not in
                   ("take_ts", "keep_acp", "leave")]
        if missing:
            raise MergeError(400, "every conflict needs a choice before apply",
                             missing_decisions=missing)

    # Links for possible duplicates are checked before anything in the view
    # changes, so a refusal leaves view.plans as it was.
    add_keys = set(add or [])
    dup_links = [d for d in view.duplicates
                 if decisions.get(d.remote.key) == "link" and d.remote.key not in add_keys]
    refused = [d.remote.key for d in dup_links if not d.linkable]
    if refused:
        raise MergeError(400, "that ACP plan is already matched to a TS plan in this upload",
                         keys=refused)
    targets = Counter(id(d.plan) for d in dup_links)
    twice = [d.remote.key for d in dup_links if targets[id(d.plan)] > 1]
    if twice:
        raise MergeError(400, "two TS plans cannot both link to the same ACP plan", keys=twice)

    for m in view.matches:
        pid = str(m.plan.get("id"))
        d = m.diff
        dec = decisions.get(pid)
        # Keeping ACP's order is a choice on the order itself. For a conflict
        # that includes the order, the plan's Keep ACP is that choice.
        order_kept = ((decisions.get(order_key(pid)) == "keep_acp" and ORDER in d.auto_pull)
                      or (dec == "keep_acp" and ORDER in d.conflict))
        if d.has_conflict:
            if dec == "take_ts":
                _take_ts(m)
                applied["take_ts"].append(pid)
            elif dec == "keep_acp":
                applied["keep_acp"].append(pid)
                # Keep ACP settles the conflicting fields. An order change
                # that is no part of the conflict is an auto-pull like any
                # other and still comes in, unless the order was kept on
                # its own, because the link records TS's order as the base
                # (goal-panel-and-ts-order.md section 5).
                pull = [f for f in d.auto_pull if f == ORDER and not order_kept]
                if pull:
                    apply_auto_pull(m.plan, m.remote.plan, pull)
                    applied["order_pulled"].append(pid)
            else:
                applied["left"].append(pid)
                continue
        elif d.auto_pull:
            # The order is its own choice. Keeping ACP's other fields leaves
            # TS's order coming in, so the base the link records (TS's
            # order) is ACP's order too and the next push leaves TS's rows
            # alone (goal-panel-and-ts-order.md section 5).
            if dec == "keep_acp":
                pull = [f for f in d.auto_pull if f == ORDER and not order_kept]
            else:
                pull = [f for f in d.auto_pull if not (f == ORDER and order_kept)]
            if pull:
                apply_auto_pull(m.plan, m.remote.plan, pull)
            if dec != "keep_acp":
                applied["auto_pull"].append(pid)
            else:
                applied["kept_acp_on_pull"].append(pid)
                if pull:
                    applied["order_pulled"].append(pid)
        else:
            applied["unchanged"].append(pid)
        if order_kept:
            applied["order_kept_acp"].append(pid)
        changed_fields = (pid in applied["take_ts"] or pid in applied["auto_pull"]
                          or pid in applied["order_pulled"])
        idm = _match_id_map(m)
        _link(view, m.plan, idm, skip_if_same=not changed_fields)
        if not order_kept:
            applied["notes"] += _note_dicts(view, _order_mismatch_notes(view, m.plan, idm))

    for plan, r in view.proposals:
        if decisions.get(r.key) == "link" and r.key not in add_keys:
            adopt_row_ids(plan, r, view.profile_id)
            _link(view, plan, r.id_map)
            applied["notes"] += _note_dicts(view, _order_mismatch_notes(view, plan, r.id_map))
            applied["linked"].append({"plan_id": plan.get("id"), "key": r.key})

    for d in dup_links:
        adopt_row_ids(d.plan, d.remote, view.profile_id)
        _link(view, d.plan, d.remote.id_map)
        applied["notes"] += _note_dicts(view, _order_mismatch_notes(view, d.plan, d.remote.id_map))
        applied["linked"].append({"plan_id": d.plan.get("id"), "key": d.remote.key})
    linked_dups = {d.remote.key for d in dup_links}
    for d in view.duplicates:
        if d.remote.key not in add_keys and d.remote.key not in linked_dups:
            applied["skipped"].append(d.remote.key)

    to_add = [r for r in view.new if r.key in add_keys] + \
             [r for _, r in view.proposals if r.key in add_keys and decisions.get(r.key) != "link"] + \
             [d.remote for d in view.duplicates if d.remote.key in add_keys]
    if to_add:
        used_ids = {p.get("id") for p in view.plans}
        used_guids = {p.get("guid") for p in view.plans if p.get("guid")}
        for r in to_add:
            plan = copy.deepcopy(r.plan)
            if plan.get("id") in used_ids or not plan.get("id"):
                plan["id"] = f"acp-imp-{uuid.uuid4().hex[:12]}"
            if plan.get("guid") and plan["guid"] in used_guids:
                plan["guid"] = str(uuid.uuid4())
            if r.is_single and new_plan_gear:
                plan["telescope_id"] = new_plan_gear["telescope_id"]
                plan["camera_id"] = new_plan_gear["camera_id"]
            used_ids.add(plan["id"])
            if plan.get("guid"):
                used_guids.add(plan["guid"])
            _link(view, plan, r.id_map)
            view.plans.append(plan)
            applied["added"].append({"plan_id": plan["id"], "key": r.key})
    return applied


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def diff_from(
    path: Path,
    profile_id: str,
    machine: str | None,
    *,
    strict: bool = False,
    immutable: bool = False,
    profile_label: str | None = None,
) -> tuple[MergeView, dict]:
    """Read the TS file at *path* and build the merge view for *profile_id*.
    Writes nothing. Returns the view and ACP's gear."""
    snap, version = read_ts(path, profile_id, immutable=immutable)
    plans, gear = load_acp_state()
    view = build_view(snap, version, plans, gear, profile_id=profile_id,
                      machine=machine, strict=strict, profile_label=profile_label)
    return view, gear


def resolve_from(
    path: Path,
    profile_id: str,
    machine: str | None,
    decisions,
    add: list[str] | None,
    new_plan_gear,
    *,
    strict: bool = False,
    immutable: bool = False,
    require_decisions: bool = False,
    expected_hash: str | None = None,
    legacy_names: list[str] | None = None,
) -> dict:
    """Apply the merge from the TS file at *path* and write plans.json.

    ``add=None`` adds every new plan (the upload's default), and each
    possible duplicate whose default is to add it. ``legacy_names``
    is the old `/import/resolve` form: TS project names whose unmatched plans
    are added. ``expected_hash`` is the preview's hash of the in-scope plans;
    a mismatch means ACP changed since the preview, and nothing is written.
    """
    from .api import _write_plans_json

    snap, version = read_ts(path, profile_id, immutable=immutable)
    with PLANS_LOCK:
        plans, gear = load_acp_state()
        view = build_view(snap, version, plans, gear, profile_id=profile_id,
                          machine=machine, strict=strict)
        if expected_hash is not None and expected_hash != view.preview_hash:
            raise MergeError(409, "ACP changed since this preview", reason="stale_preview")

        gear_choice = None
        if new_plan_gear is not None:
            gear_choice = valid_gear_choice(new_plan_gear, gear)
            if gear_choice is None:
                raise MergeError(400, "new_plan_gear names a telescope or camera ACP does not have")
        chosen = gear_choice or {
            k: v for k, v in default_new_plan_gear(view.plans, gear, profile_id).items()
            if k in ("telescope_id", "camera_id")}
        if not chosen.get("telescope_id") or not chosen.get("camera_id"):
            chosen = None

        if add is None:
            add_keys = [r.key for r in view.new] + \
                       [d.remote.key for d in view.duplicates if d.default == "add"]
        else:
            add_keys = [str(k) for k in add]
        if legacy_names:
            wanted = set(legacy_names)
            pool = view.new + [r for _, r in view.proposals] + [d.remote for d in view.duplicates]
            add_keys += [r.key for r in pool if r.key not in add_keys and
                         (r.key in wanted or (r.plan.get("project_name") or "") in wanted)]

        applied = apply_view(view, decisions=normalise_decisions(decisions), add=add_keys,
                             new_plan_gear=chosen, require_decisions=require_decisions)
        plans_path, backup_path = _write_plans_json(view.plans, overwrite=True)
        if gear_choice is not None:
            save_new_plan_gear(profile_id, gear_choice)
    return {
        "plans_path": str(plans_path),
        "plans_backup_path": str(backup_path) if backup_path else None,
        "applied": applied,
        "plan_count": len(view.plans),
    }
