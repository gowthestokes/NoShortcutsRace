"""Build a Google My Maps-importable draft of the organizer's course sheet."""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import time
import xml.sax.saxutils
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from nstt_course_planner.route_spec import CAR_CHECKPOINTS, CAR_INSTRUCTIONS, ROUTE_CHECKPOINTS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_GEOCODING_CACHE = PROJECT_ROOT / "data" / "geocoding-cache.json"
DEFAULT_ROUTING_CACHE = PROJECT_ROOT / "data" / "pedestrian-routing-cache.json"
DEFAULT_PATH_CACHE = PROJECT_ROOT / "data" / "path-geometry-cache.json"
DEFAULT_GOOGLE_USAGE = PROJECT_ROOT / "data" / "google-routes-usage.json"
USER_AGENT = "NSTT-course-planner/0.1 (personal relay map)"
GOOGLE_ROUTES_REQUEST_LIMIT = 9_500
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


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


class UnsafePedestrianRouteError(RuntimeError):
    """Raised when routing proposes a non-running transport mode."""


class GoogleRoutesRequestLimitError(RuntimeError):
    """Raised before this planner exceeds its conservative Google request cap."""


# Keep the router coupled to the reviewed organizer turn sheet.
CHECKPOINTS = tuple(Checkpoint(point.label, point.query) for point in ROUTE_CHECKPOINTS)
CAR_LAYER_CHECKPOINTS = tuple(Checkpoint(point.label, point.query) for point in CAR_CHECKPOINTS)

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
        if is_highway_1_pair(*chunk):
            # The turn sheet says "Pick up PCH" and continues to Del Prado.
            # This is a road-alignment visualization, not a claim that every
            # section is pedestrian-safe; the team still needs field review.
            cache_key = route_cache_key(*chunk, travel_mode="DRIVE", variant="highway-1-v1")
            travel_mode = "DRIVE"
            via_points = HIGHWAY_1_VIA_POINTS
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


