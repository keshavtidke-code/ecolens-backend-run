"""Process-wide provider singletons (so caches, throttling and connection pools are shared).

FastAPI dependencies point at these factory functions, which tests override with fakes.
"""
from __future__ import annotations

from typing import Optional

from app.core.settings import get_settings
from app.integrations.geocoding import GeocodingProvider, NominatimGeocodingProvider
from app.integrations.weather import OpenMeteoWeatherProvider, WeatherProvider

_weather: Optional[OpenMeteoWeatherProvider] = None
_geocoder: Optional[NominatimGeocodingProvider] = None


def get_weather_provider() -> WeatherProvider:
    global _weather
    if _weather is None:
        s = get_settings()
        _weather = OpenMeteoWeatherProvider(
            forecast_url=s.open_meteo_forecast_url,
            archive_url=s.open_meteo_archive_url,
            timeout_s=s.provider_timeout_s,
            cache_ttl_s=s.weather_cache_ttl_s,
        )
    return _weather


def get_geocoding_provider() -> GeocodingProvider:
    global _geocoder
    if _geocoder is None:
        s = get_settings()
        _geocoder = NominatimGeocodingProvider(
            url=s.geocoding_url,
            user_agent=s.geocoding_user_agent,
            timeout_s=s.provider_timeout_s,
            min_interval_s=s.geocoding_min_interval_s,
            cache_ttl_s=s.geocoding_cache_ttl_s,
        )
    return _geocoder


def close_providers() -> None:
    global _weather, _geocoder
    for provider in (_weather, _geocoder):
        if provider is not None:
            provider.close()
    _weather = _geocoder = None
