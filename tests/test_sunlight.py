"""Offline tests for the team-pace sunlight overlay."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nstt_course_planner.models.route import RunnerRouteSection
from nstt_course_planner.models.sunlight import (
    RunnerPace,
    SunlightBuildConfig,
    SunlightCategory,
)
from nstt_course_planner.sunlight import (
    RelaySunlightSimulator,
    SolarPosition,
    SunlightLayerExporter,
    TeamPaceLoader,
)

LOS_ANGELES = (34.009, -118.497)
RACE_START = datetime(2026, 10, 23, 5, tzinfo=ZoneInfo("America/Los_Angeles"))


def section(number: int, latitude: float) -> RunnerRouteSection:
    return RunnerRouteSection(
        f"Segment {number:03d} - Start to Checkpoint {number:03d}",
        ((latitude, -118.0), (latitude + 0.00725, -118.0)),
    )


def config(tmp_path, *, transfer_minutes: float | None = None) -> SunlightBuildConfig:
    return SunlightBuildConfig(
        tmp_path / "Runner.kml",
        tmp_path / "team-pace.json",
        tmp_path / "sunlight.kml",
        RACE_START,
        transfer_duration_minutes=transfer_minutes,
    )


def test_team_pace_loader_keeps_json_order(tmp_path) -> None:
    path = tmp_path / "team-pace.json"
    path.write_text('{"First": 8.0, "Second": 10.5}', encoding="utf-8")

    assert TeamPaceLoader.load(path) == (
        RunnerPace("First", 8.0),
        RunnerPace("Second", 10.5),
    )


def test_solar_categories_match_the_visibility_thresholds() -> None:
    assert SolarPosition.category(-6.1) is SunlightCategory.DARK
    assert SolarPosition.category(-2) is SunlightCategory.TWILIGHT
    assert SolarPosition.category(0) is SunlightCategory.SUNRISE_SUNSET
    assert SolarPosition.category(6) is SunlightCategory.DAYLIGHT


def test_solar_position_marks_race_start_dark_and_midday_daylight() -> None:
    noon = RACE_START.replace(hour=12)

    assert SolarPosition.altitude_degrees(RACE_START, LOS_ANGELES) < -6
    assert SolarPosition.altitude_degrees(noon, LOS_ANGELES) > 6


def test_simulator_assigns_two_half_mile_sections_per_runner(tmp_path) -> None:
    simulator = RelaySunlightSimulator(
        config(tmp_path), (RunnerPace("A", 8), RunnerPace("B", 10))
    )

    scheduled = simulator.schedule(
        [
            [
                section(1, 34.0),
                section(2, 34.00725),
                section(3, 34.0145),
                section(4, 34.02175),
            ]
        ]
    )

    assert [entry.runner.name for entry in scheduled] == ["A", "A", "B", "B"]
    assert scheduled[0].start_runner_miles == 0
    assert scheduled[1].end_runner_miles > scheduled[1].start_runner_miles
    assert (scheduled[1].start_time - RACE_START).total_seconds() == pytest.approx(
        240, abs=2
    )
    assert (scheduled[2].start_time - RACE_START).total_seconds() == pytest.approx(
        480, abs=2
    )


def test_simulator_adds_the_configured_support_car_pause(tmp_path) -> None:
    simulator = RelaySunlightSimulator(
        config(tmp_path, transfer_minutes=25), (RunnerPace("A", 8),)
    )

    scheduled = simulator.schedule([[section(1, 34.0)], [section(2, 34.00725)]])

    assert scheduled[1].start_time == scheduled[0].end_time + timedelta(minutes=25)


def test_simulator_requires_a_confirmed_transfer_duration_for_disconnected_runs(
    tmp_path,
) -> None:
    simulator = RelaySunlightSimulator(config(tmp_path), (RunnerPace("A", 8),))

    with pytest.raises(ValueError, match="support-car transfer duration"):
        simulator.schedule([[section(1, 34.0)], [section(2, 34.00725)]])


def test_exporter_writes_all_visibility_styles(tmp_path) -> None:
    simulator = RelaySunlightSimulator(config(tmp_path), (RunnerPace("A", 8),))
    scheduled = simulator.schedule([[section(1, 34.0)]])

    SunlightLayerExporter.write(tmp_path / "sunlight.kml", scheduled)

    kml = (tmp_path / "sunlight.kml").read_text(encoding="utf-8")
    assert 'id="dark"' in kml
    assert 'id="twilight"' in kml
    assert 'id="sunriseSunset"' in kml
    assert 'id="daylight"' in kml
    assert "Runner:" not in kml
    assert "Runner miles:" in kml
    assert "ETA:" in kml
    assert "Dark: Segment 001" in kml
    assert "Dark: Segment 001 - Start to Checkpoint 001" not in kml
