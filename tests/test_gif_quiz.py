"""GIF- и картинки-вопросы: файл записей, отбор без подсказки, синхронизация, показ в игре."""

import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from aiogram.methods import DeleteMessage, EditMessageCaption, EditMessageText, SendAnimation, SendMessage, SendPhoto
from aiogram.types import BufferedInputFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cache import TTLCache
from app.core.exceptions import ApiUnavailableError
from app.database.models import QuizQuestion
from app.database.seed import seed_questions
from app.services.giphy_service import GifCandidate, GiphyService
from app.services.quiz_sources import (
    GifEntry, choose_candidate, hidden_words, load_gif_entries, pick_wrong_answers, question_text,
    retire_source, reveals_answer, sync_giphy_questions,
)
from tests.conftest import FakeApiClient
from tests.test_api_handlers import make_player
from tests.test_quiz_flow import Player


def item(i: int = 0, alt: str = "", title: str = "", user: str = "") -> dict:
    return {
        "id": f"id{i}", "title": title, "alt_text": alt, "username": user,
        "images": {
            "downsized": {"url": f"https://media.giphy.com/media/g{i}/giphy.gif"},
            "downsized_still": {"url": f"https://media.giphy.com/media/g{i}/giphy_s.gif"},
        },
    }


def payload(*items: dict) -> dict:
    return {"data": list(items)}


def entry(slug: str, kind: str = "person", category: str = "nba", media: str = "gif", **kw: Any) -> GifEntry:
    return GifEntry(slug=slug, category=category, kind=kind, media=media, query=slug,
                    answer=kw.pop("answer", f"Name {slug}"), note="Пояснение", difficulty=kw.pop("difficulty", 1), **kw)


def entries(n: int = 6, **kw: Any) -> list[GifEntry]:
    return [entry(f"p{i}", **kw) for i in range(n)]


def giphy(*responses: Any) -> tuple[GiphyService, FakeApiClient]:
    client = FakeApiClient(*responses)
    return GiphyService(client, TTLCache(), "KEY"), client  # type: ignore[arg-type]


def cand(alt: str = "", user: str = "", still: bool = True, i: int = 0) -> GifCandidate:
    return GifCandidate(f"id{i}", f"https://media.giphy.com/g{i}.gif",
                        f"https://media.giphy.com/s{i}.gif" if still else None, "", alt, user)


# ---------- Файл с записями ----------


def test_gif_file_is_valid_balanced_and_has_both_media_types() -> None:
    all_entries = load_gif_entries()
    by_category = Counter(e.category for e in all_entries)
    assert set(by_category) == {"nba", "wnba", "hiphop", "rnb", "internet", "postirony"}
    assert min(by_category.values()) >= 4
    media = Counter(e.media for e in all_entries)
    assert media["gif"] >= 10 and media["image"] >= 10  # не только GIF, но и обычные картинки
    for e in all_entries:
        wrong = pick_wrong_answers(e, all_entries)
        assert len(wrong) == 3 and len({e.answer, *wrong}) == 4, e.slug
        assert wrong == pick_wrong_answers(e, all_entries)  # стабильно между запусками


def test_every_note_fits_caption_limit_with_question() -> None:
    for e in load_gif_entries():
        assert len(question_text(e)) + len(e.note) + 4 * 62 + 200 < 1024, e.slug


def test_manual_wrong_answers_override_and_are_validated(tmp_path: Path) -> None:
    e = entry("m", kind="meme", wrong=["a", "b", "c"])
    assert pick_wrong_answers(e, entries(5)) == ["a", "b", "c"]
    base = {"slug": "a", "category": "nba", "kind": "meme", "query": "q", "answer": "A", "note": "n"}
    for bad in ({"wrong": ["a", "b"]}, {"wrong": ["a", "", "c"]}, {"media": "video"}, {"kind": "x"}, {"difficulty": 9}):
        with pytest.raises(ValueError):
            GifEntry.model_validate({**base, **bad})
    path = tmp_path / "g.json"
    path.write_text(json.dumps([{**base, "wrong": ["A", "b", "c"]}]), encoding="utf-8")
    with pytest.raises(ValueError, match="разными"):
        load_gif_entries(path)  # ответ совпал с неправильным вариантом


def test_question_text_depends_on_kind_and_media() -> None:
    assert question_text(entry("a", media="gif")) == "Кто на этой GIF?"
    assert question_text(entry("a", media="image")) == "Кто на этой картинке?"
    assert question_text(entry("a", kind="meme")) == "Что это за мем или тренд?"


# ---------- Отбор GIF без подсказки ----------


def test_hidden_words_default_to_answer_and_name_parts() -> None:
    words = hidden_words(entry("x", answer="Caitlin Clark"))
    assert {"Caitlin Clark", "Caitlin", "Clark"} <= set(words)
    assert hidden_words(entry("x", kind="meme", answer="Chill Guy")) == ["Chill Guy"]  # у мема только фраза целиком
    assert hidden_words(entry("x", answer="A", hide=["zzz"])) == ["zzz"]


