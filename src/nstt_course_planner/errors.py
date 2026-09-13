"""Domain-specific errors raised before unsafe or billable work occurs."""


class UnsafePedestrianRouteError(RuntimeError):
    """Raised when routing proposes a non-running transport mode."""


class GoogleRoutesRequestLimitError(RuntimeError):
    """Raised before the planner exceeds its conservative Google request cap."""


class GoogleElevationSampleLimitError(RuntimeError):
    """Raised before elevation sampling exceeds the local conservative cap."""


class GooglePlacesRequestLimitError(RuntimeError):
    """Raised before bathroom discovery exceeds its local Google Places cap."""
