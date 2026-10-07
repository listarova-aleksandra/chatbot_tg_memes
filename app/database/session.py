"""Подключение к БД: engine и фабрика сессий.

Engine это пул соединений с PostgreSQL. Создаётся один раз на всё приложение.
Session это «рабочая единица»: в ней мы читаем и меняем объекты, а потом
делаем commit (или rollback). Сессия создаётся отдельно на каждый апдейт Telegram.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_engine(database_url: str) -> AsyncEngine:
    # pool_pre_ping: перед выдачей соединения проверяет, что оно живо
    # (БД могли перезапустить, пока бот работал).
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: после commit объекты остаются читаемыми.
    # В async-режиме обращение к «протухшему» объекту вызвало бы скрытый запрос к БД
    # и ошибку, поэтому отключаем это поведение.
    return async_sessionmaker(engine, expire_on_commit=False)