def write_outputs(
    output_dir: Path,
    points: list[Point],
    segments: list[list[tuple[float, float]]],
    distance_meters: float,
    car_points: list[Point],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    description = (
        "Draft master runner course for the No Shortcuts Time Trial (Santa Monica to San Diego, 23 Oct 2026). "
        "Built from the organizer's published turn list. It intentionally has no pod assignments, relay handoffs, "
        "or vehicle logistics. Important: validate the LA River Trail and coastal portions against the organizer's notes; "
        "this is a planning map, not a safety or navigation authority. Google walking routes are beta and can miss clear sidewalks or paths; "
        "inspect every section before running. The PCH exit is interpreted as Ocean Blue Environmental "
        "at 925 W Esther Street in Long Beach. The line from Ocean Blue to Del Prado is pinned to the organizer's Highway 1/PCH corridor "
        "and is a road-alignment reference, not a pedestrian-safety validation."
    )
    course_geometries = "".join(
        f"<LineString><tessellate>1</tessellate><coordinates>{' '.join(f'{longitude},{latitude},0' for latitude, longitude in segment)}</coordinates></LineString>"
        for segment in segments
    )
    runner_checkpoint_placemarks = "".join(
        f"""
    <Placemark>
      <name>{index:02d} - {xml.sax.saxutils.escape(point.label)}</name>
      <description>{xml.sax.saxutils.escape(point.query)}</description>
      <Point><coordinates>{point.longitude},{point.latitude},0</coordinates></Point>
    </Placemark>"""
        for index, point in enumerate(points, start=1)
    )
    instructions_by_checkpoint: dict[str, list[str]] = {}
    for instruction in CAR_INSTRUCTIONS:
        if instruction.checkpoint_label:
            instructions_by_checkpoint.setdefault(instruction.checkpoint_label, []).append(
                f"{instruction.action.capitalize()}: {instruction.road_or_place}."
            )
    car_placemarks = "".join(
        f"""
    <Placemark>
      <name>{xml.sax.saxutils.escape(point.label)}</name>
      <description>{xml.sax.saxutils.escape('Provisional support-car logistics point. ' + ' '.join(instructions_by_checkpoint.get(point.label, [])))}</description>
      <styleUrl>#carMarker</styleUrl>
      <Point><coordinates>{point.longitude},{point.latitude},0</coordinates></Point>
    </Placemark>"""
        for point in car_points
    )
    distance_miles = distance_meters / 1609.344
    car_folder = f'''    <Folder><name>Support car logistics - provisional</name>
      <description>Support-car markers only. A continuous car route is intentionally omitted until the organizer confirms safe trail-access, Coast Highway rendezvous, and the exit 54C Chevron pins.</description>
{car_placemarks}
    </Folder>
'''
    kml = f'''<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>NSTT 2026 - Master Runner Course (Draft)</name>
    <description>{xml.sax.saxutils.escape(description)}</description>
    <Style id="courseLine"><LineStyle><color>ff1e88e5</color><width>5</width></LineStyle></Style>
    <Style id="carMarker"><IconStyle><color>ff00a5ff</color><scale>1.2</scale></IconStyle></Style>
    <Folder><name>Runner route - organizer-aligned draft</name>
      <Placemark><name>NSTT 2026 master runner course</name>
        <description>{xml.sax.saxutils.escape(description)} Approximate routed distance: {distance_miles:.1f} mi.</description>
        <styleUrl>#courseLine</styleUrl>
        <MultiGeometry>{course_geometries}</MultiGeometry>
      </Placemark>
{runner_checkpoint_placemarks}
    </Folder>
{car_folder}
  </Document>
</kml>
'''
    gpx_segments = "\n".join(
        "  <trkseg>\n" + "\n".join(f'      <trkpt lat="{latitude}" lon="{longitude}"/>' for latitude, longitude in segment) + "\n  </trkseg>"
        for segment in segments
    )
    gpx = f'''<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="NSTT course planner" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>NSTT 2026 Master Runner Course (Draft)</name><desc>{xml.sax.saxutils.escape(description)}</desc></metadata>
  <trk><name>NSTT 2026 Master Runner Course (Draft)</name>
{gpx_segments}
  </trk>
</gpx>
'''
    import_notes = '''NSTT 2026 master runner course - draft

Files
- NSTT_2026_master_runner_course_draft.kml: import into a blank Google My Map.
- NSTT_2026_master_runner_course_draft.gpx: portable route backup; it can also be imported into My Maps or Footpath.

This is intentionally unsegmented: no runner assignments, pod blocks, or handoffs have been added.

Map layers
- Runner route - organizer-aligned draft: the primary runner line plus organizer checkpoints.
- Support car logistics - provisional: support-car access/rendezvous markers. It deliberately has no continuous car line until the organizer confirms safe vehicle access points.

Important verification note
The organizer's turn list is the source of truth. This route uses Google walking data for normal legs and road-alignment geometry for the explicit Sepulveda/W 78th/W 79th turns and the required Highway 1/PCH corridor from Ocean Blue to Del Prado. Those road-aligned portions are not pedestrian-safety validation and must be reviewed in the field, especially at the LA River Trail and Coast Highway portions. The PCH exit is interpreted as Ocean Blue Environmental at 925 W Esther Street, Long Beach. The line has an intentional gap from San Mateo Point to the Chevron restart because the organizer directs the support car—not runners—to use I-5 exit 54C.

Google My Maps import
1. Go to https://www.google.com/mymaps and create a new map.
2. Click Import in its first layer.
3. Select the .kml file.
4. The import creates two layers: Runner route - organizer-aligned draft and Support car logistics - provisional.
'''
    (output_dir / "NSTT_2026_master_runner_course_draft.kml").write_text(kml, encoding="utf-8")
    (output_dir / "NSTT_2026_runner_route_preview.kml").write_text(kml.replace(car_folder, ""), encoding="utf-8")
    (output_dir / "NSTT_2026_master_runner_course_draft.gpx").write_text(gpx, encoding="utf-8")
    (output_dir / "NSTT_2026_master_runner_course_README.txt").write_text(import_notes, encoding="utf-8")


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
    print("Tracing pedestrian routes between organizer checkpoints")
    segments, distance_meters = trace(
        points, routing_cache, args.routing_cache, path_cache, args.path_cache,
        google_api_key, google_usage, args.google_usage,
    )
    car_points: list[Point] = []
    for checkpoint in CAR_LAYER_CHECKPOINTS:
        print(f"Geocoding support-car point: {checkpoint.label}")
        point, from_cache = geocode(checkpoint, geocoding_cache)
        car_points.append(point)
        if not from_cache:
            save_geocoding_cache(args.geocoding_cache, geocoding_cache)
            time.sleep(1.1)
    write_outputs(args.output_dir, points, segments, distance_meters, car_points)
    print(
        f"Created {sum(len(segment) for segment in segments)} runner trace points in {len(segments)} runner segments, "
        f"{distance_meters / 1609.344:.1f} mi, "
        f"{len(points)} runner checkpoints, and {len(car_points)} provisional support-car markers."
    )


if __name__ == "__main__":
    main()
