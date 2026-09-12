"""Domain models and errors shared by course-builder services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Checkpoint:
    """A route-shaping point taken from the organizer's published turn list."""

    label: str
    query: str


@dataclass(frozen=True)
class Point:
    label: str
    query: str
    latitude: float
    longitude: float
    display_name: str


@dataclass(frozen=True)
class RunnerRouteSection:
    """One independently editable runner-route line for the KML export."""

    label: str
    coordinates: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ApprovedRouteProgress:
    """User-edited route sections whose geometry is authoritative on rebuild."""

    sections: tuple[RunnerRouteSection, ...]
    through_segment: int


@dataclass(frozen=True)
class CourseBuildConfig:
    """Filesystem inputs and optional manual-progress settings for one build."""

    output_dir: Path
    geocoding_cache_path: Path
    routing_cache_path: Path
    path_cache_path: Path
    google_usage_path: Path
    approved_segments_kml: Path | None = None
    approved_through_segment: int | None = None
    resume_at_checkpoint: str = "Del Prado / Golden Lantern"
    source_of_truth_kml: Path | None = None
    source_prefix_through_segment: int | None = None
    source_prefix_end_marker: str = "San Mateo Point"
    normalized_source_kml: Path | None = None
    official_directions_kml: Path | None = None


class UnsafePedestrianRouteError(RuntimeError):
    """Raised when routing proposes a non-running transport mode."""


class GoogleRoutesRequestLimitError(RuntimeError):
    """Raised before the planner exceeds its conservative Google request cap."""


class GoogleElevationSampleLimitError(RuntimeError):
    """Raised before elevation sampling exceeds the local conservative cap."""
