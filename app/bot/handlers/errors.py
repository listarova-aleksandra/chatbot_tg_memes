"""Глобальный обработчик ошибок.

Если любой хендлер упал с исключением, оно попадает сюда: мы пишем подробности
в лог, а пользователю отвечаем коротким сообщением. Бот при этом не падает
и продолжает обслуживать остальных.
"""

import logging

from aiogram import Router
from aiogram.types import ErrorEvent

from app.bot import texts

logger = logging.getLogger(__name__)

router = Router(name="errors")


@router.error()
async def on_error(event: ErrorEvent) -> bool:
    update = event.update
    # В лог идут тип ошибки и стек, но не содержимое апдейта (там могут быть личные данные).
    logger.error(
        "Необработанная ошибка в апдейте %s: %r",
        update.update_id,
        event.exception,
        exc_info=event.exception,
    )
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(texts.GENERIC_ERROR, show_alert=True)
        elif update.message is not None:
            await update.message.answer(texts.GENERIC_ERROR)
    except Exception:
        logger.warning("Не удалось сообщить пользователю об ошибке", exc_info=True)
    return True  # ошибка обработана, дальше не пробрасываем
