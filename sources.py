"""Public type contracts for coverage sources.

This module defines the public type contracts for coverage sources.
Implementations live elsewhere (app.py for the built-in manifest source;
out-of-tree extensions for everything else). Kept import-cheap on purpose:
no Flask, no astropy, no astroquery — extensions and the app both import
this. See docs/extensions.md (TODO) for authoring.
"""

from typing import Iterable, Literal, Protocol, TypedDict, Union

__all__ = [
    "CoverageSource",
    "CoverageRegion",
    "PolygonCoverage",
    "MocCoverage",
    "FilterCoverage",
    "SourceMetadata",
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
