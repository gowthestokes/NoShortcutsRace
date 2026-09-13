"""JSON-backed local cache and Google Routes usage services."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from nstt_course_planner.config import (
    GOOGLE_ELEVATION_SAMPLE_LIMIT,
    GOOGLE_PLACES_REQUEST_LIMIT,
    GOOGLE_ROUTES_REQUEST_LIMIT,
)
from nstt_course_planner.errors import (
    GoogleElevationSampleLimitError,
    GooglePlacesRequestLimitError,
    GoogleRoutesRequestLimitError,
)


class JsonStore:
    """Persists small JSON dictionaries used as local caches."""

    @staticmethod
    def load_cache(path: Path) -> dict[str, dict[str, object]]:
        if not path.exists():
            return {}
        contents = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(contents, dict):
            raise TypeError(f"Invalid geocoding cache: {path}")
        return contents

    @staticmethod
    def save_cache(path: Path, cache: dict[str, dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class GoogleUsageTracker:
    """Conservatively reserves and records billable Google Routes calls."""

    request_limit = GOOGLE_ROUTES_REQUEST_LIMIT
    service_name = "Google Routes"
    limit_error = GoogleRoutesRequestLimitError

    @classmethod
    def default_usage(cls) -> dict[str, object]:
        return {
            "request_limit": cls.request_limit,
            "requests_sent": 0,
            "last_request_at": None,
            "last_request_status": None,
        }

    @classmethod
    def load(cls, path: Path) -> dict[str, object]:
        if not path.exists():
            return cls.default_usage()
        usage = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(usage, dict) or not isinstance(
            usage.get("requests_sent"),
            int,
        ):
            raise TypeError(f"Invalid Google Routes usage file: {path}")
        usage["request_limit"] = cls.request_limit
        return usage

    @staticmethod
    def save(path: Path, usage: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(usage, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def reserve(cls, usage: dict[str, object], path: Path) -> None:
        requests_sent = int(usage["requests_sent"])
        if requests_sent >= cls.request_limit:
            raise cls.limit_error(
                f"{cls.service_name} request limit reached ({cls.request_limit:,}); no request was sent.",
            )
        usage["requests_sent"] = requests_sent + 1
        usage["last_request_at"] = datetime.now(UTC).isoformat()
        usage["last_request_status"] = "reserved"
        cls.save(path, usage)

    @classmethod
    def set_status(cls, usage: dict[str, object], path: Path, status: str) -> None:
        usage["last_request_status"] = status
        cls.save(path, usage)


class GooglePlacesUsageTracker(GoogleUsageTracker):
    """Records Places searches separately from route-building calls."""

    request_limit = GOOGLE_PLACES_REQUEST_LIMIT
    service_name = "Google Places"
    limit_error = GooglePlacesRequestLimitError


class GoogleElevationUsageTracker:
    """Conservatively reserves billable Google Elevation samples."""

    @staticmethod
    def default_usage() -> dict[str, object]:
        return {
            "sample_limit": GOOGLE_ELEVATION_SAMPLE_LIMIT,
            "samples_sent": 0,
            "requests_sent": 0,
            "last_request_at": None,
            "last_request_status": None,
        }

    @classmethod
    def load(cls, path: Path) -> dict[str, object]:
        if not path.exists():
            return cls.default_usage()
        usage = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(usage, dict) or not isinstance(
            usage.get("samples_sent"),
            int,
        ):
            raise TypeError(f"Invalid Google Elevation usage file: {path}")
        usage["sample_limit"] = GOOGLE_ELEVATION_SAMPLE_LIMIT
        usage.setdefault("requests_sent", 0)
        return usage

    @staticmethod
    def save(path: Path, usage: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(usage, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def reserve(cls, usage: dict[str, object], path: Path, sample_count: int) -> None:
        if sample_count <= 0:
            return
        cls.ensure_capacity(usage, sample_count)
        samples_sent = int(usage["samples_sent"])
        usage["samples_sent"] = samples_sent + sample_count
        usage["requests_sent"] = int(usage.get("requests_sent", 0)) + 1
        usage["last_request_at"] = datetime.now(UTC).isoformat()
        usage["last_request_status"] = "reserved"
        cls.save(path, usage)

    @staticmethod
    def ensure_capacity(usage: dict[str, object], sample_count: int) -> None:
        if sample_count <= 0:
            return
        if int(usage["samples_sent"]) + sample_count > GOOGLE_ELEVATION_SAMPLE_LIMIT:
            raise GoogleElevationSampleLimitError(
                f"Google Elevation sample limit would be exceeded ({GOOGLE_ELEVATION_SAMPLE_LIMIT:,}); no request was sent.",
            )

    @classmethod
    def set_status(cls, usage: dict[str, object], path: Path, status: str) -> None:
        usage["last_request_status"] = status
        cls.save(path, usage)
