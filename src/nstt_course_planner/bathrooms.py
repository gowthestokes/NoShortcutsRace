"""Build a research-backed bathroom-stop layer beside an immutable runner route."""

from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as element_tree
import xml.sax.saxutils
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class BathroomCategory(Enum):
    """Stop categories in the team's required planning order."""

    OFFICIAL_BEACH = (1, "Official beach restroom", "beachRestroom")
    GROCERY = (2, "Grocery-store backup", "groceryBackup")
    COFFEE = (3, "Coffee-chain backup", "coffeeBackup")
    FAST_FOOD_OR_GAS = (4, "Fast-food or branded-gas backup", "businessBackup")

    @property
    def priority(self) -> int:
        return self.value[0]

    @property
    def display_name(self) -> str:
        return self.value[1]

    @property
    def style_id(self) -> str:
        return self.value[2]


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


# Official locations are included only when the responsible public agency
# explicitly lists restrooms. Business entries remain fallbacks because access
# policies can change without notice.
BATHROOM_STOPS: tuple[BathroomStop, ...] = (
    BathroomStop(
        "Santa Monica State Beach / Pier restrooms", BathroomCategory.OFFICIAL_BEACH,
        34.0089, -118.4972, "Santa Monica State Beach, near Santa Monica Pier, Santa Monica, CA 90401",
        "https://www.santamonica.gov/places/parks/santa-monica-state-beach",
        "City of Santa Monica lists restrooms at Santa Monica State Beach. Confirm beach-facility hours on race day.",
    ),
    BathroomStop(
        "Huntington State Beach — Magnolia-area restrooms", BathroomCategory.OFFICIAL_BEACH,
        33.6675, -118.0128, "Huntington State Beach, near Magnolia St and Pacific Coast Hwy, Huntington Beach, CA 92646",
        "https://www.parks.ca.gov/AccessibleFeatures/Details/643",
        "California State Parks lists usable restrooms and drinking fountains along the beach bike trail. Confirm access from the runner corridor.",
    ),
    BathroomStop(
        "Doheny State Beach — North Day-Use restrooms", BathroomCategory.OFFICIAL_BEACH,
        33.4606, -117.6854, "25300 Dana Point Harbor Dr, Dana Point, CA 92629",
        "https://www.parks.ca.gov/AccessibleFeatures/Details/645",
        "California State Parks lists accessible restrooms and outdoor rinsing showers throughout the day-use area. Day-use entry rules may apply.",
    ),
    BathroomStop(
        "San Clemente State Beach — day-use restrooms", BathroomCategory.OFFICIAL_BEACH,
        33.4130, -117.5920, "225 Avenida Calafia, San Clemente, CA 92672",
        "https://www.parks.ca.gov/?page_id=646",
        "California State Parks lists restrooms, showers, and drinking water. Confirm the park entrance and facility availability before race day.",
    ),
    BathroomStop(
        "Carlsbad State Beach — Tamarack restroom/shower", BathroomCategory.OFFICIAL_BEACH,
        33.1583, -117.3506, "Tamarack Ave beach access, Carlsbad, CA 92008",
        "https://www.carlsbadca.gov/residents/about-carlsbad/beaches/about-carlsbad-beaches",
        "City of Carlsbad lists public restrooms and showers at both ends of the Carlsbad State Beach seawall path, including Tamarack access.",
    ),
    BathroomStop(
        "Moonlight Beach restrooms and showers", BathroomCategory.OFFICIAL_BEACH,
        33.0467, -117.2978, "400 B St, Encinitas, CA 92024",
        "https://www.encinitasca.gov/government/departments/parks-recreation-cultural-arts/parks-beaches-trails/beaches/",
        "City of Encinitas lists restrooms and showers at Moonlight Beach.",
    ),
    BathroomStop(
        "Kellogg Park / La Jolla Shores restrooms", BathroomCategory.OFFICIAL_BEACH,
        32.8578, -117.2562, "8300 Camino del Oro, La Jolla, CA 92037",
        "https://www.sandiego.gov/park-and-recreation/parks/regional/shoreline/kelloggpark",
        "City of San Diego lists several restrooms with showers. Park hours are listed as 4 a.m.–10 p.m.; confirm race-day status.",
    ),
    BathroomStop(
        "Pacific Beach public restroom area", BathroomCategory.OFFICIAL_BEACH,
        32.7971, -117.2563, "Pacific Beach boardwalk area, San Diego, CA 92109",
        "https://www.sandiego.gov/lifeguards/safety/bchreg",
        "City of San Diego lists public restrooms and showers at Pacific Beach. Use only if safely accessible from the runner route.",
    ),
    BathroomStop(
        "Ralphs — South San Clemente", BathroomCategory.GROCERY,
        33.4146, -117.6092, "903 S El Camino Real, San Clemente, CA 92672",
        "https://www.ralphs.com/stores/grocery/ca/san-clemente/s-san-clemente/703/00221",
        "Official Ralphs location. Confirm hours and restroom access; this is not a public restroom.",
    ),
    BathroomStop(
        "Starbucks — Carlsbad Village backup", BathroomCategory.COFFEE,
        33.1604, -117.3505, "Carlsbad Village / Carlsbad Blvd area, Carlsbad, CA 92008",
        "https://www.starbucks.com/store-locator",
        "Use the official Starbucks locator to confirm the exact storefront, hours, and restroom access before race day.",
    ),
    BathroomStop(
        "Starbucks — La Jolla Village backup", BathroomCategory.COFFEE,
        32.8323, -117.2741, "Girard Ave / Pearl St area, La Jolla, CA 92037",
        "https://www.starbucks.com/store-locator",
        "Use the official Starbucks locator to confirm the exact storefront, hours, and restroom access before race day.",
    ),
    BathroomStop(
        "Chevron — Oceanside runner restart", BathroomCategory.FAST_FOOD_OR_GAS,
        33.2093, -117.3875, "Chevron near I-5 Exit 54C / Coast Highway, Oceanside, CA 92054",
        "https://www.chevronwithtechron.com/station-finder",
        "Organizer-designated runner restart after the San Mateo-to-Chevron support-car transfer. Confirm hours and restroom access.",
    ),
)


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

    def build(self, source_path: Path, output_path: Path) -> None:
        route_runs = RunnerRouteKml.line_runs(source_path)
        placemarks = "\n".join(self._placemark(stop, route_runs) for stop in sorted(BATHROOM_STOPS, key=lambda stop: (stop.category.priority, stop.name)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self._kml(placemarks), encoding="utf-8")

    def _placemark(self, stop: BathroomStop, route_runs: tuple[tuple[tuple[float, float], ...], ...]) -> str:
        nearest_meters = self.nearest_route_distance_meters((stop.latitude, stop.longitude), route_runs)
        description = (
            f"Priority {stop.category.priority}: {stop.category.display_name}.<br/>"
            f"Address: {stop.address}<br/>"
            f"Nearest runner geometry: approximately {nearest_meters / 1609.344:.2f} mi straight-line "
            "(not a walking detour).<br/>"
            f"Verification: {stop.source_note}<br/>"
            f"Source: <a href=\"{stop.source_url}\">official / current listing</a>"
        )
        return f'''    <Placemark><name>{xml.sax.saxutils.escape(stop.name)}</name>
      <description><![CDATA[{description}]]></description>
      <styleUrl>#{stop.category.style_id}</styleUrl>
      <Point><coordinates>{stop.longitude},{stop.latitude},0</coordinates></Point>
    </Placemark>'''

    @classmethod
    def nearest_route_distance_meters(
        cls, stop: tuple[float, float], route_runs: tuple[tuple[tuple[float, float], ...], ...],
    ) -> float:
        nearest = math.inf
        for run in route_runs:
            for start, end in zip(run, run[1:], strict=False):
                nearest = min(nearest, cls._point_to_segment_meters(stop, start, end))
        if math.isinf(nearest):
            raise ValueError("Runner route geometry has no usable edges for bathroom distance calculations.")
        return nearest

    @classmethod
    def _point_to_segment_meters(
        cls, point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
        reference_latitude = math.radians(point[0])
        longitude_scale = cls.meters_per_degree_latitude * math.cos(reference_latitude)
        point_xy = (point[1] * longitude_scale, point[0] * cls.meters_per_degree_latitude)
        start_xy = (start[1] * longitude_scale, start[0] * cls.meters_per_degree_latitude)
        end_xy = (end[1] * longitude_scale, end[0] * cls.meters_per_degree_latitude)
        delta_x, delta_y = end_xy[0] - start_xy[0], end_xy[1] - start_xy[1]
        length_squared = delta_x * delta_x + delta_y * delta_y
        if length_squared == 0:
            return math.dist(point_xy, start_xy)
        fraction = max(0.0, min(1.0, ((point_xy[0] - start_xy[0]) * delta_x + (point_xy[1] - start_xy[1]) * delta_y) / length_squared))
        return math.dist(point_xy, (start_xy[0] + fraction * delta_x, start_xy[1] + fraction * delta_y))

    @staticmethod
    def _kml(placemarks: str) -> str:
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NSTT 2026 - Bathroom Stops</name>
  <description>Research-backed bathroom stop planning layer. It does not change runner geometry. Priorities are official public beach restrooms, grocery backups, coffee-chain backups, then fast-food or branded-gas backups. Confirm access, hours, closures, and any customer-only policy on race day.</description>
  <Style id="beachRestroom"><IconStyle><color>ff00aa00</color><scale>1.15</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>
  <Style id="groceryBackup"><IconStyle><color>ffb46900</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/purple-circle.png</href></Icon></IconStyle></Style>
  <Style id="coffeeBackup"><IconStyle><color>ff336699</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/brown-circle.png</href></Icon></IconStyle></Style>
  <Style id="businessBackup"><IconStyle><color>ff00a5ff</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/orange-circle.png</href></Icon></IconStyle></Style>
  <Folder><name>Bathroom stops — priority order</name>
{placemarks}
  </Folder></Document></kml>
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a bathroom-stop KML beside the immutable runner route.")
    parser.add_argument("--runner-kml", type=Path, default=Path("input/Runner.kml"), help="Immutable runner route KML used only to calculate proximity.")
    parser.add_argument("--output-kml", type=Path, default=Path("outputs/NSTT_2026_bathroom_stops.kml"), help="Bathroom-layer KML to create.")
    arguments = parser.parse_args()
    BathroomLayerBuilder().build(arguments.runner_kml, arguments.output_kml)


if __name__ == "__main__":
    main()
