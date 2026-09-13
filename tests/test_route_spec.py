"""Regression tests for the team-supplied September 2026 course turn sheet."""

import json
import math
from types import SimpleNamespace

import pytest

from nstt_course_planner.build_course import (
    CHECKPOINTS,
    CourseBuilder,
    google_route_distance_meters,
    google_route_payload,
    google_walking_route_payload,
    is_highway_1_pair,
    is_i5_transfer_pair,
    is_strict_road_pair,
    load_approved_route_progress,
    load_geocoding_cache,
    load_google_routes_usage,
    pedestrian_route_payload,
    point_from_cache,
    require_land_based_pedestrian_trip,
    reserve_google_routes_request,
    route_cache_key,
    runner_route_sections,
    save_geocoding_cache,
    write_outputs,
)
from nstt_course_planner.config import GOOGLE_ROUTES_REQUEST_LIMIT, HIGHWAY_1_VIA_POINTS
from nstt_course_planner.errors import (
    GoogleRoutesRequestLimitError,
    UnsafePedestrianRouteError,
)
from nstt_course_planner.geometry import RouteGeometry, RouteSegmenter
from nstt_course_planner.models.course import Checkpoint, CourseBuildConfig
from nstt_course_planner.models.route import Point
from nstt_course_planner.progress import ApprovedProgressLoader
from nstt_course_planner.route_spec import (
    CAR_CHECKPOINTS,
    CAR_INSTRUCTIONS,
    OPERATIONAL_NOTES,
    ROUTE_CHECKPOINTS,
    ROUTE_SPEC,
    RUNNER_INSTRUCTIONS,
)
from nstt_course_planner.routing import RunnerRouter


def test_every_runner_direction_is_present_and_in_organizer_order() -> None:
    assert [(item.action, item.road_or_place) for item in RUNNER_INSTRUCTIONS] == [
        ("start", "Santa Monica Pier"),
        ("right", "Ocean Ave"),
        ("left", "Pico Blvd"),
        ("right", "Main St"),
        ("left", "Abbot Kinney Blvd"),
        ("left", "Washington Blvd"),
        ("fork right", "West Washington Blvd"),
        ("right", "Inglewood Blvd"),
        ("left", "Culver Blvd"),
        ("right", "Mesmer Ave"),
        ("left", "Centinela Ave"),
        ("right", "Sepulveda Blvd"),
        ("left", "W 78th St"),
        ("becomes", "W 79th St"),
        ("right", "Isis Ave"),
        ("left", "W 83rd St"),
        ("right", "La Cienega Blvd"),
        ("left", "Manchester Ave"),
        ("becomes", "Firestone Blvd"),
        ("right", "Atlantic Ave"),
        ("left", "Rosecrans Ave"),
        ("right onto", "LA River Trail"),
        ("follow south until passing under", "PCH 1"),
        (
            "meet car at bike-path exit immediately after",
            "PCH 1 (Ocean Blue Environmental)",
        ),
        ("pick up", "PCH"),
        ("right", "Del Prado"),
        ("right", "Golden Lantern"),
        ("left", "Dana Point Highway Rd"),
        ("right", "Park Lantern"),
        ("continue; road becomes", "El Camino Real"),
        ("right", "Avenida Valencia"),
        ("left", "Avenida del Presidente"),
        ("team photo", "San Mateo Point"),
        ("resume Coast Highway from", "Chevron after I-5 exit 54C"),
        ("follow", "Coast Highway"),
        ("follow; road becomes", "Carlsbad Blvd"),
        ("follow; road becomes", "Coast Highway 101"),
        ("right", "Camino del Mar"),
        ("becomes", "Torrey Pines Rd"),
        ("right", "Torrey Pines Rd"),
        ("right", "La Jolla Shores Dr"),
        ("right", "Torrey Pines Rd"),
        ("left", "Girard Ave"),
        ("right", "Pearl St"),
        ("left", "Fay Ave"),
        ("right", "Nautilus St"),
        ("left", "La Jolla Blvd"),
        ("right", "Mission Blvd"),
        ("left", "Garnet Ave"),
        ("finish", "Milestone Running Shop"),
    ]


