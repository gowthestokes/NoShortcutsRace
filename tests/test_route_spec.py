"""Regression tests for the team-supplied September 2026 course turn sheet."""

import json

import pytest

from nstt_course_planner.build_course import (
    CHECKPOINTS,
    Checkpoint,
    GOOGLE_ROUTES_REQUEST_LIMIT,
    GoogleRoutesRequestLimitError,
    Point,
    UnsafePedestrianRouteError,
    load_geocoding_cache,
    load_google_routes_usage,
    HIGHWAY_1_VIA_POINTS,
    is_highway_1_pair,
    is_strict_road_pair,
    is_support_car_transfer_pair,
    point_from_cache,
    pedestrian_route_payload,
    reserve_google_routes_request,
    route_cache_key,
    require_land_based_pedestrian_trip,
    save_geocoding_cache,
    google_route_payload,
    google_walking_route_payload,
    google_route_distance_meters,
    write_outputs,
)
from nstt_course_planner.route_spec import (
    CAR_CHECKPOINTS,
    CAR_INSTRUCTIONS,
    OPERATIONAL_NOTES,
    ROUTE_CHECKPOINTS,
    RUNNER_INSTRUCTIONS,
)


def test_every_runner_direction_is_present_and_in_organizer_order() -> None:
    assert [(item.action, item.road_or_place) for item in RUNNER_INSTRUCTIONS] == [
        ("start", "Santa Monica Pier"), ("right", "Ocean Ave"), ("left", "Pico Blvd"),
        ("right", "Main St"), ("left", "Abbot Kinney Blvd"), ("left", "Washington Blvd"),
        ("fork right", "West Washington Blvd"), ("right", "Inglewood Blvd"),
        ("left", "Culver Blvd"), ("right", "Mesmer Ave"), ("left", "Centinela Ave"),
        ("right", "Sepulveda Blvd"), ("left", "W 78th St"), ("becomes", "W 79th St"),
        ("right", "Isis Ave"), ("left", "W 83rd St"), ("right", "La Cienega Blvd"),
        ("left", "Manchester Ave"), ("becomes", "Firestone Blvd"), ("right", "Atlantic Ave"),
        ("left", "Rosecrans Ave"), ("right onto", "LA River Trail"),
        ("follow south until passing under", "PCH 1"),
        ("meet car at bike-path exit immediately after", "PCH 1 (Ocean Blue Environmental)"),
        ("pick up", "PCH"), ("right", "Del Prado"), ("right", "Golden Lantern"),
        ("left", "Dana Point Highway Rd"), ("right", "Park Lantern"),
        ("continue; road becomes", "El Camino Real"), ("right", "Avenida Valencia"),
        ("left", "Avenida del Presidente"), ("team photo", "San Mateo Point"),
        ("resume Coast Highway from", "Chevron after I-5 exit 54C"),
        ("follow", "Coast Highway"),
        ("follow; road becomes", "Carlsbad Blvd"), ("follow; road becomes", "Coast Highway 101"),
        ("right", "Camino del Mar"), ("becomes", "Torrey Pines Rd"),
        ("right", "Torrey Pines Rd"), ("right", "La Jolla Shores Dr"),
        ("right", "Torrey Pines Rd"), ("left", "Girard Ave"), ("right", "Pearl St"),
        ("left", "Fay Ave"), ("right", "Nautilus St"), ("left", "La Jolla Blvd"),
        ("right", "Mission Blvd"), ("left", "Garnet Ave"), ("finish", "Milestone Running Shop"),
    ]


def test_route_builder_checkpoints_match_the_source_of_truth_exactly() -> None:
    assert [(point.label, point.query) for point in CHECKPOINTS] == [
        (point.label, point.query) for point in ROUTE_CHECKPOINTS
    ]


def test_every_direction_that_requires_a_map_location_has_a_checkpoint() -> None:
    route_labels = {point.label for point in ROUTE_CHECKPOINTS}
    assert {item.checkpoint_label for item in RUNNER_INSTRUCTIONS if item.checkpoint_label} <= route_labels


def test_special_runner_and_car_logistics_are_preserved() -> None:
    assert [(item.action, item.road_or_place) for item in CAR_INSTRUCTIONS] == [
        ("during river trail, drive ahead and meet runners where trail meets road", "LA River Trail"),
        ("send replacement runner onto trail and collect outgoing runner", "LA River Trail"),
        ("fork right to avoid I-5 / Coast Highway conflict", "after Park Lantern"),
        ("meet runners on Coast Highway", "coastal section"),
        ("drive I-5", "to exit 54C"),
        ("cross road after exit and pull into", "Chevron gas station"),
    ]


