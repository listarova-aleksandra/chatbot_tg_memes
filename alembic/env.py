"""Окружение Alembic (асинхронный вариант).

Alembic сравнивает модели из app.database.models с реальной БД и создаёт
миграции: версионированные скрипты изменения схемы. Применяются командой
`alembic upgrade head`.
"""

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.database.models  # noqa: F401  (импорт нужен, чтобы модели попали в metadata)
from app.core.config import get_settings
from app.database.base import Base

config = context.config
target_metadata = Base.metadata


def get_url() -> str:
    # Переменная окружения имеет приоритет (удобно для тестов), иначе берём из .env.
    return os.environ.get("DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    """Режим «без подключения»: только печатает SQL (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        # Сами миграции синхронные, поэтому запускаем их через run_sync.
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
