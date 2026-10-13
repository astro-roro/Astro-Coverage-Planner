"""Gear templates: the data model behind a camera filter's exposure templates.

docs/specs/gear-templates.md sections 3, 4, 5 and 11. Pure functions over
gear documents, so the migration, the derived view and the save checks can be
tested without the Flask app. `app.py` holds the locks, the files and the
routes.

A stored gear document (version 3) keeps only what the user or a sync wrote:
each camera filter holds `templates` and nothing else. Everything a reader
needs on top of that (the old per-filter scalars, each template's `status`,
`used_by`, `conflicts`, each camera's `wheel` block) is derived by `derive`
and removed again by `strip_derived` before a write, so no derived value is
ever stored.
"""
from __future__ import annotations

import copy
import math
import re
import uuid
from typing import Callable

import filter_rows as fr

SCHEMA = 3

# The fields a template shares with its TS row, as gear names them, and where
# each sits in `ts[P].base` and in a posted template list.
TEMPLATE_FIELDS = ("name", "sub_s", "gain", "offset", "bin")
BASE_KEY = {"name": "name", "sub_s": "defaultexposure", "gain": "gain", "offset": "offset", "bin": "bin"}
LIST_KEY = {"name": "name", "sub_s": "default_exposure_s", "gain": "gain", "offset": "offset", "bin": "bin"}

NARROW_FILTERS = frozenset({"Ha", "OIII", "SII"})
NAME_MAX = 64
ALIASES_MAX = 8
SUB_MAX_S = 3600

# Filter names flow into innerHTML. Real names are short tokens; a space is
# allowed so "Antlia Ha" and "IDAS + RED" save, but not at either end.
FILTER_NAME_RE = re.compile(r"^[A-Za-z0-9_+.\- ]{1,32}$")

DERIVED_TOP = ("gear_schema", "wheel_unlinked", "wheel_ambiguous", "conflict_count")
DERIVED_CAMERA = ("wheel", "template_edit_blocked")
DERIVED_FILTER = ("default_sub_s", "gain", "offset", "bin", "ts_template_name", "ts_template_id")
DERIVED_TEMPLATE = ("status", "conflicts", "used_by", "note")

PLUGIN_MESSAGE = ("The NINA plugin on this rig has not reported that it can update templates. "
                  "Install the new plugin first.")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _whole(v):
    """v as an int when it is a whole number (25 or 25.0), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v)
    return None


def _same(a, b) -> bool:
    if _is_num(a) and _is_num(b):
        return float(a) == float(b)
    return a == b


def is_migrated(doc) -> bool:
    return isinstance(doc, dict) and bool(doc.get("templates_migrated"))


def default_sub(filt: str) -> int:
    """300 s for the narrow filters (and any name the alias table maps to
    them), 120 s for anything else."""
    return 300 if fr.known_filter(filt) in NARROW_FILTERS else 120


def new_template(name: str, sub_s, gain=-1, offset=-1, bin_=1, starred=False) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "sub_s": sub_s,
        "gain": gain,
        "offset": offset,
        "bin": bin_,
        "starred": starred,
        "aliases": [],
        "ts_names": [],
        "ts": {},
    }


def _clip_name(name: str) -> str:
    return name[:NAME_MAX]


def all_templates(doc: dict):
    """(camera, filter name, template) for every template in the document."""
    for cam in doc.get("cameras") or []:
        if not isinstance(cam, dict) or not isinstance(cam.get("filters"), dict):
            continue
        for fname, cfg in cam["filters"].items():
            if isinstance(cfg, dict) and isinstance(cfg.get("templates"), list):
                for t in cfg["templates"]:
                    if isinstance(t, dict):
                        yield cam, fname, t


def find_by_id(doc: dict, template_id):
    for cam, fname, t in all_templates(doc):
        if t.get("id") == template_id:
            return cam, fname, t
    return None


# ---------------------------------------------------------------------------
# Migration, step 1: the shape (section 11)
# ---------------------------------------------------------------------------


def camera_to_v3(cam: dict) -> dict:
    """One camera's old-shape filters as gear templates, in place."""
    filters = cam.get("filters")
    if not isinstance(filters, dict):
        filters = {}
    taken: set[str] = set()
    out: dict = {}
    # Names already held by filters that are in the new shape.
    for cfg in filters.values():
        if isinstance(cfg, dict) and isinstance(cfg.get("templates"), list):
            taken.update(str(t.get("name")).lower() for t in cfg["templates"] if isinstance(t, dict))
    for fname, cfg in filters.items():
        cfg = cfg if isinstance(cfg, dict) else {}
        if isinstance(cfg.get("templates"), list):
            out[fname] = {"templates": cfg["templates"]}
            continue
        usual = f"{fname} ({cam.get('name')})"
        name = cfg.get("ts_template_name")
        if not isinstance(name, str) or not name.strip():
            name = usual
        name = _clip_name(name)
        # Two filters pinned to one template name would break the unique
        # name rule, so the later one takes the usual name instead.
        if name.lower() in taken:
            name = _clip_name(usual)
        n = 2
        while name.lower() in taken:
            name = _clip_name(f"{usual} {n}")
            n += 1
        taken.add(name.lower())
        sub = cfg.get("default_sub_s")
        if not _is_num(sub) or sub <= 0:
            sub = default_sub(fname)
        gain, offset, bin_ = _whole(cfg.get("gain")), _whole(cfg.get("offset")), _whole(cfg.get("bin"))
        out[fname] = {"templates": [new_template(
            name, sub,
            gain if gain is not None and gain >= -1 else -1,
            offset if offset is not None and offset >= -1 else -1,
            bin_ if bin_ is not None and 1 <= bin_ <= 4 else 1,
            starred=True,
        )]}
    cam["filters"] = out
    return cam


