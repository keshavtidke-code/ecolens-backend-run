from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.observation import Observation


class WeatherSnapshot(TimestampMixin, Base):
    __tablename__ = "weather_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("observations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    temperature_c: Mapped[Optional[float]]
    feels_like_c: Mapped[Optional[float]]
    humidity_pct: Mapped[Optional[float]]
    precipitation_mm: Mapped[Optional[float]]
    rain_last_24h_mm: Mapped[Optional[float]]
    wind_speed_kmh: Mapped[Optional[float]]
    wind_direction_deg: Mapped[Optional[float]]
    cloud_cover_pct: Mapped[Optional[float]]
    uv_index: Mapped[Optional[float]]
    pressure_hpa: Mapped[Optional[float]]
    soil_moisture: Mapped[Optional[float]]
    weather_code: Mapped[Optional[int]]
    raw_json: Mapped[Optional[dict]] = mapped_column(JSON)

    observation: Mapped["Observation"] = relationship(back_populates="weather_snapshot")
