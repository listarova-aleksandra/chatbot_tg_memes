"""Таблица users."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Telegram-ID может не помещаться в 32 бита, поэтому BigInteger.
    # UNIQUE + индекс: по нему мы находим пользователя при каждом апдейте.
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128))

    xp: Mapped[int] = mapped_column(Integer, default=0)
    # level выводится из xp (см. сервис геймификации) и хранится для удобства.
    level: Mapped[int] = mapped_column(Integer, default=1)
    # Серия правильных ответов подряд (между играми). Нужна для бонуса и достижения Big Brain.
    correct_streak: Mapped[int] = mapped_column(Integer, default=0)
    best_streak: Mapped[int] = mapped_column(Integer, default=0)

    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    default_category: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
