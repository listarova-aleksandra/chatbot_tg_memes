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
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.database.seed import seed_questions
from app.database.session import create_engine, create_session_factory

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

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = build_dispatcher(settings, session_factory)

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
        await engine.dispose()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
