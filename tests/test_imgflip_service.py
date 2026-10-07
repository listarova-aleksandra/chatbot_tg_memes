"""Imgflip: разбор шаблонов, кэш, запасные шаблоны, вопросы по шаблонам."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TTLCache
from app.core.exceptions import ApiBadResponseError, ApiUnavailableError
from app.database.models import QuizQuestion
from app.database.seed import seed_questions
from app.services.imgflip_service import FALLBACK_TEMPLATES, ImgflipService, parse_templates
from app.services.quiz_sources import sync_imgflip_questions
from tests.conftest import FakeApiClient


def meme(i: int, name: str | None = None, boxes: int = 2, url: str | None = None) -> dict:
    return {
        "id": str(i), "name": name or f"Template {i}", "url": url or f"https://i.imgflip.com/{i}.jpg",
        "width": 500, "height": 400, "box_count": boxes,
    }


def response(*memes: dict) -> dict:
    return {"success": True, "data": {"memes": list(memes)}}


def make(client: FakeApiClient) -> ImgflipService:
    return ImgflipService(client, TTLCache())  # type: ignore[arg-type]


def test_parse_filters_unsuitable_templates() -> None:
    payload = response(
        meme(1),
        meme(2, boxes=1),
        meme(3, boxes=4),  # у нас только верхний и нижний текст
        meme(4, url="https://evil.example.com/x.jpg"),  # чужой хост
        meme(5, name="X" * 100),  # слишком длинное название
        meme(6, name="Template 1"),  # дубль названия
        {"id": "7"},  # испорченный элемент не ломает остальные
    )
    assert [t.id for t in parse_templates(payload)] == ["1", "2"]


@pytest.mark.parametrize("payload", [None, [], {}, {"success": False}, {"success": True},
                                     {"success": True, "data": {"memes": "x"}}, response()])
def test_parse_rejects_bad_or_empty_response(payload) -> None:
    with pytest.raises(ApiBadResponseError):
        parse_templates(payload)


async def test_templates_are_cached() -> None:
    client = FakeApiClient(response(meme(1), meme(2)))
    service = make(client)
    first = await service.get_templates()
    second = await service.get_templates()
    assert not first.is_fallback and first.templates == second.templates
    assert len(client.calls) == 1


@pytest.mark.parametrize("failure", [ApiUnavailableError("down"), ApiBadResponseError("bad")])
async def test_failure_gives_fallback_templates_without_raising(failure: Exception) -> None:
    result = await make(FakeApiClient(failure)).get_templates()
    assert result.is_fallback and result.templates == FALLBACK_TEMPLATES
    assert all(t.url is None for t in result.templates)  # свой фон, без чужих картинок


async def test_fallback_is_not_cached_so_service_recovers() -> None:
    client = FakeApiClient(ApiUnavailableError("down"), response(meme(1), meme(2)))
    service = make(client)
    assert (await service.get_templates()).is_fallback
    assert not (await service.get_templates()).is_fallback


# ---------- Вопросы «угадай мем-шаблон» ----------


def templates(n: int):
    return parse_templates(response(*[meme(i) for i in range(1, n + 1)]))


async def test_sync_creates_valid_image_questions(session: AsyncSession) -> None:
    count = await sync_imgflip_questions(session, templates(20), limit=5)
    assert count == 5
    rows = list(await session.scalars(select(QuizQuestion).where(QuizQuestion.source == "imgflip")))
    assert len(rows) == 5
    for q in rows:
        options = [q.correct_answer, *q.wrong_answers]
        assert len(set(options)) == 4  # варианты разные
        assert q.image_url.startswith("https://i.imgflip.com/") and q.category == "internet"
        assert q.correct_answer == f"Template {q.slug.removeprefix('imgflip-')}"  # ответ взят из API


async def test_sync_is_idempotent_and_stable(session: AsyncSession) -> None:
    await sync_imgflip_questions(session, templates(10), limit=4)
    before = {q.slug: q.wrong_answers for q in await session.scalars(select(QuizQuestion))}
    await sync_imgflip_questions(session, templates(10), limit=4)
    after = {q.slug: q.wrong_answers for q in await session.scalars(select(QuizQuestion))}
    assert before == after and len(after) == 4  # без дублей, неправильные варианты те же


async def test_sync_deactivates_templates_that_left_the_top(session: AsyncSession) -> None:
    await sync_imgflip_questions(session, templates(10), limit=3)
    await sync_imgflip_questions(session, templates(10)[3:], limit=3)  # топ сменился
    active = {q.slug for q in await session.scalars(select(QuizQuestion).where(QuizQuestion.is_active))}
    assert active == {"imgflip-4", "imgflip-5", "imgflip-6"}


async def test_too_few_templates_creates_no_questions(session: AsyncSession) -> None:
    assert await sync_imgflip_questions(session, templates(3)) == 0  # для 4 вариантов нужно ≥4 названий


async def test_local_seed_does_not_touch_imgflip_questions(session: AsyncSession) -> None:
    await sync_imgflip_questions(session, templates(10), limit=3)
    await seed_questions(session)  # перезагрузка локальных вопросов
    imgflip = list(await session.scalars(select(QuizQuestion).where(QuizQuestion.source == "imgflip")))
    assert len(imgflip) == 3 and all(q.is_active for q in imgflip)