def test_route_builder_checkpoints_match_the_source_of_truth_exactly() -> None:
    assert [(point.label, point.query) for point in CHECKPOINTS] == [
        (point.label, point.query) for point in ROUTE_CHECKPOINTS
    ]


def test_race_route_spec_owns_the_immutable_organizer_data() -> None:
    assert ROUTE_SPEC.route_checkpoints == ROUTE_CHECKPOINTS
    assert ROUTE_SPEC.runner_instructions == RUNNER_INSTRUCTIONS
    assert ROUTE_SPEC.operational_notes == OPERATIONAL_NOTES
    assert ROUTE_SPEC.checkpoint("El Camino Real / Avenida Valencia").query == (
        "El Camino Real & Avenida Valencia, San Clemente, CA"
    )


def test_every_direction_that_requires_a_map_location_has_a_checkpoint() -> None:
    route_labels = {point.label for point in ROUTE_CHECKPOINTS}
    assert {
        item.checkpoint_label for item in RUNNER_INSTRUCTIONS if item.checkpoint_label
    } <= route_labels


def test_river_and_coast_safety_requirements_remain_explicit() -> None:
    runner_text = " ".join(
        f"{item.action} {item.road_or_place}".lower() for item in RUNNER_INSTRUCTIONS
    )
    assert "ocean blue environmental" in runner_text
    assert "la river trail" in runner_text
    assert "coast highway" in runner_text


def test_i5_leg_is_an_explicit_support_car_pickup_and_drop_off() -> None:
    assert [(item.action, item.checkpoint_label) for item in CAR_INSTRUCTIONS[-3:]] == [
        ("pick up runner", "I-5 runner pickup - San Mateo Point"),
        ("drive I-5", "I-5 runner drop-off - Chevron exit 54C"),
        (
            "cross road after exit, pull into Chevron, and drop runner",
            "I-5 runner drop-off - Chevron exit 54C",
        ),
    ]
    assert [point.label for point in CAR_CHECKPOINTS[-2:]] == [
        "I-5 runner pickup - San Mateo Point",
        "I-5 runner drop-off - Chevron exit 54C",
    ]


def test_operational_safety_notes_are_retained_for_future_pod_planning() -> None:
    assert OPERATIONAL_NOTES == (
        "Runner must always carry a phone during the LA River Trail section.",
        "Use slightly longer relay segments on the LA River Trail; the car drives ahead to road-accessible trail exits.",
        "The coast section narrows for runners and has less car support; use longer segments there.",
        "Use the coastal bike path where appropriate; the car may drop and collect runners using the same leapfrog pattern as the river trail.",
        "Use caution at the PCH roundabout.",
    )


def test_kml_exports_only_the_editable_runner_segments_layer(tmp_path) -> None:
    write_outputs(tmp_path, [[(34.0, -118.0), (34.1, -118.1)]], 1609.344)

    segments_kml = (tmp_path / "NSTT_2026_runner_route_segments.kml").read_text(
        encoding="utf-8",
    )

    assert "Runner route segments - alternating green and blue" in segments_kml
    assert "Segment 001 - Start to Checkpoint 001" in segments_kml
    assert "#segmentGreen" in segments_kml
    assert 'id="segmentBlue"' in segments_kml
    assert "<Point>" not in segments_kml
    assert "<MultiGeometry>" not in segments_kml
    assert not (tmp_path / "NSTT_2026_runner_mile_checkpoints.kml").exists()


def test_route_sections_are_editable_mile_lines_without_bridging_a_gap() -> None:
    latitude_delta_per_mile = 1609.344 * 180 / (math.pi * 6_371_000)
    segments = [
        [(0.0, 0.0), (latitude_delta_per_mile * 1.5, 0.0)],
        [(10.0, 0.0), (10.0 + latitude_delta_per_mile * 1.5, 0.0)],
    ]

    sections = runner_route_sections(segments, 1609.344 * 3)

    assert [section.label for section in sections] == [
        "Segment 001 - Start to Mile 001",
        "Segment 002 - Mile 001 to runner transfer gap",
        "Segment 002b - Runner restart to Mile 002",
        "Segment 003 - Mile 002 to Mile 003",
    ]
    assert all(
        not (
            min(latitude for latitude, _ in section.coordinates) < 1
            and max(latitude for latitude, _ in section.coordinates) > 9
        )
        for section in sections
    )


