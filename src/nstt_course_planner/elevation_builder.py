"""Create a grade-colored Google My Maps layer from the finalized Runner.kml."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from nstt_course_planner.config import (
    DEFAULT_ELEVATION_SAMPLE_METERS,
    DEFAULT_ELEVATION_SMOOTHING_METERS,
    DEFAULT_GOOGLE_ELEVATION_CACHE,
    DEFAULT_GOOGLE_ELEVATION_USAGE,
    DEFAULT_OUTPUT_DIR,
    PROJECT_ROOT,
)
from nstt_course_planner.elevation import ElevationAnalyzer, ElevationLayerExporter, GoogleElevationClient, RouteGeometrySampler
from nstt_course_planner.progress import ApprovedProgressLoader
from nstt_course_planner.storage import GoogleElevationUsageTracker, JsonStore
from nstt_course_planner.utils import Environment


@dataclass(frozen=True)
class ElevationBuildConfig:
    """Inputs and local state used for one elevation-overlay build."""

    source_kml: Path
    output_dir: Path
    cache_path: Path
    usage_path: Path
    sample_spacing_meters: float
    smoothing_meters: float
    dry_run: bool = False


class ElevationLayerBuilder:
    """Coordinates an opt-in, capped Google Elevation build."""

    def __init__(self, config: ElevationBuildConfig) -> None:
        self.config = config
        self.cache = JsonStore.load_cache(config.cache_path)
        self.usage = GoogleElevationUsageTracker.load(config.usage_path)

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--source-kml", type=Path, default=PROJECT_ROOT / "input" / "Runner.kml", help="Immutable finalized runner-route KML.")
        parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for NSTT_2026_elevation.kml.")
        parser.add_argument("--elevation-cache", type=Path, default=DEFAULT_GOOGLE_ELEVATION_CACHE, help="Local cache of sampled terrain elevations.")
        parser.add_argument("--elevation-usage", type=Path, default=DEFAULT_GOOGLE_ELEVATION_USAGE, help="Local conservative Google Elevation sample counter.")
        parser.add_argument("--sample-spacing-meters", type=float, default=DEFAULT_ELEVATION_SAMPLE_METERS, help="Terrain sample spacing; 50 m is the default.")
        parser.add_argument("--smoothing-meters", type=float, default=DEFAULT_ELEVATION_SMOOTHING_METERS, help="Elevation smoothing window, from 150 to 250 m.")
        parser.add_argument("--dry-run", action="store_true", help="Report uncached sample count and cap status without calling Google or writing outputs.")

    @classmethod
    def from_arguments(cls, args: argparse.Namespace) -> "ElevationLayerBuilder":
        return cls(ElevationBuildConfig(
            args.source_kml,
            args.output_dir,
            args.elevation_cache,
            args.elevation_usage,
            args.sample_spacing_meters,
            args.smoothing_meters,
            args.dry_run,
        ))

    def _section_runs(self):
        runs = ApprovedProgressLoader.source_section_runs(self.config.source_kml)
        if not runs:
            raise RuntimeError(f"Source KML has no editable runner segments: {self.config.source_kml}")
        return runs

    def _sample_coordinates(self, section_runs) -> list[tuple[float, float]]:
        coordinates: list[tuple[float, float]] = []
        for sections in section_runs:
            run_coordinates = ElevationAnalyzer.run_coordinates(sections)
            coordinates.extend(sample.coordinate for sample in RouteGeometrySampler.sample(run_coordinates, self.config.sample_spacing_meters))
        return coordinates

    def build(self) -> None:
        if not self.config.source_kml.exists():
            raise RuntimeError(f"Finalized runner KML was not found: {self.config.source_kml}")
        if self.config.sample_spacing_meters <= 0:
            raise ValueError("Elevation sample spacing must be greater than zero.")
        if not 150 <= self.config.smoothing_meters <= 250:
            raise ValueError("Elevation smoothing window must be between 150 and 250 meters.")
        section_runs = self._section_runs()
        sample_coordinates = self._sample_coordinates(section_runs)
        client = GoogleElevationClient(self.cache, self.config.cache_path, "", self.usage, self.config.usage_path)
        uncached = client.uncached_coordinates(sample_coordinates)
        GoogleElevationUsageTracker.ensure_capacity(self.usage, len(uncached))
        print(
            f"Elevation plan: {len(sample_coordinates):,} samples ({len(uncached):,} uncached); "
            f"{int(self.usage['samples_sent']):,}/{int(self.usage['sample_limit']):,} samples already recorded."
        )
        if self.config.dry_run:
            print("Dry run complete: no Google request was sent and no elevation output was written.")
            return
        client.api_key = Environment.google_maps_api_key()
        elevated_sections = ElevationAnalyzer.sections(
            section_runs,
            client,
            self.config.sample_spacing_meters,
            self.config.smoothing_meters,
        )
        ElevationLayerExporter.write(self.config.output_dir, elevated_sections)
        print(f"Created NSTT_2026_elevation.kml with {len(elevated_sections)} editable grade-colored segments.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    ElevationLayerBuilder.add_arguments(parser)
    ElevationLayerBuilder.from_arguments(parser.parse_args()).build()


if __name__ == "__main__":
    main()
