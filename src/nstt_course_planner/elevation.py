"""Elevation sampling, grade analysis, and KML export for the finalized route."""

from __future__ import annotations

import json
import xml.sax.saxutils
from collections.abc import Mapping
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import ClassVar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from nstt_course_planner.config import (
    GOOGLE_ELEVATION_BATCH_SIZE,
    GOOGLE_ELEVATION_URL,
    MILE_METERS,
    USER_AGENT,
)
from nstt_course_planner.geometry import RouteGeometry
from nstt_course_planner.models.elevation import (
    DistanceSample,
    ElevatedRouteSection,
    ElevationProfile,
)
from nstt_course_planner.models.route import Coordinate, RunnerRouteSection
from nstt_course_planner.storage import GoogleElevationUsageTracker, JsonStore


class RouteGeometrySampler:
    """Samples a continuous route run at fixed-distance intervals."""

    @staticmethod
    def sample(
        coordinates: list[Coordinate],
        spacing_meters: float,
    ) -> tuple[DistanceSample, ...]:
        if spacing_meters <= 0:
            raise ValueError("Elevation sample spacing must be greater than zero.")
        if len(coordinates) < 2:
            raise RuntimeError(
                "A runner route run needs at least two coordinates for elevation sampling.",
            )
        samples = [DistanceSample(coordinates[0], 0.0)]
        distance_so_far = 0.0
        next_sample_distance = spacing_meters
        for start, end in pairwise(coordinates):
            edge_meters = RouteGeometry.haversine_meters(start, end)
            if edge_meters == 0:
                continue
            while next_sample_distance <= distance_so_far + edge_meters + 1e-6:
                fraction = (next_sample_distance - distance_so_far) / edge_meters
                samples.append(
                    DistanceSample(
                        RouteGeometry.interpolate(start, end, fraction),
                        next_sample_distance,
                    ),
                )
                next_sample_distance += spacing_meters
            distance_so_far += edge_meters
        if samples[-1].coordinate != coordinates[-1]:
            samples.append(DistanceSample(coordinates[-1], distance_so_far))
        return tuple(samples)


class GoogleElevationClient:
    """Caches Google Elevation values and reserves all uncached samples first."""

    def __init__(
        self,
        cache: dict[str, dict[str, object]],
        cache_path: Path,
        api_key: str,
        usage: dict[str, object],
        usage_path: Path,
    ) -> None:
        self.cache = cache
        self.cache_path = cache_path
        self.api_key = api_key
        self.usage = usage
        self.usage_path = usage_path

    @staticmethod
    def cache_key(coordinate: Coordinate) -> str:
        latitude, longitude = coordinate
        return f"{latitude:.7f},{longitude:.7f}"

    def uncached_coordinates(self, coordinates: list[Coordinate]) -> list[Coordinate]:
        seen: set[str] = set()
        uncached: list[Coordinate] = []
        for coordinate in coordinates:
            key = self.cache_key(coordinate)
            if key not in seen and key not in self.cache:
                seen.add(key)
                uncached.append(coordinate)
        return uncached

    def elevations(self, coordinates: list[Coordinate]) -> list[float]:
        uncached = self.uncached_coordinates(coordinates)
        GoogleElevationUsageTracker.ensure_capacity(self.usage, len(uncached))
        for start in range(0, len(uncached), GOOGLE_ELEVATION_BATCH_SIZE):
            batch = uncached[start : start + GOOGLE_ELEVATION_BATCH_SIZE]
            GoogleElevationUsageTracker.reserve(self.usage, self.usage_path, len(batch))
            try:
                batch_elevations = self._request_batch(batch)
            except RuntimeError as error:
                GoogleElevationUsageTracker.set_status(
                    self.usage,
                    self.usage_path,
                    f"failed: {error}",
                )
                raise
            for coordinate, elevation_meters in zip(
                batch,
                batch_elevations,
                strict=True,
            ):
                self.cache[self.cache_key(coordinate)] = {
                    "elevation_meters": elevation_meters,
                }
            JsonStore.save_cache(self.cache_path, self.cache)
            GoogleElevationUsageTracker.set_status(
                self.usage,
                self.usage_path,
                "success",
            )
        return [
            float(self.cache[self.cache_key(coordinate)]["elevation_meters"])
            for coordinate in coordinates
        ]

    def _request_batch(self, coordinates: list[Coordinate]) -> list[float]:
        locations = "|".join(
            f"{latitude:.7f},{longitude:.7f}" for latitude, longitude in coordinates
        )
        request = Request(
            f"{GOOGLE_ELEVATION_URL}?{urlencode({'locations': locations, 'key': self.api_key})}",
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urlopen(request, timeout=60) as response:
                data = json.load(response)
        except HTTPError as error:
            raise RuntimeError(
                f"Google Elevation request failed with HTTP {error.code}.",
            ) from error
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Google Elevation request failed due to a network or response error.",
            ) from error
        if not isinstance(data, dict) or data.get("status") != "OK":
            raise RuntimeError(
                "Google Elevation returned an unsuccessful response; check the API key and Elevation API setup.",
            )
        results = data.get("results")
        if not isinstance(results, list) or len(results) != len(coordinates):
            raise RuntimeError(
                "Google Elevation returned an incomplete elevation batch.",
            )
        try:
            return [float(result["elevation"]) for result in results]
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(
                "Google Elevation returned a batch without usable elevations.",
            ) from error


