"""Динамические вопросы викторины: «Кто это?» и «Что это за мем?» по GIF и картинкам Giphy.

Как это работает. В `app/content/gif_questions.json` лежит список записей: поисковый запрос,
правильный ответ и пояснение. При запуске бота для каждой записи ищется GIF в Giphy; ссылка
сохраняется в БД как обычный вопрос викторины (источник "giphy"). Три неправильных варианта
берутся из других записей того же вида (или задаются вручную полем `wrong`).

Две «медиа»: "gif" (анимация) и "image" (обычная картинка: берётся первый кадр GIF, бот
превращает его в JPEG и отправляет как фото).

Главная проблема таких вопросов: на GIF часто есть подпись с ответом («CAITLIN CLARK»).
Поэтому выбор GIF идёт в два шага:
  1. отбрасываются GIF, чьё описание (alt_text) содержит ответ: если надпись видна на картинке,
     она обычно попадает в описание. Список «запрещённых слов» берётся из ответа
     или задаётся в записи полем `hide`;
  2. из оставшихся предпочитаются GIF с описанием и с официальных каналов (NBA, WNBA, ESPN).
Если подходящей GIF нет, запись пропускается, а не рискует показать подсказку.
Запись можно «закрепить» вручную: поле `giphy_id` берёт конкретную GIF (id виден в её ссылке).

Giphy опрашивается только для новых записей, поэтому при обычных перезапусках лишних запросов
нет, а если Giphy недоступен, викторина работает на уже сохранённых и локальных вопросах.
"""

import json
import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ApiError
from app.database.models import QuizCategory, QuizQuestion
from app.services.giphy_service import GifCandidate, GiphyService

logger = logging.getLogger(__name__)

GIF_QUESTIONS_FILE = Path(__file__).resolve().parent.parent / "content" / "gif_questions.json"
SOURCE = "giphy"
SLUG_PREFIX = "g2-"  # при смене правил отбора меняем префикс: старые вопросы выключатся сами
PREFERRED_CHANNELS = {"nba", "wnba", "espn", "bleacherreport", "sportscenter"}
KINDS = ("person", "meme")
MEDIA = ("gif", "image")


