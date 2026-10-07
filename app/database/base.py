"""Базовый класс для всех ORM-моделей.

ORM (Object-Relational Mapping): таблица описывается Python-классом,
строка таблицы это объект, а SQL генерирует SQLAlchemy. Ручная склейка
SQL-строк не нужна, а значит нет и SQL-инъекций.
"""

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Единые имена для ограничений (PK, FK, UNIQUE...). Без этого Alembic не сможет
# надёжно удалять и менять ограничения: у них были бы автогенерируемые имена.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    """Текущее время в UTC. Все даты в БД хранятся в UTC."""
    return datetime.now(UTC)