def test_car_layer_has_its_own_ordered_waypoints() -> None:
    assert [point.label for point in CAR_CHECKPOINTS] == [
        "LA River Trail support access",
        "Coast Highway car rendezvous",
        "Chevron - I-5 exit 54C car stop",
    ]
    car_labels = {point.label for point in CAR_CHECKPOINTS}
    assert {item.checkpoint_label for item in CAR_INSTRUCTIONS if item.checkpoint_label} <= car_labels


def test_river_and_coast_safety_requirements_remain_explicit() -> None:
    runner_text = " ".join(f"{item.action} {item.road_or_place}".lower() for item in RUNNER_INSTRUCTIONS)
    car_text = " ".join(f"{item.action} {item.road_or_place}".lower() for item in CAR_INSTRUCTIONS)
    assert "ocean blue environmental" in runner_text
    assert "la river trail" in car_text
    assert "coast highway" in car_text


def test_operational_safety_notes_are_retained_for_future_pod_planning() -> None:
    assert OPERATIONAL_NOTES == (
        "Runner must always carry a phone during the LA River Trail section.",
        "Use slightly longer relay segments on the LA River Trail; the car drives ahead to road-accessible trail exits.",
        "The coast section narrows for runners and has less car support; use longer segments there.",
        "Use the coastal bike path where appropriate; the car may drop and collect runners using the same leapfrog pattern as the river trail.",
        "Use caution at the PCH roundabout.",
    )


def test_kml_has_distinct_runner_and_support_car_layers(tmp_path) -> None:
    runner_point = Point("Runner point", "runner query", 34.0, -118.0, "Runner point")
    car_point = Point("LA River Trail support access", "car query", 33.9, -118.1, "Car point")
    write_outputs(tmp_path, [runner_point], [[(34.0, -118.0), (34.1, -118.1)]], 1609.344, [car_point])

    kml = (tmp_path / "NSTT_2026_master_runner_course_draft.kml").read_text(encoding="utf-8")
    assert "Runner route - organizer-aligned draft" in kml
    assert "Support car logistics - provisional" in kml
    assert "Provisional support-car logistics point" in kml


def test_geocoding_cache_round_trips_and_reuses_coordinates(tmp_path) -> None:
    cache_path = tmp_path / "data" / "geocoding-cache.json"
    cache = {
        "Pico Boulevard & Main Street, Santa Monica, CA": {
            "latitude": 34.01,
            "longitude": -118.49,
            "display_name": "Pico and Main",
        }
    }
    save_geocoding_cache(cache_path, cache)

    assert json.loads(cache_path.read_text(encoding="utf-8")) == cache
    restored = load_geocoding_cache(cache_path)
    point = point_from_cache(Checkpoint("Pico / Main", next(iter(cache))), restored[next(iter(cache))])

    assert point == Point("Pico / Main", next(iter(cache)), 34.01, -118.49, "Pico and Main")


def test_missing_geocoding_cache_is_empty(tmp_path) -> None:
    assert load_geocoding_cache(tmp_path / "not-yet-created.json") == {}


def test_route_cache_key_uses_coordinates_profile_and_variant() -> None:
    start = Point("Start", "start", 34.0, -118.0, "Start")
    end = Point("End", "end", 33.0, -117.0, "End")

    assert route_cache_key(start, end) == "google-walk:34.0000000,-118.0000000:33.0000000,-117.0000000"
    assert route_cache_key(start, end, travel_mode="DRIVE", variant="organizer-turn-v1") == (
        "google-drive:34.0000000,-118.0000000:33.0000000,-117.0000000:organizer-turn-v1"
    )


def test_pedestrian_route_rejects_ferry_geometry() -> None:
    start = Point("LA River Trail entry", "entry", 33.858, -118.198, "entry")
    end = Point("PCH exit", "exit", 33.751, -118.106, "exit")

    with pytest.raises(UnsafePedestrianRouteError, match="uses a ferry"):
        require_land_based_pedestrian_trip({"summary": {"has_ferry": True}}, start, end)


def test_walking_router_strongly_avoids_ferries_before_the_no_swimming_guard() -> None:
    start = Point("Start", "start", 34.0, -118.0, "Start")
    end = Point("End", "end", 33.0, -117.0, "End")

    assert pedestrian_route_payload([start, end])["costing_options"] == {
        "pedestrian": {"use_ferry": 0, "ferry_cost": 43200}
    }