def migrate_v2(doc: dict) -> dict:
    """Step 1 of the gear migration: a version 2 document as version 3. Does
    not touch the argument. `ts` links stay empty, because the old
    `ts_template_id` cannot be trusted (spec section 1)."""
    out = copy.deepcopy(doc)
    cams = out.get("cameras")
    out["cameras"] = [camera_to_v3(c) for c in cams if isinstance(c, dict)] if isinstance(cams, list) else []
    out.setdefault("telescopes", [])
    out["version"] = SCHEMA
    out["rev"] = 1
    out["templates_migrated"] = 1
    if not isinstance(out.get("tidy"), dict):
        out["tidy"] = {}
    if not isinstance(out.get("links_capable"), dict):
        out["links_capable"] = {}
    return out


def empty_doc() -> dict:
    return {"version": SCHEMA, "rev": 0, "templates_migrated": 1, "telescopes": [],
            "telescope_colours": {}, "tidy": {}, "links_capable": {}, "cameras": []}


# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------


def strip_derived(doc: dict) -> dict:
    """The document as it is stored: every derived key removed. A document
    that is not in the new shape is returned as it is, because its filter
    scalars are stored values there."""
    if not is_migrated(doc):
        return doc
    out = copy.deepcopy(doc)
    for k in DERIVED_TOP:
        out.pop(k, None)
    for cam in out.get("cameras") or []:
        if not isinstance(cam, dict):
            continue
        for k in DERIVED_CAMERA:
            cam.pop(k, None)
        for cfg in (cam.get("filters") or {}).values():
            if not isinstance(cfg, dict):
                continue
            for k in DERIVED_FILTER:
                cfg.pop(k, None)
            for t in cfg.get("templates") or []:
                if isinstance(t, dict):
                    for k in DERIVED_TEMPLATE:
                        t.pop(k, None)
    return out


def _profile_entries(fp_profiles) -> list[dict]:
    """What the fingerprint store says about each NINA profile."""
    out = []
    for key, entry in (fp_profiles or {}).items():
        if not isinstance(entry, dict):
            continue
        fp = entry.get("fingerprint") if isinstance(entry.get("fingerprint"), dict) else {}
        camera = fp.get("camera") if isinstance(fp.get("camera"), dict) else {}
        pid = entry.get("profile_id") or fp.get("profile_id") or None
        slots = fp.get("wheel_slots")
        out.append({
            "key": key,
            "profile_id": pid if isinstance(pid, str) and pid else None,
            "profile_name": str(entry.get("profile_name") or fp.get("profile_name") or key),
            "camera_name": str(camera.get("name") or ""),
            "slots": [s for s in slots if isinstance(s, str)] if isinstance(slots, list) else [],
            "seen_at": str(entry.get("received_at") or ""),
        })
    return out


