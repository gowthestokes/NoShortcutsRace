"""Runner-order timing simulation and solar-visibility KML export."""

from __future__ import annotations

import json
import math
import xml.sax.saxutils
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from nstt_course_planner.geometry import RouteGeometry
from nstt_course_planner.models.route import Coordinate, RunnerRouteSection
from nstt_course_planner.models.sunlight import (
    RunnerPace,
    ScheduledSegment,
    SunlightBuildConfig,
    SunlightCategory,
)


class TeamPaceLoader:
    """Loads the team's ordered, conservative per-mile pace assumptions."""

    @staticmethod
    def load(path: Path) -> tuple[RunnerPace, ...]:
        try:
            raw_paces = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not read team pace data: {path}") from error
        if not isinstance(raw_paces, dict) or not raw_paces:
            raise TypeError(
                "Team pace data must be a non-empty object of runner names and paces."
            )
        paces: list[RunnerPace] = []
        for name, minutes_per_mile in raw_paces.items():
            if not isinstance(name, str) or not name.strip():
                raise TypeError("Each team pace entry needs a non-empty runner name.")
            if not isinstance(minutes_per_mile, int | float) or minutes_per_mile <= 0:
                raise TypeError(f"{name} needs a positive minutes-per-mile value.")
            paces.append(RunnerPace(name, float(minutes_per_mile)))
        return tuple(paces)


class SolarPosition:
    """Calculates apparent solar altitude with the NOAA solar-position approximation."""

    civil_twilight_degrees = -6.0
    sunrise_sunset_degrees = -0.833
    low_sun_degrees = 6.0

    @classmethod
    def altitude_degrees(cls, moment: datetime, coordinate: Coordinate) -> float:
        if moment.tzinfo is None:
            raise ValueError("Sunlight simulation times must include a timezone.")
        latitude, longitude = coordinate
        utc_moment = moment.astimezone(ZoneInfo("UTC"))
        julian_day = utc_moment.timestamp() / 86_400 + 2_440_587.5
        centuries = (julian_day - 2_451_545.0) / 36_525
        mean_longitude = cls._mean_longitude(centuries)
        solar_longitude = cls._solar_longitude(centuries, mean_longitude)
        declination = cls._declination_degrees(centuries, solar_longitude)
        equation_of_time = cls._equation_of_time_minutes(centuries, mean_longitude)
        local_minutes = (
            moment.hour * 60
            + moment.minute
            + moment.second / 60
            + moment.microsecond / 60_000_000
        )
        utc_offset_minutes = moment.utcoffset().total_seconds() / 60
        true_solar_minutes = (
            local_minutes + equation_of_time + 4 * longitude - utc_offset_minutes
        ) % 1_440
        hour_angle = true_solar_minutes / 4 - 180
        cosine_zenith = math.sin(math.radians(latitude)) * math.sin(
            math.radians(declination)
        ) + math.cos(math.radians(latitude)) * math.cos(
            math.radians(declination)
        ) * math.cos(math.radians(hour_angle))
        return 90 - math.degrees(math.acos(max(-1, min(1, cosine_zenith))))

    @classmethod
    def category(cls, altitude_degrees: float) -> SunlightCategory:
        if altitude_degrees < cls.civil_twilight_degrees:
            return SunlightCategory.DARK
        if altitude_degrees < cls.sunrise_sunset_degrees:
            return SunlightCategory.TWILIGHT
        if altitude_degrees < cls.low_sun_degrees:
            return SunlightCategory.SUNRISE_SUNSET
        return SunlightCategory.DAYLIGHT

    @staticmethod
    def _mean_longitude(centuries: float) -> float:
        return (280.46646 + centuries * (36_000.76983 + 0.0003032 * centuries)) % 360

    @staticmethod
    def _solar_longitude(centuries: float, mean_longitude: float) -> float:
        mean_anomaly = 357.52911 + centuries * (35_999.05029 - 0.0001537 * centuries)
        center = (
            math.sin(math.radians(mean_anomaly))
            * (1.914602 - centuries * (0.004817 + 0.000014 * centuries))
            + math.sin(math.radians(2 * mean_anomaly))
            * (0.019993 - 0.000101 * centuries)
            + math.sin(math.radians(3 * mean_anomaly)) * 0.000289
        )
        return mean_longitude + center

    @staticmethod
    def _obliquity_degrees(centuries: float) -> float:
        mean_obliquity = (
            23
            + (
                26
                + (
                    (
                        21.448
                        - centuries
                        * (46.815 + centuries * (0.00059 - 0.001813 * centuries))
                    )
                    / 60
                )
            )
            / 60
        )
        return mean_obliquity + 0.00256 * math.cos(
            math.radians(125.04 - 1934.136 * centuries)
        )

    @classmethod
    def _declination_degrees(cls, centuries: float, solar_longitude: float) -> float:
        apparent_longitude = (
            solar_longitude
            - 0.00569
            - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * centuries))
        )
        return math.degrees(
            math.asin(
                math.sin(math.radians(cls._obliquity_degrees(centuries)))
                * math.sin(math.radians(apparent_longitude))
            )
        )

    @classmethod
    def _equation_of_time_minutes(
        cls, centuries: float, mean_longitude: float
    ) -> float:
        obliquity = cls._obliquity_degrees(centuries)
        y = math.tan(math.radians(obliquity) / 2) ** 2
        mean_anomaly = 357.52911 + centuries * (35_999.05029 - 0.0001537 * centuries)
        eccentricity = 0.016708634 - centuries * (
            0.000042037 + 0.0000001267 * centuries
        )
        return 4 * math.degrees(
            y * math.sin(2 * math.radians(mean_longitude))
            - 2 * eccentricity * math.sin(math.radians(mean_anomaly))
            + 4
            * eccentricity
            * y
            * math.sin(math.radians(mean_anomaly))
            * math.cos(2 * math.radians(mean_longitude))
            - 0.5 * y * y * math.sin(4 * math.radians(mean_longitude))
            - 1.25
            * eccentricity
            * eccentricity
            * math.sin(2 * math.radians(mean_anomaly))
        )


