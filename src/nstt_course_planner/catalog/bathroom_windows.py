"""Fixed-mile bathroom-window policy and known support-car constraints."""

from nstt_course_planner.models.bathrooms import CarAccessConstraint

BATHROOM_WINDOW_INTERVAL_MILES = 12.0
BATHROOM_WINDOW_MAX_OFFSET_MILES = 2.0
CAR_ACCESS_CONSTRAINTS = (
    CarAccessConstraint(25.2, 34.0, "LA River Trail"),
    CarAccessConstraint(71.0, 80.0, "Dana Point coast-highway corridor"),
)