def _camera_norms(cam: dict, norm: Callable[[str], str]) -> set[str]:
    names = [cam.get("name")] + list(cam.get("nina_names") or [])
    return {n for n in (norm(x) for x in names if isinstance(x, str)) if n}


def link_cameras(cameras: list, profiles: list[dict], norm: Callable[[str], str]) -> dict:
    """Which fingerprints belong to which gear camera (spec section 5).

    A profile links to the camera whose name (or `nina_names`) its latest
    fingerprint names. A fingerprint that names two cameras links to
    neither. A fingerprint with no `profile_id` reports the camera by name
    but is never linked to a TS profile.
    """
    cams = [c for c in cameras if isinstance(c, dict) and c.get("id")]
    norms = {c["id"]: _camera_norms(c, norm) for c in cams}
    linked: dict[str, list[dict]] = {c["id"]: [] for c in cams}
    name_reports: dict[str, list[dict]] = {c["id"]: [] for c in cams}
    unlinked, ambiguous = [], []
    for p in profiles:
        n = norm(p["camera_name"])
        matches = [cid for cid, ns in norms.items() if n and n in ns]
        for cid in matches:
            name_reports[cid].append(p)
        if p["profile_id"] is None:
            continue
        if len(matches) == 1:
            linked[matches[0]].append(p)
        elif len(matches) > 1:
            ambiguous.append({"profile_id": p["profile_id"], "profile_name": p["profile_name"],
                              "camera_name": p["camera_name"], "camera_ids": matches})
        else:
            unlinked.append({"profile_id": p["profile_id"], "profile_name": p["profile_name"],
                             "camera_name": p["camera_name"]})
    for plist in linked.values():
        plist.sort(key=lambda p: p["seen_at"], reverse=True)
    return {"linked": linked, "name_reports": name_reports, "unlinked": unlinked, "ambiguous": ambiguous}


def _is_capable(entry) -> bool:
    return isinstance(entry, dict) and bool(entry.get("templates")) and bool(entry.get("repoint"))


def edit_blocked(cam: dict, linked: list[dict], name_reports: list[dict], links_capable) -> bool:
    """Whether a rename or sub change on this camera's templates is refused
    (spec section 4). A camera is restricted when a fingerprint reports it
    by name, with or without a `profile_id`, or when any template has a TS
    link. It stays restricted until `links_capable` holds a capable entry
    for a profile that reports it."""
    pids: set[str] = {p["profile_id"] for p in linked if p["profile_id"]}
    has_link = False
    for _cam, _f, t in all_templates({"cameras": [cam]}):
        if isinstance(t.get("ts"), dict) and t["ts"]:
            has_link = True
            pids.update(t["ts"].keys())
    if not (name_reports or has_link):
        return False
    caps = links_capable if isinstance(links_capable, dict) else {}
    return not any(_is_capable(caps.get(pid)) for pid in pids)


def _find_row(ts_list: dict | None, link: dict):
    """(list known, TS row or None) for a link. A list whose templates carry
    no id cannot say whether a row is missing."""
    rows = [r for r in (ts_list or {}).get("templates") or [] if isinstance(r, dict)]
    if not rows or not any("id" in r for r in rows):
        return False, None
    lid, guid = link.get("id"), link.get("guid")
    for r in rows:
        if lid is not None and r.get("id") == lid:
            return True, r
    for r in rows:
        if guid and r.get("guid") == guid:
            return True, r
    return True, None


def diff_link(t: dict, pid: str, link: dict, ts_lists: dict) -> dict:
    """Compare a template with the base it recorded for profile *pid* and
    with TS's row now (spec section 4's `changed` and `conflict`)."""
    base = link.get("base") if isinstance(link.get("base"), dict) else {}
    known, row = _find_row(ts_lists.get(pid), link)
    changed, conflicts = [], []
    for field in TEMPLATE_FIELDS:
        b = base.get(BASE_KEY[field])
        if b is None:
            continue
        a = t.get(field)
        if _same(a, b):
            continue
        changed.append(field)
        c = row.get(LIST_KEY[field]) if row else None
        if c is not None and not _same(c, b) and not _same(c, a):
            conflicts.append({"profile_id": pid, "field": field, "acp": a, "ts": c})
    return {"changed": changed, "conflicts": conflicts, "list_known": known, "row": row}


