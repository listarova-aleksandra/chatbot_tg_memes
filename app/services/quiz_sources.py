"""Динамические вопросы викторины, построенные по данным внешних API.

Вопрос «Как называется этот мем-шаблон?» строится по списку шаблонов Imgflip: картинка
и правильное название берутся из API (поэтому они заведомо верны), а три неправильных
варианта это названия других шаблонов. Вопросы сохраняются в БД (на них ссылаются ответы
игроков), поэтому викторина работает и когда Imgflip позже недоступен.
"""

import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import QuizCategory, QuizQuestion
from app.services.imgflip_service import MemeTemplate

SOURCE = "imgflip"
QUESTIONS_LIMIT = 12


async def sync_imgflip_questions(
    session: AsyncSession, templates: list[MemeTemplate], limit: int = QUESTIONS_LIMIT
) -> int:
    """Синхронизирует вопросы с источником `imgflip`. Возвращает число активных вопросов."""
    pool = [t for t in templates if t.url]
    chosen = pool[:limit]
    existing = {
        q.slug: q for q in await session.scalars(select(QuizQuestion).where(QuizQuestion.source == SOURCE))
    }

    count = 0
    for template in chosen:
        others = [t.name for t in pool if t.name != template.name]
        if len(others) < 3:
            continue
        # Random(template.id): для одного шаблона неправильные варианты всегда одни и те же.
        wrong = random.Random(template.id).sample(others, 3)

        slug = f"imgflip-{template.id}"
        row = existing.pop(slug, None)
        if row is None:
            row = QuizQuestion(slug=slug, source=SOURCE)
            session.add(row)
        row.category = QuizCategory.INTERNET.value
        row.difficulty = 2
        row.question = "Как называется этот мем-шаблон?"
        row.image_url = template.url
        row.correct_answer = template.name
        row.wrong_answers = wrong
        row.explanation = f"Это шаблон «{template.name}»: один из самых популярных на Imgflip."
        row.is_active = True
        count += 1

    for stale in existing.values():  # шаблоны, которых больше нет в топе
        stale.is_active = False
    await session.flush()
    return count
