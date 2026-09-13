"""Unit tests for the opt-in, capped Google Elevation layer."""

from __future__ import annotations

import math

import pytest

from nstt_course_planner.elevation import (
    ElevationAnalyzer,
    ElevationLayerExporter,
    GoogleElevationClient,
    RouteGeometrySampler,
)
from nstt_course_planner.geometry import RouteGeometry
from nstt_course_planner.errors import GoogleElevationSampleLimitError
from nstt_course_planner.models.elevation import DistanceSample, ElevationProfile
from nstt_course_planner.models.route import RunnerRouteSection
from nstt_course_planner.storage import GoogleElevationUsageTracker


def latitude_offset(meters: float) -> float:
    return meters * 180 / (math.pi * 6_371_000)


def test_geometry_sampler_uses_roughly_fixed_spacing_and_keeps_the_endpoint() -> None:
    samples = RouteGeometrySampler.sample([(0.0, 0.0), (latitude_offset(120), 0.0)], 50)

    assert [round(sample.distance_meters) for sample in samples] == [0, 50, 100, 120]
    assert samples[-1].coordinate == (latitude_offset(120), 0.0)


def test_smoothing_uses_a_distance_window() -> None:
    samples = tuple(DistanceSample((0.0, 0.0), distance) for distance in (0, 50, 100, 150, 200))

    smoothed = ElevationAnalyzer.smooth([0, 100, 0, 100, 0], samples, 150)

    assert smoothed == pytest.approx((50, 100 / 3, 200 / 3, 100 / 3, 50))


def test_section_summary_calculates_signed_average_grade() -> None:
    distance = 100.0
    coordinates = ((0.0, 0.0), (latitude_offset(distance), 0.0))
    profile = ElevationProfile(
        (DistanceSample(coordinates[0], 0), DistanceSample(coordinates[1], distance)),
        (0, 8),
        (0, 8),
    )
    section = RunnerRouteSection("Segment 001 - Start to Checkpoint 001", coordinates)

    summary = ElevationAnalyzer.summarize_section(section, profile, 0)

    assert summary.average_grade_percent == pytest.approx(8, abs=0.01)
    assert summary.net_elevation_meters == pytest.approx(8)


@pytest.mark.parametrize(
    ("grade", "style"),
    [(-6, "darkBlue"), (-3, "blue"), (-1.01, "lightBlue"), (-1, "gray"), (1, "gray"), (1.01, "lightOrange"), (3, "lightOrange"), (3.01, "orange"), (6, "red")],
)
def test_grade_colors_match_the_published_legend(grade: float, style: str) -> None:
    assert ElevationLayerExporter.style_name(grade) == style


def test_elevation_client_caches_values_and_reserves_each_batch(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "elevation-cache.json"
    usage_path = tmp_path / "elevation-usage.json"
    coordinates = [(30.0 + index / 10_000, -118.0) for index in range(129)]
    cache = {GoogleElevationClient.cache_key(coordinates[0]): {"elevation_meters": 99.0}}
    usage = GoogleElevationUsageTracker.default_usage()
    client = GoogleElevationClient(cache, cache_path, "not-used", usage, usage_path)
    requested_batches: list[list[tuple[float, float]]] = []
    monkeypatch.setattr(
        client,
        "_request_batch",
        lambda batch: (requested_batches.append(batch), [float(index) for index in range(len(batch))])[1],
    )

    elevations = client.elevations(coordinates)

    assert elevations[0] == 99
    assert [len(batch) for batch in requested_batches] == [128]
    assert usage["samples_sent"] == 128
    assert usage["requests_sent"] == 1
    assert GoogleElevationUsageTracker.load(usage_path)["last_request_status"] == "success"


def test_elevation_client_stops_before_calling_google_when_the_cap_would_be_exceeded(tmp_path, monkeypatch) -> None:
    usage_path = tmp_path / "elevation-usage.json"
    usage = GoogleElevationUsageTracker.default_usage()
    usage["samples_sent"] = 4_499
    client = GoogleElevationClient({}, tmp_path / "elevation-cache.json", "not-used", usage, usage_path)
    called = False

    def unexpected_request(_batch):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(client, "_request_batch", unexpected_request)

    with pytest.raises(GoogleElevationSampleLimitError, match="no request was sent"):
        client.elevations([(30.0, -118.0), (30.1, -118.0)])

    assert not called
    assert not usage_path.exists()


def test_elevation_export_contains_legend_and_segment_popup_data(tmp_path) -> None:
    section = RunnerRouteSection("Segment 001 - Start to Checkpoint 001", ((34.0, -118.0), (34.01, -118.0)))
    profile = ElevationProfile(
        (DistanceSample(section.coordinates[0], 0), DistanceSample(section.coordinates[-1], RouteGeometry.distance_meters([list(section.coordinates)]))),
        (10, 120),
        (10, 120),
    )
    summary = ElevationAnalyzer.summarize_section(section, profile, 0)

    ElevationLayerExporter.write(tmp_path, [summary])

    kml = (tmp_path / "NSTT_2026_elevation.kml").read_text(encoding="utf-8")
    assert "Dark blue: ≤ -6%" in kml
    assert "Smoothed terrain elevation" in kml
    assert "verify bridge decks" not in kml
    assert "#red" in kml
