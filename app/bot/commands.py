"""Список команд для кнопки «Меню» в Telegram."""

import logging

from aiogram import Bot
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="cancel", description="Отменить действие"),
]


async def set_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS)
    except Exception:
        # Меню команд это украшение, бот должен работать и без него.
        logger.warning("Не удалось установить список команд", exc_info=True)
