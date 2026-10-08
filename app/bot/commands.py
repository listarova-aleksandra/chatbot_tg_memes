"""Список команд для кнопки «Меню» в Telegram."""

import logging

from aiogram import Bot
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Начать"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="play", description="Играть в викторину"),
    BotCommand(command="meme", description="Создать мем"),
    BotCommand(command="community", description="Лента сообщества"),
    BotCommand(command="daily", description="Мем дня"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="history", description="История игр"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="cancel", description="Отменить действие"),
]


async def set_bot_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS)
    except Exception as error:
        # Меню команд это украшение, бот должен работать и без него.
        logger.warning("Не удалось установить список команд: %s", type(error).__name__)
