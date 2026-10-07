"""Загрузка вопросов из app/content/questions.json в БД.

Загрузка идемпотентна: вопрос определяется по `slug`. Повторный запуск обновляет
существующие вопросы и добавляет новые, дублей не создаёт. Вопросы, которых больше нет
в файле, помечаются неактивными (но не удаляются: на них могут ссылаться ответы).
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import QuizCategory, QuizQuestion

logger = logging.getLogger(__name__)

QUESTIONS_FILE = Path(__file__).resolve().parent.parent / "content" / "questions.json"

MAX_QUESTION_LEN = 300
MAX_ANSWER_LEN = 60  # варианты выводятся в сообщении и в кнопках результата
MAX_EXPLANATION_LEN = 400  # подпись к фото в Telegram не длиннее 1024 символов


class QuestionData(BaseModel):
    """Проверка структуры вопроса: ошибка в файле обнаружится при старте, а не в игре."""

    slug: str
    category: str
    difficulty: int
    question: str
    correct_answer: str
    wrong_answers: list[str]
    explanation: str
    image_url: str | None = None

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        allowed = {c.value for c in QuizCategory if c is not QuizCategory.MIXED}
        if value not in allowed:
            raise ValueError(f"unknown category {value!r}")
        return value

    @field_validator("difficulty")
    @classmethod
    def valid_difficulty(cls, value: int) -> int:
        if value not in (1, 2, 3):
            raise ValueError("difficulty must be 1, 2 or 3")
        return value

    @model_validator(mode="after")
    def check_answers_and_lengths(self) -> "QuestionData":
        options = [self.correct_answer, *self.wrong_answers]
        if len(self.wrong_answers) != 3:
            raise ValueError(f"{self.slug}: нужно ровно 3 неправильных варианта")
        if len(set(options)) != 4:
            raise ValueError(f"{self.slug}: варианты ответа должны быть разными")
        if any(not o.strip() or len(o) > MAX_ANSWER_LEN for o in options):
            raise ValueError(f"{self.slug}: вариант пуст или длиннее {MAX_ANSWER_LEN}")
        if len(self.question) > MAX_QUESTION_LEN:
            raise ValueError(f"{self.slug}: вопрос слишком длинный")
        if len(self.explanation) > MAX_EXPLANATION_LEN:
            raise ValueError(f"{self.slug}: объяснение слишком длинное")
        return self


def load_questions(path: Path = QUESTIONS_FILE) -> list[QuestionData]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    questions = [QuestionData.model_validate(item) for item in raw]
    slugs = [q.slug for q in questions]
    if len(slugs) != len(set(slugs)):
        raise ValueError("slug вопросов должны быть уникальны")
    return questions


async def seed_questions(session: AsyncSession, path: Path = QUESTIONS_FILE) -> int:
    """Синхронизирует таблицу quiz_questions с файлом. Возвращает число вопросов в файле."""
    questions = load_questions(path)
    existing = {
        q.slug: q for q in await session.scalars(select(QuizQuestion))
    }

    for data in questions:
        row = existing.pop(data.slug, None)
        if row is None:
            row = QuizQuestion(slug=data.slug)
            session.add(row)
        row.category = data.category
        row.difficulty = data.difficulty
        row.question = data.question
        row.correct_answer = data.correct_answer
        row.wrong_answers = data.wrong_answers
        row.explanation = data.explanation
        row.image_url = data.image_url
        row.is_active = True

    for removed in existing.values():  # остались только те, которых нет в файле
        removed.is_active = False

    await session.flush()
    logger.info("Вопросы викторины загружены: %s шт.", len(questions))
    return len(questions)
