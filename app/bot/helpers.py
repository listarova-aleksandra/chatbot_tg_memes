"""Общий помощник для показа «экранов».

Вместо десятков новых сообщений мы редактируем текущее: при нажатии на inline-кнопку
сообщение превращается в следующий экран. Для команд (/menu, /profile) отправляется
новое сообщение. Для вопросов с картинкой есть нюансы: Telegram не умеет превращать
текстовое сообщение в фото (и наоборот), поэтому там старое сообщение удаляется,
а новое отправляется.
"""

import logging
from html import escape
from urllib.parse import urlparse

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)



def is_animation(url: str) -> bool:
    """GIF (в том числе с Giphy) отправляется как анимация, остальное как фото."""
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".gif") or (parsed.hostname or "").endswith("giphy.com")


async def _send(
    message: Message, text: str, kb: InlineKeyboardMarkup | None, media: str | None
) -> None:
    """Отправляет новое сообщение. Если картинка или GIF не загрузились, отправляет текст без них."""
    if media:
        try:
            if is_animation(media):
                await message.answer_animation(media, caption=text, reply_markup=kb)
            else:
                await message.answer_photo(media, caption=text, reply_markup=kb)
            return
        except TelegramBadRequest as error:
            logger.warning("Не удалось отправить медиа: %s", error)
            # Показать нечего: даём ссылку, чтобы её можно было открыть самому.
            text = f"🖼 (картинка не загрузилась: {escape(media)})\n\n{text}"
    await message.answer(text, reply_markup=kb)


async def show_screen(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    media: str | None = None,
    keep_media: bool = False,
) -> None:
    """Показывает экран.

    media:      URL картинки или GIF, если экран должен быть с ними
    keep_media: если текущее сообщение с картинкой или GIF, а новый экран без них, то
                заменить только подпись, а картинку оставить (экран с объяснением ответа)
    """
    if isinstance(event, Message):
        await _send(event, text, reply_markup, media)
        return

    message = event.message
    if not isinstance(message, Message):
        # Сообщение слишком старое и недоступно боту: пишем в личку заново.
        await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup)
        await event.answer()
        return

    has_media = bool(message.photo or message.animation)
    if media is None and not has_media:
        await _edit(message, text, reply_markup, caption=False)
    elif media is None and has_media and keep_media:
        await _edit(message, text, reply_markup, caption=True)
    else:
        # Нужна смена типа сообщения (текст ↔ картинка/GIF) или новое медиа: удаляем и шлём заново.
        try:
            await message.delete()
        except TelegramBadRequest:
            logger.debug("Старое сообщение не удалось удалить (возможно, слишком старое)")
        await _send(message, text, reply_markup, media)

    # Обязательно «отвечаем» на callback, иначе у кнопки крутится часики.
    await event.answer()


async def _edit(
    message: Message, text: str, kb: InlineKeyboardMarkup | None, *, caption: bool
) -> None:
    try:
        if caption:
            await message.edit_caption(caption=text, reply_markup=kb)
        else:
            await message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return  # нажали на ту же кнопку: текст не изменился, это не ошибка
        logger.debug("Редактирование не удалось (%s), отправляю новое сообщение", error)
        await message.answer(text, reply_markup=kb)
