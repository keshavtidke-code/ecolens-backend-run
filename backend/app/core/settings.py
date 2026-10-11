"""Application settings, loaded from environment variables and an optional .env file."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]  # the backend/ directory
INSECURE_SECRET = "change-me-in-production"


def _split_list(value: object) -> object:
    """Accept either a JSON array or a comma-separated string for list settings."""
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("["):
            return json.loads(value)
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_name: str = "EcoLens API"
    app_version: str = "0.1.0"
    environment: Literal["development", "testing", "staging", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    database_url: str = "sqlite:///./ecolens.db"
    db_echo: bool = False

    # --- Security ---
    secret_key: str = INSECURE_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30

    # --- CORS ---
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:8081",
        "http://localhost:19006",
    ]
    cors_allow_credentials: bool = False

    # --- Logging ---
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- Media storage ---
    storage_dir: Path = Path("storage")
    max_upload_mb: int = 10
    allowed_image_types: Annotated[list[str], NoDecode] = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
    ]

    # --- External lookups (backend-only; no API keys needed) ---
    external_lookups_enabled: bool = True  # false = skip geocoding/weather on capture (offline dev)
    provider_timeout_s: float = 6.0
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    weather_cache_ttl_s: int = 600
    geocoding_url: str = "https://nominatim.openstreetmap.org/reverse"
    # Nominatim's usage policy requires an identifying User-Agent (include a contact).
    geocoding_user_agent: str = "EcoLens/0.1 (set GEOCODING_USER_AGENT with a contact email)"
    geocoding_min_interval_s: float = 1.0  # Nominatim allows at most 1 request/second
    geocoding_cache_ttl_s: int = 86400

    # --- Gemini (perception stage) ---
    gemini_api_key: Optional[SecretStr] = None  # unset/empty => analysis returns 503 ai_not_configured
    gemini_model: str = "gemini-3.8-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_s: float = 60.0
    gemini_max_output_tokens: int = 8192  # Gemini 3 counts thinking tokens against this
    gemini_temperature: Optional[float] = None  # None = model default (recommended for Gemini 3)
    analysis_stale_after_s: int = 300  # an "analyzing" status older than this may be re-run
    # Perception confidence below this puts the lesson stage into cautious mode.
    low_confidence_threshold: float = 0.5

    # --- Ask-AI chat (POST /observations/{id}/chat) ---
    chat_history_messages: int = 10  # most recent stored messages sent to Gemini as conversation history
    chat_max_attempts: int = 3  # provider attempts for transient failures (timeout / 429 / 5xx)
    chat_retry_backoff_s: float = 0.5  # first retry delay; doubles each retry (plus small jitter)
    chat_request_timeout_s: float = 25.0  # per Gemini attempt
    chat_total_timeout_s: float = 50.0  # overall deadline across attempts and backoff
    chat_rate_limit_per_minute: int = 10  # per user; 0 disables
    chat_rate_limit_per_hour: int = 100  # per user; 0 disables

    @field_validator("gemini_api_key", "gemini_temperature", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("cors_origins", "allowed_image_types", mode="before")
    @classmethod
    def _parse_lists(cls, value: object) -> object:
        return _split_list(value)

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("storage_dir")
    @classmethod
    def _absolute_storage_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (BASE_DIR / value).resolve()

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.environment == "production":
            if self.secret_key == INSECURE_SECRET or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be a random string of 32+ characters in production.")
            if self.debug:
                raise ValueError("DEBUG must be false in production.")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
