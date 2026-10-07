"""Достижения: справочник achievements и связь «многие ко многим» user_achievements."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, utcnow


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)  # например "first_win"
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300))
    icon: Mapped[str] = mapped_column(String(16), default="🏅")


class UserAchievement(Base):
    """У пользователя много достижений, и одно достижение есть у многих пользователей.

    Составной первичный ключ (user_id, achievement_id) делает невозможной
    повторную выдачу одного и того же достижения.
    """

    __tablename__ = "user_achievements"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    achievement_id: Mapped[int] = mapped_column(
        ForeignKey("achievements.id", ondelete="CASCADE"), primary_key=True
    )
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
