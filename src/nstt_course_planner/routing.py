"""Google Routes planning service and runner-route policy."""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nstt_course_planner.config import (
    GOOGLE_ROUTES_URL,
    HIGHWAY_1_VIA_POINTS,
    USER_AGENT,
)
from nstt_course_planner.geometry import RouteGeometry
from nstt_course_planner.models import Point, UnsafePedestrianRouteError
from nstt_course_planner.storage import GoogleUsageTracker, JsonStore
from nstt_course_planner.utils import PolylineCodec

STRICT_ROAD_PAIRS = frozenset({
    ("Centinela Ave / Sepulveda Blvd", "Sepulveda Blvd / W 78th St"),
    ("Sepulveda Blvd / W 78th St", "W 78th St / W 79th St"),
    ("W 78th St / W 79th St", "W 79th St / Isis Ave"),
})


class RunnerRouter:
    """Applies organizer routing constraints and caches Google Routes results."""

    def __init__(self, routing_cache: dict[str, dict[str, object]], routing_cache_path: Path, api_key: str, usage: dict[str, object], usage_path: Path) -> None:
        self.routing_cache = routing_cache
        self.routing_cache_path = routing_cache_path
        self.api_key = api_key
        self.usage = usage
        self.usage_path = usage_path

    @staticmethod
    def route_cache_key(start: Point, end: Point, *, travel_mode: str = "WALK", variant: str = "") -> str:
        suffix = f":{variant}" if variant else ""
        return f"google-{travel_mode.casefold()}:{start.latitude:.7f},{start.longitude:.7f}:{end.latitude:.7f},{end.longitude:.7f}{suffix}"

    @staticmethod
    def is_i5_transfer_pair(start: Point, end: Point) -> bool:
        """Identify the requested I-5 transfer to the Exit 54C Chevron restart."""
        return end.label == "Chevron - I-5 exit 54C runner restart" and (
            start.label == "San Mateo Point"
            or start.label.startswith("Manual runner resume after Segment")
        )

    @staticmethod
    def is_strict_road_pair(start: Point, end: Point) -> bool:
        return (start.label, end.label) in STRICT_ROAD_PAIRS

    @staticmethod
    def is_highway_1_pair(start: Point, end: Point) -> bool:
        return start.label == "PCH / river-trail exit area" and end.label == "Del Prado / Golden Lantern"

    @staticmethod
    def highway_1_via_points_from(start: Point) -> tuple[tuple[float, float], ...]:
        return tuple(point for point in HIGHWAY_1_VIA_POINTS if point[1] > start.longitude)

    @staticmethod
    def pedestrian_route_payload(chunk: list[Point]) -> dict[str, object]:
        return {"locations": [{"lat": point.latitude, "lon": point.longitude} for point in chunk], "costing": "pedestrian", "costing_options": {"pedestrian": {"use_ferry": 0, "ferry_cost": 43200}}, "units": "miles"}

    @staticmethod
    def google_route_payload(start: Point, end: Point, *, travel_mode: str = "WALK", via_points: tuple[tuple[float, float], ...] = ()) -> dict[str, object]:
        payload: dict[str, object] = {"origin": {"location": {"latLng": {"latitude": start.latitude, "longitude": start.longitude}}}, "destination": {"location": {"latLng": {"latitude": end.latitude, "longitude": end.longitude}}}, "travelMode": travel_mode, "languageCode": "en-US", "units": "IMPERIAL"}
        if via_points:
            payload["intermediates"] = [{"via": True, "location": {"latLng": {"latitude": latitude, "longitude": longitude}}} for latitude, longitude in via_points]
        return payload

    @classmethod
    def google_walking_route_payload(cls, start: Point, end: Point) -> dict[str, object]:
        return cls.google_route_payload(start, end)

    @staticmethod
    def require_no_ferry_instruction(instructions: list[str], start: Point, end: Point) -> None:
        if any("ferry" in instruction.casefold() for instruction in instructions):
            raise UnsafePedestrianRouteError(f"Walking route from {start.label} to {end.label} includes a ferry instruction; confirm the runner-accessible land route before exporting a map.")

    @staticmethod
    def require_land_based_pedestrian_trip(trip: dict[str, object], start: Point, end: Point) -> None:
        summary = trip.get("summary", {})
        if isinstance(summary, dict) and summary.get("has_ferry"):
            raise UnsafePedestrianRouteError(f"Pedestrian route from {start.label} to {end.label} uses a ferry; confirm the runner-accessible land route before exporting a map.")

    @staticmethod
    def google_route_distance_meters(route: dict[str, object], geometry: list[tuple[float, float]]) -> float:
        if distance_meters := route.get("distanceMeters"):
            return float(distance_meters)
        return RouteGeometry.distance_meters([geometry])

    def google_route(self, start: Point, end: Point, *, travel_mode: str = "WALK", via_points: tuple[tuple[float, float], ...] = ()) -> tuple[list[tuple[float, float]], float]:
        GoogleUsageTracker.reserve(self.usage, self.usage_path)
        request = Request(GOOGLE_ROUTES_URL, data=json.dumps(self.google_route_payload(start, end, travel_mode=travel_mode, via_points=via_points)).encode("utf-8"), headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": "routes.distanceMeters,routes.polyline.encodedPolyline,routes.legs.steps.navigationInstruction.instructions"}, method="POST")
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed Google endpoint
                data = json.load(response)
        except HTTPError as error:
            GoogleUsageTracker.set_status(self.usage, self.usage_path, f"failed HTTP {error.code}")
            raise RuntimeError(f"Google Routes request failed with HTTP {error.code}; check the key and Routes API setup.") from error
        except OSError as error:
            GoogleUsageTracker.set_status(self.usage, self.usage_path, "failed network error")
            raise RuntimeError("Google Routes request failed due to a network error.") from error
        routes = data.get("routes", []) if isinstance(data, dict) else []
        if not routes:
            GoogleUsageTracker.set_status(self.usage, self.usage_path, "failed: no route")
            raise RuntimeError(f"Google Routes returned no {travel_mode.casefold()} route from {start.label} to {end.label}.")
        route = routes[0]
        polyline = route.get("polyline", {}).get("encodedPolyline")
        if not isinstance(polyline, str):
            GoogleUsageTracker.set_status(self.usage, self.usage_path, "failed: missing polyline")
            raise RuntimeError("Google Routes returned a route without geometry.")
        instructions = [str(step.get("navigationInstruction", {}).get("instructions", "")) for leg in route.get("legs", []) for step in leg.get("steps", [])]
        if travel_mode == "WALK":
            self.require_no_ferry_instruction(instructions, start, end)
        geometry = PolylineCodec.decode(polyline, precision=5)
        GoogleUsageTracker.set_status(self.usage, self.usage_path, "success")
        return geometry, self.google_route_distance_meters(route, geometry)

    def trace(self, points: list[Point], *, manual_highway_1_resume: bool = False) -> tuple[list[list[tuple[float, float]]], float]:
        segments: list[list[tuple[float, float]]] = [[]]
        total_meters = 0.0
        for index in range(len(points) - 1):
            start, end = points[index:index + 2]
            manual_highway = manual_highway_1_resume and index == 0 and end.label == "Del Prado / Golden Lantern"
            if self.is_i5_transfer_pair(start, end):
                # The supplied direction explicitly sends this leg on I-5 to
                # Exit 54C. No coastal-trail pins are permitted for this leg.
                mode, variant, vias = "DRIVE", "i5-runner-transfer-v1", ()
            elif self.is_highway_1_pair(start, end) or manual_highway:
                mode, variant = "DRIVE", "manual-highway-1-v1" if manual_highway else "highway-1-v1"
                vias = self.highway_1_via_points_from(start) if manual_highway else HIGHWAY_1_VIA_POINTS
            elif self.is_strict_road_pair(start, end):
                mode, variant, vias = "DRIVE", "organizer-turn-v1", ()
            else:
                mode, variant, vias = "WALK", "", ()
            cache_key = self.route_cache_key(start, end, travel_mode=mode, variant=variant)
            if cached := self.routing_cache.get(cache_key):
                geometry = [(float(latitude), float(longitude)) for latitude, longitude in cached["geometry"]]
                distance_meters = float(cached["distance_meters"])
            else:
                geometry, distance_meters = self.google_route(start, end, travel_mode=mode, via_points=vias)
                self.routing_cache[cache_key] = {"distance_meters": distance_meters, "geometry": geometry}
                JsonStore.save_cache(self.routing_cache_path, self.routing_cache)
                time.sleep(0.25)
            segments[-1].extend(geometry[1:] if segments[-1] else geometry)
            total_meters += distance_meters
        return [segment for segment in segments if segment], total_meters
