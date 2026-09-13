"""Elevation-layer model declarations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nstt_course_planner.models.route import Coordinate, RunnerRouteSection


@dataclass(frozen=True)
class DistanceSample:
    """One coordinate at a known distance along a continuous runner run."""

    coordinate: Coordinate
    distance_meters: float


@dataclass(frozen=True)
class ElevationProfile:
    """Terrain elevations and their distance-window-smoothed equivalents."""

    samples: tuple[DistanceSample, ...]
    elevation_meters: tuple[float, ...]
    smoothed_elevation_meters: tuple[float, ...]

    def smoothed_at(self, distance_meters: float) -> float:
        if not self.samples:
            raise RuntimeError("Cannot read elevation from an empty profile.")
        if distance_meters <= self.samples[0].distance_meters:
            return self.smoothed_elevation_meters[0]
        if distance_meters >= self.samples[-1].distance_meters:
            return self.smoothed_elevation_meters[-1]
        for left_index, right_sample in enumerate(self.samples[1:], start=1):
            if distance_meters <= right_sample.distance_meters:
                left_sample = self.samples[left_index - 1]
                fraction = (distance_meters - left_sample.distance_meters) / (
                    right_sample.distance_meters - left_sample.distance_meters
                )
                return self.smoothed_elevation_meters[left_index - 1] + fraction * (
                    self.smoothed_elevation_meters[left_index]
                    - self.smoothed_elevation_meters[left_index - 1]
                )
        raise AssertionError("Distance lookup should return within the profile range.")


@dataclass(frozen=True)
class ElevatedRouteSection:
    """An editable runner segment with terrain elevation summary information."""

    section: RunnerRouteSection
    distance_meters: float
    start_elevation_meters: float
    end_elevation_meters: float
    average_grade_percent: float
    start_runner_miles: float
    end_runner_miles: float

    @property
    def net_elevation_meters(self) -> float:
        return self.end_elevation_meters - self.start_elevation_meters


@dataclass(frozen=True)
class ElevationBuildConfig:
    """Inputs and local state used for one elevation-overlay build."""

    source_kml: Path
    output_dir: Path
    cache_path: Path
    usage_path: Path
    sample_spacing_meters: float
    smoothing_meters: float
    dry_run: bool = False