class RelaySunlightSimulator:
    """Assigns two consecutive half-mile sections to each runner in a circular queue."""

    def __init__(
        self, config: SunlightBuildConfig, paces: tuple[RunnerPace, ...]
    ) -> None:
        if config.half_segments_per_turn <= 0:
            raise ValueError("Half segments per turn must be positive.")
        if (
            config.transfer_duration_minutes is not None
            and config.transfer_duration_minutes < 0
        ):
            raise ValueError("Support-car transfer duration cannot be negative.")
        if not paces:
            raise ValueError("At least one runner pace is required.")
        self.config = config
        self.paces = paces

    def schedule(
        self, section_runs: list[list[RunnerRouteSection]]
    ) -> tuple[ScheduledSegment, ...]:
        scheduled: list[ScheduledSegment] = []
        moment = self.config.race_start
        section_number = 0
        for run_number, sections in enumerate(section_runs):
            if run_number:
                if self.config.transfer_duration_minutes is None:
                    raise ValueError(
                        "A support-car transfer duration is required before scheduling the post-I-5 runner restart.",
                    )
                moment += timedelta(minutes=self.config.transfer_duration_minutes)
            for section in sections:
                runner = self.paces[
                    (section_number // self.config.half_segments_per_turn)
                    % len(self.paces)
                ]
                duration_seconds = runner.seconds_per_mile * self._section_miles(
                    section
                )
                end_time = moment + timedelta(seconds=duration_seconds)
                midpoint = self._midpoint(section.coordinates)
                solar_altitude = SolarPosition.altitude_degrees(
                    moment + (end_time - moment) / 2, midpoint
                )
                scheduled.append(
                    ScheduledSegment(
                        section,
                        runner,
                        moment,
                        end_time,
                        solar_altitude,
                        SolarPosition.category(solar_altitude),
                    ),
                )
                moment = end_time
                section_number += 1
        return tuple(scheduled)

    @staticmethod
    def _section_miles(section: RunnerRouteSection) -> float:
        return RouteGeometry.distance_meters([list(section.coordinates)]) / 1_609.344

    @staticmethod
    def _midpoint(coordinates: tuple[Coordinate, ...]) -> Coordinate:
        total_meters = RouteGeometry.distance_meters([list(coordinates)])
        traversed_meters = 0.0
        for start, end in pairwise(coordinates):
            edge_meters = RouteGeometry.haversine_meters(start, end)
            if traversed_meters + edge_meters >= total_meters / 2:
                return RouteGeometry.interpolate(
                    start, end, (total_meters / 2 - traversed_meters) / edge_meters
                )
            traversed_meters += edge_meters
        return coordinates[-1]


class SunlightLayerExporter:
    """Writes the simulated daylight overlay without changing source geometry."""

    @classmethod
    def write(cls, path: Path, scheduled: tuple[ScheduledSegment, ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        styles = "".join(
            f'<Style id="{category.style_id}"><LineStyle><color>{category.kml_color}</color><width>7</width></LineStyle></Style>'
            for category in SunlightCategory
        )
        placemarks = "".join(cls._placemark(segment) for segment in scheduled)
        path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NSTT 2026 - Estimated Sunlight</name>
  <description>Estimated October 23 sunlight by runner segment. Schedule uses the ordered team pace data, two half-mile segments per runner, and the configured support-car transfer pause. This is a planning estimate, not a live tracker.</description>
  {styles}<Folder><name>Estimated runner visibility</name>{placemarks}
  </Folder></Document></kml>
""",
            encoding="utf-8",
        )

    @staticmethod
    def _placemark(segment: ScheduledSegment) -> str:
        midpoint = segment.start_time + (segment.end_time - segment.start_time) / 2
        description = xml.sax.saxutils.escape(
            f"Runner: {segment.runner.name}. Estimated segment: {segment.start_time:%-I:%M %p} to {segment.end_time:%-I:%M %p %Z}. "
            f"Midpoint: {midpoint:%-I:%M %p %Z}; 10K planning pace: {segment.runner.minutes_per_mile:.1f} min/mi. "
            f"Solar altitude: {segment.solar_altitude_degrees:.1f}°; visibility: {segment.sunlight.display_name}.",
        )
        coordinates = " ".join(
            f"{longitude},{latitude},0"
            for latitude, longitude in segment.section.coordinates
        )
        return f"""<Placemark><name>{xml.sax.saxutils.escape(f"{segment.sunlight.display_name}: {segment.section.label}")}</name>
      <description>{description}</description><styleUrl>#{segment.sunlight.style_id}</styleUrl>
      <LineString><tessellate>1</tessellate><coordinates>{coordinates}</coordinates></LineString></Placemark>"""
