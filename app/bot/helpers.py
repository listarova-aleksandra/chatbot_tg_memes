"""Общий помощник для показа «экранов».

Вместо десятков новых сообщений мы редактируем текущее: при нажатии на inline-кнопку
сообщение превращается в следующий экран. Для команд (/menu, /profile) отправляется
новое сообщение. Для вопросов с картинкой есть нюансы: Telegram не умеет превращать
текстовое сообщение в фото (и наоборот), поэтому там старое сообщение удаляется,
а новое отправляется.
"""

import logging
from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)



async def _send(
    message: Message, text: str, kb: InlineKeyboardMarkup | None, photo: str | None
) -> None:
    """Отправляет новое сообщение. Если фото не загрузилось, отправляет текст без него."""
    if photo:
        try:
            await message.answer_photo(photo, caption=text, reply_markup=kb)
            return
        except TelegramBadRequest as error:
            logger.warning("Не удалось отправить фото: %s", error)
            # Картинку не показать: даём ссылку, чтобы её можно было открыть самому.
            text = f"🖼 (картинка не загрузилась: {escape(photo)})\n\n{text}"
    await message.answer(text, reply_markup=kb)


async def show_screen(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    photo: str | None = None,
    keep_photo: bool = False,
) -> None:
    """Показывает экран.

    photo:      URL картинки, если экран должен быть с картинкой
    keep_photo: если текущее сообщение фото, а новый экран без фото, то заменить только
                подпись, а картинку оставить (так выглядит экран с объяснением ответа)
    """
    if isinstance(event, Message):
        await _send(event, text, reply_markup, photo)
        return

    message = event.message
    if not isinstance(message, Message):
        # Сообщение слишком старое и недоступно боту: пишем в личку заново.
        await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup)
        await event.answer()
        return

    has_photo = bool(message.photo)
    if photo is None and not has_photo:
        await _edit(message, text, reply_markup, caption=False)
    elif photo is None and has_photo and keep_photo:
        await _edit(message, text, reply_markup, caption=True)
    else:
        # Нужна смена типа сообщения (текст ↔ фото) или новая картинка: удаляем и шлём заново.
        try:
            await message.delete()
        except TelegramBadRequest:
            logger.debug("Старое сообщение не удалось удалить (возможно, слишком старое)")
        await _send(message, text, reply_markup, photo)

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