class GifEntry(BaseModel):
    slug: str
    category: str
    kind: str  # person или meme
    media: str = "gif"  # gif или image
    query: str
    answer: str
    note: str
    difficulty: int = 1
    hide: list[str] | None = None  # слова, которых не должно быть в описании GIF
    wrong: list[str] | None = None  # три неправильных варианта вручную
    giphy_id: str | None = None  # закрепить конкретную GIF

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, value: str) -> str:
        if value not in KINDS:
            raise ValueError(f"unknown kind {value!r}")
        return value

    @field_validator("media")
    @classmethod
    def valid_media(cls, value: str) -> str:
        if value not in MEDIA:
            raise ValueError(f"unknown media {value!r}")
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

    @field_validator("wrong")
    @classmethod
    def valid_wrong(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and (len(value) != 3 or any(not w.strip() or len(w) > 60 for w in value)):
            raise ValueError("wrong must be exactly 3 non-empty strings up to 60 chars")
        return value


def question_text(entry: GifEntry) -> str:
    if entry.kind == "person":
        return "Кто на этой GIF?" if entry.media == "gif" else "Кто на этой картинке?"
    return "Что это за мем или тренд?"


def load_gif_entries(path: Path = GIF_QUESTIONS_FILE) -> list[GifEntry]:
    entries = [GifEntry.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]
    slugs = [e.slug for e in entries]
    if len(slugs) != len(set(slugs)):
        raise ValueError("slug записей должны быть уникальны")
    answers = [e.answer for e in entries]
    if len(answers) != len(set(answers)):
        raise ValueError("ответы записей должны быть уникальны")
    for e in entries:
        if e.wrong is not None and len({e.answer, *e.wrong}) != 4:
            raise ValueError(f"{e.slug}: варианты ответа должны быть разными")
    return entries


def pick_wrong_answers(entry: GifEntry, entries: list[GifEntry]) -> list[str]:
    """Три неправильных варианта: заданные вручную или того же вида (сначала своя категория).

    Random(entry.slug) делает выбор стабильным: для одной записи варианты не меняются
    от запуска к запуску.
    """
    if entry.wrong is not None:
        return list(entry.wrong)
    same_kind = [e for e in entries if e.kind == entry.kind and e.answer != entry.answer]
    same_category = [e.answer for e in same_kind if e.category == entry.category]
    others = [e.answer for e in same_kind if e.category != entry.category]
    rng = random.Random(entry.slug)
    rng.shuffle(same_category)
    rng.shuffle(others)
    return (same_category + others)[:3]


# ---------- Отбор GIF без подсказки на картинке ----------


def _normalize(text: str) -> str:
    """Нижний регистр, знаки препинания заменены пробелами: «6-7» и «6 7» сравниваются одинаково."""
    return " " + " ".join(re.sub(r"[^\w]+", " ", text.lower()).split()) + " "


def hidden_words(entry: GifEntry) -> list[str]:
    """Слова, появление которых в описании GIF выдало бы ответ."""
    if entry.hide is not None:
        return entry.hide
    words = [entry.answer]
    if entry.kind == "person":
        tokens = re.sub(r"[^\w\s]", " ", entry.answer).split()
        words += [t for t in tokens if len(t) >= 4]  # фамилия и длинные части имени
    return words


def reveals_answer(candidate: GifCandidate, words: list[str]) -> bool:
    text = _normalize(candidate.alt_text)
    return any(f" {_normalize(w).strip()} " in text for w in words if w.strip())


def choose_candidate(
    candidates: list[GifCandidate], entry: GifEntry
) -> tuple[GifCandidate | None, int]:
    """Лучшая GIF без подсказки. Возвращает (кандидат или None, сколько отброшено)."""
    words = hidden_words(entry)
    usable = [c for c in candidates if entry.media == "gif" or c.still_url]
    clean = [c for c in usable if not reveals_answer(c, words)]
    rejected = len(usable) - len(clean)
    if not clean:
        return None, rejected
    # Описание есть и не выдаёт ответ, значит GIF проверена; затем официальные каналы; затем порядок выдачи.
    ranked = sorted(
        enumerate(clean),
        key=lambda pair: (not pair[1].alt_text, pair[1].username not in PREFERRED_CHANNELS, pair[0]),
    )
    return ranked[0][1], rejected


@dataclass(frozen=True)
class SyncReport:
    active: int  # активных вопросов из Giphy после синхронизации
    fetched: int  # для скольких записей GIF запрошена в Giphy
    skipped: int  # записи, для которых подходящей GIF не нашлось
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
        slug = f"{SLUG_PREFIX}{entry.slug}"
        wrong = pick_wrong_answers(entry, entries)
        if len(wrong) < 3:
            skipped += 1
            continue

        row = existing.pop(slug, None)
        if row is None or not row.image_url or row.media_type != entry.media:
            try:
                if entry.giphy_id:
                    found = await giphy.get_candidate(entry.giphy_id)
                    candidate, rejected = (found, 0) if found else (None, 0)
                else:
                    candidate, rejected = choose_candidate(await giphy.search_candidates(entry.query), entry)
            except ApiError as error:
                logger.warning("Giphy не ответил, синхронизация GIF-вопросов прервана: %s", error)
                aborted = True
                if row is not None:
                    existing[slug] = row  # вернём в список «не тронутых»
                break
            fetched += 1
            if candidate is None:
                logger.warning(
                    "Для записи %s не нашлось GIF без подсказки (отброшено %s): запись пропущена",
                    entry.slug, rejected,
                )
                skipped += 1
                continue
            if row is None:
                row = QuizQuestion(slug=slug, source=SOURCE)
                session.add(row)
            row.image_url = candidate.gif_url if entry.media == "gif" else candidate.still_url
            row.media_type = entry.media

        row.category = entry.category
        row.difficulty = entry.difficulty
        row.question = question_text(entry)
        row.correct_answer = entry.answer
        row.wrong_answers = wrong
        row.explanation = entry.note
        row.is_active = True

    if not aborted:
        # Записи, которых больше нет в файле (и вопросы прежних версий), выключаем:
        # не удаляем, потому что на них ссылаются ответы игроков.
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
