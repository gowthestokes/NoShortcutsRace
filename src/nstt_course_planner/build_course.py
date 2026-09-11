"""Build a Google My Maps-importable draft of the organizer's course sheet."""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import re
import time
import xml.sax.saxutils
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from nstt_course_planner.route_spec import ROUTE_CHECKPOINTS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_GEOCODING_CACHE = PROJECT_ROOT / "data" / "geocoding-cache.json"
DEFAULT_ROUTING_CACHE = PROJECT_ROOT / "data" / "pedestrian-routing-cache.json"
DEFAULT_PATH_CACHE = PROJECT_ROOT / "data" / "path-geometry-cache.json"
DEFAULT_GOOGLE_USAGE = PROJECT_ROOT / "data" / "google-routes-usage.json"
USER_AGENT = "NSTT-course-planner/0.1 (personal relay map)"
GOOGLE_ROUTES_REQUEST_LIMIT = 9_500
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
MILE_METERS = 1609.344
SEGMENT_NAME_PATTERN = re.compile(r"^Segment\s+(\d{3})([a-z]?)\s+-", re.IGNORECASE)


@dataclass(frozen=True)
class Checkpoint:
    """A route-shaping point taken from the organizer's published turn list."""

    label: str
    query: str


@dataclass(frozen=True)
class Point:
    label: str
    query: str
    latitude: float
    longitude: float
    display_name: str


@dataclass(frozen=True)
class RunnerRouteSection:
    """One independently editable runner-route line for the KML export."""

    label: str
    coordinates: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ApprovedRouteProgress:
    """User-edited one-mile sections that must remain unchanged on rebuild."""

    sections: tuple[RunnerRouteSection, ...]
    through_segment: int


class UnsafePedestrianRouteError(RuntimeError):
    """Raised when routing proposes a non-running transport mode."""


class GoogleRoutesRequestLimitError(RuntimeError):
    """Raised before this planner exceeds its conservative Google request cap."""


# Keep the router coupled to the reviewed organizer turn sheet.
CHECKPOINTS = tuple(Checkpoint(point.label, point.query) for point in ROUTE_CHECKPOINTS)

# A handful of stable route-shaping coordinates avoid ambiguous public geocoder
# results. They remain visible as reference checkpoints in the generated KML.
MANUAL_POINTS = {
    # These are road-name transitions, not normal intersections. Public text
    # geocoders otherwise select same-named streets in central Los Angeles.
    "Washington Blvd / West Washington Blvd": (33.988432, -118.451976, "Washington Boulevard / West Washington transition, Venice, CA"),
    "W 78th St / W 79th St": (33.968500, -118.385000, "West 78th Street / West 79th Street transition, Los Angeles, CA"),
    "Centinela Ave / Sepulveda Blvd": (33.977003, -118.386247, "West Centinela Avenue, Culver City, CA"),
    # The signed entry is beside Rosecrans at the actual LA River Trail, not
    # the generic Long Beach result returned by a text geocoder.
    "LA River Trail entry area": (33.903610, -118.184280, "LA River Trail - Rosecrans Avenue connector, Compton, CA"),
    "Firestone Blvd / Atlantic Ave": (33.951901, -118.183128, "Firestone Boulevard & Atlantic Avenue, South Gate, CA"),
    # Organizer landmark immediately after the PCH underpass.  This explicit
    # pin keeps the interpretation on the mainland rather than treating PCH
    # and 1st Street in Seal Beach as the intended trail exit.
    "PCH / river-trail exit area": (33.773900, -118.202400, "Ocean Blue Environmental, 925 W Esther Street, Long Beach, CA"),
    "Oceanside Coast Highway 101": (33.2265, -117.3885, "Coast Highway 101, Oceanside, CA"),
    "Encinitas Coast Highway 101": (33.0484, -117.2940, "South Coast Highway 101, Encinitas, CA"),
    "Solana Beach Coast Highway 101": (32.9900, -117.2700, "North Coast Highway 101, Solana Beach, CA"),
}


def get_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:  # noqa: S310 - public, fixed API endpoints
        return json.load(response)


def post_json(url: str, payload: object) -> object:
    """Send a JSON request to the public pedestrian-routing service."""
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310 - public, fixed API endpoint
        return json.load(response)


