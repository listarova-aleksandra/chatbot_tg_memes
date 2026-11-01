"""Регрессии, найденные при финальном аудите. Каждый тест раньше воспроизводил ошибку."""

import asyncio
import logging

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendAnimation, SendMessage, SendPhoto
from aiogram.types import BufferedInputFile
from sqlalchemy import func, select, update

from app.bot.callbacks import MemeCB, MenuCB, QuizCB
from app.bot.states.meme import MemeStates
from app.core.cache import TTLCache
from app.core.logging import RedactSecretsFilter
from app.database.models import Meme, QuizQuestion, QuizSession, User, UserAchievement
from app.database.seed import seed_questions
from app.services import meme_renderer as mr
from app.services.achievements import seed_achievements
from app.services.giphy_service import GiphyService
from tests.conftest import FakeApiClient
from tests.test_quiz_flow import Player
from tests.updates import callback_update

TOKEN = "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456"


@pytest.fixture
async def player(bot, telegram, make_dispatcher, session_factory) -> Player:
    async with session_factory() as s:
        await seed_questions(s)
        await seed_achievements(s)
        await s.commit()
    return Player(make_dispatcher(), bot, telegram, session_factory)


async def press_together(p: Player, *datas: str, photo: bool = False) -> None:
    """Несколько нажатий, пришедших одновременно (двойной тап)."""
    updates = [callback_update(d, p._next_id(), photo=photo) for d in datas]
    await asyncio.gather(*(p.dp.feed_raw_update(p.bot, u) for u in updates))


def generic_errors(p: Player) -> list[str]:
    return [c.text for c in p.telegram.of_type(AnswerCallbackQuery) if c.text and "пошло не так" in c.text]


# ---------- Двойные нажатия: апдейты одного пользователя идут по очереди ----------


async def test_double_tap_on_category_starts_one_game(player: Player) -> None:
    await player.send("/start")
    await player.press(MenuCB(action="play").pack())
    category = QuizCB(action="cat", value="nba").pack()
    await press_together(player, category, category)
    async with player.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(QuizSession)) == 1


async def test_double_tap_on_next_shows_one_question(player: Player) -> None:
    async with player.session_factory() as s:
        await s.execute(update(QuizQuestion).values(image_url="https://media.giphy.com/x/giphy.gif", media_type="gif"))
        await s.commit()
    await player.start_game()
    await player.answer(correct=True, photo=True)
    data = await player.data()
    before = len(player.telegram.of_type(SendAnimation))
    next_button = QuizCB(action="next", q=data["index"]).pack()
    await press_together(player, next_button, next_button, photo=True)
    shown = player.telegram.of_type(SendAnimation)[before:]
    assert len(shown) == 1  # одно сообщение с вопросом, его варианты совпадают с сохранёнными в FSM
    assert (await player.data())["index"] == data["index"] + 1


async def test_double_tap_on_result_shows_one_result(player: Player) -> None:
    await player.start_game()
    for i in range(5):
        await player.answer(correct=True)
        if i < 4:
            await player.next()
    data = await player.data()
    next_button = QuizCB(action="next", q=data["index"]).pack()
    await press_together(player, next_button, next_button)
    results = [c for c in player.telegram.of_type(EditMessageText) if "Игра окончена" in (c.text or "")]
    assert len(results) == 1


# ---------- Сбой «украшения» не откатывает результат игры ----------