@pytest.mark.parametrize(
    ("alt", "reveals"),
    [
        ("a woman with the words caitlin clark on the bottom", True),
        ("CLARK scores a three pointer", True),
        ("a basketball player in a yellow jersey shoots a ball", False),  # описание без имени
        ("clarkson is walking", False),  # слово целиком, не часть другого слова
        ("", False),
    ],
)
def test_reveals_answer(alt: str, reveals: bool) -> None:
    assert reveals_answer(cand(alt), ["Caitlin Clark", "Clark"]) is reveals


def test_reveals_handles_punctuation_variants() -> None:
    assert reveals_answer(cand("the number 6-7 on screen"), ["6 7"])
    assert reveals_answer(cand("a text saying six, seven!"), ["six seven"])


def test_choose_rejects_captioned_and_prefers_described_and_official() -> None:
    e = entry("clark", answer="Caitlin Clark")
    captioned = cand("text that says caitlin clark", i=0)
    no_alt = cand("", i=1)
    described = cand("a basketball player celebrates", i=2)
    official = cand("a basketball player dunks", user="wnba", i=3)
    best, rejected = choose_candidate([captioned, no_alt, described, official], e)
    assert best is official and rejected == 1  # описание есть, нет имени, официальный канал
    best, _ = choose_candidate([captioned, no_alt, described], e)
    assert best is described  # описанная GIF лучше неописанной
    best, _ = choose_candidate([captioned, no_alt], e)
    assert best is no_alt  # описания нет: судить нельзя, берём, но в последнюю очередь


def test_choose_returns_none_when_every_gif_reveals_the_answer() -> None:
    e = entry("clark", answer="Caitlin Clark")
    best, rejected = choose_candidate([cand("caitlin clark text"), cand("clark says hi", i=1)], e)
    assert best is None and rejected == 2


def test_choose_for_image_requires_still_frame() -> None:
    e = entry("x", media="image")
    best, _ = choose_candidate([cand(still=False, i=0), cand(still=True, i=1)], e)
    assert best is not None and best.gif_id == "id1"
    assert choose_candidate([cand(still=False)], e)[0] is None


# ---------- Синхронизация ----------


async def test_sync_creates_questions_with_correct_media(session: AsyncSession) -> None:
    service, _ = giphy(payload(item(0, "a player")))
    es = [entry(f"g{i}") for i in range(3)] + [entry(f"i{i}", media="image") for i in range(3)]
    report = await sync_giphy_questions(session, service, es)
    assert (report.active, report.skipped, report.aborted) == (6, 0, False)
    rows = {q.slug: q for q in await session.scalars(select(QuizQuestion).where(QuizQuestion.source == "giphy"))}
    gif, image = rows["g2-g0"], rows["g2-i0"]
    assert gif.media_type == "gif" and gif.image_url.endswith("giphy.gif") and gif.question == "Кто на этой GIF?"
    assert image.media_type == "image" and image.image_url.endswith("giphy_s.gif")  # неподвижный кадр
    assert image.question == "Кто на этой картинке?"
    assert len({gif.correct_answer, *gif.wrong_answers}) == 4


async def test_captioned_gifs_are_skipped_and_clean_one_chosen(session: AsyncSession) -> None:
    results = payload(item(0, "words name p0 on the bottom"), item(1, "a player dunks"))
    service, _ = giphy(results)
    es = [entry(f"p{i}", answer=f"Name p{i}") for i in range(5)]
    await sync_giphy_questions(session, service, es)
    row = await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == "g2-p0"))
    assert row.image_url == "https://media.giphy.com/media/g1/giphy.gif"  # чистая, а не с подписью


async def test_entry_without_clean_gif_is_skipped_not_published(session: AsyncSession) -> None:
    service, _ = giphy(payload(item(0, "name p0 caption")))
    es = [entry(f"p{i}", answer=f"Name p{i}") for i in range(5)]
    report = await sync_giphy_questions(session, service, es)
    assert report.skipped >= 1
    assert await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == "g2-p0")) is None


async def test_pinned_giphy_id_uses_exact_gif(session: AsyncSession) -> None:
    service, client = giphy({"data": item(7, "pinned")})
    es = [entry("p0", giphy_id="abc123")] + entries(4)[1:]
    es[1:] = [entry(f"p{i}") for i in range(1, 5)]
    await sync_giphy_questions(session, service, es[:1] + [entry(f"p{i}") for i in range(1, 5)])
    assert client.calls[0]["url"].endswith("/gifs/abc123")  # поиск не использовался
    row = await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == "g2-p0"))
    assert row.image_url.endswith("g7/giphy.gif")


async def test_second_sync_does_not_call_giphy_again(session: AsyncSession) -> None:
    service, _ = giphy(payload(item(0, "x")))
    await sync_giphy_questions(session, service, entries(5))
    service2, client2 = giphy(payload(item(0, "x")))
    report = await sync_giphy_questions(session, service2, entries(5))
    assert client2.calls == [] and report.fetched == 0 and report.active == 5


