# No Shortcuts Race

Creates a runner-only, import-ready draft of the No Shortcuts Time Trial course
from Santa Monica to San Diego. It creates independently editable half-mile
segments so they can be adjusted or merged in Google My Maps. It
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
- `NSTT_2026_runner_route.gpx` - a portable copy of the runner route for Footpath or other route tools.
- `NSTT_2026_runner_route_README.txt` - concise import and verification notes.

### Bathroom planning layer

Create a separate, non-routing bathroom-stop layer from the final runner KML:

```bash
uv run build-nstt-bathroom-layer
```

It writes `outputs/NSTT_2026_bathroom_stops.kml`. Import it into a third My
Maps layer after the runner segments and official directions. It never rewrites
`input/Runner.kml`; it only calculates each stop's approximate straight-line
proximity to the runner geometry. Stops are ranked in the team's requested
order: official public beach facilities, grocery stores, coffee chains, then
fast-food or branded-gas backups. Confirm operating hours, closures, safe
access, and business restroom policies immediately before the race.

The command also reports runner-mile intervals above three miles without a
researched nearby stop. Treat those as explicit gaps for field verification.

Import `NSTT_2026_runner_route_segments.kml` into a My Maps layer. The route
lines alternate green and blue and can be deleted or redrawn independently.
Each complete line is 0.5 mi according to the source geometry. The route layer
contains no support-car routes or markers. When generated, import
`NSTT_2026_official_directions.kml` into a second layer as the verified
directions reference.

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
remaining runner route lines, run:

```bash
uv run build-nstt-course \
  --approved-segments-kml "input/Runner Segments.kml" \
  --approved-through-segment 42 \
  --resume-at-checkpoint "Del Prado / Golden Lantern"
```

The approved route geometry remains exactly as drawn. If My Maps merges an
edited line during export, the builder restores half-mile sections
along that approved geometry. It then starts at the end of the last approved
segment, routes to the resume checkpoint, and regenerates all downstream
green/blue segments. For a PCH diversion after Segment
042, `Del Prado / Golden Lantern` is the intended resume checkpoint.

### Preserve the manual route on both sides of the I-5 transfer

When the exported KML itself is the source of truth, preserve its runner lines
through Segment 161 and again from the Chevron restart onward with:

```bash
uv run build-nstt-course \
  --source-of-truth-kml "input/Runner.kml" \
  --source-prefix-through-segment 161 \
  --source-prefix-end-marker "San Mateo Point"
```

This leaves every source line geometrically unchanged. It adds only
`Segment 161b`, a walking connector from the end of Segment 161 to the
user-placed San Mateo Point marker. The gap from San Mateo to the existing
Chevron restart is intentionally left blank because runners ride in the
support car on I-5.

### Normalize an updated manual route

To retain every coordinate from a corrected runner-route KML while recutting
it into consecutive half-mile lines, use:

```bash
uv run build-nstt-course \
  --normalize-source-kml "input/Runner.kml" \
  --official-directions-kml "input/Official Directions.kml"
```

This makes no routing requests. It keeps the San Mateo-to-Chevron support-car
gap and writes `outputs/NSTT_2026_official_directions.kml` as an unchanged
reference layer for the verified turn checkpoints. The final line of each
continuous runner run may be shorter than half a mile.

### Create the elevation overlay

The elevation command reads `input/Runner.kml` without editing it and writes a
separate grade-colored overlay. First inspect the exact Google Elevation sample
count without sending a request:

```bash
uv run build-nstt-elevation --dry-run
```

Then, after enabling the Google Elevation API for the existing key, create the
layer:

```bash
uv run build-nstt-elevation
```

It samples the route about every 50 m, batches at most 128 locations per
request, smooths terrain elevations over 200 m, and colors each existing
editable runner segment by signed average grade. The separate local files
`data/google-elevation-cache.json` and `data/google-elevation-usage.json`
reuse samples and stop new sampling before 4,500 locations. Import
`outputs/NSTT_2026_elevation.kml` as a layer above the runner route. Its legend
and segment pop-ups report the smoothed elevation and grade; bridge decks,
ramps, and tunnels still need field verification.

## Important

The organizer's turn list is the source of truth. The builder geocodes its
major turns and traces public pedestrian-routing data between them. The resulting
half-mile segment boundaries are convenience cuts, not verified relay handoffs.
Its output is a planning draft, not a safety or navigation authority. In particular,
validate the LA River Trail and coastal portions with the race organizer before
the event.

For the ambiguous LA River/PCH transition, the builder uses the organizer's
named landmark—Ocean Blue Environmental at 925 W Esther Street, Long Beach—as
the trail-exit checkpoint. It asks for pedestrian routing that avoids ferries
and rejects any returned segment that contains one; this prevents an export
that "swims" across harbor water.

The Ocean Blue → Del Prado line is deliberately pinned to the organizer's
Highway 1/PCH corridor. At San Mateo Point, the support car picks up the runner,
drives I-5 to Exit 54C, and drops the runner at the Oceanside Chevron. The
export has an intentional runner-route gap for that transfer: it never puts a
runner on I-5. The runner resumes Coast Highway from Chevron.
