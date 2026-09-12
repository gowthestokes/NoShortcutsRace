"""Organizer-supplied route data owned by :class:`RaceRouteSpec`.

This module is deliberately independent of a routing provider.  It captures the
turn sheet supplied by the team on 9 September 2026, and is the source of truth
that the generated map must be checked against.  ``ROUTE_CHECKPOINTS`` are the
geocodable route-shaping points used by the builder; ``RUNNER_INSTRUCTIONS``
retains every direction, including road-name changes that are not turns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteCheckpoint:
    """A named location used to shape the runner route."""

    label: str
    query: str


@dataclass(frozen=True)
class Instruction:
    """A direction or operational instruction from the organizer turn sheet."""

    role: str  # "runner" or "car"
    action: str
    road_or_place: str
    checkpoint_label: str | None = None


# Ordered, including name changes and non-turn instructions.  Spelling is
# normalized only where the intended street name is unambiguous (e.g. La Jolla).
RUNNER_INSTRUCTIONS = (
    Instruction("runner", "start", "Santa Monica Pier", "Start - Santa Monica Pier"),
    Instruction("runner", "right", "Ocean Ave", "Ocean Ave / Pico Blvd"),
    Instruction("runner", "left", "Pico Blvd", "Pico Blvd / Main St"),
    Instruction("runner", "right", "Main St", "Main St / Abbot Kinney Blvd"),
    Instruction("runner", "left", "Abbot Kinney Blvd", "Abbot Kinney / Washington Blvd"),
    Instruction("runner", "left", "Washington Blvd", "Washington Blvd / West Washington Blvd"),
    Instruction("runner", "fork right", "West Washington Blvd", "West Washington Blvd / Inglewood Blvd"),
    Instruction("runner", "right", "Inglewood Blvd", "Inglewood Blvd / Culver Blvd"),
    Instruction("runner", "left", "Culver Blvd", "Culver Blvd / Mesmer Ave"),
    Instruction("runner", "right", "Mesmer Ave", "Mesmer Ave / Centinela Ave"),
    Instruction("runner", "left", "Centinela Ave", "Centinela Ave / Sepulveda Blvd"),
    Instruction("runner", "right", "Sepulveda Blvd", "Sepulveda Blvd / W 78th St"),
    Instruction("runner", "left", "W 78th St", "W 78th St / W 79th St"),
    Instruction("runner", "becomes", "W 79th St", "W 79th St / Isis Ave"),
    Instruction("runner", "right", "Isis Ave", "Isis Ave / W 83rd St"),
    Instruction("runner", "left", "W 83rd St", "W 83rd St / La Cienega Blvd"),
    Instruction("runner", "right", "La Cienega Blvd", "La Cienega Blvd / Manchester Ave"),
    Instruction("runner", "left", "Manchester Ave", "Manchester Ave / Firestone Blvd"),
    Instruction("runner", "becomes", "Firestone Blvd", "Firestone Blvd / Atlantic Ave"),
    Instruction("runner", "right", "Atlantic Ave", "Atlantic Ave / Rosecrans Ave"),
    Instruction("runner", "left", "Rosecrans Ave", "LA River Trail entry area"),
    Instruction("runner", "right onto", "LA River Trail", "LA River Trail entry area"),
    Instruction("runner", "follow south until passing under", "PCH 1", "PCH / river-trail exit area"),
    Instruction("runner", "meet car at bike-path exit immediately after", "PCH 1 (Ocean Blue Environmental)", "PCH / river-trail exit area"),
    Instruction("runner", "pick up", "PCH", "PCH / river-trail exit area"),
    Instruction("runner", "right", "Del Prado", "Del Prado / Golden Lantern"),
    Instruction("runner", "right", "Golden Lantern", "Golden Lantern / Dana Point Highway Rd"),
    Instruction("runner", "left", "Dana Point Highway Rd", "Dana Point Highway Rd / Park Lantern"),
    Instruction("runner", "right", "Park Lantern", "Park Lantern / Coast Highway"),
    Instruction("runner", "continue; road becomes", "El Camino Real", "El Camino Real / Avenida Valencia"),
    Instruction("runner", "right", "Avenida Valencia", "Avenida Valencia / Avenida del Presidente"),
    Instruction("runner", "left", "Avenida del Presidente", "San Mateo Point"),
    Instruction("runner", "team photo", "San Mateo Point", "San Mateo Point"),
    Instruction("runner", "resume Coast Highway from", "Chevron after I-5 exit 54C", "Chevron - I-5 exit 54C runner restart"),
    Instruction("runner", "follow", "Coast Highway", "Chevron - I-5 exit 54C runner restart"),
    Instruction("runner", "follow; road becomes", "Carlsbad Blvd", "Coast Highway / Carlsbad Blvd"),
    Instruction("runner", "follow; road becomes", "Coast Highway 101", "Encinitas Coast Highway 101"),
    Instruction("runner", "right", "Camino del Mar", "Camino del Mar / Torrey Pines Rd"),
    Instruction("runner", "becomes", "Torrey Pines Rd", "Camino del Mar / Torrey Pines Rd"),
    Instruction("runner", "right", "Torrey Pines Rd", "Torrey Pines Rd / La Jolla Shores Dr"),
    Instruction("runner", "right", "La Jolla Shores Dr", "La Jolla Shores Dr / Torrey Pines Rd"),
    Instruction("runner", "right", "Torrey Pines Rd", "La Jolla Shores Dr / Torrey Pines Rd"),
    Instruction("runner", "left", "Girard Ave", "Torrey Pines Rd / Girard Ave"),
    Instruction("runner", "right", "Pearl St", "Girard Ave / Pearl St"),
    Instruction("runner", "left", "Fay Ave", "Pearl St / Fay Ave"),
    Instruction("runner", "right", "Nautilus St", "Fay Ave / Nautilus St"),
    Instruction("runner", "left", "La Jolla Blvd", "Nautilus St / La Jolla Blvd"),
    Instruction("runner", "right", "Mission Blvd", "La Jolla Blvd / Mission Blvd"),
    Instruction("runner", "left", "Garnet Ave", "Mission Blvd / Garnet Ave"),
    Instruction("runner", "finish", "Milestone Running Shop", "Finish - Milestone Running Shop"),
)


CAR_INSTRUCTIONS = (
    Instruction("car", "during river trail, drive ahead and meet runners where trail meets road", "LA River Trail", "LA River Trail support access"),
    Instruction("car", "send replacement runner onto trail and collect outgoing runner", "LA River Trail", "LA River Trail support access"),
    Instruction("car", "fork right to avoid I-5 / Coast Highway conflict", "after Park Lantern", "Coast Highway car rendezvous"),
    Instruction("car", "meet runners on Coast Highway", "coastal section", "Coast Highway car rendezvous"),
    Instruction("car", "pick up runner", "San Mateo Point before the I-5 transfer", "I-5 runner pickup - San Mateo Point"),
    Instruction("car", "drive I-5", "from San Mateo Point to exit 54C", "I-5 runner drop-off - Chevron exit 54C"),
    Instruction("car", "cross road after exit, pull into Chevron, and drop runner", "Chevron gas station", "I-5 runner drop-off - Chevron exit 54C"),
)


# Preserve safety and handoff constraints even though they do not describe a
# single mappable turn.  These will become popup notes on the runner/car layers
# and constraints on the later pod-segmentation step.
OPERATIONAL_NOTES = (
    "Runner must always carry a phone during the LA River Trail section.",
    "Use slightly longer relay segments on the LA River Trail; the car drives ahead to road-accessible trail exits.",
    "The coast section narrows for runners and has less car support; use longer segments there.",
    "Use the coastal bike path where appropriate; the car may drop and collect runners using the same leapfrog pattern as the river trail.",
    "Use caution at the PCH roundabout.",
)


# These checkpoints correspond to the explicit runner directions above.  A
# checkpoint can serve adjacent instructions at a shared intersection.
ROUTE_CHECKPOINTS = (
    RouteCheckpoint("Start - Santa Monica Pier", "200 Santa Monica Pier, Santa Monica, CA 90401"),
    RouteCheckpoint("Ocean Ave / Pico Blvd", "Ocean Avenue & Pico Boulevard, Santa Monica, CA"),
    RouteCheckpoint("Pico Blvd / Main St", "Pico Boulevard & Main Street, Santa Monica, CA"),
    RouteCheckpoint("Main St / Abbot Kinney Blvd", "Main Street & Abbot Kinney Boulevard, Venice, CA"),
    RouteCheckpoint("Abbot Kinney / Washington Blvd", "Abbot Kinney Boulevard & Washington Boulevard, Venice, CA"),
    RouteCheckpoint("Washington Blvd / West Washington Blvd", "West Washington Boulevard, Los Angeles, CA"),
    RouteCheckpoint("West Washington Blvd / Inglewood Blvd", "Washington Boulevard & Inglewood Boulevard, Los Angeles, CA"),
    RouteCheckpoint("Inglewood Blvd / Culver Blvd", "Inglewood Boulevard & Culver Boulevard, Los Angeles, CA"),
    RouteCheckpoint("Culver Blvd / Mesmer Ave", "Culver Boulevard & Mesmer Avenue, Los Angeles, CA"),
    RouteCheckpoint("Mesmer Ave / Centinela Ave", "Mesmer Avenue & Centinela Avenue, Los Angeles, CA"),
    RouteCheckpoint("Centinela Ave / Sepulveda Blvd", "Centinela Avenue & Sepulveda Boulevard, Culver City, CA 90230"),
    RouteCheckpoint("Sepulveda Blvd / W 78th St", "Sepulveda Boulevard & West 78th Street, Los Angeles, CA"),
    RouteCheckpoint("W 78th St / W 79th St", "West 79th Street, Los Angeles, CA"),
    RouteCheckpoint("W 79th St / Isis Ave", "West 79th Street & Isis Avenue, Los Angeles, CA"),
    RouteCheckpoint("Isis Ave / W 83rd St", "Isis Avenue & West 83rd Street, Los Angeles, CA"),
    RouteCheckpoint("W 83rd St / La Cienega Blvd", "West 83rd Street & La Cienega Boulevard, Los Angeles, CA"),
    RouteCheckpoint("La Cienega Blvd / Manchester Ave", "La Cienega Boulevard & Manchester Avenue, Los Angeles, CA"),
    RouteCheckpoint("Manchester Ave / Firestone Blvd", "Manchester Avenue & Firestone Boulevard, Los Angeles, CA"),
    RouteCheckpoint("Firestone Blvd / Atlantic Ave", "Firestone Boulevard & Atlantic Avenue, South Gate, CA"),
    RouteCheckpoint("Atlantic Ave / Rosecrans Ave", "Atlantic Avenue & Rosecrans Avenue, Compton, CA"),
    RouteCheckpoint("LA River Trail entry area", "LA River Trail - Rosecrans Avenue connector, Compton, CA"),
    # The organizer's landmark is Ocean Blue Environmental, 925 W Esther St.
    # This is immediately south of PCH in Long Beach; the former Seal Beach
    # interpretation crossed harbor water and was therefore not runnable.
    RouteCheckpoint("PCH / river-trail exit area", "925 W Esther Street, Long Beach, CA 90813"),
    RouteCheckpoint("Del Prado / Golden Lantern", "Del Prado Avenue & Golden Lantern, Dana Point, CA"),
    RouteCheckpoint("Golden Lantern / Dana Point Highway Rd", "Golden Lantern, Dana Point, CA"),
    RouteCheckpoint("Dana Point Highway Rd / Park Lantern", "Park Lantern, Dana Point, CA"),
    RouteCheckpoint("Park Lantern / Coast Highway", "Park Lantern, Dana Point, CA"),
    RouteCheckpoint("El Camino Real / Avenida Valencia", "El Camino Real & Avenida Valencia, San Clemente, CA"),
    RouteCheckpoint("Avenida Valencia / Avenida del Presidente", "Avenida Valencia & Avenida del Presidente, San Clemente, CA"),
    RouteCheckpoint("San Mateo Point", "San Mateo Point, San Clemente, CA"),
    # Exit 54C is Oceanside Harbor Drive; the organizer's Chevron is 1601 N
    # Coast Hwy, Oceanside—not a similarly named Chevron in San Clemente.
    RouteCheckpoint("Chevron - I-5 exit 54C runner restart", "Chevron, 1601 N Coast Hwy, Oceanside, CA 92054"),
    RouteCheckpoint("Coast Highway / Carlsbad Blvd", "Carlsbad Boulevard & Coast Highway, Carlsbad, CA"),
    RouteCheckpoint("Encinitas Coast Highway 101", "South Coast Highway 101, Encinitas, CA"),
    RouteCheckpoint("Camino del Mar / Torrey Pines Rd", "Camino del Mar & Torrey Pines Road, Del Mar, CA"),
    RouteCheckpoint("Torrey Pines Rd / La Jolla Shores Dr", "Torrey Pines Road & La Jolla Shores Drive, La Jolla, CA"),
    RouteCheckpoint("La Jolla Shores Dr / Torrey Pines Rd", "La Jolla Shores Drive & Torrey Pines Road, La Jolla, CA"),
    RouteCheckpoint("Torrey Pines Rd / Girard Ave", "Torrey Pines Road & Girard Avenue, La Jolla, CA"),
    RouteCheckpoint("Girard Ave / Pearl St", "Girard Avenue & Pearl Street, La Jolla, CA"),
    RouteCheckpoint("Pearl St / Fay Ave", "Pearl Street & Fay Avenue, La Jolla, CA"),
    RouteCheckpoint("Fay Ave / Nautilus St", "Fay Avenue & Nautilus Street, La Jolla, CA"),
    RouteCheckpoint("Nautilus St / La Jolla Blvd", "Nautilus Street & La Jolla Boulevard, La Jolla, CA"),
    RouteCheckpoint("La Jolla Blvd / Mission Blvd", "La Jolla Boulevard & Mission Boulevard, San Diego, CA"),
    RouteCheckpoint("Mission Blvd / Garnet Ave", "Mission Boulevard & Garnet Avenue, San Diego, CA"),
    RouteCheckpoint("Finish - Milestone Running Shop", "1892 Garnet Avenue, San Diego, CA 92109"),
)


# A separate layer source for the support vehicle. The I-5 transfer is explicit:
# the runner is picked up at San Mateo Point, transported by car, and dropped
# at the Chevron after exit 54C. It is never a runner-route leg.
CAR_CHECKPOINTS = (
    RouteCheckpoint("LA River Trail support access", "Los Angeles River Bike Path, Long Beach, CA"),
    RouteCheckpoint("Coast Highway car rendezvous", "Pacific Coast Highway, Dana Point, CA"),
    RouteCheckpoint("I-5 runner pickup - San Mateo Point", "San Mateo Point, San Clemente, CA"),
    RouteCheckpoint("I-5 runner drop-off - Chevron exit 54C", "Chevron, 1601 N Coast Hwy, Oceanside, CA 92054"),
)


@dataclass(frozen=True)
class RaceRouteSpec:
    """Immutable organizer route sheet consumed by the course builder."""

    runner_instructions: tuple[Instruction, ...]
    route_checkpoints: tuple[RouteCheckpoint, ...]
    operational_notes: tuple[str, ...]

    def checkpoint(self, label: str) -> RouteCheckpoint:
        """Return a named organizer checkpoint or raise a useful error."""
        try:
            return next(point for point in self.route_checkpoints if point.label == label)
        except StopIteration as error:
            raise KeyError(f"Unknown organizer checkpoint: {label}") from error


ROUTE_SPEC = RaceRouteSpec(
    runner_instructions=RUNNER_INSTRUCTIONS,
    route_checkpoints=ROUTE_CHECKPOINTS,
    operational_notes=OPERATIONAL_NOTES,
)