async def test_changed_media_triggers_refetch(session: AsyncSession) -> None:
    service, _ = giphy(payload(item(0, "x")))
    await sync_giphy_questions(session, service, entries(5))
    service2, client2 = giphy(payload(item(0, "x")))
    es = [entry("p0", media="image")] + entries(5)[1:]
    await sync_giphy_questions(session, service2, es)
    assert len(client2.calls) == 1
    assert (await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == "g2-p0"))).media_type == "image"


async def test_old_version_questions_are_deactivated(session: AsyncSession) -> None:
    session.add(QuizQuestion(slug="gif-old", source="giphy", category="nba", question="?", correct_answer="a",
                             wrong_answers=["b", "c", "d"], explanation="e", image_url="https://x"))
    service, _ = giphy(payload(item(0, "x")))
    await sync_giphy_questions(session, service, entries(5))
    assert not (await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == "gif-old"))).is_active


async def test_giphy_down_aborts_quickly_and_keeps_existing(session: AsyncSession) -> None:
    ok_service, _ = giphy(payload(item(0, "x")))
    await sync_giphy_questions(session, ok_service, entries(5))
    down, client = giphy(ApiUnavailableError("down"))
    report = await sync_giphy_questions(session, down, entries(8))
    assert report.aborted and len(client.calls) == 1
    assert len(list(await session.scalars(select(QuizQuestion).where(QuizQuestion.is_active)))) == 5


async def test_disabled_giphy_does_nothing_and_retire_source(session: AsyncSession) -> None:
    report = await sync_giphy_questions(session, GiphyService(None, TTLCache(), None), entries(5))
    assert report.active == 0 and not report.aborted
    session.add(QuizQuestion(slug="imgflip-1", source="imgflip", category="internet", question="?",
                             correct_answer="a", wrong_answers=["b", "c", "d"], explanation="e"))
    await seed_questions(session)
    await retire_source(session, "imgflip")
    rows = {q.slug: q for q in await session.scalars(select(QuizQuestion))}
    assert rows["imgflip-1"].is_active is False
    assert sum(1 for q in rows.values() if q.source == "local" and q.is_active) == 32


# ---------- В игре ----------


def png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 50, 50)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
async def media_player(bot, telegram, session_factory: async_sessionmaker[AsyncSession], make_dispatcher) -> Player:
    """Категория nba целиком из GIF-вопросов: 4 с анимацией и 4 с обычной картинкой."""
    service, client = giphy(payload(item(0, "a player")))
    client.image_bytes = png_bytes()
    async with session_factory() as s:
        await seed_questions(s)
        es = [entry(f"g{i}", media="gif") for i in range(4)] + [entry(f"i{i}", media="image") for i in range(4)]
        await sync_giphy_questions(s, service, es)
        await s.execute(QuizQuestion.__table__.update().where(QuizQuestion.source == "local").values(is_active=False))
        await s.commit()
    return make_player(bot, telegram, session_factory, make_dispatcher(giphy=service))


async def test_gif_and_image_questions_use_different_message_types(media_player: Player) -> None:
    p = media_player
    seen_animation = seen_photo = False
    for _ in range(3):
        await p.start_game("nba")
        for _ in range(5):
            question = await p.question()
            if question.media_type == "gif":
                seen_animation = True
                assert p.telegram.of_type(SendAnimation)[-1].animation == question.image_url
            else:
                seen_photo = True
                photo = p.telegram.of_type(SendPhoto)[-1].photo
                assert isinstance(photo, BufferedInputFile)  # картинка отправлена файлом, а не ссылкой
                assert Image.open(io.BytesIO(photo.data)).format == "JPEG"
            await p.answer(correct=True, photo=True)
            await p.next(photo=True)
    assert seen_animation and seen_photo


async def test_answer_edits_caption_and_keeps_picture_for_images(media_player: Player) -> None:
    p = media_player
    await p.start_game("nba")
    question = await p.question()
    deletes = len(p.telegram.of_type(DeleteMessage))
    await p.answer(correct=True, photo=True)
    assert p.telegram.of_type(EditMessageCaption) and len(p.telegram.of_type(DeleteMessage)) == deletes
    assert "Верно" in p.last_text() and question.explanation[:15] in p.last_text()


async def test_failed_image_download_falls_back_and_game_goes_on(
    bot, telegram, session_factory, make_dispatcher
) -> None:
    service, client = giphy(payload(item(0, "x")))
    async with session_factory() as s:
        await sync_giphy_questions(s, service, [entry(f"i{i}", media="image") for i in range(5)])
        await s.commit()
    client.image_bytes = ApiUnavailableError("down")  # картинку скачать не удаётся
    p = make_player(bot, telegram, session_factory, make_dispatcher(giphy=service))
    await p.start_game("nba")
    assert (await p.question()).media_type == "image"
    await p.answer(correct=True, photo=True)  # игра продолжается
    assert "Верно" in p.last_text()
