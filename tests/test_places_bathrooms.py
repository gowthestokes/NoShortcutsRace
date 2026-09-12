"""Offline tests for cached Google Places bathroom discovery."""

from pathlib import Path

from nstt_course_planner.bathrooms import BathroomCategory
from nstt_course_planner.bathrooms import BathroomStop
from nstt_course_planner.places_bathrooms import (
    GooglePlacesBathroomClient,
    PlacesBathroomBuildConfig,
    PlacesBathroomDiscovery,
    PlacesBathroomLayerBuilder,
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


def test_category_layers_split_before_the_my_maps_feature_limit(tmp_path: Path) -> None:
    source = tmp_path / "Runner.kml"
    source.write_text('''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><LineString><coordinates>0,0 0.01,0</coordinates></LineString></Placemark></Document></kml>''', encoding="utf-8")
    stop = BathroomStop("Backup", BathroomCategory.FAST_FOOD_OR_GAS, 0.0, 0.005, "Address", "https://example.com", "Confirm access.")
    builder = PlacesBathroomLayerBuilder(PlacesBathroomBuildConfig(source, tmp_path, tmp_path / "cache.json", tmp_path / "usage.json", 2_400, 2_000, 1_609, True))

    paths = builder.write_category_layers((stop,) * 2_001)

    assert [path.name for path in paths] == ["NSTT_2026_bathroom_backups.kml", "NSTT_2026_bathroom_backups_part_2.kml"]
    assert paths[0].read_text(encoding="utf-8").count("<Placemark>") == 2_000
    assert paths[1].read_text(encoding="utf-8").count("<Placemark>") == 1
