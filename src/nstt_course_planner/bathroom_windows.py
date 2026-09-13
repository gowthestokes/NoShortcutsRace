"""Select fixed-mile rolling bathroom windows from the existing bathroom layer."""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as element_tree
import xml.sax.saxutils
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from nstt_course_planner.bathrooms import BathroomLayerBuilder, RunnerRouteKml
from nstt_course_planner.catalog.bathroom_windows import (
    BATHROOM_WINDOW_INTERVAL_MILES,
    BATHROOM_WINDOW_MAX_OFFSET_MILES,
    CAR_ACCESS_CONSTRAINTS,
)
from nstt_course_planner.config import DEFAULT_OUTPUT_DIR, MILE_METERS, PROJECT_ROOT
from nstt_course_planner.geometry import RouteGeometry
from nstt_course_planner.models.bathrooms import (
    BathroomCategory,
    BathroomStop,
    BathroomWindow,
    CarAccessConstraint,
)
from nstt_course_planner.models.route import Coordinate, RouteProximity


@dataclass(frozen=True)
class BathroomWindowBuildConfig:
    """Inputs for selecting bathroom windows without changing runner geometry."""

    runner_kml: Path
    bathroom_stops_kml: Path
    output_kml: Path
    interval_miles: float = BATHROOM_WINDOW_INTERVAL_MILES
    max_offset_miles: float = BATHROOM_WINDOW_MAX_OFFSET_MILES


class BathroomStopsKml:
    """Reads researched bathroom candidates from the existing My Maps layer."""

    namespace: ClassVar[dict[str, str]] = {"kml": "http://www.opengis.net/kml/2.2"}
    category_by_name: ClassVar[dict[str, BathroomCategory]] = {
        category.display_name: category for category in BathroomCategory
    }

    @classmethod
    def load(cls, path: Path) -> tuple[BathroomStop, ...]:
        try:
            root = element_tree.parse(path).getroot()
        except (OSError, element_tree.ParseError) as error:
            raise RuntimeError(f"Could not read bathroom-stop KML: {path}") from error
        stops = tuple(
            cls._stop(placemark)
            for placemark in root.findall(".//kml:Placemark", cls.namespace)
        )
        if not stops:
            raise ValueError(f"Bathroom-stop KML has no point markers: {path}")
        return stops

    @classmethod
    def _stop(cls, placemark: element_tree.Element) -> BathroomStop:
        data = {
            field.attrib["name"]: field.findtext(
                "kml:value", default="", namespaces=cls.namespace
            )
            for field in placemark.findall(".//kml:Data", cls.namespace)
        }
        try:
            category = cls.category_by_name[data["Category"]]
            longitude, latitude, *_ = map(
                float,
                placemark.findtext(
                    ".//kml:Point/kml:coordinates", namespaces=cls.namespace
                ).split(","),
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "Bathroom-stop KML has an invalid point marker."
            ) from error
        description = placemark.findtext(
            "kml:description", default="", namespaces=cls.namespace
        )
        address = cls._description_value(description, "Address")
        source_url = cls._source_url(description)
        return BathroomStop(
            placemark.findtext(
                "kml:name", default="Bathroom stop", namespaces=cls.namespace
            ),
            category,
            latitude,
            longitude,
            address,
            source_url,
            "See the source bathroom layer for access and hours.",
        )

    @staticmethod
    def _description_value(description: str, label: str) -> str:
        match = re.search(rf"{re.escape(label)}: (.*?)<br/>", description)
        return match.group(1) if match else "See source bathroom layer."

    @staticmethod
    def _source_url(description: str) -> str:
        match = re.search(r'href="([^"]+)"', description)
        return match.group(1) if match else "https://maps.google.com"


class BathroomWindowSelector:
    """Selects one nearby rolling restroom option for each fixed-mile target."""

    def __init__(
        self,
        route_runs: tuple[tuple[Coordinate, ...], ...],
        constraints: tuple[CarAccessConstraint, ...] = CAR_ACCESS_CONSTRAINTS,
    ) -> None:
        self.route_runs = route_runs
        self.constraints = constraints
        self.builder = BathroomLayerBuilder()

    def select(
        self,
        stops: tuple[BathroomStop, ...],
        interval_miles: float,
        max_offset_miles: float,
    ) -> tuple[BathroomWindow, ...]:
        if interval_miles <= 0 or max_offset_miles < 0:
            raise ValueError("Bathroom-window distance settings must be positive.")
        candidates = tuple(
            (
                stop,
                self.builder.nearest_route_proximity(
                    (stop.latitude, stop.longitude),
                    self.route_runs,
                ),
            )
            for stop in stops
        )
        windows: list[BathroomWindow] = []
        target_miles = interval_miles
        while target_miles < self.route_miles:
            windows.append(
                self._window(target_miles, candidates, max_offset_miles),
            )
            target_miles += interval_miles
        return tuple(windows)

    @property
    def route_miles(self) -> float:
        return (
            sum(RouteGeometry.distance_meters([list(run)]) for run in self.route_runs)
            / MILE_METERS
        )

    def _window(
        self,
        target_miles: float,
        candidates: tuple[tuple[BathroomStop, RouteProximity], ...],
        max_offset_miles: float,
    ) -> BathroomWindow:
        constraint = self._constraint(target_miles)
        eligible = [
            (stop, proximity)
            for stop, proximity in candidates
            if abs(proximity.runner_miles - target_miles) <= max_offset_miles
            and not self._constraint(proximity.runner_miles)
        ]
        if not eligible:
            raise RuntimeError(
                f"No bathroom stop is available within {max_offset_miles:.1f} mi of runner-mile target {target_miles:.0f}.",
            )
        stop, proximity = min(
            eligible,
            key=lambda candidate: (
                candidate[0].category.priority,
                abs(candidate[1].runner_miles - target_miles),
                candidate[1].straight_line_meters,
                candidate[0].name,
            ),
        )
        note = (
            f"Moved outside the {constraint.label} support-car constraint."
            if constraint
            else None
        )
        return BathroomWindow(
            target_miles,
            proximity.runner_miles,
            proximity.straight_line_meters,
            stop,
            note,
        )

    def _constraint(self, runner_miles: float) -> CarAccessConstraint | None:
        return next(
            (
                constraint
                for constraint in self.constraints
                if constraint.start_miles <= runner_miles <= constraint.end_miles
            ),
            None,
        )


