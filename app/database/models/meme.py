"""Таблицы мемов сообщества: memes и meme_votes."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, utcnow


class Meme(Base):
    __tablename__ = "memes"
    __table_args__ = (
        # Составные индексы под два вида сортировки ленты: популярные и новые.
        Index("ix_memes_published_rating", "is_published", "rating"),
        Index("ix_memes_published_at", "is_published", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Картинку на диске мы не храним: после отправки мема Telegram возвращает file_id,
    # по которому фото можно отправить повторно.
    image_file_id: Mapped[str] = mapped_column(String(255))
    template_name: Mapped[str | None] = mapped_column(String(100))
    top_text: Mapped[str] = mapped_column(String(100), default="")
    bottom_text: Mapped[str] = mapped_column(String(100), default="")

    # «Создан» и «опубликован» разделены: за них начисляется XP отдельно.
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Счётчики хранятся в самой строке (денормализация), чтобы не пересчитывать голоса
    # при каждом показе ленты. Обновляются в той же транзакции, что и голос.
    likes_count: Mapped[int] = mapped_column(Integer, default=0)
    dislikes_count: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[int] = mapped_column(Integer, default=0)  # likes - dislikes
    # Бонус XP за высокий рейтинг выдаётся автору один раз.
    bonus_awarded: Mapped[bool] = mapped_column(Boolean, default=False)


class MemeVote(Base):
    __tablename__ = "meme_votes"
    __table_args__ = (
        # Главная защита от повторного голосования: одна пара (пользователь, мем) = один голос.
        # Проверку делает сама БД, поэтому она надёжна даже при двойном нажатии на кнопку.
        UniqueConstraint("user_id", "meme_id", name="uq_meme_votes_user_meme"),
        CheckConstraint("vote IN (1, -1)", name="vote_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    meme_id: Mapped[int] = mapped_column(ForeignKey("memes.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    vote: Mapped[int] = mapped_column(SmallInteger)  # +1 лайк, -1 дизлайк
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
