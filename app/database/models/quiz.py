"""Таблицы викторины: вопросы, игровые сессии и ответы.

Связи:
  users 1 ── N quiz_sessions     (пользователь сыграл много игр)
  quiz_sessions 1 ── N quiz_attempts   (в игре 5 ответов)
  quiz_questions 1 ── N quiz_attempts  (на вопрос отвечали многие)
Мы объявляем только внешние ключи и не используем relationship(): в async-режиме
«ленивая» подгрузка связанных объектов не работает, поэтому связанные данные
мы выбираем явными JOIN-запросами. Так проще понять, что происходит.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, utcnow


class QuizCategory(StrEnum):
    """Категории викторины. В БД хранятся строки (не PG ENUM): их проще мигрировать."""

    POSTIRONY = "postirony"  # 🇷🇺 Постирония и брейнрот
    NBA = "nba"
    WNBA = "wnba"
    HIPHOP = "hiphop"
    RNB = "rnb"
    INTERNET = "internet"  # 🌐 Интернет-культура
    MIXED = "mixed"  # только для игры: вопросы из всех категорий


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (
        CheckConstraint("difficulty BETWEEN 1 AND 3", name="difficulty_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # slug уникален и нужен для идемпотентной загрузки вопросов: повторный запуск seed
    # обновляет существующий вопрос, а не создаёт дубль.
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    question: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(32), index=True)
    correct_answer: Mapped[str] = mapped_column(String(200))
    # Три неправильных варианта (JSON-список). При показе варианты перемешиваются.
    wrong_answers: Mapped[list[str]] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    difficulty: Mapped[int] = mapped_column(SmallInteger, default=1)  # 1 легко … 3 сложно
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Откуда вопрос: "local" (файл questions.json) или "imgflip" (строится по данным API).
    # Каждый источник управляет только своими вопросами.
    source: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    # Тип картинки вопроса: "gif" (анимация) или "image" (обычная картинка; бот сам скачивает
    # кадр, превращает в JPEG и отправляет как фото).
    media_type: Mapped[str] = mapped_column(String(10), default="gif", server_default="gif")


class QuizSession(Base):
    """Одна игра (обычно 5 вопросов). Это история игр пользователя."""

    __tablename__ = "quiz_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    total_questions: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    xp_earned: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # NULL = игра не закончена (например, пользователь бросил её посередине).
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizAttempt(Base):
    """Ответ пользователя на один вопрос."""

    __tablename__ = "quiz_attempts"
    __table_args__ = (
        # На один вопрос в одной игре можно ответить только один раз. Это защита от двойного
        # нажатия на кнопку: даже если два апдейта пройдут одновременно, второй INSERT упадёт.
        UniqueConstraint("session_id", "question_id", name="uq_quiz_attempts_session_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("quiz_sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("quiz_questions.id"))
    selected_answer: Mapped[str] = mapped_column(String(200))
    is_correct: Mapped[bool] = mapped_column(Boolean)
    xp_awarded: Mapped[int] = mapped_column(Integer, default=0)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