def _rows_using(plans, cameras_by_id):
    """(plan id, row, template id or None) for every filter row of every
    plan. A row with no `template_id` is matched by name on the plan's
    camera, as the repair does."""
    for p in plans or []:
        if not isinstance(p, dict):
            continue
        cam = cameras_by_id.get(p.get("camera_id") or "")
        for row in fr.rows_of(p):
            tid = row.get("template_id")
            if not tid:
                tpls = fr.camera_templates(cam, row.get("filter"))
                tpl = fr.find_template(tpls, row.get("template")) if tpls else None
                tid = tpl.get("id") if tpl else None
            yield p.get("id"), row, tid


def usage_by_template(plans, cameras: list) -> dict[str, dict]:
    """template id -> {plan id: number of rows}."""
    cams = {c.get("id"): c for c in cameras if isinstance(c, dict)}
    out: dict[str, dict] = {}
    for pid, _row, tid in _rows_using(plans, cams):
        if tid:
            per = out.setdefault(tid, {})
            per[pid] = per.get(pid, 0) + 1
    return out


def derive(stored: dict, *, fingerprints: dict, ts_lists: dict, plans: list,
           norm: Callable[[str], str]) -> dict:
    """The stored document with every derived key added (spec sections 3, 4
    and 5). A document not yet in the new shape comes back as it is."""
    out = copy.deepcopy(stored)
    if not is_migrated(out):
        return out
    cameras = [c for c in out.get("cameras") or [] if isinstance(c, dict)]
    profiles = _profile_entries(fingerprints)
    links = link_cameras(cameras, profiles, norm)
    usage = usage_by_template(plans, cameras)
    ts_lists = ts_lists if isinstance(ts_lists, dict) else {}
    conflict_count = 0

    for cam in cameras:
        cid = cam.get("id")
        linked = links["linked"].get(cid, [])
        primary = linked[0] if linked else None
        filters = cam.get("filters") if isinstance(cam.get("filters"), dict) else {}
        wheel_filters: dict[str, dict] = {}
        for fname in filters:
            with_slots = [p for p in linked if p["slots"]]
            if not with_slots:
                wheel_filters[fname] = {"state": "unknown", "slots": {}}
                continue
            found = {}
            for p in with_slots:
                slot = fr.resolve_to_wheel(fname, p["slots"])
                if slot is not None:
                    found[p["profile_id"]] = slot
            state = ("on_wheel" if len(found) == len(with_slots)
                     else "partial" if found else "not_on_wheel")
            wheel_filters[fname] = {"state": state, "slots": found}
        cam["wheel"] = {
            "profiles": [{"profile_id": p["profile_id"], "profile_name": p["profile_name"],
                          "seen_at": p["seen_at"], "slots": p["slots"], "primary": p is primary}
                         for p in linked],
            "filters": wheel_filters,
        }
        cam["template_edit_blocked"] = edit_blocked(
            cam, linked, links["name_reports"].get(cid, []), out.get("links_capable"))

        for fname, cfg in filters.items():
            if not isinstance(cfg, dict):
                continue
            tpls = [t for t in cfg.get("templates") or [] if isinstance(t, dict)]
            wf = wheel_filters.get(fname) or {"state": "unknown", "slots": {}}
            primary_slot = wf["slots"].get(primary["profile_id"]) if primary else None
            primary_off = bool(primary and primary["slots"] and primary_slot is None)
            for t in tpls:
                ts = t.get("ts") if isinstance(t.get("ts"), dict) else {}
                diffs = {pid: diff_link(t, pid, link, ts_lists)
                         for pid, link in ts.items() if isinstance(link, dict)}
                t["conflicts"] = [c for d in diffs.values() for c in d["conflicts"]]
                conflict_count += len(t["conflicts"])
                t["used_by"] = sum(usage.get(t.get("id"), {}).values())
                pid = primary["profile_id"] if primary else (next(iter(diffs), None))
                link = ts.get(pid) if pid else None
                d = diffs.get(pid) if pid else None
                if primary_off:
                    t["status"] = "waiting"
                    elsewhere = [p["profile_name"] for p in linked
                                 if p is not primary and p["profile_id"] in wf["slots"]]
                    t["note"] = (f"{fname} is not on the wheel of {primary['profile_name']} yet"
                                 + (f", it is on the wheel of {', '.join(elsewhere)}." if elsewhere else "."))
                elif not isinstance(link, dict) or link.get("id") is None:
                    t["status"] = "new"
                elif d and d["list_known"] and d["row"] is None and wf["state"] != "not_on_wheel":
                    t["status"] = "new"
                elif d and any(c["profile_id"] == pid for c in d["conflicts"]):
                    t["status"] = "conflict"
                elif d and d["changed"]:
                    t["status"] = "changed"
                else:
                    t["status"] = "in_ts"
            starred = next((t for t in tpls if t.get("starred")), tpls[0] if tpls else None)
            if starred is not None:
                cfg["default_sub_s"] = starred.get("sub_s")
                cfg["gain"] = starred.get("gain", -1)
                cfg["offset"] = starred.get("offset", -1)
                cfg["bin"] = starred.get("bin", 1)
                cfg["ts_template_name"] = starred.get("name")
                link = (starred.get("ts") or {}).get(primary["profile_id"]) if primary else None
                tid = link.get("id") if isinstance(link, dict) else None
                cfg["ts_template_id"] = str(tid) if tid is not None else None

    out["gear_schema"] = SCHEMA
    out["wheel_unlinked"] = links["unlinked"]
    out["wheel_ambiguous"] = links["ambiguous"]
    out["conflict_count"] = conflict_count
    return out


