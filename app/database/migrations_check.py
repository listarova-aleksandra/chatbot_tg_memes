"""Проверка, что схема БД соответствует коду (применены все миграции Alembic).

Без этой проверки забытая миграция проявляется как длинная ошибка SQL посреди запуска
(«column ... does not exist»). Здесь мы сравниваем версию БД с последней миграцией в
репозитории и при расхождении сообщаем понятную команду.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def expected_head() -> str | None:
    """Номер последней миграции в папке alembic/versions."""
    config = Config()
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    """Версия, примененная к БД. None: миграции ещё ни разу не применялись."""
    try:
        async with engine.connect() as connection:
            row = (await connection.execute(text("SELECT version_num FROM alembic_version"))).first()
    except Exception:
        return None  # таблицы alembic_version нет: БД пустая
    return row[0] if row else None


async def migration_problem(engine: AsyncEngine) -> str | None:
    """Текст проблемы, если схема БД не соответствует коду, иначе None."""
    head, current = expected_head(), await current_revision(engine)
    if current == head:
        return None
    if current is None:
        return "В базе данных нет таблиц: миграции ещё не применялись."
    return f"Схема базы данных устарела (версия {current}, нужна {head})."
