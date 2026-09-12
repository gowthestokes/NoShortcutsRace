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


@dataclass(frozen=True)
class RouteProximity:
    """The nearest position on the runner geometry, excluding support-car gaps."""

    runner_miles: float
    straight_line_meters: float


@dataclass(frozen=True)
class CoverageGap:
    """An interval without a researched bathroom option near its midpoint."""

    start_miles: float
    end_miles: float

    @property
    def length_miles(self) -> float:
        return self.end_miles - self.start_miles


@dataclass(frozen=True)
class BathroomLayerBuildResult:
    """A generated layer and the intervals that still need field verification."""

    stop_count: int
    gaps: tuple[CoverageGap, ...]


OFFICIAL_SOURCES = {
    "santa_monica": "https://www.santamonica.gov/places/parks/santa-monica-state-beach",
    "venice": "https://beaches.lacounty.gov/venice-beach/",
    "bolsa_chica": "https://www.parks.ca.gov/AccessibleFeatures/Details/642",
    "huntington": "https://www.parks.ca.gov/AccessibleFeatures/Details/643",
    "laguna": "https://www.lagunabeachcity.net/government/departments/marine-safety/visiting-our-beaches",
    "doheny": "https://www.parks.ca.gov/AccessibleFeatures/Details/645",
    "san_clemente": "https://www.parks.ca.gov/?page_id=646",
    "carlsbad": "https://www.carlsbadca.gov/residents/about-carlsbad/beaches/about-carlsbad-beaches",
    "encinitas": "https://www.encinitasca.gov/government/departments/parks-recreation-cultural-arts/parks-beaches-trails/beaches/",
    "torrey_pines": "https://parks.ca.gov/AccessibleFeatures/Details/658",
    "del_mar": "https://www.delmar.ca.us/facilities/facility/details/Powerhouse-Park-10",
    "la_jolla": "https://www.sandiego.gov/lifeguards/beaches/cove",
    "kellogg": "https://www.sandiego.gov/park-and-recreation/parks/regional/shoreline/kelloggpark",
    "pacific_beach": "https://www.sandiego.gov/lifeguards/safety/bchreg",
}


def official_stop(name: str, latitude: float, longitude: float, address: str, source: str, note: str) -> BathroomStop:
    return BathroomStop(name, BathroomCategory.OFFICIAL_BEACH, latitude, longitude, address, source, note)


def business_stop(name: str, category: BathroomCategory, latitude: float, longitude: float, address: str, source: str) -> BathroomStop:
    return BathroomStop(
        name, category, latitude, longitude, address, source,
        "Business backup only; confirm race-day hours and customer restroom access before relying on it.",
    )


