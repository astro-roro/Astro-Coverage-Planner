"""Public type contracts for coverage sources.

This module defines the public type contracts for coverage sources.
Implementations live elsewhere (app.py for the built-in manifest source;
out-of-tree extensions for everything else). Kept import-cheap on purpose:
no Flask, no astropy, no astroquery — extensions and the app both import
this. See docs/extensions.md (TODO) for authoring.
"""

from typing import TYPE_CHECKING, Iterable, Literal, Optional, Protocol, TypedDict, Union

if TYPE_CHECKING:
    from mocpy import MOC  # noqa: F401  # forward decl only — keeps import-cheap

__all__ = [
    "CoverageSource",
    "CoverageRegion",
    "PolygonCoverage",
    "MocCoverage",
    "FilterCoverage",
    "SourceMetadata",
    "BandStatus",
    "TileCell",
    "PrioritisedTilesSource",
    "CatalogObject",
    "CategorisedCatalogSource",
]


class FilterCoverage(TypedDict):
    """Per-filter accounting for a single region.

    `hours` is integration time accumulated; `files` is the count of
    contributing frames. Both are renderer-facing summaries.
    """

    hours: float
    files: int


# Polygon and MOC are kept as separate variants rather than a single
# shape-with-optional-fields because the renderer dispatches on `kind`
# and the geometry payloads have nothing structural in common: a polygon
# is an ordered vertex list in ICRS degrees, a MOC is an opaque
# serialised blob the frontend hands to a MOC library.
class PolygonCoverage(TypedDict, total=False):
    """A region described as an ordered ICRS polygon.

    `vertices` is a list of `(ra_deg, dec_deg)` pairs. `name` and
    `metadata` are optional carry-through fields.
    """

    kind: Literal["polygon"]
    vertices: list[tuple[float, float]]
    filters: dict[str, FilterCoverage]
    name: str
    metadata: dict


class MocCoverage(TypedDict, total=False):
    """A region described as a serialised MOC.

    `moc_serialised` is representation-agnostic at the protocol level
    (base64, FITS-as-string, etc.); the consumer decides how to parse.
    """

    kind: Literal["moc"]
    moc_serialised: str
    filters: dict[str, FilterCoverage]
    name: str
    metadata: dict


# Tagged union: dispatch on the `kind` field.
CoverageRegion = Union[PolygonCoverage, MocCoverage]


class SourceMetadata(TypedDict):
    """Display and grouping metadata for a coverage source."""

    label: str
    color: str
    kind: str
    attribution: str
    enabled_default: bool


class CoverageSource(Protocol):
    """Duck-typed contract for any coverage provider.

    One interface covers both survey footprints and per-target archives;
    the difference between them is data-shaped (which `CoverageRegion`
    variant they yield), not interface-shaped. Extensions need not
    inherit from this — structural typing is enough.
    """

    def id(self) -> str:
        """Stable unique identifier; used as cache key and URL component.

        Any non-empty string; lowercase ASCII recommended.
        """
        ...

    def metadata(self) -> SourceMetadata:
        """Return display metadata for this source."""
        ...

    def coverage(self) -> Iterable[CoverageRegion]:
        """Yield coverage regions.

        Idempotent modulo underlying manifest mtime. No side effects and
        no network on the hot path — caching is the source's job.
        """
        ...

    # Phase 4 fast path: hand the gap-finder a single MOC for one filter
    # rather than making it re-derive one from polygon iteration on every
    # call. Implementations may cache. Returns None when:
    #   - mocpy isn't importable in this process,
    #   - the source has no coverage at `filter_name`,
    #   - or the source can't synthesise a MOC (e.g. cache file absent and
    #     the implementation refuses to trigger network from this path).
    def coverage_moc(self, filter_name: str) -> "Optional[MOC]":
        ...


