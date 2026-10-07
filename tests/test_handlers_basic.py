"""Сквозные тесты Этапа 3: настоящий Dispatcher, поддельный Telegram, SQLite.

Мы подаём в диспетчер «сырые» апдейты (как их присылает Telegram) и смотрим,
что бот ответил и что записалось в БД.
"""

from typing import Any

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import MenuCB, SettingsCB
from app.core.config import Settings
from app.database.models import User
from tests.conftest import FakeTelegramSession

FROM = {"id": 42, "is_bot": False, "first_name": "Аня", "username": "anya"}
CHAT = {"id": 42, "type": "private"}


def message_update(text: str, update_id: int = 1) -> dict[str, Any]:
    update: dict[str, Any] = {
        "update_id": update_id,
        "message": {"message_id": 1, "date": 0, "chat": CHAT, "from": FROM, "text": text},
    }
    if text.startswith("/"):
        length = len(text.split()[0])
        update["message"]["entities"] = [{"type": "bot_command", "offset": 0, "length": length}]
    return update


def callback_update(data: str, update_id: int = 2) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "from": FROM,
            "chat_instance": "ci",
            "data": data,
            "message": {"message_id": 7, "date": 0, "chat": CHAT, "from": {**FROM, "is_bot": True}, "text": "menu"},
        },
    }


async def test_start_registers_user_and_shows_menu(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, message_update("/start"))

    sent = telegram.of_type(SendMessage)
    assert len(sent) == 1
    assert "Добро пожаловать" in sent[0].text and "Аня" in sent[0].text
    buttons = [b.text for row in sent[0].reply_markup.inline_keyboard for b in row]
    assert "🎮 Играть" in buttons and "⚙️ Настройки" in buttons and "👤 Профиль" in buttons

    async with session_factory() as s:
        user = (await s.execute(select(User))).scalar_one()
        assert (user.telegram_id, user.username) == (42, "anya")

    # Повторный /start: тот же пользователь, другое приветствие.
    await dp.feed_raw_update(bot, message_update("/start", update_id=3))
    assert "С возвращением" in telegram.of_type(SendMessage)[-1].text
    async with session_factory() as s:
        assert len((await s.execute(select(User))).scalars().all()) == 1


async def test_profile_command(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, message_update("/profile"))
    text = telegram.of_type(SendMessage)[-1].text
    assert "Уровень: <b>1</b>" in text and "Accuracy: <b>0%</b>" in text


async def test_menu_button_edits_message_instead_of_sending_new(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, callback_update(MenuCB(action="profile").pack()))
    assert len(telegram.of_type(EditMessageText)) == 1
    assert telegram.of_type(SendMessage) == []
    assert len(telegram.of_type(AnswerCallbackQuery)) == 1  # спиннер на кнопке снят


async def test_settings_toggle_notifications_and_category(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, callback_update(SettingsCB(action="notify").pack()))
    await dp.feed_raw_update(
        bot, callback_update(SettingsCB(action="set_category", value="nba").pack(), update_id=3)
    )
    async with session_factory() as s:
        user = (await s.execute(select(User))).scalar_one()
        assert user.notifications_enabled is False
        assert user.default_category == "nba"
    assert "NBA" in telegram.of_type(EditMessageText)[-1].text


async def test_invalid_category_from_callback_is_rejected(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(
        bot, callback_update(SettingsCB(action="set_category", value="hacked").pack())
    )
    async with session_factory() as s:
        assert (await s.execute(select(User))).scalar_one().default_category is None
    assert telegram.of_type(AnswerCallbackQuery)[-1].show_alert is True


async def test_reset_requires_confirmation(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, message_update("/start"))
    async with session_factory() as s:
        user = (await s.execute(select(User))).scalar_one()
        user.xp, user.level = 300, 3
        await s.commit()

    # Шаг 1: «Сбросить прогресс» только спрашивает подтверждение, XP цел.
    await dp.feed_raw_update(bot, callback_update(SettingsCB(action="reset").pack(), update_id=5))
    confirm = telegram.of_type(EditMessageText)[-1]
    labels = [b.text for row in confirm.reply_markup.inline_keyboard for b in row]
    assert labels == ["Да, сбросить", "Отмена"]
    async with session_factory() as s:
        assert (await s.execute(select(User))).scalar_one().xp == 300

    # «Отмена» возвращает в настройки, XP по-прежнему цел.
    await dp.feed_raw_update(bot, callback_update(SettingsCB(action="back").pack(), update_id=6))
    async with session_factory() as s:
        assert (await s.execute(select(User))).scalar_one().xp == 300

    # Шаг 2: подтверждение.
    await dp.feed_raw_update(bot, callback_update(SettingsCB(action="reset_yes").pack(), update_id=7))
    async with session_factory() as s:
        user = (await s.execute(select(User))).scalar_one()
        assert (user.xp, user.level) == (0, 1)


async def test_stub_section_answers_with_alert(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, callback_update(MenuCB(action="play").pack()))
    answer = telegram.of_type(AnswerCallbackQuery)[-1]
    assert answer.show_alert is True and "следующем этапе" in answer.text


async def test_handler_error_is_caught_and_db_rolled_back(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    """Ошибка в хендлере: пользователь получает сообщение, бот не падает, БД откатывается."""
    from aiogram import Router
    from aiogram.filters import Command

    boom = Router()

    @boom.message(Command("boom"))
    async def _boom(message: Any, user: User, session: AsyncSession) -> None:
        user.xp = 999  # изменение должно откатиться
        raise RuntimeError("test failure")

    dp = make_dispatcher()
    dp.include_router(boom)
    await dp.feed_raw_update(bot, message_update("/start"))
    await dp.feed_raw_update(bot, message_update("/boom", update_id=9))

    assert "Что-то пошло не так" in telegram.of_type(SendMessage)[-1].text
    async with session_factory() as s:
        assert (await s.execute(select(User))).scalar_one().xp == 0


async def test_html_in_name_is_escaped(
    bot: Bot, telegram: FakeTelegramSession, settings: Settings,
    session_factory: async_sessionmaker[AsyncSession], make_dispatcher: Any,
) -> None:
    dp = make_dispatcher()
    update = message_update("/start")
    update["message"]["from"] = {**FROM, "first_name": "<b>Хакер</b>"}
    await dp.feed_raw_update(bot, update)
    text = telegram.of_type(SendMessage)[-1].text
    assert "<b>Хакер</b>" not in text and "&lt;b&gt;" in text