def test_ambiguous_pch_exit_uses_the_organizer_landmark_not_seal_beach() -> None:
    pch_exit = next(point for point in ROUTE_CHECKPOINTS if point.label == "PCH / river-trail exit area")

    assert pch_exit.query == "925 W Esther Street, Long Beach, CA 90813"


def test_highway_1_replaces_the_unrequested_long_beach_beach_path_detour() -> None:
    labels = [point.label for point in ROUTE_CHECKPOINTS]
    assert "Long Beach Shoreline Beach Path - west entrance" not in labels
    assert "Long Beach Shoreline Beach Path - east exit" not in labels
    start = Point("PCH / river-trail exit area", "", 33.7739, -118.2024, "")
    end = Point("Del Prado / Golden Lantern", "", 33.465, -117.698, "")
    assert is_highway_1_pair(start, end)
    assert len(HIGHWAY_1_VIA_POINTS) >= 10


def test_organizer_turns_force_the_literal_sepulveda_w78_w79_sequence() -> None:
    centinela = Point("Centinela Ave / Sepulveda Blvd", "", 33.977, -118.386, "")
    sepulveda = Point("Sepulveda Blvd / W 78th St", "", 33.970, -118.386, "")
    w78 = Point("W 78th St / W 79th St", "", 33.9685, -118.385, "")
    w79 = Point("W 79th St / Isis Ave", "", 33.968, -118.380, "")

    assert is_strict_road_pair(centinela, sepulveda)
    assert is_strict_road_pair(sepulveda, w78)
    assert is_strict_road_pair(w78, w79)


def test_i5_chevron_transition_is_a_support_car_gap_not_a_runner_leg() -> None:
    start = Point("San Mateo Point", "", 33.418, -117.604, "")
    end = Point("Chevron - I-5 exit 54C runner restart", "", 33.165, -117.354, "")

    assert is_support_car_transfer_pair(start, end)


def test_exit_54c_chevron_is_pinned_to_oceanside_not_san_clemente() -> None:
    chevron = next(point for point in ROUTE_CHECKPOINTS if point.label == "Chevron - I-5 exit 54C runner restart")

    assert chevron.query == "Chevron, 1601 N Coast Hwy, Oceanside, CA 92054"


def test_google_walking_route_requests_the_ordered_walking_leg() -> None:
    start = Point("Start", "", 34.0, -118.0, "")
    end = Point("End", "", 33.0, -117.0, "")

    assert google_walking_route_payload(start, end) == {
        "origin": {"location": {"latLng": {"latitude": 34.0, "longitude": -118.0}}},
        "destination": {"location": {"latLng": {"latitude": 33.0, "longitude": -117.0}}},
        "travelMode": "WALK",
        "languageCode": "en-US",
        "units": "IMPERIAL",
    }


def test_google_route_payload_can_pin_a_highway_1_road_alignment() -> None:
    start = Point("Start", "", 34.0, -118.0, "")
    end = Point("End", "", 33.0, -117.0, "")

    payload = google_route_payload(start, end, travel_mode="DRIVE", via_points=((33.5, -117.5),))

    assert payload["travelMode"] == "DRIVE"
    assert payload["intermediates"] == [
        {"via": True, "location": {"latLng": {"latitude": 33.5, "longitude": -117.5}}}
    ]


def test_google_route_distance_uses_geometry_when_the_api_omits_distance() -> None:
    geometry = [(34.0, -118.0), (34.001, -118.0)]

    assert 100 < google_route_distance_meters({}, geometry) < 120
    assert google_route_distance_meters({"distanceMeters": 321}, geometry) == 321


def test_google_routes_usage_stops_before_the_free_cap(tmp_path) -> None:
    usage_path = tmp_path / "google-routes-usage.json"
    usage = load_google_routes_usage(usage_path)
    usage["requests_sent"] = GOOGLE_ROUTES_REQUEST_LIMIT - 1

    reserve_google_routes_request(usage, usage_path)
    assert usage["requests_sent"] == GOOGLE_ROUTES_REQUEST_LIMIT
    assert load_google_routes_usage(usage_path)["requests_sent"] == GOOGLE_ROUTES_REQUEST_LIMIT
    with pytest.raises(GoogleRoutesRequestLimitError, match="9,500"):
        reserve_google_routes_request(usage, usage_path)
