# No Shortcuts Race

Creates an unsegmented, import-ready draft of the No Shortcuts Time Trial
course from Santa Monica to San Diego. It intentionally excludes relay pods,
runner assignments, handoffs, and support-car routing.

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

- `NSTT_2026_master_runner_course_draft.kml` - import this into Google My Maps.
- `NSTT_2026_runner_route_preview.kml` - runner-only version for map review.
- `NSTT_2026_master_runner_course_draft.gpx` - a portable backup for Footpath or other route tools.
- `NSTT_2026_master_runner_course_README.txt` - concise import and verification notes.

The KML creates two Google My Maps layers: `Runner route - organizer-aligned draft`
and `Support car logistics - provisional`. The car layer contains only the
organizer's explicit access/rendezvous notes; it intentionally does not invent
a continuous vehicle route before those access points are confirmed.

The first build calls public geocoders, Google walking Routes, and named-path map data, so it
takes a few minutes. Resolved coordinates and route geometry are saved in
`data/` (both cache files are ignored by Git), so later builds reuse them and
avoid that wait. Delete either cache file—or pass `--geocoding-cache` or
`--routing-cache` with another path—to refresh or relocate a cache.

To choose another location:

```bash
uv run build-nstt-course --output-dir /path/to/output
```

## Important

The organizer's turn list is the source of truth. The builder geocodes its
major turns and traces public pedestrian-routing data between them. Its output is a planning
draft, not a safety or navigation authority. In particular, validate the LA
River Trail and coastal portions with the race organizer before the event.

For the ambiguous LA River/PCH transition, the builder uses the organizer's
named landmark—Ocean Blue Environmental at 925 W Esther Street, Long Beach—as
the trail-exit checkpoint. It asks for pedestrian routing that avoids ferries
and rejects any returned segment that contains one; this prevents an export
that "swims" across harbor water.

The Ocean Blue → Del Prado line is deliberately pinned to the organizer's
Highway 1/PCH corridor. That road-alignment reference is not a pedestrian-
safety validation; confirm the exact runnable shoulder, sidewalk, or permitted
bike-path alternatives in the field before race day.
