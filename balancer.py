"""The imaging balancer: decides Target Scheduler priorities once a day.

Spec: docs/specs/imaging-balancer.md. `decide` is a pure function with no
Flask and no file access: plans, month hours, commitment progress, settings,
memory and the night go in, and the list of changes, the new memory and the
report come out. The small file helpers at the bottom keep settings and
memory in balancer.json.
"""
from __future__ import annotations

import calendar
import copy
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROLES = ("science", "survey", "pretty")
MODES = ("off", "dry_run", "live")
PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2}
BLOCKED_STATES = ("closed", "draft")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")
EPS = 1e-9
RECENT_NIGHTS = 7

DEFAULT_SETTINGS = {
    "mode": "dry_run",
    "run_at": "14:00",
    "shares": {"science": 35, "survey": 35, "pretty": 30},
    "tolerance": 10,
    "min_month_hours": 5,
    "finish_by": 0.75,
}

DEFAULT_MEMORY = {
    "last_run_night": None,
    "last_attempt_night": None,
    "bands": {},
    "met_seen": {},
    "skipped_runs": 0,
    "open_items": [],
}


# --- Small helpers -----------------------------------------------------------

def month_name(month: str) -> str:
    return MONTH_NAMES[int(month[5:7]) - 1]


def _fmt_h(x: float) -> str:
    return f"{x:.1f}"


def _goal_h(x: float) -> str:
    return f"{round(x, 1):g}"


def _short_date(night: str) -> str:
    d = date.fromisoformat(night)
    return f"{d.day} {MONTH_NAMES[d.month - 1][:3]}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def project_key(plan: dict) -> str:
    """The TS project a plan belongs to. The same rule as `_build_ts_export`:
    its project name, else its target's name (each unnamed plan is its own
    project), else its id."""
    explicit = str(plan.get("project_name") or "").strip()
    if explicit:
        return explicit
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    return str(target.get("name") or "").strip() or str(plan.get("id") or "") or "Untitled"


def project_priority(plans: list[dict]) -> str:
    """What the writers will read: the highest priority in the group."""
    best = "low"
    seen = False
    for p in plans:
        value = p.get("priority")
        if value not in PRIORITY_RANK:
            value = "normal"
        if not seen or PRIORITY_RANK[value] > PRIORITY_RANK[best]:
            best, seen = value, True
    return best if seen else "normal"


# --- Settings ----------------------------------------------------------------

def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
        and value not in (float("inf"), float("-inf"))


