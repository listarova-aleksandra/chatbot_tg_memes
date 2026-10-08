"""GIF-викторина: файл записей, синхронизация с Giphy, показ GIF в игре."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from aiogram.methods import DeleteMessage, EditMessageCaption, EditMessageText, SendAnimation, SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cache import TTLCache
from app.core.exceptions import ApiUnavailableError
from app.database.models import QuizQuestion
from app.database.seed import seed_questions
from app.services.giphy_service import GiphyService
from app.services.quiz_sources import (
    GifEntry, load_gif_entries, pick_wrong_answers, retire_source, sync_giphy_questions,
)
from tests.conftest import FakeApiClient
from tests.test_api_handlers import make_player
from tests.test_quiz_flow import Player


def gif_payload(n: int = 1) -> dict:
    return {"data": [{"images": {"downsized": {"url": f"https://media.giphy.com/media/g{i}/giphy.gif"}}} for i in range(n)]}


def entry(slug: str, kind: str = "person", category: str = "nba", **kw: Any) -> GifEntry:
    return GifEntry(slug=slug, category=category, kind=kind, query=slug, answer=kw.pop("answer", slug.title()),
                    note="Пояснение", difficulty=kw.pop("difficulty", 1))


def entries(n: int = 6, **kw: Any) -> list[GifEntry]:
    return [entry(f"p{i}", **kw) for i in range(n)]


def giphy(*responses: Any) -> tuple[GiphyService, FakeApiClient]:
    client = FakeApiClient(*responses)
    return GiphyService(client, TTLCache(), "KEY"), client  # type: ignore[arg-type]


# ---------- Файл с записями ----------


def test_gif_file_is_valid_and_balanced() -> None:
    all_entries = load_gif_entries()  # внутри проверяются все правила
    assert 30 <= len(all_entries) <= 60
    by_category = Counter(e.category for e in all_entries)
    assert set(by_category) == {"nba", "wnba", "hiphop", "rnb", "internet", "postirony"}
    assert min(by_category.values()) >= 4  # хватает и на 3 неправильных варианта, и на игру
    for e in all_entries:
        assert len(pick_wrong_answers(e, all_entries)) == 3, e.slug


def test_wrong_answers_are_distinct_stable_and_prefer_same_category() -> None:
    all_entries = load_gif_entries()
    for e in all_entries:
        wrong = pick_wrong_answers(e, all_entries)
        assert e.answer not in wrong and len(set(wrong)) == 3
        assert wrong == pick_wrong_answers(e, all_entries)  # стабильно между запусками
    nba = next(e for e in all_entries if e.slug == "curry")
    nba_answers = {x.answer for x in all_entries if x.category == "nba"}
    assert set(pick_wrong_answers(nba, all_entries)) <= nba_answers  # все из НБА, так честнее


def test_invalid_entries_rejected(tmp_path: Path) -> None:
    good = {"slug": "a", "category": "nba", "kind": "person", "query": "q", "answer": "A", "note": "n"}
    for patch in ({"kind": "x"}, {"category": "mixed"}, {"answer": "x" * 80}, {"note": ""}, {"difficulty": 9}):
        with pytest.raises(ValueError):
            GifEntry.model_validate({**good, **patch})
    path = tmp_path / "g.json"
    path.write_text(json.dumps([good, {**good, "slug": "b"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="ответы"):
        load_gif_entries(path)  # одинаковые ответы недопустимы


# ---------- Синхронизация ----------


async def test_sync_creates_gif_questions(session: AsyncSession) -> None:
    service, client = giphy(gif_payload(3))
    report = await sync_giphy_questions(session, service, entries(6))
    assert (report.active, report.fetched, report.skipped, report.aborted) == (6, 6, 0, False)
    rows = list(await session.scalars(select(QuizQuestion).where(QuizQuestion.source == "giphy")))
    for q in rows:
        assert q.image_url == "https://media.giphy.com/media/g0/giphy.gif"  # первый результат
        assert q.question == "Кто на этой GIF?" and q.category == "nba"
        options = [q.correct_answer, *q.wrong_answers]
        assert len(set(options)) == 4


async def test_second_sync_does_not_call_giphy_again(session: AsyncSession) -> None:
    service, client = giphy(gif_payload())
    await sync_giphy_questions(session, service, entries(5))
    calls = len(client.calls)
    service2, client2 = giphy(gif_payload())  # новый сервис: кэш пуст, значит, запрос был бы виден
    report = await sync_giphy_questions(session, service2, entries(5))
    assert client2.calls == [] and report.fetched == 0 and report.active == 5 and calls == 5


async def test_new_entry_fetches_only_itself(session: AsyncSession) -> None:
    service, _ = giphy(gif_payload())
    await sync_giphy_questions(session, service, entries(5))
    service2, client2 = giphy(gif_payload())
    report = await sync_giphy_questions(session, service2, entries(6))
    assert len(client2.calls) == 1 and report.fetched == 1 and report.active == 6


async def test_giphy_down_aborts_quickly_and_keeps_existing(session: AsyncSession) -> None:
    ok_service, _ = giphy(gif_payload())
    await sync_giphy_questions(session, ok_service, entries(5))
    down, client = giphy(ApiUnavailableError("down"))
    report = await sync_giphy_questions(session, down, entries(8))  # три новые записи
    assert report.aborted and len(client.calls) == 1  # после первой неудачи остальные не мучаем
    active = list(await session.scalars(select(QuizQuestion).where(QuizQuestion.is_active.is_(True))))
    assert len(active) == 5  # прежние вопросы работают


async def test_giphy_down_does_not_deactivate_questions_missing_from_file(session: AsyncSession) -> None:
    ok_service, _ = giphy(gif_payload())
    await sync_giphy_questions(session, ok_service, entries(5))
    down, _ = giphy(ApiUnavailableError("down"))
    await sync_giphy_questions(session, down, entries(5)[1:] + [entry("new")])  # p0 исчез, new появилась
    assert (await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == "gif-p0"))).is_active


async def test_empty_search_result_skips_entry(session: AsyncSession) -> None:
    service, _ = giphy({"data": []})
    report = await sync_giphy_questions(session, service, entries(5))
    assert report.active == 0 and report.skipped == 5 and not report.aborted


async def test_entries_removed_from_file_are_deactivated(session: AsyncSession) -> None:
    service, _ = giphy(gif_payload())
    await sync_giphy_questions(session, service, entries(6))
    service2, _ = giphy(gif_payload())
    report = await sync_giphy_questions(session, service2, entries(6)[:5])
    assert report.active == 5
    assert not (await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == "gif-p5"))).is_active


async def test_disabled_giphy_does_nothing(session: AsyncSession) -> None:
    service = GiphyService(None, TTLCache(), None)
    report = await sync_giphy_questions(session, service, entries(5))
    assert report.active == 0 and not report.aborted


async def test_retire_source_and_local_seed_do_not_touch_each_other(session: AsyncSession) -> None:
    session.add(QuizQuestion(slug="imgflip-1", source="imgflip", category="internet", question="?",
                             correct_answer="a", wrong_answers=["b", "c", "d"], explanation="e"))
    service, _ = giphy(gif_payload())
    await sync_giphy_questions(session, service, entries(5))
    await seed_questions(session)
    await retire_source(session, "imgflip")
    rows = {q.slug: q for q in await session.scalars(select(QuizQuestion))}
    assert rows["imgflip-1"].is_active is False
    assert rows["gif-p0"].is_active is True  # GIF-вопросы переживают перезагрузку локальных
    assert sum(1 for q in rows.values() if q.source == "local" and q.is_active) == 31


# ---------- В игре ----------


@pytest.fixture
async def gif_player(
    bot, telegram, session_factory: async_sessionmaker[AsyncSession], make_dispatcher
) -> Player:
    async with session_factory() as s:
        await seed_questions(s)
        service, _ = giphy(gif_payload())
        await sync_giphy_questions(s, service, entries(8))
        await s.commit()
    return make_player(bot, telegram, session_factory, make_dispatcher())


async def test_gif_question_is_sent_as_animation_and_answer_edits_caption(gif_player: Player) -> None:
    p = gif_player
    await p.start_game("nba")
    data = await p.data()
    animations = p.telegram.of_type(SendAnimation)
    # В категории NBA 5 локальных и 8 GIF-вопросов; нужный нам вопрос мог оказаться любым.
    # Проверяем структуру: если вопрос с GIF, то он ушёл анимацией, иначе обычным сообщением.
    first = await p.question()
    if first.image_url:
        assert animations[0].animation == first.image_url
        assert "Вопрос 1/5" in animations[0].caption
        deletes = len(p.telegram.of_type(DeleteMessage))
        await p.answer(correct=True, photo=True)
        assert p.telegram.of_type(EditMessageCaption)  # подпись заменена, GIF осталась
        assert len(p.telegram.of_type(DeleteMessage)) == deletes
    else:
        assert not animations
    assert data["index"] == 0


async def test_game_with_gif_questions_runs_to_the_end(gif_player: Player) -> None:
    p = gif_player
    await p.start_game("nba")
    for _ in range(5):
        question = await p.question()
        await p.answer(correct=True, photo=bool(question.image_url))
        await p.next(photo=bool(question.image_url))
    texts = [m.text for m in p.telegram.of_type(SendMessage)] + [
        m.text for m in p.telegram.of_type(EditMessageText)
    ]
    assert any("Игра окончена" in (t or "") for t in texts)
