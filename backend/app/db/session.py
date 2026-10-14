"""Engine, session factory and the request-scoped session dependency."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
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
    Render / Supabase emit  postgres://  or  postgresql://  without a driver
    qualifier.  SQLAlchemy 2.1 defaults plain postgresql:// to psycopg (v3).
    We ship psycopg[binary] (v3), so we force the dialect explicitly so the
    behaviour is deterministic even if requirements change in future.
    """
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+psycopg://", 1)
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------

def build_engine(settings: Settings) -> Engine:
    db_url = _normalise_pg_url(settings.database_url)
    logger.info("Building engine: %s", _redact_password(db_url))

    url = make_url(db_url)
    kwargs: dict = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,
    }

    is_sqlite = url.get_backend_name() == "sqlite"

    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    else:
        # psycopg v3 (libpq) keyword args — same names as psycopg2 for these.
        kwargs["connect_args"] = {
            "sslmode": "require",       # Supabase / Render Postgres require SSL
            "connect_timeout": 10,      # fail fast; prevents endless hang on /auth/me
        }
        kwargs["pool_size"] = 3
        kwargs["max_overflow"] = 2
        kwargs["pool_recycle"] = 300    # recycle connections every 5 min
        kwargs["pool_timeout"] = 15     # raise after 15 s waiting for a pool slot

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
