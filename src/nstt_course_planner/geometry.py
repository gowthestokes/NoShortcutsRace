"""Stateless route-distance and editable-segment geometry services."""

from __future__ import annotations

import math

from nstt_course_planner.config import MILE_METERS
from nstt_course_planner.models import RunnerRouteSection


class RouteGeometry:
    """Distance and interpolation operations for latitude/longitude geometry."""

    @staticmethod
    def haversine_meters(start: tuple[float, float], end: tuple[float, float]) -> float:
        latitude_1, longitude_1 = map(math.radians, start)
        latitude_2, longitude_2 = map(math.radians, end)
        a = math.sin((latitude_2 - latitude_1) / 2) ** 2 + math.cos(latitude_1) * math.cos(latitude_2) * math.sin((longitude_2 - longitude_1) / 2) ** 2
        return 6_371_000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @classmethod
    def distance_meters(cls, segments: list[list[tuple[float, float]]]) -> float:
        return sum(cls.haversine_meters(start, end) for segment in segments for start, end in zip(segment, segment[1:], strict=False))

    @staticmethod
    def interpolate(start: tuple[float, float], end: tuple[float, float], fraction: float) -> tuple[float, float]:
        start_latitude, start_longitude = start
        end_latitude, end_longitude = end
        return start_latitude + (end_latitude - start_latitude) * fraction, start_longitude + (end_longitude - start_longitude) * fraction


class RouteSegmenter:
    """Places interval markers and creates independently editable KML lines."""

    @classmethod
    def markers(
        cls, segments: list[list[tuple[float, float]]], distance_meters: float | None = None,
        *, first_mile_number: int = 1, interval_meters: float = MILE_METERS,
    ) -> list[tuple[int, float, float]]:
        geometry_meters = RouteGeometry.distance_meters(segments)
        if geometry_meters == 0:
            return []
        geometry_scale = distance_meters / geometry_meters if distance_meters is not None else 1.0
        markers: list[tuple[int, float, float]] = []
        route_meters = 0.0
        next_mile = first_mile_number
        next_target_meters = interval_meters
        for segment in segments:
            for start, end in zip(segment, segment[1:], strict=False):
                edge_meters = RouteGeometry.haversine_meters(start, end) * geometry_scale
                if edge_meters == 0:
                    continue
                while route_meters + edge_meters + 1e-6 >= next_target_meters:
                    fraction = max(0.0, min(1.0, (next_target_meters - route_meters) / edge_meters))
                    latitude, longitude = RouteGeometry.interpolate(start, end, fraction)
                    markers.append((next_mile, latitude, longitude))
                    next_mile += 1
                    next_target_meters += interval_meters
                route_meters += edge_meters
        return markers

    @classmethod
    def sections(
        cls, segments: list[list[tuple[float, float]]], distance_meters: float,
        *, first_segment_number: int = 1, start_label: str = "Start", interval_meters: float = MILE_METERS,
        checkpoint_label: str = "Mile",
    ) -> list[RunnerRouteSection]:
        geometry_meters = RouteGeometry.distance_meters(segments)
        if geometry_meters == 0:
            return []
        geometry_scale = distance_meters / geometry_meters
        sections: list[RunnerRouteSection] = []
        route_meters = 0.0
        next_mile = first_segment_number
        next_target_meters = interval_meters
        suffix = ""
        for segment_index, segment in enumerate(segments):
            if len(segment) < 2:
                continue
            section_coordinates = [segment[0]]
            for start, end in zip(segment, segment[1:], strict=False):
                edge_meters = RouteGeometry.haversine_meters(start, end) * geometry_scale
                if edge_meters == 0:
                    continue
                while route_meters + edge_meters + 1e-6 >= next_target_meters:
                    fraction = max(0.0, min(1.0, (next_target_meters - route_meters) / edge_meters))
                    marker = RouteGeometry.interpolate(start, end, fraction)
                    if section_coordinates[-1] != marker:
                        section_coordinates.append(marker)
                    sections.append(RunnerRouteSection(f"Segment {next_mile:03d}{suffix} - {start_label} to {checkpoint_label} {next_mile:03d}", tuple(section_coordinates)))
                    section_coordinates, start_label, suffix = [marker], f"{checkpoint_label} {next_mile:03d}", ""
                    next_mile += 1
                    next_target_meters += interval_meters
                if section_coordinates[-1] != end:
                    section_coordinates.append(end)
                route_meters += edge_meters
            if segment_index < len(segments) - 1:
                if len(section_coordinates) > 1:
                    sections.append(RunnerRouteSection(f"Segment {next_mile:03d}{suffix} - {start_label} to runner transfer gap", tuple(section_coordinates)))
                start_label, suffix = "Runner restart", "b"
            elif len(section_coordinates) > 1:
                sections.append(RunnerRouteSection(f"Final segment - {start_label} to Finish", tuple(section_coordinates)))
        return sections