class ElevationAnalyzer:
    """Builds smoothed elevation profiles and segment grade summaries."""

    @staticmethod
    def smooth(
        elevations: list[float],
        samples: tuple[DistanceSample, ...],
        window_meters: float,
    ) -> tuple[float, ...]:
        if not 150 <= window_meters <= 250:
            raise ValueError(
                "Elevation smoothing window must be between 150 and 250 meters.",
            )
        if len(elevations) != len(samples):
            raise ValueError("Elevation values must match the route sample count.")
        half_window = window_meters / 2
        return tuple(
            sum(
                elevation
                for elevation, other in zip(elevations, samples, strict=True)
                if abs(other.distance_meters - sample.distance_meters) <= half_window
            )
            / sum(
                1
                for other in samples
                if abs(other.distance_meters - sample.distance_meters) <= half_window
            )
            for sample in samples
        )

    @classmethod
    def profile(
        cls,
        coordinates: list[Coordinate],
        elevations: list[float],
        spacing_meters: float,
        smoothing_meters: float,
    ) -> ElevationProfile:
        samples = RouteGeometrySampler.sample(coordinates, spacing_meters)
        return ElevationProfile(
            samples,
            tuple(elevations),
            cls.smooth(elevations, samples, smoothing_meters),
        )

    @staticmethod
    def run_coordinates(sections: list[RunnerRouteSection]) -> list[Coordinate]:
        coordinates = list(sections[0].coordinates)
        for section in sections[1:]:
            coordinates.extend(
                section.coordinates[1:]
                if coordinates[-1] == section.coordinates[0]
                else section.coordinates,
            )
        return coordinates

    @staticmethod
    def summarize_section(
        section: RunnerRouteSection,
        profile: ElevationProfile,
        start_distance_meters: float,
        start_runner_miles: float = 0.0,
    ) -> ElevatedRouteSection:
        section_meters = RouteGeometry.distance_meters([list(section.coordinates)])
        start_elevation = profile.smoothed_at(start_distance_meters)
        end_elevation = profile.smoothed_at(start_distance_meters + section_meters)
        grade = (
            100 * (end_elevation - start_elevation) / section_meters
            if section_meters
            else 0.0
        )
        return ElevatedRouteSection(
            section,
            section_meters,
            start_elevation,
            end_elevation,
            grade,
            start_runner_miles,
            start_runner_miles + section_meters / MILE_METERS,
        )

    @classmethod
    def sections(
        cls,
        section_runs: list[list[RunnerRouteSection]],
        client: GoogleElevationClient,
        spacing_meters: float,
        smoothing_meters: float,
    ) -> list[ElevatedRouteSection]:
        elevated_sections: list[ElevatedRouteSection] = []
        runner_miles = 0.0
        for sections in section_runs:
            coordinates = cls.run_coordinates(sections)
            samples = RouteGeometrySampler.sample(coordinates, spacing_meters)
            elevations = client.elevations([sample.coordinate for sample in samples])
            profile = cls.profile(
                coordinates,
                elevations,
                spacing_meters,
                smoothing_meters,
            )
            offset_meters = 0.0
            previous_end: Coordinate | None = None
            for section in sections:
                if previous_end is not None:
                    offset_meters += RouteGeometry.haversine_meters(
                        previous_end,
                        section.coordinates[0],
                    )
                elevated_section = cls.summarize_section(
                    section,
                    profile,
                    offset_meters,
                    runner_miles,
                )
                elevated_sections.append(elevated_section)
                offset_meters += elevated_section.distance_meters
                previous_end = section.coordinates[-1]
                runner_miles = elevated_section.end_runner_miles
        return elevated_sections