class BathroomWindowExporter:
    """Writes a small My Maps layer containing only rolling bathroom windows."""

    @classmethod
    def write(cls, path: Path, windows: tuple[BathroomWindow, ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        styles = "".join(
            f'<Style id="{category.style_id}"><IconStyle><color>{cls._color(category)}</color><scale>1.15</scale><Icon><href>http://maps.google.com/mapfiles/kml/paddle/{cls._icon(category)}-circle.png</href></Icon></IconStyle></Style>'
            for category in BathroomCategory
        )
        placemarks = "".join(cls._placemark(window) for window in windows)
        path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NSTT 2026 - 12-Mile Bathroom Windows</name>
  <description>Rolling bathroom opportunities selected every 12 runner miles from the full bathroom-stop layer. They do not change runner geometry or require a group reset. Confirm car access, hours, and restroom policy on race day.</description>
  {styles}<Folder><name>Bathroom windows — every 12 runner miles</name>{placemarks}
  </Folder></Document></kml>
""",
            encoding="utf-8",
        )

    @staticmethod
    def _color(category: BathroomCategory) -> str:
        return (
            "ff00aa00"
            if category.priority < 3
            else "ffff0000"
            if category.priority == 3
            else "ff808080"
        )

    @staticmethod
    def _icon(category: BathroomCategory) -> str:
        return (
            "grn"
            if category.priority < 3
            else "blu"
            if category.priority == 3
            else "wht"
        )

    @staticmethod
    def _placemark(window: BathroomWindow) -> str:
        stop = window.stop
        note = f" {window.constraint_note}" if window.constraint_note else ""
        description = (
            f"Rolling bathroom window target: mile {window.target_miles:.0f}.<br/>"
            f"Selected runner mile: {window.actual_runner_miles:.1f}.<br/>"
            f"{stop.category.display_name}.<br/>Address: {stop.address}<br/>"
            f'Source: <a href="{stop.source_url}">full bathroom layer</a>.{note}'
        )
        return f"""<Placemark><name>{xml.sax.saxutils.escape(f"Bathroom window {window.target_miles:.0f} mi — {stop.name}")}</name>
      <ExtendedData><Data name="Target runner mile"><value>{window.target_miles:.0f}</value></Data>
      <Data name="Selected runner mile"><value>{window.actual_runner_miles:.1f}</value></Data>
      <Data name="Category"><value>{xml.sax.saxutils.escape(stop.category.display_name)}</value></Data></ExtendedData>
      <description><![CDATA[{description}]]></description><styleUrl>#{stop.category.style_id}</styleUrl>
      <Point><coordinates>{stop.longitude},{stop.latitude},0</coordinates></Point></Placemark>"""


class BathroomWindowLayerBuilder:
    """Coordinates a derived bathroom-window layer without API calls."""

    def __init__(self, config: BathroomWindowBuildConfig) -> None:
        self.config = config

    def build(self) -> tuple[BathroomWindow, ...]:
        route_runs = RunnerRouteKml.line_runs(self.config.runner_kml)
        stops = BathroomStopsKml.load(self.config.bathroom_stops_kml)
        windows = BathroomWindowSelector(route_runs).select(
            stops,
            self.config.interval_miles,
            self.config.max_offset_miles,
        )
        BathroomWindowExporter.write(self.config.output_kml, windows)
        return windows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runner-kml", type=Path, default=PROJECT_ROOT / "input" / "Runner.kml"
    )
    parser.add_argument(
        "--bathroom-stops-kml",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "NSTT_2026_bathroom_stops.kml",
    )
    parser.add_argument(
        "--output-kml",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "NSTT_2026_bathroom_windows.kml",
    )
    parser.add_argument(
        "--interval-miles", type=float, default=BATHROOM_WINDOW_INTERVAL_MILES
    )
    parser.add_argument(
        "--max-offset-miles", type=float, default=BATHROOM_WINDOW_MAX_OFFSET_MILES
    )
    arguments = parser.parse_args()
    windows = BathroomWindowLayerBuilder(
        BathroomWindowBuildConfig(
            arguments.runner_kml,
            arguments.bathroom_stops_kml,
            arguments.output_kml,
            arguments.interval_miles,
            arguments.max_offset_miles,
        ),
    ).build()
    print(
        f"Created {arguments.output_kml} with {len(windows)} rolling bathroom windows."
    )
