"""Middleware: регистрация пользователя при первом же обращении к боту.

Каждый апдейт от человека проходит здесь: мы находим его в БД (или создаём)
и кладём в `data["user"]`. Хендлерам не нужно думать о регистрации: нажал кнопку
в старом сообщении после сброса БД, и всё равно будешь зарегистрирован.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.services.user_service import UserService


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # event_from_user кладёт aiogram: это автор сообщения или нажавший кнопку.
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        user, created = await UserService(data["session"]).get_or_register(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        data["user"] = user
        data["is_new_user"] = created
        return await handler(event, data)
