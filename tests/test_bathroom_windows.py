"""Offline tests for fixed-mile rolling bathroom-window selection."""

from pathlib import Path

import pytest

from nstt_course_planner.bathroom_windows import (
    BathroomStopsKml,
    BathroomWindowBuildConfig,
    BathroomWindowLayerBuilder,
    BathroomWindowSelector,
)
from nstt_course_planner.bathrooms import RunnerRouteKml
from nstt_course_planner.models.bathrooms import (
    BathroomCategory,
    BathroomStop,
    CarAccessConstraint,
)


def runner_kml(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Segment 001 - Start to Checkpoint 001</name><LineString><coordinates>0,0,0 0,0.25,0</coordinates></LineString></Placemark>
</Document></kml>""",
        encoding="utf-8",
    )


def bathroom_kml(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>🚻 Near mile twelve</name><ExtendedData><Data name="Category"><value>Official beach restroom</value></Data></ExtendedData><description>Address: Test address&lt;br/&gt;Source: &lt;a href="https://example.com"&gt;source&lt;/a&gt;</description><Point><coordinates>0,0.1735,0</coordinates></Point></Placemark>
<Placemark><name>☕ Nearby coffee</name><ExtendedData><Data name="Category"><value>Coffee-chain backup</value></Data></ExtendedData><description>Address: Nearby address&lt;br/&gt;Source: &lt;a href="https://example.com/coffee"&gt;source&lt;/a&gt;</description><Point><coordinates>0.002,0.174,0</coordinates></Point></Placemark>
</Document></kml>""",
        encoding="utf-8",
    )


def test_builder_selects_a_twelve_mile_window_without_rewriting_runner_geometry(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "Runner.kml"
    stops = tmp_path / "bathroom-stops.kml"
    output = tmp_path / "bathroom-windows.kml"
    runner_kml(runner)
    bathroom_kml(stops)
    source = runner.read_text(encoding="utf-8")

    windows = BathroomWindowLayerBuilder(
        BathroomWindowBuildConfig(runner, stops, output),
    ).build()

    assert runner.read_text(encoding="utf-8") == source
    assert len(windows) == 2
    assert windows[0].target_miles == 12
    assert windows[0].actual_runner_miles == pytest.approx(12.0, abs=0.1)
    assert windows[0].eta is not None
    assert {window.stop.name for window in windows} == {
        "🚻 Near mile twelve",
        "☕ Nearby coffee",
    }
    assert "Rolling bathroom window target: mile 12" in output.read_text(
        encoding="utf-8"
    )
    assert "ETA:" in output.read_text(encoding="utf-8")
    assert "Source: full bathroom layer" not in output.read_text(encoding="utf-8")


def test_selector_moves_a_window_outside_a_car_access_constraint(
    tmp_path: Path,
) -> None:
    runner = tmp_path / "Runner.kml"
    runner_kml(runner)
    route_runs = RunnerRouteKml.line_runs(runner)
    near_target = BathroomStop(
        "Inside",
        BathroomCategory.OFFICIAL_BEACH,
        0.1735,
        0,
        "Inside",
        "https://example.com",
        "",
    )
    before_constraint = BathroomStop(
        "Before",
        BathroomCategory.GROCERY,
        0.159,
        0,
        "Before",
        "https://example.com",
        "",
    )
    selector = BathroomWindowSelector(
        route_runs,
        (CarAccessConstraint(11.5, 12.5, "test corridor"),),
    )

    window = selector.select((near_target, before_constraint), 12, 2)[0]

    assert window.stop.name == "Before"
    assert (
        window.constraint_note
        == "Moved outside the test corridor support-car constraint."
    )


def test_stop_kml_loader_preserves_category_and_source(tmp_path: Path) -> None:
    source = tmp_path / "bathroom-stops.kml"
    bathroom_kml(source)

    stop = BathroomStopsKml.load(source)[0]

    assert stop.category is BathroomCategory.OFFICIAL_BEACH
    assert stop.address == "Test address"
    assert stop.source_url == "https://example.com"
