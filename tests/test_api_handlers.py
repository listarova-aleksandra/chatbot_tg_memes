"""Сквозные тесты: внешние API работают, тормозят и падают, а бот продолжает работать."""

from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import SendAnimation, SendMessage, SendPhoto
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import MenuCB, QuizCB
from app.bot.states.quiz import QuizStates
from app.core.cache import TTLCache
from app.core.exceptions import ApiUnavailableError
from app.database.seed import seed_questions
from app.services.giphy_service import GiphyService
from app.services.reddit_service import RedditService
from tests.conftest import FakeApiClient, FakeTelegramSession
from tests.test_quiz_flow import Player
from tests.test_reddit_service import TOKEN, listing, post

GIF_PAYLOAD = {"data": [{"images": {"downsized": {"url": "https://media.giphy.com/media/a/giphy.gif"}}}]}
GIF_DOWN = "GIF-сервис временно не отвечает. Продолжаем без него."


@pytest.fixture
async def seeded(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as s:
        await seed_questions(s)
        await s.commit()


def make_player(bot: Bot, telegram: FakeTelegramSession, session_factory: Any, dp: Any) -> Player:
    return Player(dp, bot, telegram, session_factory)


async def play_game(player: Player, correct_count: int) -> None:
    await player.start_game("nba")
    for i in range(5):
        await player.answer(correct=i < correct_count)
        await player.next()


def giphy(*responses: Any) -> tuple[GiphyService, FakeApiClient]:
    client = FakeApiClient(*responses)
    return GiphyService(client, TTLCache(), "KEY"), client  # type: ignore[arg-type]


# ---------- Giphy ----------


async def test_good_result_sends_gif_after_result_screen(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service, client = giphy(GIF_PAYLOAD)
    player = make_player(bot, telegram, session_factory, make_dispatcher(giphy=service))
    await play_game(player, correct_count=5)

    gifs = telegram.of_type(SendAnimation)
    assert len(gifs) == 1 and gifs[0].animation == "https://media.giphy.com/media/a/giphy.gif"
    assert "Игра окончена" in player.last_text()  # итог показан, GIF пришла отдельным сообщением
    assert "win" not in str(client.calls[0]["params"]["api_key"])  # ключ не размножается в других полях


async def test_bad_result_also_gets_reaction(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service, client = giphy(GIF_PAYLOAD)
    player = make_player(bot, telegram, session_factory, make_dispatcher(giphy=service))
    await play_game(player, correct_count=1)  # 20%
    assert len(telegram.of_type(SendAnimation)) == 1
    assert client.calls[0]["params"]["q"] == "basketball fail"


async def test_middle_result_gets_no_gif_and_no_api_call(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service, client = giphy(GIF_PAYLOAD)
    player = make_player(bot, telegram, session_factory, make_dispatcher(giphy=service))
    await play_game(player, correct_count=3)  # 60%
    assert telegram.of_type(SendAnimation) == [] and client.calls == []


async def test_giphy_down_bot_says_so_and_game_finishes(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service, _ = giphy(ApiUnavailableError("down"))
    player = make_player(bot, telegram, session_factory, make_dispatcher(giphy=service))
    await play_game(player, correct_count=5)

    assert await player.state() == QuizStates.finished.state  # игра дошла до конца
    assert "Игра окончена" in player.last_text()
    assert telegram.of_type(SendMessage)[-1].text.endswith(GIF_DOWN)
    assert telegram.of_type(SendAnimation) == []
    await player.press(QuizCB(action="again").pack())  # бот продолжает работать
    assert await player.state() == QuizStates.choosing_category.state


async def test_without_giphy_key_game_works_silently(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    player = make_player(bot, telegram, session_factory, make_dispatcher())
    await play_game(player, correct_count=5)
    assert "Игра окончена" in player.last_text()
    assert telegram.of_type(SendAnimation) == []
    assert not any(GIF_DOWN in (m.text or "") for m in telegram.of_type(SendMessage))


async def test_telegram_rejecting_gif_does_not_break_game(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    from aiogram.exceptions import TelegramBadRequest

    original = telegram.make_request

    async def reject_animation(bot_, method, timeout=None):
        if isinstance(method, SendAnimation):
            raise TelegramBadRequest(method=method, message="Bad Request: failed to get HTTP URL content")
        return await original(bot_, method, timeout)

    telegram.make_request = reject_animation  # type: ignore[method-assign]
    service, _ = giphy(GIF_PAYLOAD)
    player = make_player(bot, telegram, session_factory, make_dispatcher(giphy=service))
    await play_game(player, correct_count=5)
    assert await player.state() == QuizStates.finished.state


# ---------- Reddit: «Мем дня» ----------


def reddit(*responses: Any) -> RedditService:
    return RedditService(FakeApiClient(*responses), TTLCache(), "id", "secret", "ua")  # type: ignore[arg-type]


async def test_daily_meme_is_shown_as_photo_with_link(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service = reddit(TOKEN, listing(post(title="Мем <b>дня</b>")), listing(), listing())
    player = make_player(bot, telegram, session_factory, make_dispatcher(reddit=service))
    await player.send("/daily")
    photo = telegram.of_type(SendPhoto)[-1]
    assert photo.photo == "https://i.redd.it/abc.jpg"
    assert "Мем дня" in photo.caption and "&lt;b&gt;" in photo.caption  # заголовок поста экранирован
    buttons = [b for row in photo.reply_markup.inline_keyboard for b in row]
    assert any(b.url and b.url.startswith("https://www.reddit.com/") for b in buttons)


async def test_daily_via_menu_button(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service = reddit(TOKEN, listing(post()), listing(), listing())
    player = make_player(bot, telegram, session_factory, make_dispatcher(reddit=service))
    await player.press(MenuCB(action="daily").pack())
    assert telegram.of_type(SendPhoto)  # сообщение-меню заменено фото с мемом


async def test_reddit_down_shows_fallback_fact_and_quiz_still_works(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service = reddit(ApiUnavailableError("down"))
    player = make_player(bot, telegram, session_factory, make_dispatcher(reddit=service))
    await player.send("/daily")
    text = telegram.of_type(SendMessage)[-1].text
    assert "Reddit временно не отвечает. Продолжаем без него." in text
    assert "💡" in text and "❓" in text  # запасной факт из локальной базы

    await player.start_game("hiphop")  # викторина на локальных вопросах работает
    assert await player.state() == QuizStates.answering.state


async def test_reddit_not_configured_message(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    player = make_player(bot, telegram, session_factory, make_dispatcher())
    await player.send("/daily")
    assert "не настроен" in telegram.of_type(SendMessage)[-1].text


async def test_reddit_no_safe_posts_message(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    service = reddit(TOKEN, listing(post(over_18=True)), listing(), listing())
    player = make_player(bot, telegram, session_factory, make_dispatcher(reddit=service))
    await player.send("/daily")
    assert "безопасные" in telegram.of_type(SendMessage)[-1].text
    assert telegram.of_type(SendPhoto) == []  # NSFW до пользователя не дошёл


async def test_broken_reddit_image_falls_back_to_link(bot, telegram, session_factory, make_dispatcher, seeded) -> None:
    telegram.fail_photos = True
    service = reddit(TOKEN, listing(post()), listing(), listing())
    player = make_player(bot, telegram, session_factory, make_dispatcher(reddit=service))
    await player.send("/daily")
    text = telegram.of_type(SendMessage)[-1].text
    assert "картинка не загрузилась" in text and "https://i.redd.it/abc.jpg" in text and "Мем дня" in text
