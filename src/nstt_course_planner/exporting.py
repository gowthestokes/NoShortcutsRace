"""KML, GPX, and My Maps import-note export service."""

from __future__ import annotations

import xml.sax.saxutils
from pathlib import Path

from nstt_course_planner.config import MILE_METERS, SEGMENT_METERS
from nstt_course_planner.geometry import RouteSegmenter
from nstt_course_planner.models import Point, RunnerRouteSection


class CourseExporter:
    """Writes runner-only files that are editable as individual My Maps lines."""

    DESCRIPTION = (
        "Runner-only draft course for the No Shortcuts Time Trial (Santa Monica to San Diego, 23 Oct 2026). "
        "Built from the organizer's published runner turn list. It includes one checkpoint at each completed half-mile route segment "
        "and intentionally excludes vehicle logistics, relay assignments, and handoffs. Important: validate the LA River Trail "
        "and coastal portions against the organizer's notes; this is a planning map, not a safety or navigation authority. "
        "The PCH exit is interpreted as Ocean Blue Environmental at 925 W Esther Street in Long Beach. The line from Ocean Blue "
        "to Del Prado is pinned to the organizer's Highway 1/PCH corridor and is a road-alignment reference, not a pedestrian-safety validation. "
        "The Segment 162 to Oceanside Chevron transfer follows I-5 to Exit 54C as directed by the supplied route sheet; it is not runner-safety validation and must be field-reviewed before race day."
    )

    def write(
        self, output_dir: Path, points: list[Point], segments: list[list[tuple[float, float]]], distance_meters: float,
        *, route_sections_override: list[RunnerRouteSection] | None = None,
        markers_override: list[tuple[int, float, float]] | None = None,
        gpx_segments_override: list[list[tuple[float, float]]] | None = None,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        markers = markers_override if markers_override is not None else RouteSegmenter.markers(segments, distance_meters, interval_meters=SEGMENT_METERS)
        sections = route_sections_override if route_sections_override is not None else RouteSegmenter.sections(segments, distance_meters, interval_meters=SEGMENT_METERS, checkpoint_label="Checkpoint")
        gpx_segments = gpx_segments_override if gpx_segments_override is not None else segments
        description = xml.sax.saxutils.escape(self.DESCRIPTION)
        section_placemarks = "".join(
            f'''\n      <Placemark><name>{xml.sax.saxutils.escape(section.label)}</name>
        <description>Editable half-mile runner-route segment. Delete or redraw this line independently without changing the other route segments. Colors alternate green and blue to make adjacent segments easier to distinguish.</description>
        <styleUrl>#{'segmentGreen' if index % 2 else 'segmentBlue'}</styleUrl>
        <LineString><tessellate>1</tessellate><coordinates>{' '.join(f'{longitude},{latitude},0' for latitude, longitude in section.coordinates)}</coordinates></LineString>
      </Placemark>'''
            for index, section in enumerate(sections, start=1)
        )
        endpoints = "".join(
            f'''\n    <Placemark><name>{xml.sax.saxutils.escape(label)}</name><description>{xml.sax.saxutils.escape(point.query)}</description>
      <Point><coordinates>{point.longitude},{point.latitude},0</coordinates></Point></Placemark>'''
            for label, point in (("Start - Santa Monica Pier", points[0]), ("Finish - Milestone Running Shop", points[-1]))
        )
        marker_placemarks = "".join(
            f'''\n    <Placemark><name>Checkpoint {mile:03d}</name><description>Endpoint of completed half-mile Segment {mile:03d}. Position is measured along the exported runner route geometry.</description>
      <styleUrl>#mileMarker</styleUrl><Point><coordinates>{longitude},{latitude},0</coordinates></Point></Placemark>'''
            for mile, latitude, longitude in markers
        )
        distance_miles = distance_meters / MILE_METERS
        route_kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NSTT 2026 - Runner Route Segments</name><description>{description}</description>
  <Style id="segmentGreen"><LineStyle><color>ff00aa00</color><width>5</width></LineStyle></Style><Style id="segmentBlue"><LineStyle><color>ffe57300</color><width>5</width></LineStyle></Style>
  <Folder><name>Runner route segments - alternating green and blue</name><description>{description} Approximate routed distance: {distance_miles:.1f} mi. Each line is independently editable.</description>{section_placemarks}
  </Folder></Document></kml>\n'''
        checkpoints_kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NSTT 2026 - Half-Mile Checkpoints</name><description>{description}</description>
  <Style id="mileMarker"><IconStyle><color>ff00a5ff</color><scale>0.9</scale></IconStyle></Style><Folder><name>Runner half-mile checkpoints</name>
    <description>Start, finish, and one checkpoint at every completed half-mile runner segment.</description>{endpoints}{marker_placemarks}
  </Folder></Document></kml>\n'''
        track_segments = "\n".join("  <trkseg>\n" + "\n".join(f'      <trkpt lat="{latitude}" lon="{longitude}"/>' for latitude, longitude in segment) + "\n  </trkseg>" for segment in gpx_segments)
        waypoints = "\n".join(f'  <wpt lat="{latitude}" lon="{longitude}"><name>Checkpoint {mile:03d}</name><desc>Completed half-mile Segment {mile:03d}.</desc></wpt>' for mile, latitude, longitude in markers)
        gpx = f'''<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="NSTT course planner" xmlns="http://www.topografix.com/GPX/1/1"><metadata><name>NSTT 2026 Runner Route with Half-Mile Checkpoints</name><desc>{description}</desc></metadata>
{waypoints}\n  <trk><name>NSTT 2026 Runner Route</name>\n{track_segments}\n  </trk></gpx>\n'''
        notes = self._import_notes(len(markers))
        (output_dir / "NSTT_2026_runner_route_segments.kml").write_text(route_kml, encoding="utf-8")
        (output_dir / "NSTT_2026_runner_mile_checkpoints.kml").write_text(checkpoints_kml, encoding="utf-8")
        (output_dir / "NSTT_2026_runner_route.gpx").write_text(gpx, encoding="utf-8")
        (output_dir / "NSTT_2026_runner_route_README.txt").write_text(notes, encoding="utf-8")

    @staticmethod
    def _import_notes(marker_count: int) -> str:
        return f'''NSTT 2026 runner route - half-mile segments

Files
- NSTT_2026_runner_route_segments.kml: import into one Google My Maps layer; contains individually editable, alternating green/blue route lines.
- NSTT_2026_runner_mile_checkpoints.kml: import into a second Google My Maps layer; contains start, finish, and half-mile checkpoints.
- NSTT_2026_runner_route.gpx: portable route backup; it can also be imported into My Maps or Footpath.

This export is runner-only: no vehicle routing, support-car markers, runner assignments, pod blocks, or handoffs are included.

Checkpoints
- Checkpoint 001 through Checkpoint {marker_count:03d}: one point at each completed half-mile runner segment, measured along the exported route geometry.
- Start - Santa Monica Pier and Finish - Milestone Running Shop: course endpoints.
- The final partial segment has no separate checkpoint; the finish point is its endpoint.

Editable route lines
- Each alternating green/blue line is an individual half-mile runner segment, named Segment 001, Segment 002, and so on.
- Delete or redraw one line in Google My Maps without changing the remaining route segments or checkpoints.
- The Segment 162 to Oceanside Chevron transfer follows I-5 to Exit 54C in this runner-layer planning map; review safety and authorization before race day.

Google My Maps import
1. Go to https://www.google.com/mymaps and create a new map.
2. Click Import in its first layer and select NSTT_2026_runner_route_segments.kml.
3. Add a layer, import NSTT_2026_runner_mile_checkpoints.kml, and edit either layer independently.
'''
