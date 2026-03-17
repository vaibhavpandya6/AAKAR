"""Alembic environment configuration for async SQLAlchemy."""

import asyncio
import logging
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from config import settings
from db.models import Base

# this is the Alembic Config object, which provides
# the values of the [alembic] section of the .ini file
# as well as other options contained within the .ini file
# that can be modified by programmatic manipulation.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = settings.postgres_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_async_migrations(engine: Connection) -> None:
    """Execute migrations inside async context."""

    def upgrade(rev, context):
        context.configure(connection=engine, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()

    asyncio.run(upgrade(None, context))


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = settings.postgres_url

    connectable = create_async_engine(
        settings.postgres_url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_async_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
