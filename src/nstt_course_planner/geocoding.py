"""Checkpoint geocoding service."""

from __future__ import annotations

from urllib.parse import urlencode

from nstt_course_planner.config import MANUAL_POINTS
from nstt_course_planner.models.course import Checkpoint
from nstt_course_planner.models.route import Point
from nstt_course_planner.utils import HttpClient


class CheckpointGeocoder:
    """Resolves route checkpoints while reusing a caller-owned cache."""

    def point_from_cache(self, checkpoint: Checkpoint, cached: dict[str, object]) -> Point:
        return Point(checkpoint.label, checkpoint.query, float(cached["latitude"]), float(cached["longitude"]), str(cached["display_name"]))

    def geocode(self, checkpoint: Checkpoint, cache: dict[str, dict[str, object]]) -> tuple[Point, bool]:
        if checkpoint.label in MANUAL_POINTS:
            latitude, longitude, display_name = MANUAL_POINTS[checkpoint.label]
            return Point(checkpoint.label, checkpoint.query, latitude, longitude, display_name), True
        if cached := cache.get(checkpoint.query):
            return self.point_from_cache(checkpoint, cached), True
        point = self._census_intersection(checkpoint)
        if point is None:
            point = self._nominatim(checkpoint)
        cache[checkpoint.query] = {"latitude": point.latitude, "longitude": point.longitude, "display_name": point.display_name}
        return point, False

    @staticmethod
    def _census_intersection(checkpoint: Checkpoint) -> Point | None:
        if "&" not in checkpoint.query:
            return None
        query = urlencode({"address": checkpoint.query.replace("&", "and"), "benchmark": "Public_AR_Current", "format": "json"})
        data = HttpClient.get_json(f"https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?{query}")
        matches = data["result"].get("addressMatches", [])
        if not matches:
            return None
        match = matches[0]
        coordinates = match["coordinates"]
        return Point(checkpoint.label, checkpoint.query, float(coordinates["y"]), float(coordinates["x"]), match["matchedAddress"])

    @staticmethod
    def _nominatim(checkpoint: Checkpoint) -> Point:
        query = checkpoint.query
        if "&" in query:
            first_road, location = query.split("&", maxsplit=1)
            query = f"{first_road.strip()}, {','.join(location.split(',')[1:]).strip()}"
        params = urlencode({"q": query, "format": "jsonv2", "limit": "1", "countrycodes": "us"})
        data = HttpClient.get_json(f"https://nominatim.openstreetmap.org/search?{params}")
        if not data:
            raise RuntimeError(f"No geocoding result for {checkpoint.label}: {checkpoint.query}")
        result = data[0]
        return Point(checkpoint.label, checkpoint.query, float(result["lat"]), float(result["lon"]), result["display_name"])
