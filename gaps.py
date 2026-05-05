"""Multi-source coverage gap-finder.

Pure functions that compute the sky region where a "have" filter is covered
but a "missing" filter is not, across a configurable subset of coverage
sources, by unioning per-source MOCs and differencing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

try:
    from mocpy import MOC
    _MOCPY_AVAILABLE = True
except ImportError:
    MOC = None  # type: ignore[assignment,misc]
    _MOCPY_AVAILABLE = False

if TYPE_CHECKING:
    from sources import CoverageSource


__all__ = [
    "GapResult",
    "GapCandidate",
    "compute_gap_moc",
    "candidates_in_moc",
]


@dataclass(frozen=True)
class GapResult:
    gap_moc: "MOC | None"           # None if mocpy missing or no overlap
    have_sources: list[str]         # source ids that contributed to the have side
    missing_sources: list[str]      # source ids that contributed to the missing side
    gap_sky_fraction: float         # 0.0..1.0 — gap area / full sky
    skipped: list[tuple[str, str]]  # [(source_id, reason)] for sources that couldn't contribute


@dataclass(frozen=True)
class GapCandidate:
    catalog: str          # e.g. "green_snrs"
    name: str             # e.g. "G34.7-0.4"
    ra_deg: float
    dec_deg: float


def compute_gap_moc(
    sources: Iterable["CoverageSource"],
    *,
    have_filter: str,
    missing_filter: str,
    source_ids: list[str] | None = None,
) -> GapResult:
    """Return where `have_filter` is covered AND `missing_filter` is NOT.

    Unions per-source MOCs on each side of the question, then differences:
    gap = union(have_filter coverage) - union(missing_filter coverage).
    """
    if not _MOCPY_AVAILABLE:
        return GapResult(
            gap_moc=None,
            have_sources=[],
            missing_sources=[],
            gap_sky_fraction=0.0,
            skipped=[("<all>", "mocpy not available")],
        )

    selected = list(sources)
    if source_ids is not None:
        wanted = set(source_ids)
        selected = [s for s in selected if s.id() in wanted]

    have_mocs: list = []
    missing_mocs: list = []
    have_ids: list[str] = []
    missing_ids: list[str] = []
    skipped: list[tuple[str, str]] = []

    for src in selected:
        sid = src.id()
        # A source might lack coverage_moc entirely if it's an old extension
        # written against the pre-Phase-4 Protocol. Don't crash — skip it.
        getter = getattr(src, "coverage_moc", None)
        if getter is None:
            skipped.append((sid, "source has no coverage_moc()"))
            continue
        h = getter(have_filter)
        m = getter(missing_filter)
        if h is None and m is None:
            skipped.append((sid, f"no coverage at {have_filter!r} or {missing_filter!r}"))
            continue
        if h is not None:
            have_mocs.append(h)
            have_ids.append(sid)
        if m is not None:
            missing_mocs.append(m)
            missing_ids.append(sid)

    if not have_mocs:
        return GapResult(
            gap_moc=None,
            have_sources=[],
            missing_sources=missing_ids,
            gap_sky_fraction=0.0,
            skipped=skipped,
        )

    have_union = have_mocs[0] if len(have_mocs) == 1 \
        else have_mocs[0].union(*have_mocs[1:])

    if missing_mocs:
        missing_union = missing_mocs[0] if len(missing_mocs) == 1 \
            else missing_mocs[0].union(*missing_mocs[1:])
        # mocpy 0.20: MOC.difference() collapses to empty when the operands are
        # fully disjoint (verified against installed 0.20.0 — overlap case is
        # correct, disjoint case returns 0). intersection(complement()) gives
        # the right answer for both, so we use that.
        gap = have_union.intersection(missing_union.complement())
    else:
        # No missing-side coverage anywhere → the entire have-side is gap.
        gap = have_union

    return GapResult(
        gap_moc=gap,
        have_sources=have_ids,
        missing_sources=missing_ids,
        gap_sky_fraction=float(gap.sky_fraction),
        skipped=skipped,
    )


def candidates_in_moc(
    moc: "MOC",
    catalogs_data: dict,
) -> list[GapCandidate]:
    """Every catalog entry whose (ra, dec) lies inside `moc`.

    Vectorised per catalog — one contains_lonlat call per catalog regardless
    of entry count. catalogs_data maps catalog-name → list-of-entry-dicts;
    each entry needs `name`, `ra_deg`, `dec_deg`.
    """
    if moc is None or not _MOCPY_AVAILABLE:
        return []

    import numpy as np
    import astropy.units as u

    out: list[GapCandidate] = []
    for catalog_name, entries in (catalogs_data or {}).items():
        if not entries:
            continue
        ras = np.array([float(e["ra_deg"]) for e in entries])
        decs = np.array([float(e["dec_deg"]) for e in entries])
        mask = moc.contains_lonlat(ras * u.deg, decs * u.deg)
        for entry, hit in zip(entries, mask):
            if hit:
                out.append(GapCandidate(
                    catalog=catalog_name,
                    name=str(entry["name"]),
                    ra_deg=float(entry["ra_deg"]),
                    dec_deg=float(entry["dec_deg"]),
                ))
    return out