class ElevationLayerExporter:
    """Writes the grade-colored runner overlay and concise import notes."""

    COLORS: ClassVar[dict[str, str]] = {
        "darkBlue": "ff913d0b",
        "blue": "ffd27619",
        "lightBlue": "fff6b564",
        "gray": "ff808080",
        "lightOrange": "ff80ccff",
        "orange": "ff0098ff",
        "red": "ff2f2fd3",
    }
    LEGEND = (
        "Dark blue: ≤ -6%; blue: -6% to -3%; light blue: -3% to -1%; gray: -1% to +1%; "
        "light orange: +1% to +3%; orange: +3% to +6%; red: ≥ +6%."
    )

    @classmethod
    def style_name(cls, grade_percent: float) -> str:
        if grade_percent <= -6:
            return "darkBlue"
        if grade_percent <= -3:
            return "blue"
        if grade_percent < -1:
            return "lightBlue"
        if grade_percent <= 1:
            return "gray"
        if grade_percent <= 3:
            return "lightOrange"
        if grade_percent < 6:
            return "orange"
        return "red"

    @classmethod
    def write(
        cls,
        output_dir: Path,
        sections: list[ElevatedRouteSection],
        eta_by_section: Mapping[str, tuple[datetime, datetime]] | None = None,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        styles = "".join(
            f'<Style id="{name}"><LineStyle><color>{color}</color><width>6</width></LineStyle></Style>'
            for name, color in cls.COLORS.items()
        )
        placemarks = "".join(
            cls._placemark(section, (eta_by_section or {}).get(section.section.label))
            for section in sections
        )
        description = xml.sax.saxutils.escape(
            f"Terrain-elevation overlay for the finalized runner route. {cls.LEGEND}",
        )
        kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NSTT 2026 - Elevation by Grade</name><description>{description}</description>
  {styles}
  <Folder><name>Runner route elevation grade</name><description>{description}</description>{placemarks}
  </Folder></Document></kml>
"""
        notes = f"""NSTT 2026 elevation overlay

Files
- NSTT_2026_elevation.kml: import as a separate Google My Maps layer above the runner segments.

Legend
- {cls.LEGEND}

Method
- Samples the immutable Runner.kml geometry about every 50 m using Google Elevation terrain data.
- Smooths elevations over a configurable 150 to 250 m window (default 200 m), then colors each existing editable runner segment by signed average grade.
- Each segment pop-up lists smoothed start/end elevation, net elevation change, and average grade.
"""
        (output_dir / "NSTT_2026_elevation.kml").write_text(kml, encoding="utf-8")
        (output_dir / "NSTT_2026_elevation_README.txt").write_text(
            notes,
            encoding="utf-8",
        )

    @classmethod
    def _placemark(
        cls,
        section: ElevatedRouteSection,
        eta: tuple[datetime, datetime] | None,
    ) -> str:
        start_feet = section.start_elevation_meters * 3.28084
        end_feet = section.end_elevation_meters * 3.28084
        net_feet = section.net_elevation_meters * 3.28084
        eta_text = f"ETA: {eta[0]:%-I:%M %p} to {eta[1]:%-I:%M %p %Z}. " if eta else ""
        description = xml.sax.saxutils.escape(
            f"Runner miles: {section.start_runner_miles:.1f}-{section.end_runner_miles:.1f}. "
            + eta_text
            + f"Smoothed terrain elevation: {start_feet:.0f} ft to {end_feet:.0f} ft; "
            f"net gain/loss: {net_feet:+.0f} ft; signed average grade: {section.average_grade_percent:+.1f}%.",
        )
        coordinates = " ".join(
            f"{longitude},{latitude},0"
            for latitude, longitude in section.section.coordinates
        )
        return f"""\n      <Placemark><name>{xml.sax.saxutils.escape(section.section.segment_name)}</name>
        <description>{description}</description><styleUrl>#{cls.style_name(section.average_grade_percent)}</styleUrl>
        <LineString><tessellate>1</tessellate><coordinates>{coordinates}</coordinates></LineString>
      </Placemark>"""
