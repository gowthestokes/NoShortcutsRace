"""Build a Google My Maps-importable runner-route draft from the organizer sheet."""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from nstt_course_planner.config import DEFAULT_GEOCODING_CACHE, DEFAULT_GOOGLE_USAGE, DEFAULT_OUTPUT_DIR, DEFAULT_PATH_CACHE, DEFAULT_ROUTING_CACHE, GOOGLE_ROUTES_REQUEST_LIMIT, HIGHWAY_1_VIA_POINTS, SEGMENT_METERS
from nstt_course_planner.exporting import CourseExporter
from nstt_course_planner.geocoding import CheckpointGeocoder
from nstt_course_planner.geometry import RouteGeometry, RouteSegmenter
from nstt_course_planner.errors import GoogleRoutesRequestLimitError, UnsafePedestrianRouteError
from nstt_course_planner.models.course import Checkpoint, CourseBuildConfig
from nstt_course_planner.models.route import ApprovedRouteProgress, Point, RunnerRouteSection
from nstt_course_planner.progress import ApprovedProgressLoader
from nstt_course_planner.models.route import RaceRouteSpec
from nstt_course_planner.route_spec import ROUTE_SPEC
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
haversine_meters = RouteGeometry.haversine_meters
geometry_distance_meters = RouteGeometry.distance_meters
interpolate_coordinate = RouteGeometry.interpolate
runner_route_sections = RouteSegmenter.sections
write_outputs = CourseExporter().write
route_cache_key = RunnerRouter.route_cache_key
is_i5_transfer_pair = RunnerRouter.is_i5_transfer_pair
is_i5_support_car_transfer_pair = RunnerRouter.is_i5_support_car_transfer_pair
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
        parser.add_argument("--source-of-truth-kml", type=Path, help="KML whose route segments are retained exactly on both sides of a transfer gap.")
        parser.add_argument("--source-prefix-through-segment", type=int, help="Last immutable source segment before the transfer gap.")
        parser.add_argument("--source-prefix-end-marker", default="San Mateo Point", help="Named point that extends the final immutable source segment before the transfer gap.")
        parser.add_argument("--normalize-source-kml", type=Path, help="Manual runner KML whose exact geometry is re-cut into half-mile lines.")
        parser.add_argument("--official-directions-kml", type=Path, help="Verified organizer-directions KML copied into outputs as a reference layer.")

    @classmethod
    def from_arguments(cls, args: argparse.Namespace) -> "CourseBuilder":
        return cls(CourseBuildConfig(args.output_dir, args.geocoding_cache, args.routing_cache, args.path_cache, args.google_usage, args.approved_segments_kml, args.approved_through_segment, args.resume_at_checkpoint, args.source_of_truth_kml, args.source_prefix_through_segment, args.source_prefix_end_marker, args.normalize_source_kml, args.official_directions_kml))

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

    def build_from_approved_progress(self, points: list[Point]) -> tuple[list[list[tuple[float, float]]], list[RunnerRouteSection]]:
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
        support_car_gap = self.router.is_i5_support_car_transfer_pair(
            manual_resume, points[resume_index]
        )
        if support_car_gap:
            # The last immutable source segment ends at the support-car pickup.
            # Keep it intact and begin a new runner geometry segment at Chevron.
            output = [approved_geometry, *downstream]
            downstream_start_label = "Runner restart"
        else:
            if downstream and downstream[0][0] != (latitude, longitude):
                downstream[0].insert(0, (latitude, longitude))
            output = [[*approved_geometry, *downstream[0][1:]], *downstream[1:]]
            downstream_start_label = f"Checkpoint {progress.through_segment:03d}"
        distance = RouteGeometry.distance_meters(output)
        source_sections = list(progress.sections)
        downstream_distance = RouteGeometry.distance_meters(downstream)
        downstream_sections = RouteSegmenter.sections(
            downstream,
            downstream_distance,
            first_segment_number=progress.through_segment + 1,
            start_label=downstream_start_label,
            interval_meters=SEGMENT_METERS,
            checkpoint_label="Checkpoint",
        )
        return output, [*source_sections, *downstream_sections]

    @staticmethod
    def _combine_sections(sections: list[RunnerRouteSection]) -> list[tuple[float, float]]:
        """Join continuous source lines without modifying their individual geometry."""
        if not sections:
            return []
        coordinates = list(sections[0].coordinates)
        for section in sections[1:]:
            coordinates.extend(section.coordinates[1:])
        return coordinates

    def build_from_source_of_truth(self) -> tuple[list[list[tuple[float, float]]], list[RunnerRouteSection]]:
        """Retain the user's KML exactly, adding only the San Mateo connector."""
        source_path = self.config.source_of_truth_kml
        through_segment = self.config.source_prefix_through_segment
        if source_path is None or through_segment is None:
            raise RuntimeError("Source KML and source-prefix-through-segment are required for a source-of-truth rebuild.")

        prefix = list(ApprovedProgressLoader.load(source_path, through_segment).sections)
        tail = list(ApprovedProgressLoader.load_tail(source_path, through_segment + 1))
        marker_latitude, marker_longitude = ApprovedProgressLoader.named_point(source_path, self.config.source_prefix_end_marker)
        endpoint_latitude, endpoint_longitude = prefix[-1].coordinates[-1]
        source_endpoint = Point(
            f"Checkpoint {through_segment:03d}",
            "Endpoint of immutable user-drawn route segment",
            endpoint_latitude,
            endpoint_longitude,
            f"Checkpoint {through_segment:03d}",
        )
        san_mateo = Point(
            self.config.source_prefix_end_marker,
            "User-placed San Mateo Point team photo and support-car pickup",
            marker_latitude,
            marker_longitude,
            self.config.source_prefix_end_marker,
        )
        print(f"Preserving source Segment 001 through Segment {through_segment:03d} exactly; adding only the walkable connector to {san_mateo.label}.")
        connector_segments, _ = self.router.trace([source_endpoint, san_mateo])
        if len(connector_segments) != 1 or len(connector_segments[0]) < 2:
            raise RuntimeError("Could not create the runner connector from the source endpoint to San Mateo Point.")
        connector = RunnerRouteSection(
            f"Segment {through_segment:03d}b - Checkpoint {through_segment:03d} to {san_mateo.label}",
            tuple(connector_segments[0]),
        )

        # The two geometries deliberately remain separate: runners stop at San
        # Mateo, ride with the support car on I-5, then resume at the source
        # KML's Chevron/Coast Highway segment. No source line is regenerated.
        route_segments = [
            self._combine_sections([*prefix, connector]),
            self._combine_sections(tail),
        ]
        source_sections = [*prefix, connector, *tail]
        return route_segments, source_sections

    def build_from_normalized_source(self) -> tuple[list[list[tuple[float, float]]], list[RunnerRouteSection]]:
        """Use the manual KML geometry verbatim while normalizing its line cuts."""
        source_path = self.config.normalized_source_kml
        if source_path is None:
            raise RuntimeError("A normalized source KML is required for a normalization rebuild.")
        runs = ApprovedProgressLoader.source_geometry_runs(source_path)
        if len(runs) != 2:
            raise RuntimeError(f"Expected one runner run before and one after the support-car transfer, found {len(runs)} source runs.")
        sections = RouteSegmenter.normalized_sections(runs, interval_meters=SEGMENT_METERS)
        return runs, sections

    def build(self) -> None:
        if self.config.normalized_source_kml:
            segments, sections = self.build_from_normalized_source()
            distance = RouteGeometry.distance_meters(segments)
            self.exporter.write(
                self.config.output_dir,
                segments,
                distance,
                route_sections_override=sections,
                gpx_segments_override=segments,
            )
            if self.config.official_directions_kml:
                official_output = self.config.output_dir / "NSTT_2026_official_directions.kml"
                shutil.copyfile(self.config.official_directions_kml, official_output)
                notes_path = self.config.output_dir / "NSTT_2026_runner_route_README.txt"
                notes_path.write_text(
                    notes_path.read_text(encoding="utf-8")
                    + "\n- NSTT_2026_official_directions.kml: unchanged reference layer containing the manually verified organizer turn checkpoints.\n",
                    encoding="utf-8",
                )
        elif self.config.source_of_truth_kml:
            segments, sections = self.build_from_source_of_truth()
            distance = RouteGeometry.distance_meters(segments)
            self.exporter.write(
                self.config.output_dir,
                segments,
                distance,
                route_sections_override=sections,
                gpx_segments_override=segments,
            )
        else:
            points = self.resolve_points()
            if self.config.approved_segments_kml:
                segments, sections = self.build_from_approved_progress(points)
            else:
                print("Tracing pedestrian routes between organizer checkpoints")
                segments, _ = self.router.trace(points)
                distance = RouteGeometry.distance_meters(segments)
                sections = RouteSegmenter.sections(segments, distance, interval_meters=SEGMENT_METERS, checkpoint_label="Checkpoint")
            distance = RouteGeometry.distance_meters(segments)
            self.exporter.write(self.config.output_dir, segments, distance, route_sections_override=sections, gpx_segments_override=segments)
        print(f"Created {sum(len(segment) for segment in segments)} runner trace points in {len(segments)} runner runs, {distance / 1609.344:.1f} mi, {len(sections)} editable route lines.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    CourseBuilder.add_arguments(parser)
    CourseBuilder.from_arguments(parser.parse_args()).build()


if __name__ == "__main__":
    main()
