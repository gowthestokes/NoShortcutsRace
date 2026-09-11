"""Google My Maps KML import for manually approved route progress."""

from __future__ import annotations

import xml.etree.ElementTree as element_tree
from pathlib import Path

from nstt_course_planner.config import SEGMENT_METERS, SEGMENT_NAME_PATTERN
from nstt_course_planner.geometry import RouteGeometry, RouteSegmenter
from nstt_course_planner.models import ApprovedRouteProgress, RunnerRouteSection


class ApprovedProgressLoader:
    """Loads and validates the user-edited route geometry exported by My Maps."""

    @staticmethod
    def parse_coordinates(raw_coordinates: str) -> tuple[tuple[float, float], ...]:
        coordinates: list[tuple[float, float]] = []
        for raw_coordinate in raw_coordinates.split():
            values = raw_coordinate.split(",")
            if len(values) < 2:
                raise RuntimeError(f"Invalid KML coordinate: {raw_coordinate!r}")
            longitude, latitude = map(float, values[:2])
            coordinates.append((latitude, longitude))
        if len(coordinates) < 2:
            raise RuntimeError("An approved runner segment must contain at least two coordinates.")
        return tuple(coordinates)

    @classmethod
    def load(cls, path: Path, through_segment: int) -> ApprovedRouteProgress:
        try:
            root = element_tree.parse(path).getroot()
        except (OSError, element_tree.ParseError) as error:
            raise RuntimeError(f"Could not read approved segment KML: {path}") from error
        namespace = {"kml": "http://www.opengis.net/kml/2.2"}
        found: dict[int, list[RunnerRouteSection]] = {}
        for placemark in root.findall(".//kml:Placemark", namespace):
            name = placemark.findtext("kml:name", default="", namespaces=namespace).strip()
            match = SEGMENT_NAME_PATTERN.match(name)
            if not match or match.group(2):
                continue
            number = int(match.group(1))
            if number > through_segment:
                continue
            raw = placemark.findtext(".//kml:LineString/kml:coordinates", default="", namespaces=namespace)
            if raw:
                found.setdefault(number, []).append(RunnerRouteSection(name, cls.parse_coordinates(raw)))
        if 1 not in found or through_segment not in found:
            raise RuntimeError(f"Approved KML must contain Segment 001 and Segment {through_segment:03d}.")
        sections: list[RunnerRouteSection] = []
        for number in sorted(found):
            pieces = found[number]
            coordinates = list(pieces[0].coordinates)
            for previous, current in zip(pieces, pieces[1:], strict=False):
                if RouteGeometry.haversine_meters(previous.coordinates[-1], current.coordinates[0]) > 25:
                    raise RuntimeError(f"Google My Maps split Segment {number:03d} into pieces that do not connect; join or rename those lines before rebuilding.")
                coordinates.extend(current.coordinates[1:])
            sections.append(RunnerRouteSection(pieces[0].label, tuple(coordinates)))
        for previous, current in zip(sections, sections[1:], strict=False):
            if RouteGeometry.haversine_meters(previous.coordinates[-1], current.coordinates[0]) > 50:
                raise RuntimeError(f"{previous.label} does not connect to {current.label}; join their endpoints in My Maps before rebuilding.")
        return ApprovedRouteProgress(tuple(sections), through_segment)

    @staticmethod
    def geometry(progress: ApprovedRouteProgress) -> list[tuple[float, float]]:
        geometry = list(progress.sections[0].coordinates)
        for section in progress.sections[1:]:
            geometry.extend(section.coordinates[1:])
        return geometry

    @classmethod
    def sections(cls, progress: ApprovedRouteProgress) -> list[RunnerRouteSection]:
        geometry = cls.geometry(progress)
        return RouteSegmenter.sections([geometry], RouteGeometry.distance_meters([geometry]), interval_meters=SEGMENT_METERS, checkpoint_label="Checkpoint")

    @classmethod
    def markers(cls, progress: ApprovedRouteProgress) -> list[tuple[int, float, float]]:
        geometry = cls.geometry(progress)
        return RouteSegmenter.markers([geometry], RouteGeometry.distance_meters([geometry]), interval_meters=SEGMENT_METERS)
