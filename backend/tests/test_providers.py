"""Provider-level tests: the real parsing/error code, driven by a mocked HTTP transport."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.exceptions import BadGatewayError, BadRequestError, GatewayTimeoutError
from app.core.utils import utcnow
from app.integrations.geocoding import NominatimGeocodingProvider
from app.integrations.weather import OpenMeteoWeatherProvider

FORECAST, ARCHIVE = "https://wx.test/forecast", "https://wx.test/archive"
GEOCODE = "https://geo.test/reverse"

CURRENT_PAYLOAD = {
    "latitude": 19.0,
    "longitude": 72.875,
    "current": {
        "time": "2026-10-03T05:15", "interval": 900, "temperature_2m": 29.4,
        "apparent_temperature": 33.1, "relative_humidity_2m": 74, "precipitation": 0.0,
        "wind_speed_10m": 11.2, "wind_direction_10m": 250, "cloud_cover": 40,
        "weather_code": 2, "pressure_msl": 1009.3,
    },
}


def weather_provider(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenMeteoWeatherProvider(
        forecast_url=FORECAST, archive_url=ARCHIVE, timeout_s=5, client=client
    )


def geocoder(handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return NominatimGeocodingProvider(
        url=GEOCODE, user_agent="EcoLens-test", timeout_s=5, min_interval_s=0, client=client, **kwargs
    )


def hourly_payload(day: str, temp_base=20.0):
    times = [f"{day}T{h:02d}:00" for h in range(24)]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [temp_base + h for h in range(24)],
            "apparent_temperature": [temp_base + h + 2 for h in range(24)],
            "relative_humidity_2m": [60] * 24,
            "precipitation": [0.0] * 24,
            "wind_speed_10m": [8.0] * 24,
            "wind_direction_10m": [180] * 24,
            "cloud_cover": [50] * 24,
            "weather_code": [1] * 24,
            "pressure_msl": [1010.0] * 24,
        }
    }


# ------------------------------ weather ------------------------------
def test_weather_current_parsing_and_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=CURRENT_PAYLOAD)

    reading = weather_provider(handler).fetch(19.0760, 72.8777)
    assert reading.temperature_c == 29.4 and reading.feels_like_c == 33.1
    assert reading.humidity_pct == 74 and reading.precipitation_mm == 0.0
    assert reading.wind_speed_kmh == 11.2 and reading.wind_direction_deg == 250
    assert reading.cloud_cover_pct == 40 and reading.weather_code == 2
    assert reading.observed_at == datetime(2026, 10, 3, 5, 15, tzinfo=timezone.utc)
    q = seen[0].url.params
    assert str(seen[0].url).startswith(FORECAST)
    assert q["latitude"] == "19.076" and q["wind_speed_unit"] == "kmh" and q["timezone"] == "UTC"
    assert "temperature_2m" in q["current"] and "weather_code" in q["current"]


def test_weather_current_is_cached_for_nearby_points():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=CURRENT_PAYLOAD)

    provider = weather_provider(handler)
    provider.fetch(19.0760, 72.8777)
    provider.fetch(19.0762, 72.8779)  # ~25 m away -> same ~1 km bucket
    assert len(calls) == 1


def test_weather_recent_timestamp_uses_current():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=CURRENT_PAYLOAD)

    weather_provider(handler).fetch(19.0, 72.8, at=utcnow() - timedelta(minutes=30))
    assert "current" in seen[0].url.params


def test_weather_historical_uses_forecast_api_hourly():
    at = (utcnow() - timedelta(days=10)).replace(hour=14, minute=40, second=0, microsecond=0)
    day = at.date().isoformat()
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=hourly_payload(day))

    reading = weather_provider(handler).fetch(19.0, 72.8, at=at)
    assert str(seen[0].url).startswith(FORECAST)
    assert seen[0].url.params["start_date"] == day == seen[0].url.params["end_date"]
    assert reading.observed_at.hour == 15          # 14:40 rounds to the nearest hour
    assert reading.temperature_c == 35.0           # 20 + 15


def test_weather_very_old_uses_archive_api():
    at = utcnow() - timedelta(days=200)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=hourly_payload(at.date().isoformat()))

    weather_provider(handler).fetch(19.0, 72.8, at=at)
    assert str(seen[0].url).startswith(ARCHIVE)


def test_weather_timestamp_before_supported_range():
    provider = weather_provider(lambda r: httpx.Response(200, json=CURRENT_PAYLOAD))
    with pytest.raises(BadRequestError) as exc:
        provider.fetch(19.0, 72.8, at=datetime(1900, 1, 1, tzinfo=timezone.utc))
    assert exc.value.code == "weather_timestamp_out_of_range"


def test_weather_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GatewayTimeoutError) as exc:
        weather_provider(handler).fetch(19.0, 72.8)
    assert exc.value.status_code == 504 and exc.value.code == "weather_provider_timeout"


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(429, text="slow down"),
        lambda r: httpx.Response(400, json={"error": True, "reason": "bad param"}),
        lambda r: httpx.Response(200, text="<html>not json</html>"),
        lambda r: httpx.Response(200, json={"error": True, "reason": "nope"}),
        lambda r: httpx.Response(200, json={"unexpected": 1}),                 # no "current"
        lambda r: httpx.Response(200, json={"current": {"time": "2026-10-03T05:15"}}),  # all values null
    ],
    ids=["500", "429", "400", "bad-json", "error-flag", "no-current", "all-null"],
)
def test_weather_provider_failures(handler):
    with pytest.raises(BadGatewayError) as exc:
        weather_provider(handler).fetch(19.0, 72.8)
    assert exc.value.status_code == 502 and exc.value.code == "weather_provider_error"


def test_weather_unreachable():
    def handler(request):
        raise httpx.ConnectError("dns", request=request)

    with pytest.raises(BadGatewayError) as exc:
        weather_provider(handler).fetch(19.0, 72.8)
    assert exc.value.code == "weather_provider_error"


# ------------------------------ geocoding ------------------------------
MUMBAI = {
    "name": "Bandra West",
    "display_name": "Bandra West, Mumbai, Maharashtra, India",
    "address": {
        "suburb": "Bandra West", "city": "Mumbai", "state_district": "Mumbai Suburban",
        "state": "Maharashtra", "country": "India", "country_code": "in",
    },
}


def test_geocoding_india_city_and_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=MUMBAI)

    loc = geocoder(handler).reverse(19.0596, 72.8295)
    assert loc.as_dict() == {"place_name": "Mumbai", "region": "Maharashtra", "country": "India"}
    q = seen[0].url.params
    assert q["lat"] == "19.0596" and q["lon"] == "72.8295"
    assert q["format"] == "jsonv2" and q["accept-language"] == "en"


def test_geocoding_village_falls_back_to_smaller_locality():
    payload = {"address": {"village": "Lavasa", "county": "Pune", "state": "Maharashtra", "country": "India"}}
    loc = geocoder(lambda r: httpx.Response(200, json=payload)).reverse(18.41, 73.50)
    assert loc.place_name == "Lavasa" and loc.region == "Maharashtra"


def test_geocoding_unresolvable_point_is_not_an_error():
    loc = geocoder(lambda r: httpx.Response(200, json={"error": "Unable to geocode"})).reverse(0.0, -30.0)
    assert loc.as_dict() == {"place_name": None, "region": None, "country": None}


def test_geocoding_results_are_cached():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=MUMBAI)

    provider = geocoder(handler)
    provider.reverse(19.0601, 72.8293)
    provider.reverse(19.0602, 72.8294)  # ~15 m away, same ~110 m bucket
    assert len(calls) == 1
    provider.reverse(19.0700, 72.8293)  # ~1 km away -> different bucket -> new request
    assert len(calls) == 2


def test_geocoding_sends_identifying_user_agent():
    provider = NominatimGeocodingProvider(url=GEOCODE, user_agent="EcoLens/1.0 (me@example.com)", timeout_s=5)
    assert provider._client.headers["User-Agent"] == "EcoLens/1.0 (me@example.com)"
    provider.close()


def test_geocoding_timeout():
    def handler(request):
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(GatewayTimeoutError) as exc:
        geocoder(handler).reverse(19.0, 72.8)
    assert exc.value.status_code == 504 and exc.value.code == "geocoding_provider_timeout"


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(429, text="too many"),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, json=["unexpected", "list"]),
    ],
    ids=["500", "429", "bad-json", "wrong-shape"],
)
def test_geocoding_provider_failures(handler):
    with pytest.raises(BadGatewayError) as exc:
        geocoder(handler).reverse(19.0, 72.8)
    assert exc.value.status_code == 502 and exc.value.code == "geocoding_provider_error"
