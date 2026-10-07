"""Middleware: одна сессия БД на один апдейт Telegram.

Middleware это «обёртка» вокруг обработчика: код до `await handler(...)` выполняется
до хендлера, код после него после. Здесь мы:
  1. открываем сессию БД и кладём её в `data` (хендлеры получают её параметром `session`);
  2. если хендлер отработал без ошибок, делаем commit;
  3. если было исключение, делаем rollback и пробрасываем ошибку дальше
     (её поймает глобальный обработчик ошибок).
Благодаря этому изменения в БД атомарны: либо сохраняются все, либо ни одно.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
