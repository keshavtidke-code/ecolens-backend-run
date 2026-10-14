"""Engine, session factory and the request-scoped session dependency."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _redact_password(url: str) -> str:
    """Replace the password in a connection URL with ***."""
    return re.sub(r"(:)[^:@]+(@)", r"\1***\2", url)


def _normalise_pg_url(raw: str) -> str:
    """
    Render (and many PaaS providers) emit  postgres://  or  postgresql://
    without a driver qualifier.  We ship psycopg2-binary, so we force the
    dialect to postgresql+psycopg2 exactly once.
    """
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+psycopg2://", 1)
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        return raw.replace("postgresql://", "postgresql+psycopg2://", 1)
    return raw


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def build_engine(settings: Settings) -> Engine:
    db_url = _normalise_pg_url(settings.database_url)
    logger.info("Building engine for backend: %s", _redact_password(db_url))

    url = make_url(db_url)
    kwargs: dict = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,  # discard stale connections before use
    }

    is_sqlite = url.get_backend_name() == "sqlite"

    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    else:
        # PostgreSQL on Render free tier.
        # connect_timeout: fail fast instead of hanging when the DB is unreachable.
        # sslmode: Render Postgres requires SSL on both internal and external URLs.
        kwargs["connect_args"] = {
            "sslmode": "require",
            "connect_timeout": 10,  # seconds; prevents the endless hang on /auth/me
        }
        kwargs["pool_size"] = 3
        kwargs["max_overflow"] = 2
        kwargs["pool_recycle"] = 300          # recycle connections every 5 min
        kwargs["pool_timeout"] = 15           # raise after 15 s waiting for a pool slot
        # statement timeout via server-side execution option (psycopg2 syntax)
        kwargs["execution_options"] = {"timeout": 30}

    engine = create_engine(db_url, **kwargs)

    if is_sqlite:
        event.listen(engine, "connect", _sqlite_pragmas)

    return engine


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

engine = build_engine(get_settings())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One session per request. Services commit explicitly; errors roll back."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
