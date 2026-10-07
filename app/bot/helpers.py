"""Общий помощник для показа «экранов».

Вместо десятков новых сообщений мы редактируем текущее: при нажатии на inline-кнопку
сообщение с меню превращается в следующий экран. Для команд (/menu, /profile)
отправляется новое сообщение.
"""

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


async def show_screen(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if isinstance(event, Message):
        await event.answer(text, reply_markup=reply_markup)
        return

    # Ниже: нажатие на inline-кнопку.
    message = event.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                pass  # нажали на ту же кнопку: текст не изменился, это не ошибка
            else:
                # Например, прошлое сообщение было фото (текст у него не редактируется).
                logger.debug("edit_text не удался (%s), отправляю новое сообщение", error)
                await message.answer(text, reply_markup=reply_markup)
    else:
        # Сообщение слишком старое и недоступно боту: пишем в личку заново.
        await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup)

    # Обязательно «отвечаем» на callback, иначе у кнопки крутится часики.
    await event.answer()