def validate_settings(patch, current: dict) -> tuple[dict | None, str | None]:
    """The settings with `patch` applied, or (None, why not)."""
    if not isinstance(patch, dict):
        return None, "settings must be a JSON object"
    out = copy.deepcopy(current)
    for key, value in patch.items():
        if key == "mode":
            if value not in MODES:
                return None, "mode must be off, dry_run or live"
        elif key == "run_at":
            if not isinstance(value, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
                return None, "run_at must be a time as HH:MM"
        elif key == "shares":
            if not isinstance(value, dict) or set(value) != set(ROLES) \
                    or any(not _number(v) or v < 0 for v in value.values()):
                return None, "shares must give science, survey and pretty as numbers of 0 or more"
            if abs(sum(value.values()) - 100) > EPS:
                return None, "shares must add up to 100"
        elif key == "tolerance":
            if not _number(value) or not 0 <= value <= 50:
                return None, "tolerance must be a number from 0 to 50"
        elif key == "min_month_hours":
            if not _number(value) or value < 0:
                return None, "min_month_hours must be a number of 0 or more"
        elif key == "finish_by":
            if not _number(value) or not 0 < value <= 1:
                return None, "finish_by must be a number above 0 and up to 1"
        else:
            return None, f"unknown setting {key!r}"
        out[key] = copy.deepcopy(value)
    return out, None


# --- Schedule ----------------------------------------------------------------

def is_due(local_now: datetime, night: str, settings: dict, memory: dict) -> bool:
    """Whether the scheduler should run now: the mode is not off, local time
    is at or after run_at and before 23:00, and tonight has not run yet."""
    if settings.get("mode") == "off":
        return False
    hh, mm = (int(x) for x in settings["run_at"].split(":"))
    minutes = local_now.hour * 60 + local_now.minute
    if minutes < hh * 60 + mm or minutes >= 23 * 60:
        return False
    return night not in (memory.get("last_run_night"), memory.get("last_attempt_night"))


# --- Commitments -------------------------------------------------------------

def _canon_map(mapping, canon) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in (mapping.items() if isinstance(mapping, dict) else ()):
        if _number(v):
            key = canon(k) or k
            out[key] = out.get(key, 0.0) + float(v)
    return out


def _judge(plan: dict, c: dict, done: dict, canon, cur_month: str, elapsed: float,
           finish_by: float, met_seen: dict) -> dict:
    """One commitment's progress. `done` is hours per canonical filter for
    this plan, as the live page counts them."""
    goals = _canon_map(c.get("goals"), canon)
    baseline = _canon_map(c.get("baseline"), canon)
    counted = {k: g for k, g in goals.items() if k in done}
    ignored = sorted(k for k in goals if k not in done)
    month = c.get("month")
    out = {"plan_id": plan.get("id"), "month": month, "met_at": c.get("met_at"),
           "current": isinstance(month, str) and month <= cur_month,
           "skipped": not counted, "ignored": ignored}
    if not counted:
        return out
    progress = sum(min(g, max(0.0, float(done[k]) - baseline.get(k, 0.0)))
                   for k, g in counted.items())
    total = sum(counted.values())
    met_now = all(max(0.0, float(done[k]) - baseline.get(k, 0.0)) >= g - EPS
                  for k, g in counted.items())
    sticky = bool(c.get("met_at")) or f"{plan.get('id')}:{month}" in met_seen
    required = 1.0 if month < cur_month else min(1.0, elapsed / finish_by)
    frac = progress / total if total else 1.0
    met = met_now or sticky
    out.update({
        "goal_h": total, "done_h": progress, "met_now": met_now, "met": met,
        "required_frac": required, "required_h": required * total,
        "behind": (not met) and frac < required - EPS,
    })
    return out


# --- The decision ------------------------------------------------------------

def _band_for(role: str, share: float, target: float, tol: float, prev: str | None) -> str:
    if share > target + tol:
        return "over"
    if share < target - tol:
        return "under"
    if prev == "over" and share > target:
        return "over"
    if prev == "under" and share < target:
        return "under"
    return "in"


def decide(plans: list[dict], month_hours: dict, done_hours: dict, settings: dict,
           memory: dict, night: str, *, newest_report_night: str | None = None,
           keep_nights: int | None = None, canon=None, late: bool = False,
           recent_hours: dict | None = None) -> dict:
    """The whole decision for one run. `month_hours` is hours this month per
    project name, `done_hours` is current hours per canonical filter for each
    plan id (only plans with commitments need one). `late` says the run is
    after the nightly launcher (15:00), so its changes reach TS a night
    later. `recent_hours` is hours per project name over the last
    RECENT_NIGHTS nights; without it no role is treated as having a hole.
    Returns a dict, see the keys built at the bottom."""
    canon = canon or (lambda s: s)
    settings = {**DEFAULT_SETTINGS, **settings}
    memory = {**copy.deepcopy(DEFAULT_MEMORY), **(memory or {})}
    mode = settings["mode"]
    cur_month = night[:7]
    day = date.fromisoformat(night)
    dim = calendar.monthrange(day.year, day.month)[1]
    elapsed = (day.day - 1) / dim
    finish_by = settings["finish_by"]
    warnings: list[str] = []

    groups: dict[str, list[dict]] = {}
    for p in plans:
        if isinstance(p, dict):
            groups.setdefault(project_key(p), []).append(p)

    info: dict[str, dict] = {}
    for name, group in groups.items():
        roles = {p.get("balance_role") for p in group if p.get("balance_role") in ROLES}
        if not roles:
            continue
        info[name] = {
            "name": name, "plans": group,
            "role": next(iter(roles)) if len(roles) == 1 else None,
            "hold": any(p.get("balance_hold") is True for p in group),
            "blocked": any(p.get("state") in BLOCKED_STATES for p in group),
            "hours": float(month_hours.get(name) or 0.0),
        }

    # Commitments, judged for every project the balancer can see.
    commitments: list[dict] = []
    for name in sorted(info):
        for p in info[name]["plans"]:
            listed = p.get("commitments")
            for c in listed if isinstance(listed, list) else []:
                if not isinstance(c, dict) or c.get("kind", "vote") != "vote":
                    continue
                if not (isinstance(c.get("month"), str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", c["month"])):
                    warnings.append(
                        f"{name}: a vote commitment has no usable month, so the balancer skipped it.")
                    continue
                j = _judge(p, c, done_hours.get(p.get("id")) or {}, canon, cur_month,
                           elapsed, finish_by, memory["met_seen"])
                j["project"] = name
                if j["skipped"]:
                    if j["current"]:
                        warnings.append(
                            f"{name}: the {month_name(j['month'])} vote commitment names no "
                            "filter the plan can count, so the balancer ignored it.")
                elif j["ignored"]:
                    warnings.append(
                        f"{name}: the {month_name(j['month'])} vote commitment names "
                        f"{', '.join(j['ignored'])}, which the plan cannot count, so those "
                        "filters were left out of its progress.")
                commitments.append(j)
    live_commitments = [j for j in commitments if j["current"] and not j["skipped"]]
    for name, pi in info.items():
        mine = [j for j in live_commitments if j["project"] == name]
        pi["unmet"] = [j for j in mine if not j["met"]]
        pi["current"] = mine
    urgent = any(pi["unmet"] for pi in info.values())

    # Shares and bands.
    role_hours = {r: 0.0 for r in ROLES}
    role_projects = {r: 0 for r in ROLES}
    for pi in info.values():
        if pi["role"]:
            role_hours[pi["role"]] += pi["hours"]
            role_projects[pi["role"]] += 1
    present = [r for r in ROLES if role_projects[r]]
    total = sum(role_hours[r] for r in present)
    share_sum = sum(settings["shares"][r] for r in present) or 1
    guard = total < settings["min_month_hours"]
    targets = {r: settings["shares"][r] * 100.0 / share_sum for r in present}
    pcts = {r: role_hours[r] / total * 100.0 if total > 0 else 0.0 for r in present}

    # A role with a hole: nothing in it was up on recent nights, so it hands
    # its unused share to the other roles, evenly. Weather is not a hole: when
    # no role imaged recently, nobody is treated as having one.
    holes: dict[str, float] = {}
    nothing_up = False
    base_targets = dict(targets)
    if recent_hours is not None and not guard:
        recent = {r: 0.0 for r in ROLES}
        up = {r: False for r in ROLES}
        for pi in info.values():
            if pi["role"]:
                h = float(recent_hours.get(pi["name"]) or 0.0)
                recent[pi["role"]] += h
                if h > EPS and not pi["blocked"] and not any(
                        p.get("state") == "inactive" for p in pi["plans"]):
                    up[pi["role"]] = True
        empty = [r for r in present if not up[r]]
        others = [r for r in present if up[r]]
        nothing_up = bool(present) and not others
        if others:
            for r in empty:
                if pcts[r] < targets[r] - EPS:
                    holes[r] = targets[r] - pcts[r]
            for r in empty:
                if r in holes:
                    targets[r] = pcts[r]
            spare = sum(holes.values())
            for r in others:
                targets[r] += spare / len(others)
    hole_notes: list[str] = []
    if nothing_up:
        hole_notes.append("Nothing up in any role, so shares were not rebalanced.")
    if holes:
        others = [r for r in present if r not in holes]
        names = ", ".join(r.capitalize() for r in holes)
        verb = "has" if len(holes) == 1 else "have"
        given = ", ".join(f"{r} {targets[r]:.1f}" for r in others)
        hole_notes.append(
            f"{names} {verb} nothing up, so its spare {sum(holes.values()):.0f} points are split: {given}."
            if len(holes) == 1 else
            f"{names} {verb} nothing up, so their spare {sum(holes.values()):.0f} points go to: {given}.")

    shares: dict[str, dict] = {}
    bands: dict[str, str] = {}
    for r in present:
        target = targets[r]
        pct = pcts[r]
        band = "in" if guard else _band_for(
            r, pct, target, settings["tolerance"], memory["bands"].get(r))
        bands[r] = band
        shares[r] = {"hours": round(role_hours[r], 3), "share": round(pct, 2),
                     "target": round(target, 2), "band": band}
        if holes:
            shares[r]["base_target"] = round(base_targets[r], 2)
            shares[r]["hole"] = r in holes

    # Rules.
    changes: list[dict] = []
    for name in sorted(info):
        pi = info[name]
        if pi["hold"] or pi["blocked"] or pi["role"] is None:
            if pi["role"] is None:
                warnings.append(
                    f"{name}: its plans disagree on balancer role, so the balancer left it alone.")
            continue
        role = pi["role"]
        band = bands[role]
        want = rule = why = None
        if pi["unmet"]:
            j = pi["unmet"][0]
            want, rule = "high", 2
            pace = "behind the pace" if j["behind"] else "on the pace"
            why = (f"{month_name(j['month'])} vote winner has {_fmt_h(j['done_h'])} of "
                   f"{_goal_h(j['goal_h'])} h, {pace} of {_goal_h(j['required_h'])} h.")
            note = f"the {month_name(j['month'])} vote goal is not covered yet."
        elif pi["current"]:
            j = pi["current"][0]
            want, rule = "low", 3
            why = f"The {month_name(j['month'])} vote goal is covered."
            note = f"the {month_name(j['month'])} vote goal is covered."
        elif role == "science":
            rule = 4
            if band == "over":
                want = "low" if urgent else "normal"
            else:
                want = "normal" if urgent else "high"
        elif role == "survey":
            rule = 5
            want = "low" if (urgent or band == "over") else "normal"
        else:
            rule = 6
            want = "normal" if (band == "under" and not urgent) else "low"
        if rule >= 4:
            label = role.capitalize()
            if urgent:
                why = "The vote winner comes first until its goal is covered."
                note = "the vote winner comes first until its goal is covered."
            elif band == "over":
                why = f"{label} is over its share of the month."
                note = f"{label.lower()} is over its share of the month."
            elif band == "under":
                why = f"{label} is under its share of the month."
                note = f"{label.lower()} is under its share of the month."
            else:
                why = f"{label} is within its share of the month."
                note = f"{label.lower()} is within its share of the month."
        if nothing_up and rule >= 4:
            continue
        was = project_priority(pi["plans"])
        if was != want:
            changes.append({"project": name, "field": "priority", "from": was, "to": want,
                            "rule": rule, "reason": why, "note": note})
    changes.sort(key=lambda c: (c["rule"], c["project"].lower()))

    # Newly met commitments.
    met_new = []
    for j in live_commitments:
        sticky_seen = f"{j['plan_id']}:{j['month']}" in memory["met_seen"]
        if not j["met"] or j["met_at"]:
            continue
        if mode == "live" or (j["met_now"] and not sticky_seen):
            met_new.append({"plan_id": j["plan_id"], "month": j["month"], "project": j["project"]})
    met_seen = dict(memory["met_seen"])
    if mode != "live":
        for m in met_new:
            met_seen[f"{m['plan_id']}:{m['month']}"] = night

    # Needs-you items.
    wanted: dict[str, dict] = {}
    open_before = set(memory["open_items"])
    for j in live_commitments:
        if j["met"]:
            continue
        m = month_name(j["month"])
        base = f"{j['plan_id']}:{j['month']}"
        figures = f"{_fmt_h(j['done_h'])} of {_goal_h(j['goal_h'])} h"
        short_key, missed_key = f"balancer_short:{base}", f"balancer_missed:{base}"
        if j["month"] == cur_month and elapsed >= finish_by - EPS or short_key in open_before:
            wanted[short_key] = {
                "key": short_key, "plan_id": j["plan_id"], "month": j["month"],
                "title": f"{j['project']} is short of its {m} vote goal with a week to go: {figures}"}
        if j["month"] < cur_month:
            wanted[missed_key] = {
                "key": missed_key, "plan_id": j["plan_id"], "month": j["month"],
                "title": f"{j['project']} missed its {m} vote goal: {figures}"}
    items_open = [w for k, w in wanted.items() if k not in open_before]
    items_resolve = sorted(open_before - set(wanted))

    # Warnings that come from the inputs.
    if newest_report_night:
        try:
            age = (day - date.fromisoformat(newest_report_night)).days
        except ValueError:
            age = 0
        if age >= 4:
            warnings.append(f"last progress report {age} nights old, so hours may be stale.")
    if keep_nights is not None and keep_nights < 31:
        warnings.append(
            f"Activity keeps only {keep_nights} nights, so this month's hours may be undercounted.")

    # Report.
    dry = mode != "live"
    n = len(changes)
    if n:
        title = (f"Balancer (dry run): would change {_plural(n, 'project')}" if dry
                 else f"Balancer: changed {_plural(n, 'project')}")
    else:
        title = "Balancer (dry run): no changes" if dry else "Balancer: no changes"
    lines = [f"{c['project']}: priority {c['from']} to {c['to']}. {c['reason']}" for c in changes]
    for j in sorted(commitments, key=lambda j: (j["project"].lower(), str(j["month"]))):
        if not j["current"] or j["skipped"]:
            continue
        m = month_name(j["month"])
        if j["met"]:
            when = j["met_at"] or met_seen.get(f"{j['plan_id']}:{j['month']}") \
                or (night if j["met_now"] else None)
            lines.append(f"{j['project']}: {m} vote goal met" + (f" {_short_date(when[:10])}." if when else "."))
        else:
            pace = (f"behind pace, needs {_goal_h(j['required_h'])} h" if j["behind"]
                    else "on pace")
            lines.append(f"{j['project']}: {m} vote goal {_fmt_h(j['done_h'])} of "
                         f"{_goal_h(j['goal_h'])} h, {pace}.")
    words = {"over": "over", "under": "under", "in": "in range"}
    lines.append(" ".join(
        f"{r.capitalize()} {_fmt_h(shares[r]['hours'])} h, {shares[r]['share']:.0f} percent, "
        f"{words[shares[r]['band']]} (target {shares[r]['target']:g})."
        for r in present))
    if guard:
        lines.append(f"Under {_goal_h(settings['min_month_hours'])} h this month, so no share is judged yet.")
    lines.extend(hole_notes)
    lines.extend(warnings)
    if late and not dry and changes:
        lines.append("This run was after 15:00, so its changes reach TS at the next night's sync.")

    data = {
        "mode": mode, "urgent": urgent, "elapsed": round(elapsed, 4),
        "holes": sorted(holes), "nothing_up": nothing_up, "shares": shares, "bands": bands,
        "commitments": [{"project": j["project"], "plan_id": j["plan_id"], "month": j["month"],
                         "done_h": round(j["done_h"], 3), "goal_h": round(j["goal_h"], 3),
                         "required_h": round(j["required_h"], 3), "met": j["met"],
                         "behind": j["behind"]}
                        for j in live_commitments],
        "changes": [{k: c[k] for k in ("project", "field", "from", "to", "rule")} for c in changes],
    }
    new_memory = {
        **memory, "bands": bands, "met_seen": met_seen, "open_items": sorted(wanted),
    }
    return {
        "mode": mode, "night": night, "changes": changes, "met_new": met_new,
        "memory": new_memory, "urgent": urgent, "shares": shares, "bands": bands,
        "warnings": warnings, "items_open": items_open, "items_resolve": items_resolve,
        "title": title, "detail": "\n".join(lines), "data": data,
        "severity": "warn" if warnings else "info",
    }


# --- balancer.json -----------------------------------------------------------

def load_state(path: Path) -> dict:
    """Settings and memory from balancer.json, defaults filled in. A missing
    or unreadable file gives the defaults."""
    raw: dict = {}
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw = loaded
    except (OSError, ValueError):
        pass
    settings, err = validate_settings(raw.get("settings") or {}, copy.deepcopy(DEFAULT_SETTINGS))
    if settings is None:
        settings = copy.deepcopy(DEFAULT_SETTINGS)
    memory = copy.deepcopy(DEFAULT_MEMORY)
    stored = raw.get("memory")
    if isinstance(stored, dict):
        memory.update({k: stored[k] for k in memory if k in stored})
    return {"settings": settings, "memory": memory}


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)
