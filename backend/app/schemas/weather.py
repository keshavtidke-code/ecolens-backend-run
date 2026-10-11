from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.common import ORMModel


class WeatherMeasurements(BaseModel):
    temperature_c: Optional[float] = None
    feels_like_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    precipitation_mm: Optional[float] = None
    rain_last_24h_mm: Optional[float] = None
    wind_speed_kmh: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    uv_index: Optional[float] = None
    pressure_hpa: Optional[float] = None
    soil_moisture: Optional[float] = None
    weather_code: Optional[int] = None


class WeatherSnapshotOut(ORMModel, WeatherMeasurements):
    id: int
    provider: str
    observed_at: datetime


class LocationOut(BaseModel):
    """Null fields mean the place could not be resolved (e.g. open ocean)."""

    place_name: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None


class WeatherReadingOut(WeatherMeasurements):
    provider: str
    observed_at: datetime


class WeatherPreviewOut(BaseModel):
    location: LocationOut
    weather: WeatherReadingOut
