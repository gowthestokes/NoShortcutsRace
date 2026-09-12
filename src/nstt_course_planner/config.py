"""Project-wide, non-secret configuration for the course builder."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_GEOCODING_CACHE = PROJECT_ROOT / "data" / "geocoding-cache.json"
DEFAULT_ROUTING_CACHE = PROJECT_ROOT / "data" / "pedestrian-routing-cache.json"
DEFAULT_PATH_CACHE = PROJECT_ROOT / "data" / "path-geometry-cache.json"
DEFAULT_GOOGLE_USAGE = PROJECT_ROOT / "data" / "google-routes-usage.json"
DEFAULT_GOOGLE_ELEVATION_CACHE = PROJECT_ROOT / "data" / "google-elevation-cache.json"
DEFAULT_GOOGLE_ELEVATION_USAGE = PROJECT_ROOT / "data" / "google-elevation-usage.json"
USER_AGENT = "NSTT-course-planner/0.1 (personal relay map)"
GOOGLE_ROUTES_REQUEST_LIMIT = 9_500
GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_ELEVATION_URL = "https://maps.googleapis.com/maps/api/elevation/json"
GOOGLE_ELEVATION_SAMPLE_LIMIT = 4_500
GOOGLE_ELEVATION_BATCH_SIZE = 128
DEFAULT_ELEVATION_SAMPLE_METERS = 50.0
DEFAULT_ELEVATION_SMOOTHING_METERS = 200.0
MILE_METERS = 1609.344
SEGMENT_METERS = MILE_METERS / 2
SEGMENT_NAME_PATTERN = re.compile(r"^Segment\s+(\d{3})([a-z]?)\s+-", re.IGNORECASE)

# Stable route-shaping positions, used when a public text geocoder is ambiguous.
MANUAL_POINTS = {
    "Washington Blvd / West Washington Blvd": (33.988432, -118.451976, "Washington Boulevard / West Washington transition, Venice, CA"),
    "W 78th St / W 79th St": (33.968500, -118.385000, "West 78th Street / West 79th Street transition, Los Angeles, CA"),
    "Centinela Ave / Sepulveda Blvd": (33.977003, -118.386247, "West Centinela Avenue, Culver City, CA"),
    "LA River Trail entry area": (33.903610, -118.184280, "LA River Trail - Rosecrans Avenue connector, Compton, CA"),
    "Firestone Blvd / Atlantic Ave": (33.951901, -118.183128, "Firestone Boulevard & Atlantic Avenue, South Gate, CA"),
    "PCH / river-trail exit area": (33.773900, -118.202400, "Ocean Blue Environmental, 925 W Esther Street, Long Beach, CA"),
    "Oceanside Coast Highway 101": (33.2265, -117.3885, "Coast Highway 101, Oceanside, CA"),
    "Encinitas Coast Highway 101": (33.0484, -117.2940, "South Coast Highway 101, Encinitas, CA"),
    "Solana Beach Coast Highway 101": (32.9900, -117.2700, "North Coast Highway 101, Solana Beach, CA"),
}

HIGHWAY_1_VIA_POINTS = (
    (33.767700, -118.197000), (33.758600, -118.178800),
    (33.744000, -118.105000), (33.701000, -118.055000),
    (33.656000, -118.020000), (33.616000, -117.930000),
    (33.570000, -117.820000), (33.536000, -117.780000),
    (33.505000, -117.735000), (33.476000, -117.710000),
)
