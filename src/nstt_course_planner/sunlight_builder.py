"""Create a sunlight overlay from conservative team paces and finalized runner segments."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nstt_course_planner.config import DEFAULT_OUTPUT_DIR, PROJECT_ROOT
from nstt_course_planner.models.sunlight import SunlightBuildConfig
from nstt_course_planner.progress import ApprovedProgressLoader
from nstt_course_planner.sunlight import (
    RelaySunlightSimulator,
    SunlightLayerExporter,
    TeamPaceLoader,
)

DEFAULT_RACE_START = datetime(2026, 10, 23, 5, tzinfo=ZoneInfo("America/Los_Angeles"))


class SunlightLayerBuilder:
    """Coordinates the offline sunlight-overlay build."""

    @staticmethod
    def add_arguments(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--source-kml",
            type=Path,
            default=PROJECT_ROOT / "input" / "Runner.kml",
            help="Immutable finalized runner-route KML.",
        )
        parser.add_argument(
            "--team-pace",
            type=Path,
            default=PROJECT_ROOT / "data" / "team-pace.json",
            help="Ordered runner paces in minutes per mile.",
        )
        parser.add_argument(
            "--output-kml",
            type=Path,
            default=DEFAULT_OUTPUT_DIR / "NSTT_2026_sunlight.kml",
            help="Sunlight-overlay KML to create.",
        )
        parser.add_argument(
            "--start-time",
            type=datetime.fromisoformat,
            default=DEFAULT_RACE_START,
            help="Timezone-aware ISO race start; defaults to 2026-10-23T05:00:00-07:00.",
        )
        parser.add_argument(
            "--half-segments-per-turn",
            type=int,
            default=2,
            help="Consecutive half-mile segments assigned to one runner.",
        )
        parser.add_argument(
            "--transfer-minutes",
            type=float,
            help="Confirmed San Mateo-to-Chevron support-car travel time; required to schedule the post-I-5 restart.",
        )

    @classmethod
    def from_arguments(cls, arguments: argparse.Namespace) -> SunlightBuildConfig:
        if arguments.start_time.tzinfo is None:
            raise ValueError("Start time must include a timezone offset.")
        return SunlightBuildConfig(
            arguments.source_kml,
            arguments.team_pace,
            arguments.output_kml,
            arguments.start_time,
            arguments.half_segments_per_turn,
            arguments.transfer_minutes,
        )

    @staticmethod
    def build(config: SunlightBuildConfig) -> int:
        paces = TeamPaceLoader.load(config.pace_path)
        section_runs = ApprovedProgressLoader.source_section_runs(config.source_kml)
        scheduled = RelaySunlightSimulator(config, paces).schedule(section_runs)
        SunlightLayerExporter.write(config.output_path, scheduled)
        return len(scheduled)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    SunlightLayerBuilder.add_arguments(parser)
    config = SunlightLayerBuilder.from_arguments(parser.parse_args())
    segment_count = SunlightLayerBuilder.build(config)
    print(f"Created {config.output_path} with {segment_count} timed runner segments.")


if __name__ == "__main__":
    main()