def post_form(url: str, values: dict[str, str]) -> object:
    """Submit a form request to a fixed public map-data endpoint."""
    request = Request(
        url,
        data=urlencode(values).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:  # noqa: S310 - public, fixed API endpoint
        return json.load(response)


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load simple KEY=VALUE entries without adding a runtime dependency."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        os.environ.setdefault(key.strip(), value.strip())


def google_maps_api_key() -> str:
    """Read the local, untracked Google Routes API key."""
    load_dotenv()
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing GOOGLE_MAPS_API_KEY. Add it to the local .env file before building.")
    return api_key


def default_google_routes_usage() -> dict[str, object]:
    return {
        "request_limit": GOOGLE_ROUTES_REQUEST_LIMIT,
        "requests_sent": 0,
        "last_request_at": None,
        "last_request_status": None,
    }


def load_google_routes_usage(path: Path) -> dict[str, object]:
    """Load the local conservative count of billable Google route requests."""
    if not path.exists():
        return default_google_routes_usage()
    usage = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(usage, dict) or not isinstance(usage.get("requests_sent"), int):
        raise RuntimeError(f"Invalid Google Routes usage file: {path}")
    usage["request_limit"] = GOOGLE_ROUTES_REQUEST_LIMIT
    return usage


def save_google_routes_usage(path: Path, usage: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def reserve_google_routes_request(usage: dict[str, object], usage_path: Path) -> None:
    """Persist a conservative request reservation before contacting Google."""
    requests_sent = int(usage["requests_sent"])
    if requests_sent >= GOOGLE_ROUTES_REQUEST_LIMIT:
        raise GoogleRoutesRequestLimitError(
            f"Google Routes request limit reached ({GOOGLE_ROUTES_REQUEST_LIMIT:,}); no request was sent."
        )
    usage["requests_sent"] = requests_sent + 1
    usage["last_request_at"] = datetime.now(UTC).isoformat()
    usage["last_request_status"] = "reserved"
    save_google_routes_usage(usage_path, usage)


def set_google_routes_request_status(usage: dict[str, object], usage_path: Path, status: str) -> None:
    usage["last_request_status"] = status
    save_google_routes_usage(usage_path, usage)


def load_geocoding_cache(cache_path: Path) -> dict[str, dict[str, object]]:
    """Load locally cached public-geocoder results, tolerating a missing cache."""
    if not cache_path.exists():
        return {}
    contents = json.loads(cache_path.read_text(encoding="utf-8"))
    if not isinstance(contents, dict):
        raise RuntimeError(f"Invalid geocoding cache: {cache_path}")
    return contents


def save_geocoding_cache(cache_path: Path, cache: dict[str, dict[str, object]]) -> None:
    """Persist cache entries as they are found so interrupted builds can resume."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def point_from_cache(checkpoint: Checkpoint, cached: dict[str, object]) -> Point:
    """Restore a checkpoint-specific Point from a cached coordinate result."""
    return Point(
        checkpoint.label,
        checkpoint.query,
        float(cached["latitude"]),
        float(cached["longitude"]),
        str(cached["display_name"]),
    )


def geocode(checkpoint: Checkpoint, cache: dict[str, dict[str, object]]) -> tuple[Point, bool]:
    """Resolve a route checkpoint, prioritizing the Census intersection data."""
    if checkpoint.label in MANUAL_POINTS:
        latitude, longitude, display_name = MANUAL_POINTS[checkpoint.label]
        return Point(checkpoint.label, checkpoint.query, latitude, longitude, display_name), True

    if cached := cache.get(checkpoint.query):
        return point_from_cache(checkpoint, cached), True

    if "&" in checkpoint.query:
        census_query = urlencode(
            {
                "address": checkpoint.query.replace("&", "and"),
                "benchmark": "Public_AR_Current",
                "format": "json",
            }
        )
        census_data = get_json(f"https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?{census_query}")
        matches = census_data["result"].get("addressMatches", [])
        if matches:
            match = matches[0]
            coordinates = match["coordinates"]
            point = Point(
                checkpoint.label,
                checkpoint.query,
                float(coordinates["y"]),
                float(coordinates["x"]),
                match["matchedAddress"],
            )
            cache[checkpoint.query] = {
                "latitude": point.latitude,
                "longitude": point.longitude,
                "display_name": point.display_name,
            }
            return point, False

    # When an intersection has no geocoding record, retain its first named
    # road as an approximate route-shaping point instead of silently omitting it.
    query = checkpoint.query
    if "&" in query:
        first_road, location = query.split("&", maxsplit=1)
        query = f"{first_road.strip()}, {','.join(location.split(',')[1:]).strip()}"
    params = urlencode({"q": query, "format": "jsonv2", "limit": "1", "countrycodes": "us"})
    data = get_json(f"https://nominatim.openstreetmap.org/search?{params}")
    if not data:
        raise RuntimeError(f"No geocoding result for {checkpoint.label}: {checkpoint.query}")
    result = data[0]
    point = Point(checkpoint.label, checkpoint.query, float(result["lat"]), float(result["lon"]), result["display_name"])
    cache[checkpoint.query] = {
        "latitude": point.latitude,
        "longitude": point.longitude,
        "display_name": point.display_name,
    }
    return point, False


def decode_polyline(encoded: str, precision: int) -> list[tuple[float, float]]:
    """Decode a standard Google/Valhalla encoded route geometry."""
    latitude = longitude = index = 0
    decoded: list[tuple[float, float]] = []
    while index < len(encoded):
        deltas: list[int] = []
        for _ in range(2):
            shift = value = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                value |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(value >> 1) if value & 1 else value >> 1)
        latitude += deltas[0]
        longitude += deltas[1]
        decoded.append((latitude / 10**precision, longitude / 10**precision))
    return decoded


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Decode Valhalla's default six-decimal encoded route geometry."""
    return decode_polyline(encoded, precision=6)


def route_cache_key(
    start: Point,
    end: Point,
    *,
    travel_mode: str = "WALK",
    variant: str = "",
) -> str:
    """Return a stable cache key for a Google-routed checkpoint pair."""
    suffix = f":{variant}" if variant else ""
    return (
        f"google-{travel_mode.casefold()}:"
        f"{start.latitude:.7f},{start.longitude:.7f}:"
        f"{end.latitude:.7f},{end.longitude:.7f}{suffix}"
    )


def require_land_based_pedestrian_trip(trip: dict[str, object], start: Point, end: Point) -> None:
    """Reject a walking route if the provider says it contains a ferry leg."""
    summary = trip.get("summary", {})
    if isinstance(summary, dict) and summary.get("has_ferry"):
        raise UnsafePedestrianRouteError(
            f"Pedestrian route from {start.label} to {end.label} uses a ferry; "
            "confirm the runner-accessible land route before exporting a map."
        )


def haversine_meters(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Calculate a short geographic edge length without another API request."""
    latitude_1, longitude_1 = map(math.radians, start)
    latitude_2, longitude_2 = map(math.radians, end)
    a = math.sin((latitude_2 - latitude_1) / 2) ** 2 + math.cos(latitude_1) * math.cos(latitude_2) * math.sin((longitude_2 - longitude_1) / 2) ** 2
    return 6_371_000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_kml_coordinates(raw_coordinates: str) -> tuple[tuple[float, float], ...]:
    """Read KML longitude,latitude coordinates into this module's latitude,longitude form."""
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


def load_approved_route_progress(path: Path, through_segment: int) -> ApprovedRouteProgress:
    """Load and validate user-edited route sections exported by Google My Maps."""
    try:
        import xml.etree.ElementTree as element_tree

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
        segment_number = int(match.group(1))
        if segment_number > through_segment:
            continue
        raw_coordinates = placemark.findtext(
            ".//kml:LineString/kml:coordinates", default="", namespaces=namespace
        )
        if not raw_coordinates:
            continue
        # My Maps can split a manually edited line into adjacent pieces while
        # retaining its original name. Keep them in export order and stitch
        # their geometry below.
        found.setdefault(segment_number, []).append(
            RunnerRouteSection(name, parse_kml_coordinates(raw_coordinates))
        )

    if 1 not in found or through_segment not in found:
        raise RuntimeError(
            f"Approved KML must contain Segment 001 and Segment {through_segment:03d}."
        )
    sections_list: list[RunnerRouteSection] = []
    for number in sorted(found):
        pieces = found[number]
        coordinates = list(pieces[0].coordinates)
        for previous, current in zip(pieces, pieces[1:], strict=False):
            if haversine_meters(previous.coordinates[-1], current.coordinates[0]) > 25:
                raise RuntimeError(
                    f"Google My Maps split Segment {number:03d} into pieces that do not connect; "
                    "join or rename those lines before rebuilding."
                )
            coordinates.extend(current.coordinates[1:])
        sections_list.append(RunnerRouteSection(pieces[0].label, tuple(coordinates)))
    sections = tuple(sections_list)
    for previous, current in zip(sections, sections[1:], strict=False):
        if haversine_meters(previous.coordinates[-1], current.coordinates[0]) > 25:
            raise RuntimeError(
                f"{previous.label} does not connect to {current.label}; join their endpoints in My Maps before rebuilding."
            )
    return ApprovedRouteProgress(sections, through_segment)


def approved_mile_markers(progress: ApprovedRouteProgress) -> list[tuple[int, float, float]]:
    """Recreate fixed mile markers along the approved geometry through its named segment."""
    return mile_markers([approved_route_geometry(progress)], progress.through_segment * MILE_METERS)


def approved_route_geometry(progress: ApprovedRouteProgress) -> list[tuple[float, float]]:
    """Join approved My Maps line pieces into the user's exact edited route shape."""
    geometry = list(progress.sections[0].coordinates)
    for section in progress.sections[1:]:
        geometry.extend(section.coordinates[1:])
    return geometry


def approved_route_sections(progress: ApprovedRouteProgress) -> list[RunnerRouteSection]:
    """Restore one editable mile line per approved mile after My Maps merges lines."""
    return runner_route_sections(
        [approved_route_geometry(progress)], progress.through_segment * MILE_METERS
    )


def shoreline_beach_path_geometry(
    start: Point,
    end: Point,
    path_cache: dict[str, dict[str, object]],
    path_cache_path: Path,
) -> list[tuple[float, float]]:
    """Trace the actual named Long Beach Shoreline foot/bike path from OSM."""
    cache_key = "long-beach-shoreline-path:osm-named-ways:v1"
    if cached := path_cache.get(cache_key):
        return [(float(latitude), float(longitude)) for latitude, longitude in cached["geometry"]]

    query = """[out:json];
    way[\"name\"~\"Shoreline Beach (Bike|Pedestrian) Path\",i][\"highway\"](33.74,-118.20,33.77,-118.11);
    out geom;"""
    data = post_form("https://overpass-api.de/api/interpreter", {"data": query})
    elements = data.get("elements", []) if isinstance(data, dict) else []
    nodes: dict[int, tuple[float, float]] = {}
    graph: dict[int, list[tuple[int, float]]] = {}
    for way in elements:
        if not isinstance(way, dict):
            continue
        way_nodes, geometry = way.get("nodes"), way.get("geometry")
        if not isinstance(way_nodes, list) or not isinstance(geometry, list) or len(way_nodes) != len(geometry):
            continue
        for node_id, coordinate in zip(way_nodes, geometry, strict=True):
            if isinstance(node_id, int) and isinstance(coordinate, dict):
                nodes[node_id] = (float(coordinate["lat"]), float(coordinate["lon"]))
        for left, right in zip(way_nodes, way_nodes[1:], strict=False):
            if left not in nodes or right not in nodes:
                continue
            distance = haversine_meters(nodes[left], nodes[right])
            graph.setdefault(left, []).append((right, distance))
            graph.setdefault(right, []).append((left, distance))
    if not nodes:
        raise RuntimeError("OpenStreetMap returned no usable Long Beach Shoreline Beach Path geometry")

    start_node = min(nodes, key=lambda node: haversine_meters((start.latitude, start.longitude), nodes[node]))
    end_node = min(nodes, key=lambda node: haversine_meters((end.latitude, end.longitude), nodes[node]))
    queue: list[tuple[float, int]] = [(0.0, start_node)]
    cost = {start_node: 0.0}
    previous: dict[int, int] = {}
    while queue:
        distance, current = heapq.heappop(queue)
        if current == end_node:
            break
        if distance != cost[current]:
            continue
        for neighbor, edge in graph.get(current, []):
            candidate = distance + edge
            if candidate < cost.get(neighbor, float("inf")):
                cost[neighbor] = candidate
                previous[neighbor] = current
                heapq.heappush(queue, (candidate, neighbor))
    if end_node not in cost:
        raise RuntimeError("The named Long Beach Shoreline Beach Path geometry is not connected end-to-end")
    path_nodes = [end_node]
    while path_nodes[-1] != start_node:
        path_nodes.append(previous[path_nodes[-1]])
    geometry = [nodes[node] for node in reversed(path_nodes)]
    path_cache[cache_key] = {"geometry": geometry, "source": "OpenStreetMap named Shoreline Beach paths"}
    save_geocoding_cache(path_cache_path, path_cache)
    return geometry


def is_shoreline_beach_path_pair(start: Point, end: Point) -> bool:
    return (
        start.label == "Long Beach Shoreline Beach Path - west entrance"
        and end.label == "Long Beach Shoreline Beach Path - east exit"
    )


def is_support_car_transfer_pair(start: Point, end: Point) -> bool:
    """Identify the organizer-directed I-5 transfer, which runners do not run."""
    return (
        start.label == "San Mateo Point"
        and end.label == "Chevron - I-5 exit 54C runner restart"
    )


# Google walking occasionally prioritizes access ramps and short paths over the
# named road sequence. These three organizer-directed turns are therefore
# traced with road geometry, so the exported line follows the stated streets.
STRICT_ROAD_PAIRS = frozenset(
    {
        ("Centinela Ave / Sepulveda Blvd", "Sepulveda Blvd / W 78th St"),
        ("Sepulveda Blvd / W 78th St", "W 78th St / W 79th St"),
        ("W 78th St / W 79th St", "W 79th St / Isis Ave"),
    }
)


# Coast Highway / Highway 1 is the organizer's required runner corridor after
# Ocean Blue.  These are route-shaping points on that roadway—not new runner
# checkpoints—and prevent an engine from substituting a shorter inland route.
HIGHWAY_1_VIA_POINTS = (
    (33.767700, -118.197000),  # Ocean Blvd, Long Beach
    (33.758600, -118.178800),  # Ocean Blvd / Belmont Shore
    (33.744000, -118.105000),  # PCH, Seal Beach
    (33.701000, -118.055000),  # PCH, Huntington Beach
    (33.656000, -118.020000),  # PCH, Costa Mesa
    (33.616000, -117.930000),  # PCH, Corona del Mar
    (33.570000, -117.820000),  # PCH, Laguna Beach
    (33.536000, -117.780000),  # PCH, south Laguna
    (33.505000, -117.735000),  # PCH, Laguna Niguel
    (33.476000, -117.710000),  # PCH, Dana Point
)


def is_strict_road_pair(start: Point, end: Point) -> bool:
    """Return whether this leg must visibly follow the organizer's road names."""
    return (start.label, end.label) in STRICT_ROAD_PAIRS


def is_highway_1_pair(start: Point, end: Point) -> bool:
    """Return whether this leg is the organizer-required Highway 1 corridor."""
    return (
        start.label == "PCH / river-trail exit area"
        and end.label == "Del Prado / Golden Lantern"
    )


def highway_1_via_points_from(start: Point) -> tuple[tuple[float, float], ...]:
    """Keep only Highway 1 pins ahead of a manual eastbound PCH diversion."""
    return tuple(point for point in HIGHWAY_1_VIA_POINTS if point[1] > start.longitude)


def pedestrian_route_payload(chunk: list[Point]) -> dict[str, object]:
    """Ask for the shortest walking route while strongly avoiding ferries."""
    return {
        "locations": [{"lat": point.latitude, "lon": point.longitude} for point in chunk],
        "costing": "pedestrian",
        # A zero ferry preference plus a large ferry penalty tells Valhalla to
        # choose a land route when it exists.  The post-route guard above is
        # still authoritative: an exported runner line may never contain one.
        "costing_options": {"pedestrian": {"use_ferry": 0, "ferry_cost": 43200}},
        "units": "miles",
    }


def google_route_payload(
    start: Point,
    end: Point,
    *,
    travel_mode: str = "WALK",
    via_points: tuple[tuple[float, float], ...] = (),
) -> dict[str, object]:
    """Build an ordered Google Routes request, optionally pinned through vias."""
    payload: dict[str, object] = {
        "origin": {"location": {"latLng": {"latitude": start.latitude, "longitude": start.longitude}}},
        "destination": {"location": {"latLng": {"latitude": end.latitude, "longitude": end.longitude}}},
        "travelMode": travel_mode,
        "languageCode": "en-US",
        "units": "IMPERIAL",
    }
    if via_points:
        payload["intermediates"] = [
            {
                "via": True,
                "location": {"latLng": {"latitude": latitude, "longitude": longitude}},
            }
            for latitude, longitude in via_points
        ]
    return payload


def google_walking_route_payload(start: Point, end: Point) -> dict[str, object]:
    """Build a minimal Google Routes request for one ordered walking leg."""
    return google_route_payload(start, end)


def require_no_ferry_instruction(instructions: list[str], start: Point, end: Point) -> None:
    """Reject a Google walking leg that explicitly tells a runner to use a ferry."""
    if any("ferry" in instruction.casefold() for instruction in instructions):
        raise UnsafePedestrianRouteError(
            f"Walking route from {start.label} to {end.label} includes a ferry instruction; "
            "confirm the runner-accessible land route before exporting a map."
        )


def google_route_distance_meters(route: dict[str, object], geometry: list[tuple[float, float]]) -> float:
    """Use Google's reported distance, or conservatively sum its returned shape."""
    if distance_meters := route.get("distanceMeters"):
        return float(distance_meters)
    return sum(haversine_meters(left, right) for left, right in zip(geometry, geometry[1:], strict=False))


def google_route(
    start: Point,
    end: Point,
    api_key: str,
    usage: dict[str, object],
    usage_path: Path,
    *,
    travel_mode: str = "WALK",
    via_points: tuple[tuple[float, float], ...] = (),
) -> tuple[list[tuple[float, float]], float]:
    """Request one Google route and count it before the billable call."""
    reserve_google_routes_request(usage, usage_path)
    request = Request(
        GOOGLE_ROUTES_URL,
        data=json.dumps(
            google_route_payload(start, end, travel_mode=travel_mode, via_points=via_points)
        ).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.polyline.encodedPolyline,routes.legs.steps.navigationInstruction.instructions",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed Google Routes endpoint
            data = json.load(response)
    except HTTPError as error:
        set_google_routes_request_status(usage, usage_path, f"failed HTTP {error.code}")
        raise RuntimeError(f"Google Routes request failed with HTTP {error.code}; check the key and Routes API setup.") from error
    except OSError as error:
        set_google_routes_request_status(usage, usage_path, "failed network error")
        raise RuntimeError("Google Routes request failed due to a network error.") from error

    routes = data.get("routes", []) if isinstance(data, dict) else []
    if not routes:
        set_google_routes_request_status(usage, usage_path, "failed: no route")
        raise RuntimeError(f"Google Routes returned no {travel_mode.casefold()} route from {start.label} to {end.label}.")
    route = routes[0]
    polyline = route.get("polyline", {}).get("encodedPolyline")
    if not isinstance(polyline, str):
        set_google_routes_request_status(usage, usage_path, "failed: missing polyline")
        raise RuntimeError("Google Routes returned a route without geometry.")
    instructions = [
        str(step.get("navigationInstruction", {}).get("instructions", ""))
        for leg in route.get("legs", [])
        for step in leg.get("steps", [])
    ]
    if travel_mode == "WALK":
        require_no_ferry_instruction(instructions, start, end)
    geometry = decode_polyline(polyline, precision=5)
    set_google_routes_request_status(usage, usage_path, "success")
    return geometry, google_route_distance_meters(route, geometry)


def google_walking_route(
    start: Point,
    end: Point,
    api_key: str,
    usage: dict[str, object],
    usage_path: Path,
) -> tuple[list[tuple[float, float]], float]:
    """Compatibility wrapper for the normal walking profile."""
    return google_route(start, end, api_key, usage, usage_path)


def trace(
    points: list[Point],
    routing_cache: dict[str, dict[str, object]],
    routing_cache_path: Path,
    path_cache: dict[str, dict[str, object]],
    path_cache_path: Path,
    google_api_key: str,
    google_usage: dict[str, object],
    google_usage_path: Path,
    manual_highway_1_resume: bool = False,
) -> tuple[list[list[tuple[float, float]]], float]:
    """Trace the course, using road alignment only where the turn sheet requires it."""
    segments: list[list[tuple[float, float]]] = [[]]
    total_meters = 0.0
    for start in range(len(points) - 1):
        chunk = points[start : start + 2]
        if is_support_car_transfer_pair(*chunk):
            # The organizer says the car uses I-5 to exit 54C and the runner
            # resumes at Chevron.  This is not a runner route through Camp
            # Pendleton, so deliberately leave a visible gap in the KML.
            if segments[-1]:
                segments.append([])
            continue
        if is_highway_1_pair(*chunk) or (
            manual_highway_1_resume and start == 0 and chunk[1].label == "Del Prado / Golden Lantern"
        ):
            # The turn sheet says "Pick up PCH" and continues to Del Prado.
            # This is a road-alignment visualization, not a claim that every
            # section is pedestrian-safe; the team still needs field review.
            variant = "manual-highway-1-v1" if manual_highway_1_resume and start == 0 else "highway-1-v1"
            cache_key = route_cache_key(*chunk, travel_mode="DRIVE", variant=variant)
            travel_mode = "DRIVE"
            via_points = highway_1_via_points_from(chunk[0]) if manual_highway_1_resume and start == 0 else HIGHWAY_1_VIA_POINTS
        elif is_strict_road_pair(*chunk):
            cache_key = route_cache_key(*chunk, travel_mode="DRIVE", variant="organizer-turn-v1")
            travel_mode = "DRIVE"
            via_points = ()
        else:
            cache_key = route_cache_key(*chunk)
            travel_mode = "WALK"
            via_points = ()
        if cached := routing_cache.get(cache_key):
            geometry = [(float(latitude), float(longitude)) for latitude, longitude in cached["geometry"]]
            distance_meters = float(cached["distance_meters"])
        else:
            geometry, distance_meters = google_route(
                chunk[0], chunk[1], google_api_key, google_usage, google_usage_path,
                travel_mode=travel_mode, via_points=via_points,
            )
            routing_cache[cache_key] = {
                "distance_meters": distance_meters,
                "geometry": geometry,
            }
            save_geocoding_cache(routing_cache_path, routing_cache)
            time.sleep(0.25)
        segments[-1].extend(geometry[1:] if segments[-1] else geometry)
        total_meters += distance_meters
    return [segment for segment in segments if segment], total_meters


def interpolate_coordinate(
    start: tuple[float, float], end: tuple[float, float], fraction: float
) -> tuple[float, float]:
    """Return the coordinate fractionally along a short route-geometry edge."""
    start_latitude, start_longitude = start
    end_latitude, end_longitude = end
    return (
        start_latitude + (end_latitude - start_latitude) * fraction,
        start_longitude + (end_longitude - start_longitude) * fraction,
    )


def mile_markers(
    segments: list[list[tuple[float, float]]],
    distance_meters: float | None = None,
    *,
    first_mile_number: int = 1,
) -> list[tuple[int, float, float]]:
    """Place a marker at each completed runner mile without bridging route gaps.

    The organiser's I-5 transfer creates separate geometry segments. Mileage is
    cumulative across those runner segments, but this function never draws or
    places a point in the non-runner gap between them. When supplied, the route
    provider's total distance calibrates the simplified route geometry.
    """
    geometry_meters = sum(
        haversine_meters(start, end)
        for segment in segments
        for start, end in zip(segment, segment[1:], strict=False)
    )
    if geometry_meters == 0:
        return []
    # Google reports route distance separately from its simplified polyline.
    # Scale each polyline edge to that authoritative total so Mile 001, etc.
    # match routed mileage rather than the line's approximation.
    geometry_scale = distance_meters / geometry_meters if distance_meters is not None else 1.0
    markers: list[tuple[int, float, float]] = []
    route_meters = 0.0
    next_mile = first_mile_number
    next_target_meters = MILE_METERS
    for segment in segments:
        for start, end in zip(segment, segment[1:], strict=False):
            edge_meters = haversine_meters(start, end) * geometry_scale
            if edge_meters == 0:
                continue
            while route_meters + edge_meters + 1e-6 >= next_target_meters:
                fraction = max(0.0, min(1.0, (next_target_meters - route_meters) / edge_meters))
                latitude, longitude = interpolate_coordinate(start, end, fraction)
                markers.append((next_mile, latitude, longitude))
                next_mile += 1
                next_target_meters += MILE_METERS
            route_meters += edge_meters
    return markers


def runner_route_sections(
    segments: list[list[tuple[float, float]]],
    distance_meters: float,
    *,
    first_segment_number: int = 1,
    start_label: str = "Start",
) -> list[RunnerRouteSection]:
    """Split runner geometry into independently editable one-mile KML lines.

    Each trace segment is retained as a separate physical path. This matters at
    the organizer-directed I-5 transfer: its two partial mile lines are never
    connected across the non-runner gap.
    """
    geometry_meters = sum(
        haversine_meters(start, end)
        for segment in segments
        for start, end in zip(segment, segment[1:], strict=False)
    )
    if geometry_meters == 0:
        return []
    geometry_scale = distance_meters / geometry_meters
    sections: list[RunnerRouteSection] = []
    route_meters = 0.0
    next_mile = first_segment_number
    next_target_meters = MILE_METERS
    suffix = ""

    for segment_index, segment in enumerate(segments):
        if len(segment) < 2:
            continue
        section_coordinates = [segment[0]]
        for start, end in zip(segment, segment[1:], strict=False):
            edge_meters = haversine_meters(start, end) * geometry_scale
            if edge_meters == 0:
                continue
            while route_meters + edge_meters + 1e-6 >= next_target_meters:
                fraction = max(0.0, min(1.0, (next_target_meters - route_meters) / edge_meters))
                marker = interpolate_coordinate(start, end, fraction)
                if section_coordinates[-1] != marker:
                    section_coordinates.append(marker)
                sections.append(
                    RunnerRouteSection(
                        f"Segment {next_mile:03d}{suffix} - {start_label} to Mile {next_mile:03d}",
                        tuple(section_coordinates),
                    )
                )
                section_coordinates = [marker]
                start_label = f"Mile {next_mile:03d}"
                suffix = ""
                next_mile += 1
                next_target_meters += MILE_METERS
            if section_coordinates[-1] != end:
                section_coordinates.append(end)
            route_meters += edge_meters

        if segment_index < len(segments) - 1:
            if len(section_coordinates) > 1:
                sections.append(
                    RunnerRouteSection(
                        f"Segment {next_mile:03d}{suffix} - {start_label} to runner transfer gap",
                        tuple(section_coordinates),
                    )
                )
            # The next geometry begins after an intentional non-runner gap.
            start_label = "Runner restart"
            suffix = "b"
        elif len(section_coordinates) > 1:
            sections.append(
                RunnerRouteSection(
                    f"Final segment - {start_label} to Finish", tuple(section_coordinates)
                )
            )
    return sections


def write_outputs(
    output_dir: Path,
    points: list[Point],
    segments: list[list[tuple[float, float]]],
    distance_meters: float,
    *,
    route_sections_override: list[RunnerRouteSection] | None = None,
    markers_override: list[tuple[int, float, float]] | None = None,
    gpx_segments_override: list[list[tuple[float, float]]] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    markers = markers_override if markers_override is not None else mile_markers(segments, distance_meters)
    route_sections = route_sections_override if route_sections_override is not None else runner_route_sections(segments, distance_meters)
    gpx_segments_source = gpx_segments_override if gpx_segments_override is not None else segments
    description = (
        "Runner-only draft course for the No Shortcuts Time Trial (Santa Monica to San Diego, 23 Oct 2026). "
        "Built from the organizer's published runner turn list. It includes one checkpoint at each completed route mile "
        "and intentionally excludes vehicle logistics, relay assignments, and handoffs. Important: validate the LA River Trail "
        "and coastal portions against the organizer's notes; "
        "this is a planning map, not a safety or navigation authority. Google walking routes are beta and can miss clear sidewalks or paths; "
        "inspect every section before running. The PCH exit is interpreted as Ocean Blue Environmental "
        "at 925 W Esther Street in Long Beach. The line from Ocean Blue to Del Prado is pinned to the organizer's Highway 1/PCH corridor "
        "and is a road-alignment reference, not a pedestrian-safety validation. The route deliberately has a gap from San Mateo Point "
        "to the Oceanside Chevron restart because the organizer directs the car—not runners—to use I-5 for that transfer."
    )
    route_section_placemarks = "".join(
        f"""
      <Placemark>
        <name>{xml.sax.saxutils.escape(section.label)}</name>
        <description>Editable runner-route segment. Delete or redraw this line independently without changing the other mile segments. Colors alternate green and blue to make adjacent segments easier to distinguish.</description>
        <styleUrl>#{'segmentGreen' if index % 2 else 'segmentBlue'}</styleUrl>
        <LineString><tessellate>1</tessellate><coordinates>{' '.join(f'{longitude},{latitude},0' for latitude, longitude in section.coordinates)}</coordinates></LineString>
      </Placemark>"""
        for index, section in enumerate(route_sections, start=1)
    )
    start_finish_placemarks = "".join(
        f"""
    <Placemark>
      <name>{xml.sax.saxutils.escape(label)}</name>
      <description>{xml.sax.saxutils.escape(point.query)}</description>
      <Point><coordinates>{point.longitude},{point.latitude},0</coordinates></Point>
    </Placemark>"""
        for label, point in (("Start - Santa Monica Pier", points[0]), ("Finish - Milestone Running Shop", points[-1]))
    )
    mile_marker_placemarks = "".join(
        f"""
    <Placemark>
      <name>Mile {mile:03d}</name>
      <description>Completed runner mile {mile}. Position is interpolated along the exported runner route geometry and calibrated to its routed distance.</description>
      <styleUrl>#mileMarker</styleUrl>
      <Point><coordinates>{longitude},{latitude},0</coordinates></Point>
    </Placemark>"""
        for mile, latitude, longitude in markers
    )
    distance_miles = distance_meters / 1609.344
    route_segments_kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>NSTT 2026 - Runner Route Segments</name>
    <description>{xml.sax.saxutils.escape(description)}</description>
    <Style id="segmentGreen"><LineStyle><color>ff00aa00</color><width>5</width></LineStyle></Style>
    <Style id="segmentBlue"><LineStyle><color>ffe57300</color><width>5</width></LineStyle></Style>
    <Folder><name>Runner route segments - alternating green and blue</name>
      <description>{xml.sax.saxutils.escape(description)} Approximate routed distance: {distance_miles:.1f} mi. Each line is independently editable.</description>
{route_section_placemarks}
    </Folder>
  </Document>
</kml>
'''
    checkpoints_kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>NSTT 2026 - Mile Checkpoints</name>
    <description>{xml.sax.saxutils.escape(description)}</description>
    <Style id="mileMarker"><IconStyle><color>ff00a5ff</color><scale>0.9</scale></IconStyle></Style>
    <Folder><name>Runner mile checkpoints</name>
      <description>Start, finish, and one checkpoint at every completed runner mile.</description>
{start_finish_placemarks}
{mile_marker_placemarks}
    </Folder>
  </Document>
</kml>
'''
    gpx_segments = "\n".join(
        "  <trkseg>\n" + "\n".join(f'      <trkpt lat="{latitude}" lon="{longitude}"/>' for latitude, longitude in segment) + "\n  </trkseg>"
        for segment in gpx_segments_source
    )
    gpx_waypoints = "\n".join(
        f'  <wpt lat="{latitude}" lon="{longitude}"><name>Mile {mile:03d}</name><desc>Completed runner mile {mile}.</desc></wpt>'
        for mile, latitude, longitude in markers
    )
    gpx = f'''<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="NSTT course planner" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>NSTT 2026 Runner Route with Mile Checkpoints</name><desc>{xml.sax.saxutils.escape(description)}</desc></metadata>
{gpx_waypoints}
  <trk><name>NSTT 2026 Runner Route</name>
{gpx_segments}
  </trk>
</gpx>
'''
    import_notes = f'''NSTT 2026 runner route - one-mile checkpoints

Files
- NSTT_2026_runner_route_segments.kml: import into one Google My Maps layer; contains individually editable, alternating green/blue route lines.
- NSTT_2026_runner_mile_checkpoints.kml: import into a second Google My Maps layer; contains start, finish, and mile checkpoints.
- NSTT_2026_runner_route.gpx: portable route backup; it can also be imported into My Maps or Footpath.

This export is runner-only: no vehicle routing, support-car markers, runner assignments, pod blocks, or handoffs are included.

Checkpoints
- Mile 001 through Mile {len(markers):03d}: one point at each completed runner mile, positioned along the exported route geometry and calibrated to the routing provider's reported distance.
- Start - Santa Monica Pier and Finish - Milestone Running Shop: course endpoints.
- The final partial mile has no separate marker; the finish point is its endpoint.

Editable route lines
- Each alternating green/blue line is an individual one-mile runner segment, named Segment 001, Segment 002, and so on.
- Delete or redraw one line in Google My Maps without changing the remaining route segments or checkpoints.
- The San Mateo Point to Oceanside restart transfer remains physically split; no orange line crosses that non-runner gap.

Important verification note
The organizer's turn list is the source of truth. This route uses Google walking data for normal legs and road-alignment geometry for the explicit Sepulveda/W 78th/W 79th turns and the required Highway 1/PCH corridor from Ocean Blue to Del Prado. Those road-aligned portions are not pedestrian-safety validation and must be reviewed in the field, especially at the LA River Trail and Coast Highway portions. The PCH exit is interpreted as Ocean Blue Environmental at 925 W Esther Street in Long Beach. The line has an intentional gap from San Mateo Point to the Chevron restart because the organizer directs the car—not runners—to use I-5 exit 54C. Mile numbering continues across runner mileage on either side of that gap, but no line or marker is created within it.

Google My Maps import
1. Go to https://www.google.com/mymaps and create a new map.
2. Click Import in its first layer.
3. Select NSTT_2026_runner_route_segments.kml.
4. Click Add layer, then Import, and select NSTT_2026_runner_mile_checkpoints.kml.
5. You will have separate route-segment and checkpoint layers.
'''
    (output_dir / "NSTT_2026_runner_route_segments.kml").write_text(route_segments_kml, encoding="utf-8")
    (output_dir / "NSTT_2026_runner_mile_checkpoints.kml").write_text(checkpoints_kml, encoding="utf-8")
    (output_dir / "NSTT_2026_runner_route.gpx").write_text(gpx, encoding="utf-8")
    (output_dir / "NSTT_2026_runner_route_README.txt").write_text(import_notes, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for KML, GPX, and import notes.")
    parser.add_argument(
        "--geocoding-cache",
        type=Path,
        default=DEFAULT_GEOCODING_CACHE,
        help="Locally persisted public-geocoder results (default: data/geocoding-cache.json).",
    )
    parser.add_argument(
        "--routing-cache",
        type=Path,
        default=DEFAULT_ROUTING_CACHE,
        help="Locally persisted public pedestrian-routing results (default: data/pedestrian-routing-cache.json).",
    )
    parser.add_argument(
        "--path-cache",
        type=Path,
        default=DEFAULT_PATH_CACHE,
        help="Locally persisted named-path geometry (default: data/path-geometry-cache.json).",
    )
    parser.add_argument(
        "--google-usage",
        type=Path,
        default=DEFAULT_GOOGLE_USAGE,
        help="Conservative local Google Routes request counter (default: data/google-routes-usage.json).",
    )
    parser.add_argument(
        "--approved-segments-kml",
        type=Path,
        help="Google My Maps KML containing user-edited route segments to preserve unchanged.",
    )
    parser.add_argument(
        "--approved-through-segment",
        type=int,
        help="Last consecutively approved segment number in --approved-segments-kml.",
    )
    parser.add_argument(
        "--resume-at-checkpoint",
        default="Del Prado / Golden Lantern",
        help="Organizer checkpoint at which rerouting resumes after approved progress (default: Del Prado / Golden Lantern).",
    )
    args = parser.parse_args()

    geocoding_cache = load_geocoding_cache(args.geocoding_cache)
    routing_cache = load_geocoding_cache(args.routing_cache)
    path_cache = load_geocoding_cache(args.path_cache)
    google_usage = load_google_routes_usage(args.google_usage)
    google_api_key = google_maps_api_key()
    points: list[Point] = []
    for checkpoint in CHECKPOINTS:
        print(f"Geocoding: {checkpoint.label}")
        point, from_cache = geocode(checkpoint, geocoding_cache)
        points.append(point)
        if not from_cache:
            save_geocoding_cache(args.geocoding_cache, geocoding_cache)
            time.sleep(1.1)  # Respect Nominatim's public-service request policy.
    if args.approved_segments_kml:
        if args.approved_through_segment is None:
            raise RuntimeError("--approved-through-segment is required with --approved-segments-kml.")
        progress = load_approved_route_progress(
            args.approved_segments_kml, args.approved_through_segment
        )
        try:
            resume_index = next(
                index for index, point in enumerate(points) if point.label == args.resume_at_checkpoint
            )
        except StopIteration as error:
            raise RuntimeError(
                f"Unknown resume checkpoint: {args.resume_at_checkpoint!r}. Use an organizer checkpoint label."
            ) from error
        if resume_index == 0:
            raise RuntimeError("The resume checkpoint must be after the course start.")
        approved_geometry = approved_route_geometry(progress)
        approved_sections = approved_route_sections(progress)
        approved_markers = approved_mile_markers(progress)
        latitude, longitude = approved_geometry[-1]
        manual_resume = Point(
            f"Manual runner resume after Segment {progress.through_segment:03d}",
            "Approved My Maps route endpoint",
            latitude,
            longitude,
            "Approved My Maps route endpoint",
        )
        print(
            f"Preserving approved My Maps segments through {progress.sections[-1].label}; "
            f"rerouting from its endpoint to {args.resume_at_checkpoint}."
        )
        downstream_segments, downstream_distance_meters = trace(
            [manual_resume, *points[resume_index:]],
            routing_cache,
            args.routing_cache,
            path_cache,
            args.path_cache,
            google_api_key,
            google_usage,
            args.google_usage,
            manual_highway_1_resume=True,
        )
        # Google snaps an arbitrary My Maps endpoint to its nearest routable
        # road coordinate. Retain the user's exact final vertex so Segment 043
        # visibly joins their approved Segment 042 instead of leaving a gap.
        if downstream_segments and downstream_segments[0][0] != (latitude, longitude):
            downstream_segments[0].insert(0, (latitude, longitude))
        sections = [*approved_sections, *runner_route_sections(
            downstream_segments,
            downstream_distance_meters,
            first_segment_number=progress.through_segment + 1,
            start_label=f"Mile {progress.through_segment:03d}",
        )]
        markers = [
            *approved_markers,
            *mile_markers(
                downstream_segments,
                downstream_distance_meters,
                first_mile_number=progress.through_segment + 1,
            ),
        ]
        output_segments = [
            approved_geometry,
            *downstream_segments,
        ]
        distance_meters = progress.through_segment * MILE_METERS + downstream_distance_meters
        write_outputs(
            args.output_dir,
            points,
            downstream_segments,
            distance_meters,
            route_sections_override=sections,
            markers_override=markers,
            gpx_segments_override=output_segments,
        )
    else:
        print("Tracing pedestrian routes between organizer checkpoints")
        output_segments, distance_meters = trace(
            points, routing_cache, args.routing_cache, path_cache, args.path_cache,
            google_api_key, google_usage, args.google_usage,
        )
        write_outputs(args.output_dir, points, output_segments, distance_meters)
    print(
        f"Created {sum(len(segment) for segment in output_segments)} runner trace points in {len(output_segments)} runner segments, "
        f"{distance_meters / 1609.344:.1f} mi, "
        f"{len(markers) if args.approved_segments_kml else len(mile_markers(output_segments, distance_meters))} one-mile checkpoints."
    )


if __name__ == "__main__":
    main()
