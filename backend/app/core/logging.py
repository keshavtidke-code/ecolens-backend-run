"""Logging setup: console or JSON output, with a per-request correlation id."""
from __future__ import annotations

import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import datetime, timezone

from app.core.settings import Settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "color_message",
    "request_id",
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": RequestIdFilter}},
            "formatters": {
                "console": {
                    "format": "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"
                },
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": settings.log_format,
                    "filters": ["request_id"],
                }
            },
            "root": {"level": settings.log_level, "handlers": ["default"]},
            "loggers": {
                # Route uvicorn through our handler; we emit our own access log line.
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "propagate": True},
                "uvicorn.access": {"handlers": [], "propagate": True, "level": "WARNING"},
                "sqlalchemy.engine": {"level": "INFO" if settings.db_echo else "WARNING"},
                # httpx logs full request URLs at INFO (exact coordinates!) - keep them out of logs.
                "httpx": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
            },
        }
    )
