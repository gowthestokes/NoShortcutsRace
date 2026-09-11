"""Build a Google My Maps-importable runner-route draft from the organizer sheet."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from nstt_course_planner.config import DEFAULT_GEOCODING_CACHE, DEFAULT_GOOGLE_USAGE, DEFAULT_OUTPUT_DIR, DEFAULT_PATH_CACHE, DEFAULT_ROUTING_CACHE, GOOGLE_ROUTES_REQUEST_LIMIT, HIGHWAY_1_VIA_POINTS, SEGMENT_METERS
from nstt_course_planner.exporting import CourseExporter
from nstt_course_planner.geocoding import CheckpointGeocoder
from nstt_course_planner.geometry import RouteGeometry, RouteSegmenter
from nstt_course_planner.models import ApprovedRouteProgress, Checkpoint, CourseBuildConfig, GoogleRoutesRequestLimitError, Point, RunnerRouteSection, UnsafePedestrianRouteError
from nstt_course_planner.progress import ApprovedProgressLoader
from nstt_course_planner.route_spec import ROUTE_SPEC, RaceRouteSpec
from nstt_course_planner.routing import RunnerRouter
from nstt_course_planner.storage import GoogleUsageTracker, JsonStore
from nstt_course_planner.utils import Environment

# Compatibility exports for callers of the original single-module API.
CHECKPOINTS = tuple(Checkpoint(point.label, point.query) for point in ROUTE_SPEC.route_checkpoints)
load_geocoding_cache = JsonStore.load_cache
save_geocoding_cache = JsonStore.save_cache
load_google_routes_usage = GoogleUsageTracker.load
reserve_google_routes_request = GoogleUsageTracker.reserve
set_google_routes_request_status = GoogleUsageTracker.set_status
point_from_cache = CheckpointGeocoder().point_from_cache
load_approved_route_progress = ApprovedProgressLoader.load
approved_route_geometry = ApprovedProgressLoader.geometry
approved_route_sections = ApprovedProgressLoader.sections
approved_mile_markers = ApprovedProgressLoader.markers
haversine_meters = RouteGeometry.haversine_meters
geometry_distance_meters = RouteGeometry.distance_meters
interpolate_coordinate = RouteGeometry.interpolate
mile_markers = RouteSegmenter.markers
runner_route_sections = RouteSegmenter.sections
write_outputs = CourseExporter().write
route_cache_key = RunnerRouter.route_cache_key
is_i5_transfer_pair = RunnerRouter.is_i5_transfer_pair
is_strict_road_pair = RunnerRouter.is_strict_road_pair
is_highway_1_pair = RunnerRouter.is_highway_1_pair
highway_1_via_points_from = RunnerRouter.highway_1_via_points_from
pedestrian_route_payload = RunnerRouter.pedestrian_route_payload
google_route_payload = RunnerRouter.google_route_payload
google_walking_route_payload = RunnerRouter.google_walking_route_payload
require_land_based_pedestrian_trip = RunnerRouter.require_land_based_pedestrian_trip
google_route_distance_meters = RunnerRouter.google_route_distance_meters


class CourseBuilder:
    """Coordinates one ``RaceRouteSpec`` build using focused domain services."""

    def __init__(self, config: CourseBuildConfig, route_spec: RaceRouteSpec = ROUTE_SPEC) -> None:
        self.config = config
        self.route_spec = route_spec
        self.geocoding_cache = JsonStore.load_cache(config.geocoding_cache_path)
        self.routing_cache = JsonStore.load_cache(config.routing_cache_path)
        self.path_cache = JsonStore.load_cache(config.path_cache_path)
        self.google_usage = GoogleUsageTracker.load(config.google_usage_path)
        self.router = RunnerRouter(self.routing_cache, config.routing_cache_path, Environment.google_maps_api_key(), self.google_usage, config.google_usage_path)
        self.geocoder = CheckpointGeocoder()
        self.exporter = CourseExporter()

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for KML, GPX, and import notes.")
        parser.add_argument("--geocoding-cache", type=Path, default=DEFAULT_GEOCODING_CACHE, help="Locally persisted public-geocoder results.")
        parser.add_argument("--routing-cache", type=Path, default=DEFAULT_ROUTING_CACHE, help="Locally persisted route geometry.")
        parser.add_argument("--path-cache", type=Path, default=DEFAULT_PATH_CACHE, help="Locally persisted named-path geometry.")
        parser.add_argument("--google-usage", type=Path, default=DEFAULT_GOOGLE_USAGE, help="Conservative local Google Routes request counter.")
        parser.add_argument("--approved-segments-kml", type=Path, help="Google My Maps KML containing user-edited route segments to preserve.")
        parser.add_argument("--approved-through-segment", type=int, help="Last consecutively approved source segment number.")
        parser.add_argument("--resume-at-checkpoint", default="Del Prado / Golden Lantern", help="Organizer checkpoint at which rerouting resumes.")

    @classmethod
    def from_arguments(cls, args: argparse.Namespace) -> "CourseBuilder":
        return cls(CourseBuildConfig(args.output_dir, args.geocoding_cache, args.routing_cache, args.path_cache, args.google_usage, args.approved_segments_kml, args.approved_through_segment, args.resume_at_checkpoint))

    def resolve_points(self) -> list[Point]:
        points: list[Point] = []
        for checkpoint in self.route_spec.route_checkpoints:
            print(f"Geocoding: {checkpoint.label}")
            point, from_cache = self.geocoder.geocode(Checkpoint(checkpoint.label, checkpoint.query), self.geocoding_cache)
            points.append(point)
            if not from_cache:
                JsonStore.save_cache(self.config.geocoding_cache_path, self.geocoding_cache)
                time.sleep(1.1)
        return points

    def build_from_approved_progress(self, points: list[Point]) -> tuple[list[list[tuple[float, float]]], list[RunnerRouteSection], list[tuple[int, float, float]]]:
        if not self.config.approved_segments_kml or self.config.approved_through_segment is None:
            raise RuntimeError("Approved KML and approved-through segment are required for a progress rebuild.")
        progress = ApprovedProgressLoader.load(self.config.approved_segments_kml, self.config.approved_through_segment)
        try:
            resume_index = next(index for index, point in enumerate(points) if point.label == self.config.resume_at_checkpoint)
        except StopIteration as error:
            raise RuntimeError(f"Unknown resume checkpoint: {self.config.resume_at_checkpoint!r}.") from error
        if resume_index == 0:
            raise RuntimeError("The resume checkpoint must be after the course start.")
        approved_geometry = ApprovedProgressLoader.geometry(progress)
        latitude, longitude = approved_geometry[-1]
        manual_resume = Point(f"Manual runner resume after Segment {progress.through_segment:03d}", "Approved My Maps route endpoint", latitude, longitude, "Approved My Maps route endpoint")
        print(f"Preserving approved My Maps segments through {progress.sections[-1].label}; rerouting from its endpoint to {self.config.resume_at_checkpoint}.")
        downstream, _ = self.router.trace([manual_resume, *points[resume_index:]], manual_highway_1_resume=True)
        if downstream and downstream[0][0] != (latitude, longitude):
            downstream[0].insert(0, (latitude, longitude))
        output = [[*approved_geometry, *downstream[0][1:]], *downstream[1:]]
        distance = RouteGeometry.distance_meters(output)
        return output, RouteSegmenter.sections(output, distance, interval_meters=SEGMENT_METERS, checkpoint_label="Checkpoint"), RouteSegmenter.markers(output, distance, interval_meters=SEGMENT_METERS)

    def build(self) -> None:
        points = self.resolve_points()
        if self.config.approved_segments_kml:
            segments, sections, markers = self.build_from_approved_progress(points)
            distance = RouteGeometry.distance_meters(segments)
            self.exporter.write(self.config.output_dir, points, segments, distance, route_sections_override=sections, markers_override=markers, gpx_segments_override=segments)
        else:
            print("Tracing pedestrian routes between organizer checkpoints")
            segments, _ = self.router.trace(points)
            distance = RouteGeometry.distance_meters(segments)
            self.exporter.write(self.config.output_dir, points, segments, distance)
            markers = RouteSegmenter.markers(segments, distance, interval_meters=SEGMENT_METERS)
        print(f"Created {sum(len(segment) for segment in segments)} runner trace points in {len(segments)} runner segments, {distance / 1609.344:.1f} mi, {len(markers)} half-mile checkpoints.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    CourseBuilder.add_arguments(parser)
    CourseBuilder.from_arguments(parser.parse_args()).build()


if __name__ == "__main__":
    main()
