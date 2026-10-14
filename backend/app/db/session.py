"""Engine, session factory and the request-scoped session dependency."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings, get_settings


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")  # enforce FKs / ON DELETE CASCADE
    cursor.execute("PRAGMA journal_mode=WAL")  # concurrent readers while writing
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def build_engine(settings: Settings) -> Engine:
    url = make_url(settings.database_url)
    kwargs: dict = {"echo": settings.db_echo, "pool_pre_ping": True}
    is_sqlite = url.get_backend_name() == "sqlite"
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    else:
        # PostgreSQL (Supabase) — conservative pool for free-tier Render instances.
        kwargs["pool_size"] = 3
        kwargs["max_overflow"] = 2
        kwargs["pool_recycle"] = 300  # recycle connections every 5 min
        kwargs["connect_args"] = {"sslmode": "require"}  # Supabase requires SSL
    engine = create_engine(settings.database_url, **kwargs)
    if is_sqlite:
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


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
