"""Small infrastructure services: HTTP, environment loading, and polylines."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from nstt_course_planner.config import PROJECT_ROOT, USER_AGENT


class HttpClient:
    """HTTP operations used by the public geocoding and routing services."""

    @staticmethod
    def get_json(url: str) -> object:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            return json.load(response)

    @staticmethod
    def post_json(
        url: str,
        payload: object,
        headers: dict[str, str] | None = None,
    ) -> object:
        request_headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            **(headers or {}),
        }
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            return json.load(response)

    @staticmethod
    def post_form(url: str, values: dict[str, str]) -> object:
        request = Request(
            url,
            data=urlencode(values).encode("utf-8"),
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            return json.load(response)


class Environment:
    """Reads the local, untracked Google key without adding dependencies."""

    @staticmethod
    def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
        if not path.exists():
            return
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", maxsplit=1)
                os.environ.setdefault(key.strip(), value.strip())

    @classmethod
    def google_maps_api_key(cls) -> str:
        cls.load_dotenv()
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "Missing GOOGLE_MAPS_API_KEY. Add it to the local .env file before building.",
            )
        return api_key


class PolylineCodec:
    """Decodes Google and Valhalla route polylines."""

    @staticmethod
    def decode(encoded: str, precision: int) -> list[tuple[float, float]]:
        latitude = longitude = index = 0
        decoded: list[tuple[float, float]] = []
        while index < len(encoded):
            deltas: list[int] = []
            for _ in range(2):
                shift = value = 0
                while True:
                    byte = ord(encoded[index]) - 63
                    index += 1
                    value |= (byte & 0x1F) << shift
                    shift += 5
                    if byte < 0x20:
                        break
                deltas.append(~(value >> 1) if value & 1 else value >> 1)
            latitude += deltas[0]
            longitude += deltas[1]
            decoded.append((latitude / 10**precision, longitude / 10**precision))
        return decoded
