"""Offline tests for cached Google Places bathroom discovery."""

from pathlib import Path

from nstt_course_planner.models.bathrooms import BathroomCategory, BathroomStop
from nstt_course_planner.places_bathrooms import (
    GooglePlacesBathroomClient,
    PlacesBathroomLayerBuilder,
    PlacesBathroomDiscovery,
    RouteAnchorSampler,
)
from nstt_course_planner.storage import GooglePlacesUsageTracker


def test_places_client_caches_an_uncached_search(tmp_path: Path) -> None:
    calls: list[object] = []

    def requester(url: str, payload: object, headers: dict[str, str] | None) -> object:
        calls.append((url, payload, headers))
        return {"places": [{"id": "place-1"}]}

    usage_path = tmp_path / "usage.json"
    client = GooglePlacesBathroomClient({}, tmp_path / "cache.json", GooglePlacesUsageTracker.load(usage_path), usage_path, "key", requester)

    first = client.search((34.0, -118.0), "public_bathroom", 2_000)
    second = client.search((34.0, -118.0), "public_bathroom", 2_000)

    assert first == second == ({"id": "place-1"},)
    assert len(calls) == 1
    assert client.usage["requests_sent"] == 1
    assert calls[0][1] == {
        "includedTypes": ["public_bathroom"],
        "maxResultCount": 20,
        "rankPreference": "DISTANCE",
        "locationRestriction": {"circle": {"center": {"latitude": 34.0, "longitude": -118.0}, "radius": 2_000}},
    }


def test_route_anchor_sampler_keeps_support_car_gap_out_of_searches() -> None:
    anchors = RouteAnchorSampler.anchors((((0.0, 0.0), (0.0, 0.01)), ((1.0, 1.0), (1.0, 1.01))), 500)

    assert anchors[0] == (0.0, 0.0)
    assert anchors[-1] == (1.0, 1.01)
    assert (1.0, 1.0) in anchors


def test_route_anchor_sampler_merges_adjacent_editable_segments() -> None:
    runs = RouteAnchorSampler.continuous_runs((((0.0, 0.0), (0.0, 0.01)), ((0.0, 0.01), (0.0, 0.02))))

    assert runs == (((0.0, 0.0), (0.0, 0.01), (0.0, 0.02)),)


def test_discovery_ranks_public_places_before_business_backups(tmp_path: Path) -> None:
    place = {
        "id": "restroom",
        "displayName": {"text": "Public restroom"},
        "formattedAddress": "Beach, CA",
        "location": {"latitude": 0.0, "longitude": 0.005},
        "googleMapsUri": "https://maps.google.com/?q=restroom",
        "businessStatus": "OPERATIONAL",
    }

    def requester(url: str, payload: object, headers: dict[str, str] | None) -> object:
        return {"places": [place]}

    usage_path = tmp_path / "usage.json"
    client = GooglePlacesBathroomClient({}, tmp_path / "cache.json", GooglePlacesUsageTracker.load(usage_path), usage_path, "key", requester)
    stops = PlacesBathroomDiscovery(client, 1_000).discover(((0.0, 0.0),), (((0.0, 0.0), (0.0, 0.01)),), 2_000)

    assert len(stops) == 1
    assert stops[0].category is BathroomCategory.PLACES_PUBLIC
    assert "Google Places lists" in stops[0].source_note


def test_merged_layer_keeps_higher_priorities_and_evenly_limits_backups() -> None:
    def stop(name: str, category: BathroomCategory, longitude: float) -> BathroomStop:
        return BathroomStop(name, category, 0.0, longitude, "Address", "https://example.com", "Confirm access.")

    public = stop("Public", BathroomCategory.PLACES_PUBLIC, 0.001)
    grocery = stop("Grocery", BathroomCategory.GROCERY, 0.002)
    coffee = stop("Coffee", BathroomCategory.COFFEE, 0.003)
    backups = tuple(stop(f"Backup {index}", BathroomCategory.FAST_FOOD_OR_GAS, 0.004 + index / 10_000) for index in range(2_100))

    selected = PlacesBathroomLayerBuilder.select_for_my_maps((public, grocery, coffee, *backups), (((0.0, 0.0), (0.0, 1.0)),))

    assert len(selected) == 2_000
    assert {public, grocery, coffee} <= set(selected)
    assert selected[-1].name == "Backup 2099"