# ---------------------------------------------------------------------------
# Saving (spec section 4)
# ---------------------------------------------------------------------------


def valid_template_fields(t, where: str) -> str | None:
    """Range checks on one template as a client sends it."""
    if not isinstance(t, dict):
        return f"{where} must be an object"
    name = t.get("name")
    if not isinstance(name, str) or not name.strip():
        return f"{where}.name must be a non-empty string"
    if len(name) > NAME_MAX:
        return f"{where}.name must be at most {NAME_MAX} characters"
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return f"{where}.name must not contain control characters"
    sub = t.get("sub_s")
    if not _is_num(sub) or not 0 < sub <= SUB_MAX_S:
        return f"{where}.sub_s must be a number above 0 and at most {SUB_MAX_S}"
    for key in ("gain", "offset"):
        v = _whole(t.get(key))
        if v is None or v < -1:
            return f"{where}.{key} must be -1 or a whole number of at least 0"
    b = _whole(t.get("bin"))
    if b is None or not 1 <= b <= 4:
        return f"{where}.bin must be a whole number from 1 to 4"
    if "starred" in t and not isinstance(t["starred"], bool):
        return f"{where}.starred must be true or false"
    return None


def clean_template(t: dict, stored: dict | None) -> dict:
    """A client's template as it will be stored: server-owned fields
    (`aliases`, `ts_names`, `ts`) come from the stored template with the
    same id, and a rename pushes the old name onto `aliases`."""
    out = {
        "id": t["id"],
        "name": t["name"],
        "sub_s": t["sub_s"],
        "gain": _whole(t["gain"]),
        "offset": _whole(t["offset"]),
        "bin": _whole(t["bin"]),
        "starred": bool(t.get("starred")),
        "aliases": list(stored.get("aliases") or []) if stored else [],
        "ts_names": list(stored.get("ts_names") or []) if stored else [],
        "ts": copy.deepcopy(stored.get("ts") or {}) if stored else {},
    }
    if stored and stored.get("name") != out["name"]:
        out["aliases"] = push_alias(out["aliases"], stored.get("name"), out["name"])
    return out


def push_alias(aliases: list, old_name, new_name) -> list:
    """*old_name* at the front of *aliases*, newest first, at most 8, and
    never the current name."""
    out = [a for a in aliases if a != old_name and a != new_name]
    if isinstance(old_name, str) and old_name and old_name != new_name:
        out.insert(0, old_name)
    return out[:ALIASES_MAX]


def repair_stars(templates: list[dict]) -> None:
    """Exactly one starred template per filter that has templates: none
    starred stars the first, several keep the first."""
    if not templates:
        return
    seen = False
    for t in templates:
        if t.get("starred") and not seen:
            seen = True
        else:
            t["starred"] = False
    if not seen:
        templates[0]["starred"] = True


def wheel_slot_for(cam_wheel: dict | None, filt: str, pid: str | None = None):
    slots = ((cam_wheel or {}).get("filters") or {}).get(filt, {}).get("slots") or {}
    return slots.get(pid) if pid else next(iter(slots.values()), None)
