"""Discover additional route-near bathroom options with Google Places."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError

from nstt_course_planner.bathrooms import BATHROOM_STOPS, BathroomCategory, BathroomLayerBuilder, BathroomStop, RunnerRouteKml
from nstt_course_planner.config import (
    DEFAULT_GOOGLE_PLACES_CACHE,
    DEFAULT_GOOGLE_PLACES_USAGE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PLACES_ANCHOR_SPACING_METERS,
    DEFAULT_PLACES_MAX_ROUTE_DISTANCE_METERS,
    DEFAULT_PLACES_SEARCH_RADIUS_METERS,
    GOOGLE_PLACES_URL,
    PROJECT_ROOT,
)
from nstt_course_planner.elevation import RouteGeometrySampler
from nstt_course_planner.storage import GooglePlacesUsageTracker, JsonStore
from nstt_course_planner.utils import Environment, HttpClient

Coordinate = tuple[float, float]
PlaceRequester = Callable[[str, object, dict[str, str] | None], object]


@dataclass(frozen=True)
class PlacesBathroomBuildConfig:
    """Inputs and local state for one Places bathroom discovery pass."""

    source_kml: Path
    output_dir: Path
    cache_path: Path
    usage_path: Path
    anchor_spacing_meters: float
    search_radius_meters: float
    max_route_distance_meters: float
    dry_run: bool = False


class RouteAnchorSampler:
    """Creates search centers along each continuous runner run."""

    @staticmethod
    def continuous_runs(route_lines: tuple[tuple[Coordinate, ...], ...]) -> tuple[tuple[Coordinate, ...], ...]:
        runs: list[tuple[Coordinate, ...]] = []
        current: list[Coordinate] = []
        for line in route_lines:
            if current and current[-1] != line[0]:
                runs.append(tuple(current))
                current = list(line)
            elif current:
                current.extend(line[1:])
            else:
                current = list(line)
        if current:
            runs.append(tuple(current))
        return tuple(runs)

    @classmethod
    def anchors(cls, route_lines: tuple[tuple[Coordinate, ...], ...], spacing_meters: float) -> tuple[Coordinate, ...]:
        if spacing_meters <= 0:
            raise ValueError("Places anchor spacing must be greater than zero.")
        return tuple(
            sample.coordinate
            for run in cls.continuous_runs(route_lines)
            for sample in RouteGeometrySampler.sample(list(run), spacing_meters)
        )


class GooglePlacesBathroomClient:
    """Caches Nearby Search responses and counts every uncached request."""

    field_mask = ",".join((
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.primaryType",
        "places.types",
        "places.businessStatus",
        "places.googleMapsUri",
    ))

    def __init__(
        self,
        cache: dict[str, dict[str, object]],
        cache_path: Path,
        usage: dict[str, object],
        usage_path: Path,
        api_key: str,
        requester: PlaceRequester = HttpClient.post_json,
    ) -> None:
        self.cache = cache
        self.cache_path = cache_path
        self.usage = usage
        self.usage_path = usage_path
        self.api_key = api_key
        self.requester = requester

    @staticmethod
    def cache_key(coordinate: Coordinate, place_type: str, radius_meters: float) -> str:
        return f"{coordinate[0]:.5f},{coordinate[1]:.5f}|{place_type}|{radius_meters:.0f}"

    def search(self, coordinate: Coordinate, place_type: str, radius_meters: float) -> tuple[dict[str, object], ...]:
        key = self.cache_key(coordinate, place_type, radius_meters)
        cached = self.cache.get(key)
        if cached is not None:
            return self._places(cached)
        payload = {
            "includedTypes": [place_type],
            "maxResultCount": 20,
            "rankPreference": "DISTANCE",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": coordinate[0], "longitude": coordinate[1]},
                    "radius": radius_meters,
                }
            },
        }
        GooglePlacesUsageTracker.reserve(self.usage, self.usage_path)
        try:
            response = self.requester(
                GOOGLE_PLACES_URL,
                payload,
                {"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": self.field_mask},
            )
        except HTTPError as error:
            GooglePlacesUsageTracker.set_status(self.usage, self.usage_path, f"failed HTTP {error.code}")
            raise RuntimeError(f"Google Places request failed with HTTP {error.code}.") from error
        except (OSError, json.JSONDecodeError) as error:
            GooglePlacesUsageTracker.set_status(self.usage, self.usage_path, "failed network error")
            raise RuntimeError("Google Places request failed due to a network or response error.") from error
        if not isinstance(response, dict):
            GooglePlacesUsageTracker.set_status(self.usage, self.usage_path, "failed invalid response")
            raise RuntimeError("Google Places returned an invalid response.")
        places = self._places(response)
        self.cache[key] = {"places": list(places)}
        JsonStore.save_cache(self.cache_path, self.cache)
        GooglePlacesUsageTracker.set_status(self.usage, self.usage_path, "success")
        return places

    @staticmethod
    def _places(response: dict[str, object]) -> tuple[dict[str, object], ...]:
        raw_places = response.get("places", [])
        if not isinstance(raw_places, list):
            raise RuntimeError("Google Places returned a response without a usable places list.")
        return tuple(place for place in raw_places if isinstance(place, dict))


class PlacesBathroomDiscovery:
    """Converts cached Places results into clearly labeled planning stops."""

    place_types = (
        ("public_bathroom", BathroomCategory.PLACES_PUBLIC),
        ("grocery_store", BathroomCategory.GROCERY),
        ("coffee_shop", BathroomCategory.COFFEE),
        ("fast_food_restaurant", BathroomCategory.FAST_FOOD_OR_GAS),
        ("gas_station", BathroomCategory.FAST_FOOD_OR_GAS),
    )

    def __init__(self, client: GooglePlacesBathroomClient, max_route_distance_meters: float) -> None:
        self.client = client
        self.max_route_distance_meters = max_route_distance_meters

    def discover(
        self,
        anchors: tuple[Coordinate, ...],
        route_runs: tuple[tuple[Coordinate, ...], ...],
        radius_meters: float,
    ) -> tuple[BathroomStop, ...]:
        candidates: dict[str, tuple[BathroomCategory, dict[str, object]]] = {}
        for coordinate in anchors:
            for place_type, category in self.place_types:
                for place in self.client.search(coordinate, place_type, radius_meters):
                    place_id = place.get("id")
                    if isinstance(place_id, str) and place_id:
                        current = candidates.get(place_id)
                        if current is None or category.priority < current[0].priority:
                            candidates[place_id] = (category, place)
        builder = BathroomLayerBuilder()
        stops = (
            self._stop(category, place)
            for category, place in candidates.values()
            if self._is_route_near(place, route_runs, builder)
        )
        return tuple(sorted(stops, key=lambda stop: (stop.category.priority, stop.name)))

    def _is_route_near(
        self,
        place: dict[str, object],
        route_runs: tuple[tuple[Coordinate, ...], ...],
        builder: BathroomLayerBuilder,
    ) -> bool:
        coordinate = self._coordinate(place)
        return coordinate is not None and builder.nearest_route_distance_meters(coordinate, route_runs) <= self.max_route_distance_meters

    @classmethod
    def _stop(cls, category: BathroomCategory, place: dict[str, object]) -> BathroomStop:
        coordinate = cls._coordinate(place)
        if coordinate is None:
            raise RuntimeError("Google Places returned a place without usable coordinates.")
        name = cls._name(place)
        address = str(place.get("formattedAddress", "Google Maps location"))
        maps_url = place.get("googleMapsUri")
        source_url = str(maps_url) if isinstance(maps_url, str) else "https://www.google.com/maps"
        status = str(place.get("businessStatus", "status not supplied")).replace("_", " ").lower()
        return BathroomStop(name, category, coordinate[0], coordinate[1], address, source_url, cls._note(category, status))

    @staticmethod
    def _coordinate(place: dict[str, object]) -> Coordinate | None:
        location = place.get("location")
        if not isinstance(location, dict):
            return None
        try:
            return float(location["latitude"]), float(location["longitude"])
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _name(place: dict[str, object]) -> str:
        display_name = place.get("displayName")
        if isinstance(display_name, dict) and isinstance(display_name.get("text"), str):
            return display_name["text"]
        return "Google Places bathroom option"

    @staticmethod
    def _note(category: BathroomCategory, status: str) -> str:
        if category is BathroomCategory.PLACES_PUBLIC:
            return f"Google Places lists this as a public restroom ({status}); confirm access and hours on race day."
        return f"Google Places business backup ({status}); confirm customer restroom access and hours on race day."


class PlacesBathroomLayerBuilder:
    """Builds the merged curated-and-Places bathroom KML without touching the route."""

    def __init__(self, config: PlacesBathroomBuildConfig) -> None:
        self.config = config
        self.cache = JsonStore.load_cache(config.cache_path)
        self.usage = GooglePlacesUsageTracker.load(config.usage_path)

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--source-kml", type=Path, default=PROJECT_ROOT / "input" / "Runner.kml", help="Immutable finalized runner-route KML.")
        parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for the bathroom-category KML files.")
        parser.add_argument("--places-cache", type=Path, default=DEFAULT_GOOGLE_PLACES_CACHE, help="Cached Google Places results.")
        parser.add_argument("--places-usage", type=Path, default=DEFAULT_GOOGLE_PLACES_USAGE, help="Local conservative Google Places request counter.")
        parser.add_argument("--anchor-spacing-meters", type=float, default=DEFAULT_PLACES_ANCHOR_SPACING_METERS, help="Distance between route-near search centers; 2,400 m by default.")
        parser.add_argument("--search-radius-meters", type=float, default=DEFAULT_PLACES_SEARCH_RADIUS_METERS, help="Google Places radius around each center; 2,000 m by default.")
        parser.add_argument("--max-route-distance-meters", type=float, default=DEFAULT_PLACES_MAX_ROUTE_DISTANCE_METERS, help="Keep results within this straight-line distance of the route; one mile by default.")
        parser.add_argument("--dry-run", action="store_true", help="Report uncached Places searches without calling Google or writing output.")

    @classmethod
    def from_arguments(cls, arguments: argparse.Namespace) -> "PlacesBathroomLayerBuilder":
        return cls(PlacesBathroomBuildConfig(
            arguments.source_kml,
            arguments.output_dir,
            arguments.places_cache,
            arguments.places_usage,
            arguments.anchor_spacing_meters,
            arguments.search_radius_meters,
            arguments.max_route_distance_meters,
            arguments.dry_run,
        ))

    def build(self) -> None:
        if self.config.search_radius_meters <= 0 or self.config.max_route_distance_meters <= 0:
            raise ValueError("Places search radius and route distance must be positive.")
        route_runs = RunnerRouteKml.line_runs(self.config.source_kml)
        anchors = RouteAnchorSampler.anchors(route_runs, self.config.anchor_spacing_meters)
        planned_queries = len(anchors) * len(PlacesBathroomDiscovery.place_types)
        cached_queries = sum(
            GooglePlacesBathroomClient.cache_key(anchor, place_type, self.config.search_radius_meters) in self.cache
            for anchor in anchors
            for place_type, _ in PlacesBathroomDiscovery.place_types
        )
        print(
            f"Places plan: {len(anchors)} route anchors, {planned_queries - cached_queries} uncached searches, "
            f"{int(self.usage['requests_sent']):,}/{int(self.usage['request_limit']):,} searches already recorded."
        )
        if self.config.dry_run:
            print("Dry run complete: no Google request was sent and no bathroom output was written.")
            return
        client = GooglePlacesBathroomClient(
            self.cache,
            self.config.cache_path,
            self.usage,
            self.config.usage_path,
            Environment.google_maps_api_key(),
        )
        discovered = PlacesBathroomDiscovery(client, self.config.max_route_distance_meters).discover(
            anchors,
            route_runs,
            self.config.search_radius_meters,
        )
        output_paths = self.write_category_layers((*BATHROOM_STOPS, *discovered))
        print(f"Created {len(output_paths)} bathroom-category KML files with {len(BATHROOM_STOPS)} curated and {len(discovered)} Google Places stops.")

    def write_category_layers(self, stops: tuple[BathroomStop, ...]) -> tuple[Path, ...]:
        names = {
            1: "public",
            2: "grocery",
            3: "coffee",
            4: "backups",
        }
        output_paths: list[Path] = []
        for priority, name in names.items():
            matching = tuple(stop for stop in stops if stop.category.priority == priority)
            for part, batch_start in enumerate(range(0, len(matching), 2_000), start=1):
                suffix = "" if part == 1 else f"_part_{part}"
                output_path = self.config.output_dir / f"NSTT_2026_bathroom_{name}{suffix}.kml"
                BathroomLayerBuilder().build(self.config.source_kml, output_path, matching[batch_start:batch_start + 2_000])
                output_paths.append(output_path)
        return tuple(output_paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    PlacesBathroomLayerBuilder.add_arguments(parser)
    PlacesBathroomLayerBuilder.from_arguments(parser.parse_args()).build()


if __name__ == "__main__":
    main()
