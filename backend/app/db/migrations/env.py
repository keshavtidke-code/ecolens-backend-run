from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.settings import get_settings
from app.db.base import Base
from app.db.session import _normalise_pg_url  # reuse the same normalisation

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

settings = get_settings()
database_url = _normalise_pg_url(settings.database_url)
is_sqlite = database_url.startswith("sqlite")

# SQLite cannot ALTER most things in place; batch mode recreates tables instead.
_opts = dict(target_metadata=target_metadata, render_as_batch=is_sqlite, compare_type=True)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_opts,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connect_args: dict = {}
    if not is_sqlite:
        connect_args = {"sslmode": "require", "connect_timeout": 10}

    engine = create_engine(database_url, poolclass=pool.NullPool, connect_args=connect_args)
    with engine.connect() as connection:
        context.configure(connection=connection, **_opts)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
