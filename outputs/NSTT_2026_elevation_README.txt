NSTT 2026 elevation overlay

Files
- NSTT_2026_elevation.kml: import as a separate Google My Maps layer above the runner segments.

Legend
- Dark blue: ≤ -6%; blue: -6% to -3%; light blue: -3% to -1%; gray: -1% to +1%; light orange: +1% to +3%; orange: +3% to +6%; red: ≥ +6%.

Method
- Samples the immutable Runner.kml geometry about every 50 m using Google Elevation terrain data.
- Smooths elevations over a configurable 150–250 m window (default 200 m), then colors each existing editable runner segment by signed average grade.
- Each segment pop-up lists smoothed start/end elevation, net elevation change, and average grade.

Caveat
- Google reports terrain elevation; verify bridge decks, ramps, tunnels, and other grade-separated sections in person.
