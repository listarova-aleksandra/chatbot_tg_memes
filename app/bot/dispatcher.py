"""Сборка диспетчера: хранилище FSM, middleware и роутеры.

Вынесено из main.py, чтобы тесты могли собрать такой же диспетчер без запуска бота.
"""

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import common, errors, profile, quiz, settings
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.user import UserMiddleware
from app.core.config import Settings


def build_dispatcher(
    app_settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> Dispatcher:
    # MemoryStorage: состояния FSM лежат в памяти процесса. При перезапуске бота
    # незаконченные игры сбрасываются. Для учебного проекта это приемлемо.
    #
    # Именованные аргументы Dispatcher(...) становятся доступны в хендлерах
    # по имени параметра. Так в aiogram устроено внедрение зависимостей (DI).
    dp = Dispatcher(storage=MemoryStorage(), settings=app_settings)

    # Порядок важен: сначала сессия БД, потом пользователь (ему нужна сессия).
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(UserMiddleware())

    dp.include_router(errors.router)
    dp.include_router(common.router)
    dp.include_router(profile.router)
    dp.include_router(quiz.router)
    dp.include_router(settings.router)
    return dp