def test_approved_kml_preserves_consecutive_user_edited_segments(tmp_path) -> None:
    approved_kml = tmp_path / "Runner Segments.kml"
    approved_kml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>Segment 001 - Start to Mile 001</name><LineString><coordinates>-118.0,34.0,0 -118.01,34.01,0</coordinates></LineString></Placemark>
  <Placemark><name>Segment 002 - Mile 001 to Mile 002</name><LineString><coordinates>-118.01,34.01,0 -118.02,34.02,0</coordinates></LineString></Placemark>
</Document></kml>""",
        encoding="utf-8",
    )

    progress = load_approved_route_progress(approved_kml, 2)

    assert progress.through_segment == 2
    assert [section.label for section in progress.sections] == [
        "Segment 001 - Start to Mile 001",
        "Segment 002 - Mile 001 to Mile 002",
    ]
    assert progress.sections[-1].coordinates[-1] == (34.02, -118.02)


def test_approved_kml_ignores_an_orphan_duplicate_when_canonical_segment_exists(
    tmp_path,
) -> None:
    approved_kml = tmp_path / "Runner.kml"
    approved_kml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>Segment 001 - Start to Checkpoint 001</name><LineString><coordinates>-118.0,34.0,0 -118.01,34.01,0</coordinates></LineString></Placemark>
  <Placemark><name>Segment 002 - Checkpoint 001 to Checkpoint 002</name><LineString><coordinates>-118.01,34.01,0 -118.02,34.02,0</coordinates></LineString></Placemark>
  <Placemark><name>Segment 002 - Checkpoint 200 to Checkpoint 002</name><LineString><coordinates>-117.0,33.0,0 -117.01,33.01,0</coordinates></LineString></Placemark>
</Document></kml>""",
        encoding="utf-8",
    )

    progress = load_approved_route_progress(approved_kml, 2)

    assert [section.label for section in progress.sections] == [
        "Segment 001 - Start to Checkpoint 001",
        "Segment 002 - Checkpoint 001 to Checkpoint 002",
    ]
    assert progress.sections[-1].coordinates[-1] == (34.02, -118.02)


def test_source_of_truth_build_preserves_both_sides_of_the_i5_transfer(
    tmp_path,
) -> None:
    source_kml = tmp_path / "Runner route segments.kml"
    source_kml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>Segment 001 - Start to Checkpoint 001</name><LineString><coordinates>-118.0,34.0,0 -118.01,34.01,0</coordinates></LineString></Placemark>
  <Placemark><name>Segment 002 - Checkpoint 001 to Checkpoint 002</name><LineString><coordinates>-118.01,34.01,0 -118.02,34.02,0</coordinates></LineString></Placemark>
  <Placemark><name>Segment 003 - Runner restart to Checkpoint 003</name><LineString><coordinates>-117.0,33.0,0 -117.01,33.01,0</coordinates></LineString></Placemark>
  <Placemark><name>San Mateo Point</name><Point><coordinates>-118.03,34.03,0</coordinates></Point></Placemark>
