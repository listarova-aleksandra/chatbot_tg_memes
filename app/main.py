"""Точка входа: `python -m app.main`.

Здесь собираются все части приложения («composition root»):
настройки → логирование → БД → бот → диспетчер → запуск long polling.

Long polling: бот сам периодически спрашивает у Telegram «есть новые сообщения?».
Для учебного проекта это проще webhook: не нужен публичный адрес и HTTPS.
"""

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from sqlalchemy import text

from app.bot.commands import set_bot_commands
from app.bot.dispatcher import build_dispatcher
from app.core.cache import TTLCache
from app.core.config import get_settings
from app.core.http import ApiClient
from app.core.logging import setup_logging
from app.database.migrations_check import migration_problem
from app.database.seed import seed_questions
from app.database.session import create_engine, create_session_factory
from app.services.giphy_service import GiphyService
from app.services.imgflip_service import ImgflipService
from app.services.quiz_sources import load_gif_entries, retire_source, sync_giphy_questions
from app.services.reddit_service import RedditService

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Запуск МемоМастера...")

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    # Проверяем БД сразу при старте: лучше упасть здесь с понятным сообщением,
    # чем при первом сообщении пользователя.
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception(
            "Не удалось подключиться к БД. Запущен ли PostgreSQL и верен ли DATABASE_URL?"
        )
        await engine.dispose()
        sys.exit(1)

    # Забытая миграция: сообщаем понятно и сразу, а не длинной ошибкой SQL посреди запуска.
    problem = await migration_problem(engine)
    if problem:
        logger.error("%s Выполните: python -m alembic upgrade head, затем запустите бота снова.", problem)
        await engine.dispose()
        sys.exit(1)

    # Вопросы викторины хранятся в app/content/questions.json и загружаются в БД при старте
    # (повторная загрузка безопасна: вопрос определяется по slug).
    try:
        async with session_factory() as session:
            await seed_questions(session)
            await session.commit()
    except Exception:
        logger.exception("Не удалось загрузить вопросы. Применены ли миграции (alembic upgrade head)?")
        await engine.dispose()
        sys.exit(1)

    # Внешние API: один HTTP-клиент (timeout + retry) и один общий кэш на все сервисы.
    api_client = ApiClient(
        timeout=settings.http_timeout_seconds, max_attempts=settings.http_max_retries
    )
    cache = TTLCache()
    imgflip = ImgflipService(api_client, cache)
    giphy = GiphyService(
        api_client, cache, settings.giphy_api_key.get_secret_value() if settings.giphy_api_key else None
    )
    reddit = RedditService(
        api_client,
        cache,
        settings.reddit_client_id.get_secret_value() if settings.reddit_client_id else None,
        settings.reddit_client_secret.get_secret_value() if settings.reddit_client_secret else None,
        settings.reddit_user_agent,
    )
    logger.info("Giphy: %s, Reddit: %s", "вкл" if giphy.enabled else "выкл (нет ключа)",
                "вкл" if reddit.enabled else "выкл (нет ключей)")

    # Вопросы «Кто на этой GIF?» и «Что это за мем?» строятся по GIF из Giphy. GIF ищется
    # только для новых записей, поэтому при обычных перезапусках в Giphy бот не ходит.
    # Если Giphy недоступен, викторина работает на уже сохранённых и локальных вопросах.
    try:
        async with session_factory() as session:
            await retire_source(session, "imgflip")  # устаревшие вопросы по шаблонам Imgflip
            report = await sync_giphy_questions(session, giphy, load_gif_entries())
            await session.commit()
        if giphy.enabled:
            logger.info(
                "GIF-вопросы: активных %s, запрошено в Giphy %s, пропущено %s%s",
                report.active, report.fetched, report.skipped,
                " (Giphy не ответил, синхронизация прервана)" if report.aborted else "",
            )
    except Exception:
        logger.exception("Не удалось подготовить GIF-вопросы, продолжаем без них")

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = build_dispatcher(settings, session_factory, giphy=giphy, reddit=reddit, imgflip=imgflip)

    try:
        await set_bot_commands(bot)
        logger.info("Бот запущен, ожидаю сообщения")
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        logger.error("Telegram отклонил токен. Проверьте BOT_TOKEN в .env")
        sys.exit(1)
    except TelegramNetworkError as error:
        logger.error("Нет связи с Telegram: %s", error)
        sys.exit(1)
    finally:
        logger.info("Остановка бота...")
        await bot.session.close()
        await api_client.close()
        await engine.dispose()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
