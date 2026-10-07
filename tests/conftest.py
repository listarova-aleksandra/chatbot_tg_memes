"""Общие фикстуры тестов.

БД в тестах: SQLite в памяти (быстро, без установки PostgreSQL). Модели не используют
специфику PostgreSQL, поэтому поведение совпадает.

Telegram в тестах: вместо реальной сети подставляется FakeTelegramSession,
которая запоминает все вызовы Bot API (sendMessage, editMessageText...).
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.database.base import Base
from app.database.models import *  # noqa: F401,F403  (регистрирует модели в metadata)
from app.database.session import create_session_factory


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # StaticPool: все соединения делят одну in-memory БД (иначе каждая была бы пустой).
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session


@pytest.fixture
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None, bot_token="123456:TEST", database_url="sqlite+aiosqlite://"
    )


class FakeTelegramSession(BaseSession):
    """Заменяет HTTP-сессию aiogram: ничего не отправляет, а записывает вызовы."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Any] = []

    async def close(self) -> None:
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError
        yield b""

    async def make_request(self, bot: Bot, method: Any, timeout: Any = None) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            return Message(
                message_id=len(self.calls) + 1000,
                date=datetime.now(),
                chat=Chat(id=1, type="private"),
                text=getattr(method, "text", None),
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    def of_type(self, method_type: type) -> list[Any]:
        return [call for call in self.calls if isinstance(call, method_type)]


@pytest.fixture
def telegram() -> FakeTelegramSession:
    return FakeTelegramSession()


@pytest.fixture
def bot(telegram: FakeTelegramSession) -> Bot:
    return Bot(token="123456:TEST", session=telegram)


@pytest.fixture
def make_dispatcher(settings: Settings, session_factory: async_sessionmaker[AsyncSession]):
    """Собирает свежий Dispatcher.

    Router в aiogram можно подключить только к одному родителю, а наши роутеры
    создаются при импорте модулей. Поэтому перед каждой сборкой модули перезагружаются.
    """
    import importlib

    from app.bot import dispatcher as dispatcher_module
    from app.bot.handlers import common, errors, profile
    from app.bot.handlers import settings as settings_handlers

    def factory():
        for module in (errors, common, profile, settings_handlers):
            importlib.reload(module)
        return dispatcher_module.build_dispatcher(settings, session_factory)

    return factory
