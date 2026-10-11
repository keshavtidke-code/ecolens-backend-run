"""Reverse geocoding via OpenStreetMap Nominatim (free, no API key).

Nominatim's usage policy: identifying User-Agent, max 1 request/second, cache results.
All three are handled here.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import httpx

from app.core.exceptions import BadGatewayError
from app.core.utils import coarse_coords
from app.integrations.cache import TTLCache
from app.integrations.http import get_json

logger = logging.getLogger(__name__)

# Preference order for the human-friendly "place name" (city first, then smaller localities).
PLACE_KEYS = (
    "city", "town", "village", "municipality", "suburb", "hamlet",
    "city_district", "neighbourhood", "county", "state_district",
)
REGION_KEYS = ("state", "region", "province", "state_district")


@dataclass(frozen=True)
class Location:
    place_name: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None

    def as_dict(self) -> dict[str, Optional[str]]:
        return {"place_name": self.place_name, "region": self.region, "country": self.country}


class GeocodingProvider(Protocol):
    def reverse(self, latitude: float, longitude: float) -> Location: ...


def _first(address: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = address.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_nominatim(data: Any) -> Location:
    if not isinstance(data, dict):
        raise BadGatewayError("The geocoding service returned an invalid response.", code="geocoding_provider_error")
    if "error" in data:  # e.g. {"error": "Unable to geocode"} for open ocean: not a failure
        return Location()
    address = data.get("address")
    address = address if isinstance(address, dict) else {}
    name = data.get("name") if isinstance(data.get("name"), str) and data.get("name") else None
    return Location(
        place_name=_first(address, PLACE_KEYS) or name,
        region=_first(address, REGION_KEYS),
        country=_first(address, ("country",)),
    )


class _Throttle:
    """Serialises calls so they are at least `min_interval_s` apart."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval_s <= 0:
            return
        with self._lock:
            delay = self._last + self.min_interval_s - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


class NominatimGeocodingProvider:
    name = "nominatim"

    def __init__(
        self,
        *,
        url: str,
        user_agent: str,
        timeout_s: float,
        min_interval_s: float = 1.0,
        cache_ttl_s: float = 86400,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._url = url
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=min(3.0, timeout_s)),
            headers={"User-Agent": user_agent, "Accept-Language": "en"},
        )
        self._throttle = _Throttle(min_interval_s)
        self._cache = TTLCache(cache_ttl_s, maxsize=2048)

    def reverse(self, latitude: float, longitude: float) -> Location:
        key = (round(latitude, 3), round(longitude, 3))  # ~110 m buckets
        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("geocoding cache hit near %s", coarse_coords(latitude, longitude))
            return cached

        self._throttle.wait()
        started = time.perf_counter()
        data = get_json(
            self._client,
            self._url,
            params={
                "format": "jsonv2",
                "lat": latitude,
                "lon": longitude,
                "zoom": 14,
                "addressdetails": 1,
                "accept-language": "en",
            },
            kind="geocoding",
        )
        location = parse_nominatim(data)
        logger.info(
            "geocoding ok near %s -> %s (%.0f ms)",
            coarse_coords(latitude, longitude),
            location.place_name or "unresolved",
            (time.perf_counter() - started) * 1000,
        )
        self._cache.set(key, location)
        return location

    def close(self) -> None:
        self._client.close()
