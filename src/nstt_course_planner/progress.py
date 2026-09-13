"""Google My Maps KML import for manually approved route progress."""

from __future__ import annotations

import xml.etree.ElementTree as element_tree
from pathlib import Path

from nstt_course_planner.config import SEGMENT_METERS, SEGMENT_NAME_PATTERN
from nstt_course_planner.geometry import RouteGeometry, RouteSegmenter
from nstt_course_planner.models.route import ApprovedRouteProgress, RunnerRouteSection


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

    @staticmethod
    def canonical_section_name(number: int) -> str:
        """Return the standard half-mile label for a source KML segment."""
        start = "Start" if number == 1 else f"Checkpoint {number - 1:03d}"
        return f"Segment {number:03d} - {start} to Checkpoint {number:03d}"

    @classmethod
    def _found_sections(cls, path: Path) -> dict[int, list[RunnerRouteSection]]:
        found: dict[int, list[RunnerRouteSection]] = {}
        for section in cls.source_sections(path, include_suffixes=False):
            number = int(SEGMENT_NAME_PATTERN.match(section.label).group(1))  # type: ignore[union-attr]
            found.setdefault(number, []).append(section)
        return found

    @classmethod
    def source_sections(cls, path: Path, *, include_suffixes: bool = True) -> tuple[RunnerRouteSection, ...]:
        """Read the runner lines in KML order, including manual suffix lines."""
        try:
            root = element_tree.parse(path).getroot()
        except (OSError, element_tree.ParseError) as error:
            raise RuntimeError(f"Could not read approved segment KML: {path}") from error
        namespace = {"kml": "http://www.opengis.net/kml/2.2"}
        sections: list[RunnerRouteSection] = []
        for placemark in root.findall(".//kml:Placemark", namespace):
            name = placemark.findtext("kml:name", default="", namespaces=namespace).strip()
            match = SEGMENT_NAME_PATTERN.match(name)
            if not match or (match.group(2) and not include_suffixes):
                continue
            raw = placemark.findtext(".//kml:LineString/kml:coordinates", default="", namespaces=namespace)
            if raw:
                sections.append(RunnerRouteSection(name, cls.parse_coordinates(raw)))
        return tuple(sections)

    @classmethod
    def source_geometry_runs(cls, path: Path, *, connection_tolerance_meters: float = 50) -> list[list[tuple[float, float]]]:
        """Join numbered source lines while retaining support-car gaps.

        My Maps may append a manually repaired line to the end of a KML export.
        Segment labels, rather than XML placement order, are the canonical route
        order; a letter suffix follows its corresponding numbered segment.
        """
        return [
            [coordinate for section in run for coordinate in section.coordinates]
            for run in cls.source_section_runs(path, connection_tolerance_meters=connection_tolerance_meters)
        ]

    @classmethod
    def source_section_runs(cls, path: Path, *, connection_tolerance_meters: float = 50) -> list[list[RunnerRouteSection]]:
        """Group ordered editable source lines into runner runs separated by car gaps."""
        sections = sorted(
            cls.source_sections(path),
            key=lambda section: (
                int(SEGMENT_NAME_PATTERN.match(section.label).group(1)),  # type: ignore[union-attr]
                SEGMENT_NAME_PATTERN.match(section.label).group(2),  # type: ignore[union-attr]
            ),
        )
        if not sections:
            raise RuntimeError(f"Source KML has no named runner-route segments: {path}")
        runs: list[list[RunnerRouteSection]] = []
        for section in sections:
            if not runs or RouteGeometry.haversine_meters(runs[-1][-1].coordinates[-1], section.coordinates[0]) > connection_tolerance_meters:
                runs.append([section])
            else:
                runs[-1].append(section)
        return runs

    @classmethod
    def _sections(cls, path: Path, start_segment: int, through_segment: int, *, require_continuity: bool) -> tuple[RunnerRouteSection, ...]:
        found = cls._found_sections(path)
        if start_segment not in found or through_segment not in found:
            raise RuntimeError(f"Approved KML must contain Segment {start_segment:03d} and Segment {through_segment:03d}.")
        sections: list[RunnerRouteSection] = []
        for number in range(start_segment, through_segment + 1):
            if number not in found:
                raise RuntimeError(f"Approved KML is missing Segment {number:03d}.")
            pieces = found[number]
            # My Maps can leave an old, wrongly numbered line in an export.
            # When the normal half-mile source segment is present, it is the
            # authoritative one; do not let an orphan duplicate move it.
            canonical = [
                piece for piece in pieces
                if piece.label.casefold() == cls.canonical_section_name(number).casefold()
            ]
            if canonical:
                pieces = canonical
            coordinates = list(pieces[0].coordinates)
            for previous, current in zip(pieces, pieces[1:], strict=False):
                if RouteGeometry.haversine_meters(previous.coordinates[-1], current.coordinates[0]) > 25:
                    raise RuntimeError(f"Google My Maps split Segment {number:03d} into pieces that do not connect; join or rename those lines before rebuilding.")
                coordinates.extend(current.coordinates[1:])
            sections.append(RunnerRouteSection(pieces[0].label, tuple(coordinates)))
        if require_continuity:
            for previous, current in zip(sections, sections[1:], strict=False):
                if RouteGeometry.haversine_meters(previous.coordinates[-1], current.coordinates[0]) > 50:
                    raise RuntimeError(f"{previous.label} does not connect to {current.label}; join their endpoints in My Maps before rebuilding.")
        return tuple(sections)

    @classmethod
    def load(cls, path: Path, through_segment: int) -> ApprovedRouteProgress:
        return ApprovedRouteProgress(cls._sections(path, 1, through_segment, require_continuity=True), through_segment)

    @classmethod
    def load_tail(cls, path: Path, start_segment: int) -> tuple[RunnerRouteSection, ...]:
        """Read an immutable downstream KML tail without crossing a transfer gap."""
        found = cls._found_sections(path)
        numbers = [number for number in found if number >= start_segment]
        if not numbers:
            raise RuntimeError(f"Source KML has no Segment {start_segment:03d} or later.")
        return cls._sections(path, start_segment, max(numbers), require_continuity=True)

    @classmethod
    def named_point(cls, path: Path, name: str) -> tuple[float, float]:
        """Return a named KML point as a latitude/longitude pair."""
        try:
            root = element_tree.parse(path).getroot()
        except (OSError, element_tree.ParseError) as error:
            raise RuntimeError(f"Could not read source KML: {path}") from error
        namespace = {"kml": "http://www.opengis.net/kml/2.2"}
        for placemark in root.findall(".//kml:Placemark", namespace):
            if placemark.findtext("kml:name", default="", namespaces=namespace).strip() != name:
                continue
            raw = placemark.findtext(".//kml:Point/kml:coordinates", default="", namespaces=namespace).strip()
            if raw:
                longitude, latitude, *_ = raw.split()[0].split(",")
                return float(latitude), float(longitude)
        raise RuntimeError(f"Source KML has no point named {name!r}.")

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