# Official stops are supported by the responsible agency. Private businesses
# are deliberately kept as fallbacks because restroom access can change.
BATHROOM_STOPS: tuple[BathroomStop, ...] = (
    official_stop("Santa Monica State Beach / Pier restrooms", 34.0089, -118.4972, "Santa Monica State Beach near Santa Monica Pier, Santa Monica, CA 90401", OFFICIAL_SOURCES["santa_monica"], "City of Santa Monica lists beach restrooms; confirm facility hours on race day."),
    official_stop("Venice Beach — Washington Boulevard restroom area", 33.9857, -118.4720, "Washington Blvd / Ocean Front Walk, Venice, CA 90291", OFFICIAL_SOURCES["venice"], "Los Angeles County lists restrooms and showers at Venice Beach."),
    official_stop("Bolsa Chica State Beach — Warner-area restrooms", 33.7042, -118.0525, "17851 Pacific Coast Hwy, Huntington Beach, CA 92649", OFFICIAL_SOURCES["bolsa_chica"], "California State Parks lists restrooms and drinking water within each lot along the trail."),
    official_stop("Bolsa Chica State Beach — Seapoint-area restrooms", 33.6874, -118.0380, "Pacific Coast Hwy near Seapoint St, Huntington Beach, CA 92649", OFFICIAL_SOURCES["bolsa_chica"], "California State Parks lists restrooms and drinking water within each lot along the trail."),
    official_stop("Huntington State Beach — Magnolia-area restrooms", 33.6675, -118.0128, "Huntington State Beach near Magnolia St and Pacific Coast Hwy, Huntington Beach, CA 92646", OFFICIAL_SOURCES["huntington"], "California State Parks lists restrooms and drinking fountains along the beach bike trail."),
    official_stop("Huntington State Beach — southern trail restrooms", 33.6495, -117.9916, "Huntington State Beach near Brookhurst St, Huntington Beach, CA 92646", OFFICIAL_SOURCES["huntington"], "California State Parks lists restrooms and drinking fountains along the beach bike trail."),
    official_stop("Laguna Beach — Main Beach public restrooms", 33.5426, -117.7838, "Main Beach, Laguna Beach, CA 92651", OFFICIAL_SOURCES["laguna"], "City of Laguna Beach lists Main Beach public restrooms and outdoor showers."),
    official_stop("Laguna Beach — Aliso Creek Beach restrooms", 33.5097, -117.7548, "31106 S Coast Hwy, Laguna Beach, CA 92651", OFFICIAL_SOURCES["laguna"], "City of Laguna Beach lists Aliso Creek Beach public restrooms and outdoor showers."),
    official_stop("Doheny State Beach — North Day-Use restrooms", 33.4606, -117.6854, "25300 Dana Point Harbor Dr, Dana Point, CA 92629", OFFICIAL_SOURCES["doheny"], "California State Parks lists accessible restrooms and outdoor rinsing showers in the day-use area."),
    official_stop("Capistrano Beach public restroom area", 33.4582, -117.6718, "Capistrano Beach Park, Dana Point, CA 92624", "https://www.danapoint.org/department/general-services/parks/parks-trails/capistrano-beach-park", "City park listing identifies restroom facilities; confirm beach access before race day."),
    official_stop("San Clemente State Beach — day-use restrooms", 33.4130, -117.5920, "225 Avenida Calafia, San Clemente, CA 92672", OFFICIAL_SOURCES["san_clemente"], "California State Parks lists restrooms, showers, and drinking water."),
    official_stop("Oceanside Harbor Beach public restrooms", 33.2081, -117.3952, "Oceanside Harbor Beach, Oceanside, CA 92054", "https://www.ci.oceanside.ca.us/government/parks-recreation/parks-beaches-and-trails/beaches", "City beach listing identifies public amenities; confirm the runner-accessible entrance."),
    official_stop("Carlsbad State Beach — Tamarack restroom/shower", 33.1583, -117.3506, "Tamarack Ave beach access, Carlsbad, CA 92008", OFFICIAL_SOURCES["carlsbad"], "City of Carlsbad lists public restrooms and showers at the seawall path."),
    official_stop("South Carlsbad State Beach restrooms", 33.1200, -117.3208, "South Carlsbad State Beach, Carlsbad, CA 92008", "https://www.parks.ca.gov/?page_id=660", "California State Parks lists restrooms and showers; confirm entry/access from Coast Highway."),
    official_stop("Moonlight Beach restrooms and showers", 33.0467, -117.2978, "400 B St, Encinitas, CA 92024", OFFICIAL_SOURCES["encinitas"], "City of Encinitas lists restrooms and showers at Moonlight Beach."),
    official_stop("Cardiff State Beach public restrooms", 33.0201, -117.2817, "2488 Highway 101, Cardiff-by-the-Sea, CA 92007", "https://www.parks.ca.gov/?page_id=660", "California State Parks lists facilities at Cardiff State Beach; confirm the accessible beach entry."),
    official_stop("Fletcher Cove public restrooms", 32.9929, -117.2746, "111 S Sierra Ave, Solana Beach, CA 92075", "https://www.cityofsolanabeach.org/enjoy-sb/beaches-parks/fletcher-cove", "City of Solana Beach identifies public facilities at Fletcher Cove; confirm hours and access."),
    official_stop("Powerhouse Park public restrooms", 32.9595, -117.2657, "Coast Blvd, Del Mar, CA 92014", OFFICIAL_SOURCES["del_mar"], "City of Del Mar lists accessible public restrooms, showers, and water at Powerhouse Park."),
    official_stop("Torrey Pines State Beach — North Beach restrooms", 32.9347, -117.2587, "Carmel Valley Rd / McGonigle Rd, San Diego, CA 92037", OFFICIAL_SOURCES["torrey_pines"], "California State Parks lists restrooms, showers, and a ramp at North Beach."),
    official_stop("Torrey Pines State Beach — South Beach restrooms", 32.9212, -117.2552, "N Torrey Pines Rd beach entrance, San Diego, CA 92037", OFFICIAL_SOURCES["torrey_pines"], "California State Parks lists restrooms at the South Beach parking area."),
    official_stop("Kellogg Park / La Jolla Shores restrooms", 32.8578, -117.2562, "8300 Camino del Oro, La Jolla, CA 92037", OFFICIAL_SOURCES["kellogg"], "City of San Diego lists several restrooms with showers; published park hours are 4 a.m.–10 p.m."),
    official_stop("La Jolla Cove public restrooms and showers", 32.8508, -117.2723, "1100 Coast Blvd, La Jolla, CA 92037", OFFICIAL_SOURCES["la_jolla"], "City of San Diego lists restrooms and showers at La Jolla Cove."),
    official_stop("Pacific Beach public restroom area", 32.7971, -117.2563, "Pacific Beach boardwalk area, San Diego, CA 92109", OFFICIAL_SOURCES["pacific_beach"], "City of San Diego lists public restrooms and showers at Pacific Beach."),
    business_stop("Vons / Target Starbucks — Sepulveda backup", BathroomCategory.COFFEE, 33.9846, -118.3944, "6000 Sepulveda Blvd, Culver City, CA 90230", "https://www.target.com/sl/culver-city-westfield-mall/2632/starbucks"),
    business_stop("Starbucks — Firestone and Long Beach backup", BathroomCategory.COFFEE, 33.955165, -118.219045, "8924 Long Beach Blvd, South Gate, CA 90280", "https://www.starbucks.com/store-locator"),
    business_stop("Starbucks — Firestone and California backup", BathroomCategory.COFFEE, 33.9542, -118.2061, "4704 Firestone Blvd, South Gate, CA 90280", "https://www.starbucks.com/store-locator/store/1019314"),
    business_stop("Chevron — Atlantic Avenue backup", BathroomCategory.FAST_FOOD_OR_GAS, 33.9291, -118.1850, "11401 Atlantic Ave, Lynwood, CA 90262", "https://www.chevronwithtechron.com/station/11401-Atlantic-Ave-Lynwood-CA-90262-id90495"),
    business_stop("Ralphs — South San Clemente", BathroomCategory.GROCERY, 33.4146, -117.6092, "903 S El Camino Real, San Clemente, CA 92672", "https://www.ralphs.com/stores/grocery/ca/san-clemente/s-san-clemente/703/00221"),
    business_stop("Starbucks — Carlsbad Village backup", BathroomCategory.COFFEE, 33.1604, -117.3505, "Carlsbad Village / Carlsbad Blvd area, Carlsbad, CA 92008", "https://www.starbucks.com/store-locator"),
    business_stop("Starbucks — La Jolla Village backup", BathroomCategory.COFFEE, 32.8323, -117.2741, "Girard Ave / Pearl St area, La Jolla, CA 92037", "https://www.starbucks.com/store-locator"),
    business_stop("Chevron — Oceanside runner restart", BathroomCategory.FAST_FOOD_OR_GAS, 33.2093, -117.3875, "Chevron near I-5 Exit 54C / Coast Highway, Oceanside, CA 92054", "https://www.chevronwithtechron.com/station-finder"),
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

    def build(self, source_path: Path, output_path: Path) -> BathroomLayerBuildResult:
        route_runs = RunnerRouteKml.line_runs(source_path)
        placemarks = "\n".join(self._placemark(stop, route_runs) for stop in sorted(BATHROOM_STOPS, key=lambda stop: (stop.category.priority, stop.name)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self._kml(placemarks), encoding="utf-8")
        return BathroomLayerBuildResult(
            len(BATHROOM_STOPS),
            BathroomCoverageAnalyzer(self).gaps(BATHROOM_STOPS, route_runs),
        )

    def _placemark(self, stop: BathroomStop, route_runs: tuple[tuple[tuple[float, float], ...], ...]) -> str:
        proximity = self.nearest_route_proximity((stop.latitude, stop.longitude), route_runs)
        description = (
            f"Priority {stop.category.priority}: {stop.category.display_name}.<br/>"
            f"Address: {stop.address}<br/>"
            f"Approximate runner mile: {proximity.runner_miles:.1f}.<br/>"
            f"Nearest runner geometry: approximately {proximity.straight_line_meters / 1609.344:.2f} mi straight-line "
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
  <Style id="groceryBackup"><IconStyle><color>ffb46900</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/purple-circle.png</href></Icon></IconStyle></Style>
  <Style id="coffeeBackup"><IconStyle><color>ff336699</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/brown-circle.png</href></Icon></IconStyle></Style>
  <Style id="businessBackup"><IconStyle><color>ff00a5ff</color><Icon><href>http://maps.google.com/mapfiles/kml/paddle/orange-circle.png</href></Icon></IconStyle></Style>
  <Folder><name>Bathroom stops — priority order</name>
{placemarks}
  </Folder></Document></kml>
'''


class BathroomCoverageAnalyzer:
    """Finds runner-mile intervals without a researched bathroom option."""

    def __init__(self, builder: BathroomLayerBuilder | None = None) -> None:
        self.builder = builder or BathroomLayerBuilder()

    def gaps(
        self,
        stops: tuple[BathroomStop, ...],
        route_runs: tuple[tuple[tuple[float, float], ...], ...],
        maximum_spacing_miles: float = 3.0,
    ) -> tuple[CoverageGap, ...]:
        if maximum_spacing_miles <= 0:
            raise ValueError("Maximum bathroom spacing must be positive.")
        stop_miles = sorted(
            self.builder.nearest_route_proximity((stop.latitude, stop.longitude), route_runs).runner_miles
            for stop in stops
        )
        boundaries = (0.0, *stop_miles, self.route_length_miles(route_runs))
        return tuple(
            CoverageGap(start, end)
            for start, end in zip(boundaries, boundaries[1:], strict=False)
            if end - start > maximum_spacing_miles
        )

    def route_length_miles(self, route_runs: tuple[tuple[tuple[float, float], ...], ...]) -> float:
        return sum(
            self.builder._segment_meters(start, end)
            for run in route_runs
            for start, end in zip(run, run[1:], strict=False)
        ) / 1609.344


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a bathroom-stop KML beside the immutable runner route.")
    parser.add_argument("--runner-kml", type=Path, default=Path("input/Runner.kml"), help="Immutable runner route KML used only to calculate proximity.")
    parser.add_argument("--output-kml", type=Path, default=Path("outputs/NSTT_2026_bathroom_stops.kml"), help="Bathroom-layer KML to create.")
    arguments = parser.parse_args()
    result = BathroomLayerBuilder().build(arguments.runner_kml, arguments.output_kml)
    print(f"Created {arguments.output_kml} with {result.stop_count} researched stops.")
    if result.gaps:
        intervals = ", ".join(f"{gap.start_miles:.1f}–{gap.end_miles:.1f} mi" for gap in result.gaps)
        print(f"No researched nearby stop in these >3 mi intervals: {intervals}")


if __name__ == "__main__":
    main()
