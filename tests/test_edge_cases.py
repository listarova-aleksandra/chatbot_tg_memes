"""Крайние случаи: мусорные апдейты, секреты в логах, список команд, нестандартные пользователи."""

import logging
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.commands import COMMANDS, set_bot_commands
from app.core.logging import RedactSecretsFilter, setup_logging
from app.database.models import User
from app.services.names import display_name
from tests.conftest import FakeTelegramSession
from tests.updates import CHAT, FROM, callback_update, message_update

TOKEN = "123456789:AAE-abcdefghijklmnopqrstuvwxyz0123456"


def errors_logged(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---------- Секреты в логах ----------


def redact(message: str, *args: Any) -> str:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, message, args, None)
    RedactSecretsFilter().filter(record)
    return record.getMessage()


def test_bot_token_is_hidden_in_log_message_and_in_args() -> None:
    assert TOKEN not in redact(f"запрос https://api.telegram.org/bot{TOKEN}/getMe")
    assert TOKEN not in redact("токен %s", TOKEN)
    assert "<bot-token>" in redact("токен %s", TOKEN)


def test_api_key_is_hidden_in_url() -> None:
    text = redact("GET https://api.giphy.com/v1/gifs/search?q=nba&api_key=SECRETKEY123&limit=5")
    assert "SECRETKEY123" not in text and "api_key=<hidden>" in text and "limit=5" in text
    assert "SECRETKEY" not in redact("API_KEY=SECRETKEY")


def test_ordinary_messages_are_not_changed() -> None:
    assert redact("Игра id=%s: категория=%s", 21, "nba") == "Игра id=21: категория=nba"
    assert redact("время 12:30:45 и число 1234567") == "время 12:30:45 и число 1234567"


def test_setup_logging_installs_filter_and_level() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        setup_logging("warning")
        assert root.level == logging.WARNING
        assert any(isinstance(f, RedactSecretsFilter) for h in root.handlers for f in h.filters)
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


# ---------- Имена ----------


def test_display_name_prefers_username() -> None:
    assert display_name("anya", "Аня") == "@anya"
    assert display_name(None, "Аня") == "Аня"
    assert display_name("", "Аня") == "Аня"  # пустой ник считается отсутствующим


# ---------- Меню команд ----------


def test_command_menu_is_valid_for_telegram() -> None:
    names = [c.command for c in COMMANDS]
    assert len(names) == len(set(names)) and len(names) <= 100
    for command in COMMANDS:
        assert command.command == command.command.lower() and command.command.isascii()
        assert 1 <= len(command.description) <= 256


async def test_every_menu_command_has_a_working_handler(
    bot: Bot, telegram: FakeTelegramSession, make_dispatcher: Any, caplog: pytest.LogCaptureFixture
) -> None:
    dp = make_dispatcher()
    for number, command in enumerate(COMMANDS, start=1):
        before = len(telegram.calls)
        await dp.feed_raw_update(bot, message_update(f"/{command.command}", update_id=number * 2))
        assert len(telegram.calls) > before, f"/{command.command} ничего не ответила"
        await dp.feed_raw_update(bot, message_update("/cancel", update_id=number * 2 + 1))
    assert not errors_logged(caplog)


async def test_failed_set_commands_does_not_crash(bot: Bot, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("telegram недоступен")

    monkeypatch.setattr(bot, "set_my_commands", boom)
    await set_bot_commands(bot)  # не бросает исключение


# ---------- Мусорные апдейты ----------


async def test_free_text_and_unknown_command_outside_any_flow_are_ignored(
    bot: Bot, telegram: FakeTelegramSession, make_dispatcher: Any, caplog: pytest.LogCaptureFixture
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, message_update("просто текст", update_id=1))
    await dp.feed_raw_update(bot, message_update("/nonexistent", update_id=2))
    assert not errors_logged(caplog)


@pytest.mark.parametrize(
    "data",
    ["", "garbage", "quiz:", "quiz:ans:x:y:z:w", "m:" + "x" * 60, "💥", "menu:main:extra:parts"],
)
async def test_tampered_callback_data_does_not_crash(
    bot: Bot, telegram: FakeTelegramSession, make_dispatcher: Any, caplog: pytest.LogCaptureFixture, data: str
) -> None:
    dp = make_dispatcher()
    await dp.feed_raw_update(bot, callback_update(data, update_id=1))
    assert not errors_logged(caplog)


async def test_update_from_a_bot_user_is_not_registered(
    bot: Bot, make_dispatcher: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    dp = make_dispatcher()
    update = message_update("/start")
    update["message"]["from"] = {**FROM, "id": 777, "is_bot": True}
    await dp.feed_raw_update(bot, update)
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 0


async def test_update_without_sender_does_not_crash(
    bot: Bot, make_dispatcher: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Сообщение без автора (например, из канала): пользователя нет, бот не падает."""
    dp = make_dispatcher()
    update = message_update("/start")
    del update["message"]["from"]
    await dp.feed_raw_update(bot, update)
    assert not [r for r in errors_logged(caplog) if "KeyError" in r.getMessage()]


async def test_user_without_username_is_shown_by_first_name(
    bot: Bot, telegram: FakeTelegramSession, make_dispatcher: Any
) -> None:
    dp = make_dispatcher()
    update = message_update("/start")
    update["message"]["from"] = {k: v for k, v in FROM.items() if k != "username"}
    await dp.feed_raw_update(bot, update)
    assert "Аня" in telegram.of_type(SendMessage)[-1].text


async def test_button_under_inaccessible_old_message_still_gets_an_answer(
    bot: Bot, telegram: FakeTelegramSession, make_dispatcher: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Telegram иногда присылает кнопку под слишком старым сообщением (без текста и даты):
    бот отвечает новым сообщением, а не падает."""
    dp = make_dispatcher()
    update = callback_update("menu:main", update_id=5)
    update["callback_query"]["message"] = {"message_id": 7, "date": 0, "chat": CHAT}
    await dp.feed_raw_update(bot, update)
    assert not errors_logged(caplog)
    assert telegram.of_type(SendMessage) or telegram.of_type(EditMessageText)
    assert telegram.of_type(AnswerCallbackQuery)
