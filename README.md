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

It writes `outputs/NSTT_2026_bathroom_stops.kml`, a single color-coded layer.
It never rewrites
`input/Runner.kml`; it only calculates each stop's approximate straight-line
proximity to the runner geometry. It combines researched public beach facilities
with cached Google Places results within one mile of the route. The priorities
and colors are: public facilities (green, priority 1), grocery stores (green,
priority 2), coffee shops (blue, priority 3), and fast-food or gas backups
(gray, priority 4). To fit My Maps' 2,000-feature import limit, it preserves
all priorities 1–3 and evenly thins only priority-4 backups. Confirm operating hours, closures, safe access, and any
customer-only policy immediately before the race.

My Maps may replace imported KML icons with its default blue marker. The KML
includes `Category` and `Priority` fields and emoji-prefixed names so you can
choose **Style → Group places by → Category**, then apply My Maps' restroom
emoji to the two public-restroom categories in a few clicks.

Create the smaller team-operations subset at fixed 12-runner-mile intervals:

```bash
uv run build-nstt-bathroom-windows
```

It writes `outputs/NSTT_2026_bathroom_windows.kml` from the already generated
bathroom layer without changing `input/Runner.kml`. The selection prefers the
highest-priority available facility within two miles of each target and moves
the LA River Trail and constrained Dana Point coast-highway targets outside
their support-car access corridors.

All ETA-bearing layers accept the same schedule overrides. For example, a
different team's 42-minute transfer uses:

```bash
uv run --active build-nstt-sunlight-layer --transfer-minutes 42
uv run --active build-nstt-elevation --transfer-minutes 42
uv run --active build-nstt-bathroom-windows --transfer-minutes 42
```

Enable the Places API (New) for the existing Google Maps key before running the
command. The first pass searches every 2.4 km along each continuous runner run,
then writes reusable responses to `data/google-places-bathrooms-cache.json`.
`data/google-places-usage.json` separately records every uncached request and
stops at 1,000 searches. Preview the exact uncached count without contacting
Google with `uv run build-nstt-bathroom-layer --dry-run`.

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
