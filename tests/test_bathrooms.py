"""Offline unit tests for the standalone bathroom planning layer."""

from pathlib import Path

import pytest

from nstt_course_planner.bathrooms import (
    BATHROOM_STOPS,
    BathroomCategory,
    BathroomLayerBuilder,
    BathroomStop,
    RunnerRouteKml,
)


def test_bathroom_categories_follow_the_team_priority_order() -> None:
    assert [category.priority for category in BathroomCategory] == [1, 1, 2, 3, 4]
    assert {stop.category for stop in BATHROOM_STOPS} == {
        BathroomCategory.OFFICIAL_BEACH,
        BathroomCategory.GROCERY,
        BathroomCategory.COFFEE,
        BathroomCategory.FAST_FOOD_OR_GAS,
    }


def test_official_beach_stops_use_public_agency_sources() -> None:
    official_hosts = (
        "santamonica.gov", "beaches.lacounty.gov", "parks.ca.gov", "carlsbadca.gov",
        "encinitasca.gov", "sandiego.gov", "lagunabeachcity.net", "danapoint.org",
        "ci.oceanside.ca.us", "cityofsolanabeach.org", "delmar.ca.us",
    )
    for stop in BATHROOM_STOPS:
        if stop.category is BathroomCategory.OFFICIAL_BEACH:
            assert any(host in stop.source_url for host in official_hosts)
        else:
            assert "confirm" in stop.source_note.lower()


def test_builder_preserves_source_and_writes_ranked_kml(tmp_path: Path) -> None:
    source = tmp_path / "Runner.kml"
    source_text = '''<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><LineString><coordinates>-118.50,34.00,0 -118.49,34.01,0</coordinates></LineString></Placemark>
    </Document></kml>'''
    source.write_text(source_text, encoding="utf-8")
    output = tmp_path / "bathrooms.kml"

    result = BathroomLayerBuilder().build(source, output)

    assert source.read_text(encoding="utf-8") == source_text
    text = output.read_text(encoding="utf-8")
    assert text.count("<Placemark>") == len(BATHROOM_STOPS)
    assert "Priority 1: Official beach restroom" in text
    assert "Priority 4: Fast-food or branded-gas backup" in text
    assert '<Data name="Category"><value>Official beach restroom</value></Data>' in text
    assert "🚻 Santa Monica State Beach" in text
    assert "Approximate runner mile:" in text
    assert "not a walking detour" in text
    assert result.stop_count == len(BATHROOM_STOPS)


def test_route_reader_requires_line_geometry(tmp_path: Path) -> None:
    source = tmp_path / "empty.kml"
    source.write_text('<kml xmlns="http://www.opengis.net/kml/2.2"><Document/></kml>', encoding="utf-8")

    with pytest.raises(ValueError, match="no usable LineString geometry"):
        RunnerRouteKml.line_runs(source)


def test_nearest_route_distance_uses_each_route_run_without_crossing_a_gap() -> None:
    route_runs = (((0.0, 0.0), (0.0, 0.01)), ((1.0, 1.0), (1.0, 1.01)))

    distance = BathroomLayerBuilder.nearest_route_distance_meters((0.001, 0.005), route_runs)

    assert distance == pytest.approx(111.32, abs=1.0)


def test_researched_stops_are_explicitly_nearby_not_long_detours() -> None:
    builder = BathroomLayerBuilder()
    source = Path(__file__).parents[1] / "input" / "Runner.kml"
    route_runs = RunnerRouteKml.line_runs(source)

    distances = [builder.nearest_route_distance_meters((stop.latitude, stop.longitude), route_runs) for stop in BATHROOM_STOPS]

    assert max(distances) < 1_000
