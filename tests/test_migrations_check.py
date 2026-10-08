"""Проверка версии схемы БД при запуске."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from app.database.migrations_check import current_revision, expected_head, migration_problem


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    yield eng
    await eng.dispose()


async def set_version(engine, version: str | None) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await conn.execute(text("DELETE FROM alembic_version"))
        if version:
            await conn.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})


def test_repository_has_exactly_one_migration_head() -> None:
    assert expected_head() is not None  # две «головы» вызвали бы ошибку


async def test_empty_database_is_reported(engine) -> None:
    assert await current_revision(engine) is None
    assert "миграции ещё не применялись" in await migration_problem(engine)


async def test_outdated_schema_is_reported_with_both_versions(engine) -> None:
    await set_version(engine, "0003")
    problem = await migration_problem(engine)
    assert "0003" in problem and expected_head() in problem


async def test_up_to_date_schema_is_ok(engine) -> None:
    await set_version(engine, expected_head())
    assert await migration_problem(engine) is None


async def test_alembic_table_without_rows_is_reported(engine) -> None:
    await set_version(engine, None)
    assert await migration_problem(engine) is not None
