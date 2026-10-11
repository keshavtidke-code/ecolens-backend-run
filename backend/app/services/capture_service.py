"""Creating, reading and deleting observations (photo + location capture)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError, UnsupportedMediaTypeError
from app.core.settings import Settings
from app.core.utils import utcnow
from app.models.enums import ObservationStatus, PhotoSource
from app.models.observation import Observation
from app.integrations.weather import MEASUREMENT_FIELDS
from app.models.user import User
from app.models.weather import WeatherSnapshot
from app.repositories.observation import ObservationRepository
from app.services.context_service import ContextLookup, ContextService
from app.storage.images import EXTENSIONS, read_limited, sniff_image_type, validate_image_data
from app.storage.local import LocalFileStorage

logger = logging.getLogger(__name__)


class CaptureService:
    def __init__(
        self, db: Session, storage: LocalFileStorage, settings: Settings, context: ContextService
    ) -> None:
        self.observations = ObservationRepository(db)
        self.storage = storage
        self.settings = settings
        self.context = context

    def get_owned(self, user: User, observation_id: int, *, detail: bool = False) -> Observation:
        observation = self.observations.get_for_user(observation_id, user.id, detail=detail)
        if observation is None:
            raise NotFoundError("Observation not found.", code="observation_not_found")
        return observation

    def create(
        self,
        user: User,
        *,
        upload: UploadFile,
        latitude: float,
        longitude: float,
        captured_at: Optional[datetime],
        location_accuracy_m: Optional[float],
        photo_source: PhotoSource,
        user_note: Optional[str],
    ) -> Observation:
        data = read_limited(upload.file, self.settings.max_upload_bytes)
        if not data:
            raise BadRequestError("The uploaded photo is empty.", code="empty_photo")
        mime = sniff_image_type(data[:16])
        if mime is None or mime not in self.settings.allowed_image_types:
            raise UnsupportedMediaTypeError(
                "Photo must be one of: " + ", ".join(self.settings.allowed_image_types),
                code="unsupported_photo_type",
            )
        # Validate the full image bytes for corruption / truncation.
        validate_image_data(data, mime)
        if captured_at is None:
            captured_at = utcnow()
        elif captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)

        # Place name + weather are best-effort: an outage must never lose the user's photo.
        lookup: Optional[ContextLookup] = None
        if self.settings.external_lookups_enabled:
            lookup = self.context.lookup(latitude, longitude, captured_at)

        relative_path = self.storage.save(
            data, subdir=f"observations/{user.id}", extension=EXTENSIONS[mime]
        )
        observation = Observation(
            user_id=user.id,
            captured_at=captured_at,
            latitude=latitude,
            longitude=longitude,
            location_accuracy_m=location_accuracy_m,
            photo_path=relative_path,
            photo_mime_type=mime,
            photo_size_bytes=len(data),
            photo_source=photo_source.value,
            status=ObservationStatus.pending.value,
            user_note=user_note,
        )
        if lookup is not None:
            self._apply_context(observation, lookup)
        try:
            return self.observations.add(observation)
        except Exception:
            self.observations.db.rollback()
            self.storage.delete(relative_path)  # don't orphan the file
            raise

    def refresh_context(self, user: User, observation_id: int) -> Observation:
        """Re-run location + weather lookup (e.g. after a provider outage at capture time)."""
        observation = self.get_owned(user, observation_id, detail=True)
        lookup = self.context.lookup(
            observation.latitude, observation.longitude, observation.captured_at
        )
        if lookup.location is None and lookup.weather is None:
            raise lookup.first_error()  # nothing usable came back: tell the client
        self._apply_context(observation, lookup)
        self.observations.save()
        return observation

    @staticmethod
    def _apply_context(observation: Observation, lookup: ContextLookup) -> None:
        if lookup.location is not None:
            # Never overwrite a known value with "unknown".
            for attr in ("place_name", "region", "country"):
                value = getattr(lookup.location, attr)
                if value:
                    setattr(observation, attr, value)
        if lookup.weather is not None:
            reading = lookup.weather
            snapshot = observation.weather_snapshot or WeatherSnapshot()
            snapshot.provider = reading.provider
            snapshot.observed_at = reading.observed_at
            for name in MEASUREMENT_FIELDS:
                setattr(snapshot, name, getattr(reading, name))
            snapshot.raw_json = reading.raw
            observation.weather_snapshot = snapshot

    def delete(self, user: User, observation_id: int) -> None:
        observation = self.get_owned(user, observation_id)
        photo_path = observation.photo_path
        self.observations.delete(observation)
        try:
            self.storage.delete(photo_path)
        except OSError:
            logger.warning("Could not delete photo file", extra={"photo_path": photo_path})
