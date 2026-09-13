"""Sunlight-overlay model declarations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from nstt_course_planner.models.route import RunnerRouteSection


class SunlightCategory(Enum):
    """Visible-light categories based on the Sun's apparent altitude."""

    DARK = ("Dark", "dark", "ff000000")
    TWILIGHT = ("Twilight", "twilight", "ff800080")
    SUNRISE_SUNSET = ("Sunrise / sunset", "sunriseSunset", "ff008cff")
    DAYLIGHT = ("Daylight", "daylight", "ff00ffff")

    @property
    def display_name(self) -> str:
        return self.value[0]

    @property
    def style_id(self) -> str:
        return self.value[1]

    @property
    def kml_color(self) -> str:
        return self.value[2]


@dataclass(frozen=True)
class RunnerPace:
    """One runner's conservative working pace for simulation."""

    name: str
    minutes_per_mile: float

    @property
    def seconds_per_mile(self) -> float:
        return self.minutes_per_mile * 60


@dataclass(frozen=True)
class SunlightBuildConfig:
    """Inputs and assumptions for one sunlight-overlay build."""

    source_kml: Path
    pace_path: Path
    output_path: Path
    race_start: datetime
    half_segments_per_turn: int = 2
    transfer_duration_minutes: float | None = None


@dataclass(frozen=True)
class ScheduledSegment:
    """A route section with its simulated runner, timing, and sunlight."""

    section: RunnerRouteSection
    runner: RunnerPace
    start_time: datetime
    end_time: datetime
    solar_altitude_degrees: float
    sunlight: SunlightCategory
