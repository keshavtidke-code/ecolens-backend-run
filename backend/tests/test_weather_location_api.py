"""API-level tests for /weather/preview and location/weather capture on observations."""
from datetime import datetime, timezone

import pytest

from app.core.exceptions import BadGatewayError, BadRequestError, GatewayTimeoutError
from app.core.settings import get_settings
from app.integrations.geocoding import Location
from tests.conftest import register, upload

MUMBAI = {"lat": 19.0760, "lon": 72.8777}
PREVIEW = "/api/v1/weather/preview"
WEATHER_KEYS = {
    "temperature_c", "feels_like_c", "humidity_pct", "precipitation_mm", "wind_speed_kmh",
    "wind_direction_deg", "cloud_cover_pct", "weather_code",
}


def wx_error(status=502):
    return (BadGatewayError if status == 502 else GatewayTimeoutError)(
        "weather down", code="weather_provider_error" if status == 502 else "weather_provider_timeout"
    )


def geo_error(status=502):
    return (BadGatewayError if status == 502 else GatewayTimeoutError)(
        "geo down", code="geocoding_provider_error" if status == 502 else "geocoding_provider_timeout"
    )


# ------------------------------ preview: success ------------------------------
def test_preview_valid_india_coordinates(client, auth, weather_provider, geocoder):
    r = client.get(PREVIEW, params=MUMBAI, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["location"] == {"place_name": "Mumbai", "region": "Maharashtra", "country": "India"}
    assert WEATHER_KEYS <= set(body["weather"])
    assert body["weather"]["temperature_c"] == 31.5 and body["weather"]["weather_code"] == 3
    assert body["weather"]["provider"] == "open-meteo"
    assert weather_provider.calls[0][:2] == (19.076, 72.8777)
    assert geocoder.calls == [(19.076, 72.8777)]


def test_preview_accepts_boundary_and_zero_coordinates(client, auth):
    for lat, lon in [(0, 0), (90, 180), (-90, -180)]:
        assert client.get(PREVIEW, params={"lat": lat, "lon": lon}, headers=auth).status_code == 200


def test_preview_unresolvable_place_returns_nulls(client, auth, geocoder):
    geocoder.location = Location()
    r = client.get(PREVIEW, params={"lat": 0, "lon": -30}, headers=auth)
    assert r.status_code == 200
    assert r.json()["location"] == {"place_name": None, "region": None, "country": None}


def test_preview_passes_timestamp_to_provider(client, auth, weather_provider):
    r = client.get(PREVIEW, params={**MUMBAI, "at": "2026-09-01T10:00:00Z"}, headers=auth)
    assert r.status_code == 200
    assert weather_provider.calls[-1][2] == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)


def test_preview_requires_auth(client):
    assert client.get(PREVIEW, params=MUMBAI).status_code == 401


# ------------------------------ preview: invalid coordinates ------------------------------
@pytest.mark.parametrize(
    "params",
    [
        {"lat": 91, "lon": 72},
        {"lat": -91, "lon": 72},
        {"lat": 19, "lon": 181},
        {"lat": 19, "lon": -181},
        {"lat": "abc", "lon": 72},
        {"lat": "nan", "lon": 72},
        {"lat": 19, "lon": "inf"},
        {"lat": 19},
        {"lon": 72},
        {},
    ],
    ids=["lat>90", "lat<-90", "lon>180", "lon<-180", "lat-text", "lat-nan", "lon-inf", "no-lon", "no-lat", "none"],
)
def test_preview_invalid_coordinates(client, auth, weather_provider, geocoder, params):
    r = client.get(PREVIEW, params=params, headers=auth)
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error" and r.json()["details"]
    assert weather_provider.calls == [] and geocoder.calls == []  # providers never called


def test_preview_timestamp_out_of_range_is_400(client, auth, weather_provider):
    weather_provider.error = BadRequestError("too old", code="weather_timestamp_out_of_range")
    r = client.get(PREVIEW, params={**MUMBAI, "at": "1900-01-01T00:00:00Z"}, headers=auth)
    assert r.status_code == 400 and r.json()["code"] == "weather_timestamp_out_of_range"


# ------------------------------ preview: provider failures ------------------------------
@pytest.mark.parametrize("status,code", [(502, "weather_provider_error"), (504, "weather_provider_timeout")])
def test_preview_weather_provider_failure(client, auth, weather_provider, status, code):
    weather_provider.error = wx_error(status)
    r = client.get(PREVIEW, params=MUMBAI, headers=auth)
    assert r.status_code == status and r.json()["code"] == code
    assert "request_id" in r.json()


@pytest.mark.parametrize("status,code", [(502, "geocoding_provider_error"), (504, "geocoding_provider_timeout")])
def test_preview_geocoding_provider_failure(client, auth, geocoder, status, code):
    geocoder.error = geo_error(status)
    r = client.get(PREVIEW, params=MUMBAI, headers=auth)
    assert r.status_code == status and r.json()["code"] == code