@pytest.mark.parametrize(
    "error",
    [
        lambda m: TelegramForbiddenError(method=m, message="Forbidden: bot can't initiate conversation with a user"),
        lambda m: TelegramNetworkError(method=m, message="connection reset"),
    ],
)
async def test_failed_reaction_gif_keeps_game_result(bot, telegram, make_dispatcher, session_factory, error) -> None:
    async with session_factory() as s:
        await seed_questions(s)
        await seed_achievements(s)
        await s.commit()
    payload = {"data": [{"id": "1", "images": {"downsized": {"url": "https://media.giphy.com/a/giphy.gif"}}}]}
    giphy = GiphyService(FakeApiClient(payload), TTLCache(), "key")  # type: ignore[arg-type]
    p = Player(make_dispatcher(giphy=giphy), bot, telegram, session_factory)
    original = telegram.make_request

    async def make_request(b, method, timeout=None):
        if isinstance(method, SendAnimation):
            raise error(method)
        return await original(b, method, timeout)

    telegram.make_request = make_request
    await p.start_game()
    for _ in range(5):
        await p.answer(correct=True)
        await p.next()
    async with session_factory() as s:
        game = (await s.execute(select(QuizSession))).scalar_one()
        user = (await s.execute(select(User))).scalar_one()
        achievements = await s.scalar(select(func.count()).select_from(UserAchievement))
    assert game.finished_at is not None and game.correct_count == 5
    assert user.xp == game.xp_earned > 0 and achievements >= 2
    assert not generic_errors(p)


# ---------- Кнопки старого превью мема ----------


@pytest.mark.skipif(mr.find_font_path() is None, reason="нет системного шрифта с кириллицей")
async def test_publish_under_old_preview_does_not_publish_the_new_meme(player: Player) -> None:
    for top in ("MEME A", "MEME B"):
        await player.send("/meme")
        await player.press(MemeCB(action="tpl", value="local-dark").pack())  # запасной шаблон
        await player.send(top)
        await player.send("-")
    previews = [c for c in player.telegram.of_type(SendPhoto) if isinstance(c.photo, BufferedInputFile)]
    first_id = player.telegram.message_ids[id(previews[0])]
    await player.press(MemeCB(action="publish").pack(), photo=True, message_id=first_id)
    assert "неактуален" in player.telegram.of_type(AnswerCallbackQuery)[-1].text
    async with player.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Meme)) == 0
    assert await player.state() == MemeStates.preview.state  # текущее превью B по-прежнему можно опубликовать


# ---------- Команды посреди создания мема ----------


@pytest.mark.parametrize("command", ["/top", "/community", "/achievements", "/settings", "/profile", "/daily"])
async def test_menu_command_during_meme_flow_runs_and_exits_flow(player: Player, command: str) -> None:
    await player.send("/meme")
    await player.press(MemeCB(action="photo").pack())
    assert await player.state() == MemeStates.waiting_photo.state
    await player.send(command)
    assert await player.state() is None
    last = player.telegram.calls[-1]
    text = getattr(last, "text", None) or getattr(last, "caption", None) or ""
    assert "Пришли" not in text and "фото" not in text.split("\n")[0].lower()


async def test_cancel_and_unknown_command_during_meme_flow(player: Player) -> None:
    await player.send("/meme")
    await player.press(MemeCB(action="photo").pack())
    await player.send("/whatever")  # неизвестная команда: остаёмся в сценарии с подсказкой
    assert await player.state() == MemeStates.waiting_photo.state
    await player.send("/cancel")
    assert await player.state() is None
    assert player.telegram.of_type(SendMessage)[-1].text.startswith("Отменено")


# ---------- Подделанные callback_data ----------


async def test_superscript_digit_in_callback_is_rejected_quietly(player: Player) -> None:
    await player.start_game()
    await player.press(QuizCB(action="ans", value="²", q=0).pack())
    await player.press(MemeCB(action="publish_saved", value="²").pack())
    await player.press(MemeCB(action="publish_saved", value=str(2**40)).pack())
    assert not generic_errors(player)


# ---------- Секрет в трассировке ----------


def test_token_is_hidden_in_traceback_text() -> None:
    try:
        raise RuntimeError(f"502 Bad Gateway, url='https://api.telegram.org/file/bot{TOKEN}/photos/1.jpg'")
    except RuntimeError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "ошибка %s", ("x",), exc_info=__import__("sys").exc_info())
    RedactSecretsFilter().filter(record)
    output = logging.Formatter("%(message)s").format(record)
    assert TOKEN not in output and "<bot-token>" in output
