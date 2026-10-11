"""Weather via Open-Meteo (free, no API key).

- timestamp is "now" (or within the last 2 hours / in the future) -> current conditions
- up to ~85 days old -> hourly values from the forecast API (it keeps recent past days)
- older -> hourly values from the archive (reanalysis) API
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

import httpx

from app.core.exceptions import BadGatewayError, BadRequestError
from app.core.utils import coarse_coords, utcnow
from app.integrations.cache import TTLCache
from app.integrations.http import get_json

logger = logging.getLogger(__name__)

PROVIDER_NAME = "open-meteo"
CURRENT_WINDOW = timedelta(hours=2)
FORECAST_API_PAST_LIMIT = timedelta(days=85)
EARLIEST_SUPPORTED = datetime(1940, 1, 1, tzinfo=timezone.utc)  # start of ERA5 archive

# Open-Meteo variable -> our field name
VARIABLES = {
    "temperature_2m": "temperature_c",
    "apparent_temperature": "feels_like_c",
    "relative_humidity_2m": "humidity_pct",
    "precipitation": "precipitation_mm",
    "wind_speed_10m": "wind_speed_kmh",
    "wind_direction_10m": "wind_direction_deg",
    "cloud_cover": "cloud_cover_pct",
    "weather_code": "weather_code",
    "pressure_msl": "pressure_hpa",
}
MEASUREMENT_FIELDS = tuple(VARIABLES.values())


@dataclass(frozen=True)
class WeatherReading:
    provider: str
    observed_at: datetime
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    precipitation_mm: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    weather_code: Optional[int] = None
    pressure_hpa: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def measurements(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in MEASUREMENT_FIELDS}


class WeatherProvider(Protocol):
    def fetch(self, latitude: float, longitude: float, at: Optional[datetime] = None) -> WeatherReading: ...


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_time(value: Any) -> datetime:
    try:
        return _utc(datetime.fromisoformat(str(value)))  # requested with timezone=UTC
    except ValueError:
        raise BadGatewayError(
            "The weather service returned an invalid response.", code="weather_provider_error"
        ) from None


class OpenMeteoWeatherProvider:
    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        forecast_url: str,
        archive_url: str,
        timeout_s: float,
        cache_ttl_s: float = 600,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self._forecast_url = forecast_url
        self._archive_url = archive_url
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=min(3.0, timeout_s)),
            headers={"User-Agent": "EcoLens/0.1"},
        )
        self._cache = TTLCache(cache_ttl_s, maxsize=512)

    # ---- public ----
    def fetch(self, latitude: float, longitude: float, at: Optional[datetime] = None) -> WeatherReading:
        now = utcnow()
        at = _utc(at) if at else None
        started = time.perf_counter()
        if at is None or at >= now - CURRENT_WINDOW:
            reading = self._current(latitude, longitude)
            mode = "current"
        else:
            if at < EARLIEST_SUPPORTED:
                raise BadRequestError(
                    "Timestamp is too far in the past for weather data.",
                    code="weather_timestamp_out_of_range",
                )
            url = self._forecast_url if now - at <= FORECAST_API_PAST_LIMIT else self._archive_url
            reading = self._hourly(url, latitude, longitude, at)
            mode = "historical"
        logger.info(
            "weather ok (%s) near %s: %s C, code %s (%.0f ms)",
            mode,
            coarse_coords(latitude, longitude),
            reading.temperature_c,
            reading.weather_code,
            (time.perf_counter() - started) * 1000,
        )
        return reading

    def close(self) -> None:
        self._client.close()

    # ---- internals ----
    def _base_params(self, latitude: float, longitude: float) -> dict[str, Any]:
        return {
            "latitude": latitude,
            "longitude": longitude,
            "wind_speed_unit": "kmh",
            "timezone": "UTC",
        }

    def _current(self, latitude: float, longitude: float) -> WeatherReading:
        key = (round(latitude, 2), round(longitude, 2))  # ~1 km buckets
        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("weather cache hit near %s", coarse_coords(latitude, longitude))
            return cached
        data = self._request({**self._base_params(latitude, longitude), "current": ",".join(VARIABLES)})
        current = data.get("current")
        if not isinstance(current, dict):
            raise self._invalid("missing current conditions")
        observed_at = _parse_time(current["time"]) if current.get("time") else utcnow()
        reading = self._build(current, observed_at)
        self._cache.set(key, reading)
        return reading

    def _hourly(self, url: str, latitude: float, longitude: float, at: datetime) -> WeatherReading:
        day = at.date().isoformat()
        data = self._request(
            {
                **self._base_params(latitude, longitude),
                "hourly": ",".join(VARIABLES),
                "start_date": day,
                "end_date": day,
            },
            url=url,
        )
        hourly = data.get("hourly")
        times = hourly.get("time") if isinstance(hourly, dict) else None
        if not isinstance(times, list) or not times:
            raise self._invalid("missing hourly data")
        parsed = [_parse_time(t) for t in times]
        index = min(range(len(parsed)), key=lambda i: abs(parsed[i] - at))
        values: dict[str, Any] = {}
        for variable in VARIABLES:
            series = hourly.get(variable)
            values[variable] = series[index] if isinstance(series, list) and index < len(series) else None
        values["time"] = times[index]
        return self._build(values, parsed[index])

    def _request(self, params: dict[str, Any], url: Optional[str] = None) -> dict[str, Any]:
        data = get_json(self._client, url or self._forecast_url, params=params, kind="weather")
        if not isinstance(data, dict) or data.get("error"):
            if isinstance(data, dict):
                logger.warning("weather provider reported an error: %s", str(data.get("reason"))[:200])
            raise self._invalid("provider reported an error")
        return data

    @staticmethod
    def _invalid(why: str) -> BadGatewayError:
        logger.warning("weather provider response unusable: %s", why)
        return BadGatewayError("The weather service returned an invalid response.", code="weather_provider_error")

    def _build(self, values: dict[str, Any], observed_at: datetime) -> WeatherReading:
        fields: dict[str, Any] = {ours: _num(values.get(theirs)) for theirs, ours in VARIABLES.items()}
        if fields["weather_code"] is not None:
            fields["weather_code"] = int(fields["weather_code"])
        if all(v is None for v in fields.values()):
            raise BadGatewayError(
                "No weather data is available for this location and time.",
                code="weather_provider_error",
            )
        raw = {k: v for k, v in values.items() if k in VARIABLES or k == "time"}
        return WeatherReading(provider=PROVIDER_NAME, observed_at=observed_at, raw=raw, **fields)