def test_preview_both_fail_reports_weather_first(client, auth, weather_provider, geocoder):
    weather_provider.error, geocoder.error = wx_error(), geo_error()
    assert client.get(PREVIEW, params=MUMBAI, headers=auth).json()["code"] == "weather_provider_error"


# ------------------------------ observation capture ------------------------------
def test_capture_saves_location_and_weather(client, auth, weather_provider):
    r = upload(client, auth, latitude="19.076", longitude="72.8777", captured_at="2026-09-01T10:00:00Z")
    assert r.status_code == 201, r.text
    obs = r.json()
    assert (obs["place_name"], obs["region"], obs["country"]) == ("Mumbai", "Maharashtra", "India")
    assert obs["weather_snapshot"]["temperature_c"] == 31.5
    assert obs["weather_snapshot"]["humidity_pct"] == 78.0
    assert obs["weather_snapshot"]["provider"] == "open-meteo"
    # the photo's own timestamp (not "now") was used for the weather lookup
    assert weather_provider.calls[0][2] == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)

    fetched = client.get(f"/api/v1/observations/{obs['id']}", headers=auth).json()  # persisted
    assert fetched["place_name"] == "Mumbai" and fetched["weather_snapshot"]["weather_code"] == 3


def test_capture_survives_weather_failure(client, auth, weather_provider):
    weather_provider.error = wx_error(504)
    r = upload(client, auth)
    assert r.status_code == 201
    obs = r.json()
    assert obs["weather_snapshot"] is None and obs["place_name"] == "Mumbai"


def test_capture_survives_geocoding_failure(client, auth, geocoder):
    geocoder.error = geo_error()
    obs = upload(client, auth).json()
    assert obs["place_name"] is None and obs["weather_snapshot"]["temperature_c"] == 31.5


@pytest.mark.parametrize("error", [None, RuntimeError("provider bug")], ids=["app-errors", "unexpected-bug"])
def test_capture_survives_total_provider_outage(client, auth, weather_provider, geocoder, error):
    weather_provider.error = error or wx_error()
    geocoder.error = error or geo_error()
    r = upload(client, auth)
    assert r.status_code == 201
    obs = r.json()
    assert obs["weather_snapshot"] is None and obs["place_name"] is None
    assert client.get(obs["photo_url"], headers=auth).status_code == 200  # photo was kept


def test_capture_skips_lookups_when_disabled(client, auth, weather_provider, geocoder, monkeypatch):
    monkeypatch.setattr(get_settings(), "external_lookups_enabled", False)
    obs = upload(client, auth).json()
    assert weather_provider.calls == [] and geocoder.calls == []
    assert obs["weather_snapshot"] is None and obs["place_name"] is None


# ------------------------------ refresh-context ------------------------------
def test_refresh_context_recovers_after_outage(client, auth, weather_provider, geocoder):
    weather_provider.error, geocoder.error = wx_error(), geo_error()
    obs = upload(client, auth).json()
    assert obs["weather_snapshot"] is None

    weather_provider.error = geocoder.error = None
    r = client.post(f"/api/v1/observations/{obs['id']}/refresh-context", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["place_name"] == "Mumbai" and r.json()["weather_snapshot"]["temperature_c"] == 31.5


def test_refresh_context_updates_existing_snapshot_in_place(client, auth, weather_provider):
    obs = upload(client, auth).json()
    first_id = obs["weather_snapshot"]["id"]
    weather_provider.temperature_c = 18.0
    r = client.post(f"/api/v1/observations/{obs['id']}/refresh-context", headers=auth).json()
    assert r["weather_snapshot"]["temperature_c"] == 18.0 and r["weather_snapshot"]["id"] == first_id


def test_refresh_context_keeps_known_place_when_geocoder_returns_nothing(client, auth, geocoder):
    obs = upload(client, auth).json()
    geocoder.location = Location()
    r = client.post(f"/api/v1/observations/{obs['id']}/refresh-context", headers=auth).json()
    assert r["place_name"] == "Mumbai"


def test_refresh_context_fails_loudly_when_providers_still_down(client, auth, weather_provider, geocoder):
    obs = upload(client, auth).json()
    weather_provider.error, geocoder.error = wx_error(504), geo_error()
    r = client.post(f"/api/v1/observations/{obs['id']}/refresh-context", headers=auth)
    assert r.status_code == 504 and r.json()["code"] == "weather_provider_timeout"


def test_refresh_context_partial_success_is_saved(client, auth, weather_provider, geocoder):
    weather_provider.error, geocoder.error = wx_error(), geo_error()
    obs = upload(client, auth).json()
    geocoder.error = None  # only geocoding recovers
    r = client.post(f"/api/v1/observations/{obs['id']}/refresh-context", headers=auth)
    assert r.status_code == 200 and r.json()["place_name"] == "Mumbai" and r.json()["weather_snapshot"] is None


def test_refresh_context_is_owner_only(client, auth):
    obs = upload(client, auth).json()
    other = register(client, "b@example.com")
    assert client.post(f"/api/v1/observations/{obs['id']}/refresh-context", headers=other).status_code == 404
