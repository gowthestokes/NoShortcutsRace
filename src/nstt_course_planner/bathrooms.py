"""Build a research-backed bathroom-stop layer beside an immutable runner route."""

from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as element_tree
import xml.sax.saxutils
from pathlib import Path

from nstt_course_planner.catalog.bathrooms import BATHROOM_STOPS
from nstt_course_planner.models.bathrooms import (
    BathroomCategory,
    BathroomLayerBuildResult,
    BathroomStop,
)
from nstt_course_planner.models.route import RouteProximity

class RunnerRouteKml:
    """Reads independent route lines without ever rewriting their source KML."""

    namespace = {"kml": "http://www.opengis.net/kml/2.2"}

    @classmethod
    def line_runs(cls, source_path: Path) -> tuple[tuple[tuple[float, float], ...], ...]:
        if not source_path.is_file():
            raise FileNotFoundError(f"Runner route source was not found: {source_path}")
        root = element_tree.parse(source_path).getroot()
        runs = tuple(
            coordinates
            for node in root.findall(".//kml:LineString/kml:coordinates", cls.namespace)
            if len(coordinates := cls._coordinates(node.text)) > 1
        )
        if not runs:
            raise ValueError(f"Runner route source contains no usable LineString geometry: {source_path}")
        return runs

    @staticmethod
    def _coordinates(text: str | None) -> tuple[tuple[float, float], ...]:
        return tuple(
            (float(parts[1]), float(parts[0]))
            for token in (text or "").split()
            if len(parts := token.split(",")) >= 2
        )


class BathroomLayerBuilder:
    """Exports researched stops and their approximate distance to immutable runner geometry."""

    meters_per_degree_latitude = 111_320.0

    def build(self, source_path: Path, output_path: Path, stops: tuple[BathroomStop, ...] = BATHROOM_STOPS) -> BathroomLayerBuildResult:
        route_runs = RunnerRouteKml.line_runs(source_path)
        placemarks = "\n".join(self._placemark(stop, route_runs) for stop in sorted(stops, key=lambda stop: (stop.category.priority, stop.name)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self._kml(placemarks), encoding="utf-8")
        return BathroomLayerBuildResult(
            len(stops),
            (),
        )

    def _placemark(self, stop: BathroomStop, route_runs: tuple[tuple[tuple[float, float], ...], ...]) -> str:
        proximity = self.nearest_route_proximity((stop.latitude, stop.longitude), route_runs)
        description = (
            f"Priority {stop.category.priority}: {stop.category.display_name}.<br/>"
            f"Address: {stop.address}<br/>"
            f"Approximate runner mile: {proximity.runner_miles:.1f}.<br/>"
            f"Source: <a href=\"{stop.source_url}\">official / current listing</a>"
        )
        return f'''    <Placemark><name>{xml.sax.saxutils.escape(f"{stop.category.symbol} {stop.name}")}</name>
      <ExtendedData><Data name="Category"><value>{xml.sax.saxutils.escape(stop.category.display_name)}</value></Data>
      <Data name="Priority"><value>{stop.category.priority}</value></Data></ExtendedData>
      <description><![CDATA[{description}]]></description>
      <styleUrl>#{stop.category.style_id}</styleUrl>
      <Point><coordinates>{stop.longitude},{stop.latitude},0</coordinates></Point>
    </Placemark>'''

    @classmethod
    def nearest_route_distance_meters(
        cls, stop: tuple[float, float], route_runs: tuple[tuple[tuple[float, float], ...], ...],
    ) -> float:
        return cls.nearest_route_proximity(stop, route_runs).straight_line_meters

    @classmethod
    def nearest_route_proximity(
        cls, stop: tuple[float, float], route_runs: tuple[tuple[tuple[float, float], ...], ...],
    ) -> RouteProximity:
        nearest = math.inf
        nearest_miles = 0.0
        route_meters = 0.0
        for run in route_runs:
            for start, end in zip(run, run[1:], strict=False):
                distance, fraction = cls._point_to_segment(stop, start, end)
                if distance < nearest:
                    nearest = distance
                    nearest_miles = (route_meters + cls._segment_meters(start, end) * fraction) / 1609.344
                route_meters += cls._segment_meters(start, end)
        if math.isinf(nearest):
            raise ValueError("Runner route geometry has no usable edges for bathroom distance calculations.")
        return RouteProximity(nearest_miles, nearest)

    @classmethod
    def _point_to_segment(
        cls, point: tuple[float, float], start: tuple[float, float], end: tuple[float, float],
    ) -> tuple[float, float]:
        reference_latitude = math.radians(point[0])
        longitude_scale = cls.meters_per_degree_latitude * math.cos(reference_latitude)
        point_xy = (point[1] * longitude_scale, point[0] * cls.meters_per_degree_latitude)
        start_xy = (start[1] * longitude_scale, start[0] * cls.meters_per_degree_latitude)
        end_xy = (end[1] * longitude_scale, end[0] * cls.meters_per_degree_latitude)
        delta_x, delta_y = end_xy[0] - start_xy[0], end_xy[1] - start_xy[1]
        length_squared = delta_x * delta_x + delta_y * delta_y
        if length_squared == 0:
            return math.dist(point_xy, start_xy), 0.0
        fraction = max(0.0, min(1.0, ((point_xy[0] - start_xy[0]) * delta_x + (point_xy[1] - start_xy[1]) * delta_y) / length_squared))
        return math.dist(point_xy, (start_xy[0] + fraction * delta_x, start_xy[1] + fraction * delta_y)), fraction

    @classmethod
    def _segment_meters(cls, start: tuple[float, float], end: tuple[float, float]) -> float:
        latitude_scale = cls.meters_per_degree_latitude
        longitude_scale = latitude_scale * math.cos(math.radians((start[0] + end[0]) / 2))
        return math.hypot((end[1] - start[1]) * longitude_scale, (end[0] - start[0]) * latitude_scale)

    @staticmethod
    def _kml(placemarks: str) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NSTT 2026 - Bathroom Stops</name>
  <description>Research-backed bathroom stop planning layer. It does not change runner geometry. Priorities are official public beach restrooms, grocery backups, coffee-chain backups, then fast-food or branded-gas backups. Confirm access, hours, closures, and any customer-only policy on race day.</description>
  <Style id="beachRestroom"><IconStyle><color>ff00aa00</color><scale>1.15</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>
  <Style id="placesRestroom"><IconStyle><color>ff00aa00</color><scale>1.05</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>
  <Style id="groceryBackup"><IconStyle><color>ff00aa00</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>
  <Style id="coffeeBackup"><IconStyle><color>ffff0000</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/blu-circle.png</href></Icon></IconStyle></Style>
  <Style id="businessBackup"><IconStyle><color>ff808080</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/wht-circle.png</href></Icon></IconStyle></Style>
  <Folder><name>Bathroom stops — priority order</name>
{placemarks}
  </Folder></Document></kml>
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a bathroom-stop KML beside the immutable runner route.")
    parser.add_argument("--runner-kml", type=Path, default=Path("input/Runner.kml"), help="Immutable runner route KML used only to calculate proximity.")
    parser.add_argument("--output-kml", type=Path, default=Path("outputs/NSTT_2026_bathroom_stops.kml"), help="Bathroom-layer KML to create.")
    arguments = parser.parse_args()
    result = BathroomLayerBuilder().build(arguments.runner_kml, arguments.output_kml)
    print(f"Created {arguments.output_kml} with {result.stop_count} researched stops.")


if __name__ == "__main__":
    main()
