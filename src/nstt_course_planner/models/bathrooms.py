"""Bathroom-planning model declarations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class BathroomCategory(Enum):
    """Stop categories in the team's required planning order."""

    OFFICIAL_BEACH = (1, "Official beach restroom", "beachRestroom", "🚻")
    PLACES_PUBLIC = (1, "Places-listed public restroom", "placesRestroom", "🚻")
    GROCERY = (2, "Grocery-store backup", "groceryBackup", "🛒")
    COFFEE = (3, "Coffee-chain backup", "coffeeBackup", "☕")
    FAST_FOOD_OR_GAS = (4, "Fast-food or branded-gas backup", "businessBackup", "⛽")

    @property
    def priority(self) -> int:
        return self.value[0]

    @property
    def display_name(self) -> str:
        return self.value[1]

    @property
    def style_id(self) -> str:
        return self.value[2]

    @property
    def symbol(self) -> str:
        return self.value[3]


@dataclass(frozen=True)
class BathroomStop:
    """A researched restroom or lower-priority business fallback."""

    name: str
    category: BathroomCategory
    latitude: float
    longitude: float
    address: str
    source_url: str
    source_note: str


@dataclass(frozen=True)
class CarAccessConstraint:
    """A runner-mile interval where the support car cannot make a bathroom stop."""

    start_miles: float
    end_miles: float
    label: str


@dataclass(frozen=True)
class BathroomWindow:
    """One rolling restroom opportunity selected near a fixed runner-mile target."""

    target_miles: float
    actual_runner_miles: float
    straight_line_meters: float
    stop: BathroomStop
    constraint_note: str | None = None
    is_primary: bool = True
    eta: datetime | None = None


@dataclass(frozen=True)
class CoverageGap:
    """An interval without a researched bathroom option near its midpoint."""

    start_miles: float
    end_miles: float

    @property
    def length_miles(self) -> float:
        return self.end_miles - self.start_miles


@dataclass(frozen=True)
class BathroomLayerBuildResult:
    """A generated layer and the intervals that still need field verification."""

    stop_count: int
    gaps: tuple[CoverageGap, ...]


@dataclass(frozen=True)
class PlacesBathroomBuildConfig:
    """Inputs and local state for one Places bathroom discovery pass."""

    source_kml: Path
    output_kml: Path
    cache_path: Path
    usage_path: Path
    anchor_spacing_meters: float
    search_radius_meters: float
    max_route_distance_meters: float
    dry_run: bool = False
