"""Работа с пользователями: регистрация, статистика профиля, настройки.

Сервис не знает про Telegram (не импортирует aiogram): ему передаются обычные
значения. Поэтому его можно тестировать без бота. Запросы к БД пишутся здесь же
средствами SQLAlchemy ORM (без ручного SQL).
"""

import logging
from dataclasses import dataclass

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Meme,
    QuizAttempt,
    QuizCategory,
    QuizSession,
    User,
    UserAchievement,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProfileStats:
    games: int  # завершённых игр
    correct_answers: int
    total_answers: int
    memes_created: int
    memes_rating: int  # суммарный рейтинг опубликованных мемов

    @property
    def accuracy(self) -> float:
        """Доля правильных ответов в процентах (0, если ответов ещё не было)."""
        if self.total_answers == 0:
            return 0.0
        return self.correct_answers / self.total_answers * 100


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_register(
        self, telegram_id: int, username: str | None, first_name: str
    ) -> tuple[User, bool]:
        """Находит пользователя по Telegram-ID или создаёт нового.

        Возвращает (пользователь, создан_ли_только_что).
        """
        user = await self._find(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, username=username, first_name=first_name[:128])
            try:
                # begin_nested() = SAVEPOINT: если вставка упадёт, откатится только она,
                # а не вся транзакция. Нужно, когда один человек шлёт два апдейта
                # одновременно: оба не нашли пользователя, и второй INSERT нарушит UNIQUE.
                async with self.session.begin_nested():
                    self.session.add(user)
                    await self.session.flush()
            except IntegrityError:
                user = await self._find(telegram_id)
                assert user is not None
                return user, False
            logger.info("Зарегистрирован новый пользователь id=%s", user.id)
            return user, True

        # Имя и ник в Telegram можно поменять, поэтому подтягиваем актуальные.
        if user.username != username:
            user.username = username
        if user.first_name != first_name[:128]:
            user.first_name = first_name[:128]
        return user, False

    async def _find(self, telegram_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def get_profile_stats(self, user: User) -> ProfileStats:
        games = await self.session.scalar(
            select(func.count())
            .select_from(QuizSession)
            .where(QuizSession.user_id == user.id, QuizSession.finished_at.is_not(None))
        )
        # Один запрос даёт и число ответов, и число правильных:
        # CASE превращает is_correct в 1/0, и SUM считает единицы.
        answers = await self.session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((QuizAttempt.is_correct, 1), else_=0)), 0),
            ).where(QuizAttempt.user_id == user.id)
        )
        total_answers, correct_answers = answers.one()
        memes_created = await self.session.scalar(
            select(func.count()).select_from(Meme).where(Meme.author_id == user.id)
        )
        memes_rating = await self.session.scalar(
            select(func.coalesce(func.sum(Meme.rating), 0)).where(
                Meme.author_id == user.id, Meme.is_published.is_(True)
            )
        )
        return ProfileStats(
            games=games or 0,
            correct_answers=int(correct_answers),
            total_answers=total_answers,
            memes_created=memes_created or 0,
            memes_rating=int(memes_rating or 0),
        )

    # ---------- Настройки ----------

    async def toggle_notifications(self, user: User) -> bool:
        user.notifications_enabled = not user.notifications_enabled
        return user.notifications_enabled

    async def set_default_category(self, user: User, category: str | None) -> bool:
        """Сохраняет категорию по умолчанию. None = «не выбрана».

        Возвращает False, если категория некорректна (значение пришло из callback_data,
        а такие данные всегда проверяем).
        """
        if category is not None and category not in {c.value for c in QuizCategory}:
            return False
        user.default_category = category
        return True

    async def reset_progress(self, user: User) -> None:
        """Сброс прогресса: XP, уровень, серии, история игр и достижения.

        Мемы и голоса остаются: это контент сообщества, а не личный прогресс.
        """
        # Сначала дочерние таблицы (ответы), затем игры: так не нарушаются внешние ключи.
        await self.session.execute(delete(QuizAttempt).where(QuizAttempt.user_id == user.id))
        await self.session.execute(delete(QuizSession).where(QuizSession.user_id == user.id))
        await self.session.execute(
            delete(UserAchievement).where(UserAchievement.user_id == user.id)
        )
        user.xp = 0
        user.level = 1
        user.correct_streak = 0
        user.best_streak = 0
        logger.info("Прогресс пользователя id=%s сброшен", user.id)