</Document></kml>""",
        encoding="utf-8",
    )
    builder = CourseBuilder.__new__(CourseBuilder)
    builder.config = CourseBuildConfig(
        tmp_path / "outputs",
        tmp_path / "geocoding.json",
        tmp_path / "routing.json",
        tmp_path / "path.json",
        tmp_path / "usage.json",
        source_of_truth_kml=source_kml,
        source_prefix_through_segment=2,
    )
    builder.router = SimpleNamespace(
        trace=lambda route: (
            [
                [
                    (route[0].latitude, route[0].longitude),
                    (34.025, -118.025),
                    (route[1].latitude, route[1].longitude),
                ],
            ],
            1.0,
        ),
    )

    segments, sections = builder.build_from_source_of_truth()

    assert sections[0].coordinates == ((34.0, -118.0), (34.01, -118.01))
    assert sections[1].coordinates == ((34.01, -118.01), (34.02, -118.02))
    assert sections[2].label == "Segment 002b - Checkpoint 002 to San Mateo Point"
    assert sections[3].coordinates == ((33.0, -117.0), (33.01, -117.01))
    assert segments == [
        [
            (34.0, -118.0),
            (34.01, -118.01),
            (34.02, -118.02),
            (34.025, -118.025),
            (34.03, -118.03),
        ],
        [(33.0, -117.0), (33.01, -117.01)],
    ]


def test_normalization_uses_segment_numbers_not_kml_placement_and_keeps_transfer_gap(
    tmp_path,
) -> None:
    latitude_per_mile = 1609.344 * 180 / (math.pi * 6_371_000)
    source_kml = tmp_path / "manual.kml"
    source_kml.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>Segment 001 - Start to Checkpoint 001</name><LineString><coordinates>0,0,0 0,{latitude_per_mile * 0.4},0</coordinates></LineString></Placemark>
  <Placemark><name>Segment 003 - Runner restart to Checkpoint 003</name><LineString><coordinates>1,1,0 1,{1 + latitude_per_mile * 0.75},0</coordinates></LineString></Placemark>
  <Placemark><name>Segment 002 - Checkpoint 001 to support-car pickup</name><LineString><coordinates>0,{latitude_per_mile * 0.4},0 0,{latitude_per_mile * 0.75},0</coordinates></LineString></Placemark>
</Document></kml>""",
        encoding="utf-8",
    )

    runs = ApprovedProgressLoader.source_geometry_runs(source_kml)
    sections = RouteSegmenter.normalized_sections(runs, interval_meters=1609.344 / 2)

    assert len(runs) == 2
    assert [section.label for section in sections] == [
        "Segment 001 - Start to Checkpoint 001",
        "Segment 002 - Checkpoint 001 to support-car pickup",
        "Segment 003 - Runner restart to Checkpoint 003",
        "Segment 004 - Checkpoint 003 to Finish",
    ]
    assert all(
        RouteGeometry.distance_meters([list(section.coordinates)])
        <= 1609.344 / 2 + 1e-6
        for section in sections
    )


def test_geocoding_cache_round_trips_and_reuses_coordinates(tmp_path) -> None:
    cache_path = tmp_path / "data" / "geocoding-cache.json"
    cache = {
        "Pico Boulevard & Main Street, Santa Monica, CA": {
            "latitude": 34.01,
            "longitude": -118.49,
            "display_name": "Pico and Main",
        },
    }
    save_geocoding_cache(cache_path, cache)

    assert json.loads(cache_path.read_text(encoding="utf-8")) == cache
    restored = load_geocoding_cache(cache_path)
    point = point_from_cache(
        Checkpoint("Pico / Main", next(iter(cache))),
        restored[next(iter(cache))],
    )

    assert point == Point(
        "Pico / Main",
        next(iter(cache)),
        34.01,
        -118.49,
        "Pico and Main",
    )


def test_missing_geocoding_cache_is_empty(tmp_path) -> None:
    assert load_geocoding_cache(tmp_path / "not-yet-created.json") == {}


def test_route_cache_key_uses_coordinates_profile_and_variant() -> None:
    start = Point("Start", "start", 34.0, -118.0, "Start")
    end = Point("End", "end", 33.0, -117.0, "End")

    assert (
        route_cache_key(start, end)
        == "google-walk:34.0000000,-118.0000000:33.0000000,-117.0000000"
    )
    assert route_cache_key(
        start,
        end,
        travel_mode="DRIVE",
        variant="organizer-turn-v1",
    ) == (
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
        "pedestrian": {"use_ferry": 0, "ferry_cost": 43200},
    }


def test_ambiguous_pch_exit_uses_the_organizer_landmark_not_seal_beach() -> None:
    pch_exit = next(
        point
        for point in ROUTE_CHECKPOINTS
        if point.label == "PCH / river-trail exit area"
    )

    assert pch_exit.query == "925 W Esther Street, Long Beach, CA 90813"


def test_highway_1_replaces_the_unrequested_long_beach_beach_path_detour() -> None:
    labels = [point.label for point in ROUTE_CHECKPOINTS]
    assert "Long Beach Shoreline Beach Path - west entrance" not in labels
    assert "Long Beach Shoreline Beach Path - east exit" not in labels
    start = Point("PCH / river-trail exit area", "", 33.7739, -118.2024, "")
    end = Point("Del Prado / Golden Lantern", "", 33.465, -117.698, "")
    assert is_highway_1_pair(start, end)
    assert len(HIGHWAY_1_VIA_POINTS) >= 8
    # The PCH profile deliberately omits the old Ocean Blvd/Long Beach
    # beachfront shaping points and begins at the unambiguous Seal Beach PCH.
    assert HIGHWAY_1_VIA_POINTS[0] == (33.744000, -118.105000)
    assert (33.767700, -118.197000) not in HIGHWAY_1_VIA_POINTS
    assert (33.758600, -118.178800) not in HIGHWAY_1_VIA_POINTS


