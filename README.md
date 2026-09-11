# No Shortcuts Race

Creates a runner-only, import-ready draft of the No Shortcuts Time Trial course
from Santa Monica to San Diego. It creates independently editable half-mile
segments and matching checkpoints so they can be adjusted or merged in Google
My Maps. It
intentionally excludes relay pods, runner assignments, handoffs, and support-car
routing.

## Code structure

`RaceRouteSpec` in `route_spec.py` owns the immutable organizer turn sheet,
checkpoints, and operational notes. `CourseBuilder` in `build_course.py` is the
small CLI/orchestration layer. Its collaborators are focused classes: the
configuration and models in `config.py`/`models.py`, `CheckpointGeocoder`,
`RunnerRouter`, `ApprovedProgressLoader`, `RouteGeometry`/`RouteSegmenter`,
`CourseExporter`, `JsonStore`/`GoogleUsageTracker`, and `HttpClient`.

Each production module is under 200 lines; the compatibility imports in
`build_course.py` preserve the original script-level API while new code uses the
focused service classes.

## Run

This project uses [uv](https://docs.astral.sh/uv/) and Python 3.14.

```bash
uv run build-nstt-course
```

## Google Maps key (for the planned Google walking-router upgrade)

The builder uses Google's walking Routes API. Put the key in the local `.env`
file:

```bash
GOOGLE_MAPS_API_KEY=your_key_here
```

`.env` is ignored by Git; commit `.env.example` instead. Restrict the key in
Google Cloud to the Routes API before using it. `data/google-routes-usage.json`
records requests made by this planner and stops new calls at 9,500, below the
current 10,000 monthly Essentials free usage cap. The counter is conservative:
it counts a request before sending it, including a request that later fails.

By default, it writes these files to `outputs/` inside this project:

- `NSTT_2026_runner_route_segments.kml` - import into the first Google My Maps layer; contains individually editable, alternating green/blue half-mile route segments.
- `NSTT_2026_runner_mile_checkpoints.kml` - import into a second Google My Maps layer; contains the half-mile checkpoints, start, and finish.
- `NSTT_2026_runner_route.gpx` - a portable route and checkpoint backup for Footpath or other route tools.
- `NSTT_2026_runner_route_README.txt` - concise import and verification notes.

Import the two KML files into separate My Maps layers: one for route segments
and one for checkpoints. The route lines alternate green and blue and can be
deleted or redrawn independently. `Checkpoint 001`, `Checkpoint 002`, and later
markers are calculated from the actual KML line geometry, so the distance shown
by My Maps is 0.5 mi for each complete segment. Neither layer contains
support-car routes or markers.

The first build calls public geocoders, Google walking Routes, and named-path map data, so it
takes a few minutes. Resolved coordinates and route geometry are saved in
`data/` (both cache files are ignored by Git), so later builds reuse them and
avoid that wait. Delete either cache file—or pass `--geocoding-cache` or
`--routing-cache` with another path—to refresh or relocate a cache.

To choose another location:

```bash
uv run build-nstt-course --output-dir /path/to/output
```

## Continue from manual edits

Export the edited route-segments layer from Google My Maps as KML and place it
in `input/`. To preserve all segments through a checkpoint and rebuild the
remaining runner route and mile markers, run:

```bash
uv run build-nstt-course \
  --approved-segments-kml "input/Runner Segments.kml" \
  --approved-through-segment 42 \
  --resume-at-checkpoint "Del Prado / Golden Lantern"
```

The approved route geometry remains exactly as drawn. If My Maps merges an
edited line during export, the builder restores half-mile sections and markers
along that approved geometry. It then starts at the end of the last approved
segment, routes to the resume checkpoint, and regenerates all downstream
green/blue segments and mile checkpoints. For a PCH diversion after Segment
042, `Del Prado / Golden Lantern` is the intended resume checkpoint.

## Important

The organizer's turn list is the source of truth. The builder geocodes its
major turns and traces public pedestrian-routing data between them. The resulting
half-mile checkpoints are convenience markers, not verified relay handoffs. Its
output is a planning draft, not a safety or navigation authority. In particular,
validate the LA River Trail and coastal portions with the race organizer before
the event.

For the ambiguous LA River/PCH transition, the builder uses the organizer's
named landmark—Ocean Blue Environmental at 925 W Esther Street, Long Beach—as
the trail-exit checkpoint. It asks for pedestrian routing that avoids ferries
and rejects any returned segment that contains one; this prevents an export
that "swims" across harbor water.

The Ocean Blue → Del Prado line is deliberately pinned to the organizer's
Highway 1/PCH corridor. Per the supplied directions, the transfer from the
Segment 162 endpoint to the Oceanside Chevron follows I-5 to Exit 54C. This
appears in the runner layer solely as a planning reference, not as pedestrian-
safety validation; confirm the exact runnable shoulder, permissions, and event
authorization in the field before race day.
