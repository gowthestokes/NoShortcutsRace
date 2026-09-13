"""Route and organizer-sheet model declarations."""

from __future__ import annotations

from dataclasses import dataclass

Coordinate = tuple[float, float]


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
    coordinates: tuple[Coordinate, ...]


@dataclass(frozen=True)
class ApprovedRouteProgress:
    """User-edited route sections whose geometry is authoritative on rebuild."""

    sections: tuple[RunnerRouteSection, ...]
    through_segment: int


@dataclass(frozen=True)
class RouteProximity:
    """The nearest position on runner geometry, excluding support-car gaps."""

    runner_miles: float
    straight_line_meters: float


@dataclass(frozen=True)
class RouteCheckpoint:
    """A named location used to shape the runner route."""

    label: str
    query: str


@dataclass(frozen=True)
class Instruction:
    """A direction or operational instruction from the organizer turn sheet."""

    role: str
    action: str
    road_or_place: str
    checkpoint_label: str | None = None


@dataclass(frozen=True)
class RaceRouteSpec:
    """Immutable organizer route sheet consumed by the course builder."""

    runner_instructions: tuple[Instruction, ...]
    route_checkpoints: tuple[RouteCheckpoint, ...]
    operational_notes: tuple[str, ...]

    def checkpoint(self, label: str) -> RouteCheckpoint:
        """Return a named organizer checkpoint or raise a useful error."""
        try:
            return next(
                point for point in self.route_checkpoints if point.label == label
            )
        except StopIteration as error:
            raise KeyError(f"Unknown organizer checkpoint: {label}") from error