# --- Prioritised tiles / inventory sources --------------------------------
# Distinct from CoverageSource: a tiles source publishes a curated, ranked
# list of "interesting cells on the sky" with per-band coverage status,
# rather than raw observed footprints. Use this when a third party (or an
# extension) has done the prioritisation work upstream and the renderer
# only needs to display it. The renderer dispatches on type, not on `kind`,
# so PrioritisedTilesSource is a separate Protocol.

class BandStatus(TypedDict, total=False):
    """Status of a single band for one tile.

    All fields optional — `covered` is the only one renderers must
    interpret. `source` is a free-form label (e.g. "personal", "external",
    "survey:foo") so the consumer can group/filter without baking in a
    fixed source taxonomy. `quality` is an ordinal rank — higher = better
    — useful for "is my own future imaging worth pointing here?" filters.
    `hours` is integration time when applicable.
    """

    covered: bool
    source: str
    quality: int
    hours: float


class TileCell(TypedDict, total=False):
    """One curated sky cell ready to be plotted and filtered.

    Geometry: `(ra_deg, dec_deg)` is the centre, `footprint` is an
    ordered ICRS polygon. If `footprint` is omitted, the renderer falls
    back to a square box derived from `fov_arcmin`.

    Ranking: `priority_level` is an ordinal — 1 = most urgent — that
    drives the colour bucket. `score` is the within-bucket sort key.

    Filtering: `per_band` lets the inventory panel build "missing band
    X" filters; `category_counts` gives per-class chip filters
    ("only cells with at least one PN candidate"). Extensions can
    stuff arbitrary extras into `metadata`.
    """

    id: str
    ra_deg: float
    dec_deg: float
    footprint: list[tuple[float, float]]
    fov_arcmin: tuple[float, float]
    priority_level: int
    score: float
    per_band: dict[str, BandStatus]
    category_counts: dict[str, int]
    metadata: dict


class PrioritisedTilesSource(Protocol):
    """A source of curated, ranked sky cells with per-band coverage.

    Distinct from CoverageSource because the rendering and filtering UI
    are different shapes (cells with priority + per-band status vs raw
    footprint regions). Extensions register an instance via
    `app.tile_sources.append(...)`.
    """

    def id(self) -> str:
        ...

    def metadata(self) -> SourceMetadata:
        ...

    def tiles(self) -> Iterable[TileCell]:
        """Yield ranked cells. Idempotent modulo upstream data mtime."""
        ...


# --- Categorised catalogue sources ----------------------------------------
# Generic point-catalogue with per-object class tags. Replaces the
# hard-coded Green/SMGPS/EMU/WISE/Messier/Sharpless/ESO catalogue handling
# with a registry-driven model where any extension can publish a class-
# tagged list of objects and get the same chip-filter UI for free.

class CatalogObject(TypedDict, total=False):
    """One point object in a categorised catalogue.

    `category` is a free-form string (e.g. "PNe", "HII", "SNR"); the
    renderer groups by it for chip filters. `tags` is an optional
    secondary dimension — arbitrary string labels per object that the
    cross-catalogue Object filter exposes alongside category chips
    (e.g. ``["needs-work"]`` for a PNe lacking some filter coverage).
    `metadata` carries any extras the source wants to expose in
    tooltips (frequency, flag, diameter, …).
    """

    name: str
    ra_deg: float
    dec_deg: float
    category: str
    tags: list
    metadata: dict


class CategorisedCatalogSource(Protocol):
    """A source of class-tagged point objects.

    Implementations declare which categories they publish so the rail
    can render chip filters before the catalogue data has finished
    loading. Extensions register via `app.catalog_sources.append(...)`.
    """

    def id(self) -> str:
        ...

    def metadata(self) -> SourceMetadata:
        ...

    def categories(self) -> list[str]:
        """Ordered list of category labels this source publishes."""
        ...

    def objects(self) -> Iterable[CatalogObject]:
        """Yield catalogue objects. Idempotent modulo upstream mtime."""
        ...
