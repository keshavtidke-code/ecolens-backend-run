from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Query

from app.api.deps import ContextServiceDep, CurrentUser
from app.integrations.providers import get_weather_provider
from app.schemas.weather import LocationOut, WeatherPreviewOut, WeatherReadingOut

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get(
    "/preview",
    response_model=WeatherPreviewOut,
    responses={
        400: {"description": "Timestamp out of supported range"},
        502: {"description": "Weather or geocoding provider failed"},
        504: {"description": "Weather or geocoding provider timed out"},
    },
)
def weather_preview(
    user: CurrentUser,
    context: ContextServiceDep,
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude in degrees", examples=[19.076])],
    lon: Annotated[float, Query(ge=-180, le=180, description="Longitude in degrees", examples=[72.8777])],
    at: Annotated[
        Optional[datetime],
        Query(description="Optional ISO-8601 timestamp (UTC if no zone). Defaults to now."),
    ] = None,
) -> WeatherPreviewOut:
    """Place name + weather for a coordinate, without saving anything.

    Strict by design: if either provider fails you get a 502/504 naming which one,
    unlike observation capture, which saves the photo even when providers are down.
    """
    location, reading = context.preview(lat, lon, at)
    return WeatherPreviewOut(
        location=LocationOut(**location.as_dict()),
        weather=WeatherReadingOut(
            provider=reading.provider, observed_at=reading.observed_at, **reading.measurements()
        ),
    )


@router.get(
    "/current",
    response_model=WeatherReadingOut,
    responses={
        400: {"description": "Timestamp out of supported range"},
        502: {"description": "Weather provider failed"},
        504: {"description": "Weather provider timed out"},
    },
)
def weather_current(
    user: CurrentUser,
    lat: Annotated[float, Query(ge=-90, le=90, description="Latitude in degrees", examples=[19.076])],
    lon: Annotated[float, Query(ge=-180, le=180, description="Longitude in degrees", examples=[72.8777])],
    at: Annotated[
        Optional[datetime],
        Query(description="Optional ISO-8601 timestamp (UTC if no zone). Defaults to now."),
    ] = None,
) -> WeatherReadingOut:
    """Weather-only lookup for a coordinate — no geocoding, faster and more reliable.

    Use this endpoint when place name is not required (e.g., the review screen
    weather widget).  Returns just the weather reading without a location block.
    """
    provider = get_weather_provider()
    reading = provider.fetch(lat, lon, at)
    return WeatherReadingOut(
        provider=reading.provider, observed_at=reading.observed_at, **reading.measurements()
    )
