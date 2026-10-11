"""Location + weather lookup for a coordinate (and optional timestamp).

The two providers are independent, so they are queried in parallel; total latency is the
slower of the two rather than their sum.
"""
from __future__ import annotations

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from app.core.exceptions import AppError, BadGatewayError
from app.core.utils import coarse_coords
from app.integrations.geocoding import GeocodingProvider, Location
from app.integrations.weather import WeatherProvider, WeatherReading

logger = logging.getLogger(__name__)


@dataclass
class ContextLookup:
    location: Optional[Location] = None
    weather: Optional[WeatherReading] = None
    errors: dict[str, Exception] = field(default_factory=dict)  # keys: "weather", "location"

    def first_error(self) -> Exception:
        # Weather is the primary signal, so report its failure first.
        for key in ("weather", "location"):
            if key in self.errors:
                return self.errors[key]
        return BadGatewayError("Location and weather lookup failed.")


class ContextService:
    def __init__(self, geocoder: GeocodingProvider, weather: WeatherProvider) -> None:
        self.geocoder = geocoder
        self.weather = weather

    def lookup(self, latitude: float, longitude: float, at: Optional[datetime] = None) -> ContextLookup:
        """Lenient: never raises for provider trouble; failures are returned in `errors`."""
        started = time.perf_counter()
        result = ContextLookup()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="context") as pool:
            geo_future = self._submit(pool, self.geocoder.reverse, latitude, longitude)
            wx_future = self._submit(pool, self.weather.fetch, latitude, longitude, at)
            result.location, geo_error = geo_future.result()
            result.weather, wx_error = wx_future.result()
        if geo_error:
            result.errors["location"] = geo_error
        if wx_error:
            result.errors["weather"] = wx_error
        logger.info(
            "context lookup near %s: location=%s weather=%s (%.0f ms)",
            coarse_coords(latitude, longitude),
            "ok" if result.location else f"failed({getattr(geo_error, 'code', type(geo_error).__name__)})",
            "ok" if result.weather else f"failed({getattr(wx_error, 'code', type(wx_error).__name__)})",
            (time.perf_counter() - started) * 1000,
        )
        return result

    def preview(
        self, latitude: float, longitude: float, at: Optional[datetime] = None
    ) -> tuple[Location, WeatherReading]:
        """Preview lookup for the review screen.

        Weather failure → raises (caller gets 502 — nothing useful to show).
        Geocoding failure → returns a null-field Location fallback (weather is
        still shown; the place name is cosmetic and not worth blocking on).
        """
        result = self.lookup(latitude, longitude, at)
        if "weather" in result.errors:
            raise result.errors["weather"]
        assert result.weather is not None
        # Geocoding failed — return a Location with all-null fields rather than
        # propagating the error, since the place name is non-essential for preview.
        location = result.location if result.location is not None else Location(
            place_name=None, region=None, country=None
        )
        return location, result.weather

    @staticmethod
    def _submit(pool: ThreadPoolExecutor, fn: Callable[..., Any], *args: Any):
        def safe() -> tuple[Any, Optional[Exception]]:
            try:
                return fn(*args), None
            except AppError as exc:
                return None, exc
            except Exception as exc:  # provider bug: log the traceback, keep the request alive
                logger.exception("Unexpected error in context provider")
                return None, exc

        # Copy per task so request-id logging context follows into the worker thread.
        return pool.submit(contextvars.copy_context().run, safe)