def test_organizer_turns_force_the_literal_sepulveda_w78_w79_sequence() -> None:
    centinela = Point("Centinela Ave / Sepulveda Blvd", "", 33.977, -118.386, "")
    sepulveda = Point("Sepulveda Blvd / W 78th St", "", 33.970, -118.386, "")
    w78 = Point("W 78th St / W 79th St", "", 33.9685, -118.385, "")
    w79 = Point("W 79th St / Isis Ave", "", 33.968, -118.380, "")

    assert is_strict_road_pair(centinela, sepulveda)
    assert is_strict_road_pair(sepulveda, w78)
    assert is_strict_road_pair(w78, w79)


def test_i5_chevron_transition_is_a_support_car_gap_not_runner_geometry(
    tmp_path,
    monkeypatch,
) -> None:
    before = Point("Before", "", 33.42, -117.61, "")
    start = Point("San Mateo Point", "", 33.418, -117.604, "")
    end = Point("Chevron - I-5 exit 54C runner restart", "", 33.165, -117.354, "")
    after = Point("After", "", 33.16, -117.35, "")
    router = RunnerRouter(
        {},
        tmp_path / "routing-cache.json",
        "unused",
        {},
        tmp_path / "usage.json",
    )
    router.routing_cache = {
        router.route_cache_key(before, start): {
            "geometry": [
                (before.latitude, before.longitude),
                (start.latitude, start.longitude),
            ],
            "distance_meters": 100,
        },
        router.route_cache_key(end, after): {
            "geometry": [
                (end.latitude, end.longitude),
                (after.latitude, after.longitude),
            ],
            "distance_meters": 100,
        },
    }
    monkeypatch.setattr(
        router,
        "google_route",
        lambda *args, **kwargs: pytest.fail("I-5 transfer must not be routed"),
    )

    assert is_i5_transfer_pair(start, end)
    segments, distance = router.trace([before, start, end, after])
    assert segments == [
        [(before.latitude, before.longitude), (start.latitude, start.longitude)],
        [(end.latitude, end.longitude), (after.latitude, after.longitude)],
    ]
    assert distance == 200


def test_exit_54c_chevron_is_pinned_to_oceanside_not_san_clemente() -> None:
    chevron = next(
        point
        for point in ROUTE_CHECKPOINTS
        if point.label == "Chevron - I-5 exit 54C runner restart"
    )

    assert chevron.query == "Chevron, 1601 N Coast Hwy, Oceanside, CA 92054"


def test_google_walking_route_requests_the_ordered_walking_leg() -> None:
    start = Point("Start", "", 34.0, -118.0, "")
    end = Point("End", "", 33.0, -117.0, "")

    assert google_walking_route_payload(start, end) == {
        "origin": {"location": {"latLng": {"latitude": 34.0, "longitude": -118.0}}},
        "destination": {
            "location": {"latLng": {"latitude": 33.0, "longitude": -117.0}},
        },
        "travelMode": "WALK",
        "languageCode": "en-US",
        "units": "IMPERIAL",
    }


def test_google_route_payload_can_pin_a_highway_1_road_alignment() -> None:
    start = Point("Start", "", 34.0, -118.0, "")
    end = Point("End", "", 33.0, -117.0, "")

    payload = google_route_payload(
        start,
        end,
        travel_mode="DRIVE",
        via_points=((33.5, -117.5),),
    )

    assert payload["travelMode"] == "DRIVE"
    assert payload["intermediates"] == [
        {"via": True, "location": {"latLng": {"latitude": 33.5, "longitude": -117.5}}},
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
    assert (
        load_google_routes_usage(usage_path)["requests_sent"]
        == GOOGLE_ROUTES_REQUEST_LIMIT
    )
    with pytest.raises(GoogleRoutesRequestLimitError, match="9,500"):
        reserve_google_routes_request(usage, usage_path)
