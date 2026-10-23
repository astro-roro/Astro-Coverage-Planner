"""ACP's canonical filter names, for translating a TS filter or template name.

TS holds filter names as whatever Voyager or the wheel calls them ("O", "H",
"S"); ACP's own plans use "OIII", "Ha", "SII" and so on. This table mirrors
the alias list in two other places, and any change here should be checked
against both:

- the NINA plugin's `TsFilterNames.Canon` in
  `ACP.NINA.Plugin/Services/TargetScheduler/TsFilterNames.cs`
- ACP's `FILTER_CANON` in `scripts/build_archive_manifest.py`

Keys are matched upper-cased. A name the table does not know passes through
unchanged, same as ACP's archive scanner.
"""
from __future__ import annotations

FILTER_CANON: dict[str, str] = {
    "H": "Ha", "HA": "Ha", "HALPHA": "Ha", "H-ALPHA": "Ha", "H_ALPHA": "Ha",
    "O": "OIII", "O3": "OIII", "OIII": "OIII", "O-III": "OIII", "O_III": "OIII",
    "S": "SII", "S2": "SII", "SII": "SII", "S-II": "SII", "S_II": "SII",
    "L": "L", "LUM": "L", "LUMINANCE": "L", "LIGHT": "L", "CLEAR": "L",
    "R": "R", "RED": "R",
    "G": "G", "GREEN": "G",
    "B": "B", "BLUE": "B",
    "V": "V", "IR": "IR", "UV": "UV",
    "NOFILTER": "NoFilter", "NO FILTER": "NoFilter", "NO_FILTER": "NoFilter",
    "NONE": "NoFilter", "EMPTY": "NoFilter", "OPEN": "NoFilter",
}


def canonical_filter_name(name: str | None) -> str:
    """ACP's canonical spelling for *name*, or the trimmed input when the
    table does not know it."""
    trimmed = (name or "").strip()
    return FILTER_CANON.get(trimmed.upper(), trimmed)


def resolve_to_wheel(plan_filter: str | None, wheel_slots) -> str | None:
    """The wheel slot a filter should be written under, or None when no slot
    is the same filter (docs/specs/gear-templates.md section 2).

    An exact match wins, then a match ignoring case and surrounding spaces,
    then the first slot whose alias-table spelling equals the filter's. The
    order matters for a wheel with both an "H" and an "Ha" slot: a filter
    asking for "Ha" gets the "Ha" slot. Mirrors `TsFilterNames.ResolveToWheel`
    in the plugin; `tests/fixtures/wheel_resolve_cases.json` drives both.
    """
    if plan_filter is None or not str(plan_filter).strip() or wheel_slots is None:
        return None
    slots = [s for s in wheel_slots if isinstance(s, str) and s.strip()]
    for s in slots:
        if s == plan_filter:
            return s
    for s in slots:
        if s.strip().upper() == plan_filter.strip().upper():
            return s
    want = canonical_filter_name(plan_filter).upper()
    for s in slots:
        if canonical_filter_name(s).upper() == want:
            return s
    return None
