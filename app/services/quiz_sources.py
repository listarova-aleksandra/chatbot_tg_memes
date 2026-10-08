"""Динамические вопросы викторины: «Кто на этой GIF?» и «Что это за мем?».

Как это работает. В `app/content/gif_questions.json` лежит список записей: поисковый
запрос, правильный ответ и пояснение. При запуске бота для каждой записи ищется GIF
в Giphy; ссылка сохраняется в БД как обычный вопрос викторины (с источником "giphy").
Три неправильных варианта берутся из ответов других записей того же вида.

Экономия запросов к Giphy: GIF ищется только для записей, которых ещё нет в БД. При
следующих запусках бот в Giphy не ходит, а если Giphy недоступен, викторина работает на
уже сохранённых вопросах и на локальных (questions.json).
"""

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.database.models import QuizCategory, QuizQuestion
from app.services.giphy_service import GiphyService

logger = logging.getLogger(__name__)

GIF_QUESTIONS_FILE = Path(__file__).resolve().parent.parent / "content" / "gif_questions.json"
SOURCE = "giphy"
QUESTION_TEXT = {"person": "Кто на этой GIF?", "meme": "Что это за мем или тренд?"}


class GifEntry(BaseModel):
    slug: str
    category: str
    kind: str  # person или meme
    query: str
    answer: str
    note: str
    difficulty: int = 1

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        if value not in QUESTION_TEXT:
            raise ValueError(f"unknown kind {value!r}")
        return value

    @field_validator("category")
    @classmethod
    def valid_category(cls, value: str) -> str:
        if value not in {c.value for c in QuizCategory if c is not QuizCategory.MIXED}:
            raise ValueError(f"unknown category {value!r}")
        return value

    @field_validator("difficulty")
    @classmethod
    def valid_difficulty(cls, value: int) -> int:
        if value not in (1, 2, 3):
            raise ValueError("difficulty must be 1..3")
        return value

    @field_validator("answer")
    @classmethod
    def valid_answer(cls, value: str) -> str:
        if not value.strip() or len(value) > 60:
            raise ValueError("answer must be 1..60 chars")
        return value

    @field_validator("note")
    @classmethod
    def valid_note(cls, value: str) -> str:
        if not value.strip() or len(value) > 400:
            raise ValueError("note must be 1..400 chars")
        return value


def load_gif_entries(path: Path = GIF_QUESTIONS_FILE) -> list[GifEntry]:
    entries = [GifEntry.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    slugs = [e.slug for e in entries]
    if len(slugs) != len(set(slugs)):
        raise ValueError("slug записей должны быть уникальны")
    answers = [e.answer for e in entries]
    if len(answers) != len(set(answers)):
        raise ValueError("ответы записей должны быть уникальны")
    return entries


def pick_wrong_answers(entry: GifEntry, entries: list[GifEntry]) -> list[str]:
    """Три неправильных варианта того же вида: сначала из своей категории, потом из остальных.

    Random(entry.slug) делает выбор стабильным: для одной записи варианты не меняются
    от запуска к запуску.
    """
    same_kind = [e for e in entries if e.kind == entry.kind and e.answer != entry.answer]
    same_category = [e.answer for e in same_kind if e.category == entry.category]
    others = [e.answer for e in same_kind if e.category != entry.category]
    rng = random.Random(entry.slug)
    rng.shuffle(same_category)
    rng.shuffle(others)
    return (same_category + others)[:3]


@dataclass(frozen=True)
class SyncReport:
    active: int  # активных вопросов из Giphy после синхронизации
    fetched: int  # для скольких записей GIF запрошена в Giphy
    skipped: int  # записи, для которых GIF не нашлась или Giphy не ответил
    aborted: bool  # True: Giphy не ответил, синхронизация прервана


async def sync_giphy_questions(
    session: AsyncSession, giphy: GiphyService, entries: list[GifEntry]
) -> SyncReport:
    """Приводит вопросы источника "giphy" в соответствие с файлом записей."""
    if not giphy.enabled:
        return SyncReport(active=0, fetched=0, skipped=0, aborted=False)

    existing = {
        q.slug: q for q in await session.scalars(select(QuizQuestion).where(QuizQuestion.source == SOURCE))
    }
    fetched = skipped = 0
    aborted = False

    for entry in entries:
        slug = f"gif-{entry.slug}"
        wrong = pick_wrong_answers(entry, entries)
        if len(wrong) < 3:
            skipped += 1
            continue

        row = existing.pop(slug, None)
        if row is None or not row.image_url:
            # GIF для этой записи ещё нет: ищем в Giphy.
            try:
                urls = await giphy.search(entry.query)
            except ApiError as error:
                logger.warning("Giphy не ответил, синхронизация GIF-вопросов прервана: %s", error)
                aborted = True
                if row is not None:
                    existing[slug] = row  # вернём в список «не тронутых»
                break
            fetched += 1
            if not urls:
                logger.warning("Giphy не нашёл GIF по запросу %r, запись %s пропущена", entry.query, entry.slug)
                skipped += 1
                continue
            if row is None:
                row = QuizQuestion(slug=slug, source=SOURCE)
                session.add(row)
            row.image_url = urls[0]  # первый результат самый релевантный

        row.category = entry.category
        row.difficulty = entry.difficulty
        row.question = QUESTION_TEXT[entry.kind]
        row.correct_answer = entry.answer
        row.wrong_answers = wrong
        row.explanation = entry.note
        row.is_active = True

    if not aborted:
        # Записи, которых больше нет в файле, выключаем (но не удаляем: на них ссылаются ответы).
        for stale in existing.values():
            stale.is_active = False

    await session.flush()
    active = len(
        list(
            await session.scalars(
                select(QuizQuestion.id).where(QuizQuestion.source == SOURCE, QuizQuestion.is_active.is_(True))
            )
        )
    )
    return SyncReport(active=active, fetched=fetched, skipped=skipped, aborted=aborted)


async def retire_source(session: AsyncSession, source: str) -> None:
    """Выключает все вопросы источника (например, устаревшие вопросы по шаблонам Imgflip)."""
    await session.execute(
        update(QuizQuestion).where(QuizQuestion.source == source).values(is_active=False)
    )
