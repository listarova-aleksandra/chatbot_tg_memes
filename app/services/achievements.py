"""Достижения: каталог, расчёт прогресса и выдача.

Идея, которую легко объяснить: каждое достижение = «метрика» + «цель».
Метрика это число из статистики игрока (правильных ответов в категории, созданных мемов...),
цель это порог. Достигнута цель, и достижение выдано. Новое достижение добавляется одной
записью в каталог ACHIEVEMENTS, остальной код менять не нужно.

Выдача идемпотентна: у таблицы user_achievements составной первичный ключ
(user_id, achievement_id), поэтому одно достижение нельзя получить дважды, даже если проверка
сработает два раза одновременно.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Achievement,
    Meme,
    QuizAttempt,
    QuizQuestion,
    QuizSession,
    User,
    UserAchievement,
)
from app.services.xp import QUESTIONS_PER_GAME

logger = logging.getLogger(__name__)

WIN_THRESHOLD_PERCENT = 60  # «победа в игре»: 3 из 5 правильных и больше


@dataclass(frozen=True)
class UserStats:
    """Всё, что нужно для расчёта достижений. Собирается из БД одним вызовом."""

    wins: int = 0  # завершённых игр с результатом 60% и выше
    perfect_games: int = 0  # игр 5/5
    best_streak: int = 0  # лучшая серия правильных ответов подряд
    correct_basketball: int = 0  # правильных ответов в MNBA и WNBA
    correct_hiphop: int = 0
    correct_postirony: int = 0
    memes_created: int = 0
    best_meme_rating: int = 0  # рейтинг лучшего опубликованного мема


@dataclass(frozen=True)
class AchievementDef:
    code: str
    icon: str
    name: str
    description: str  # что нужно сделать
    target: int  # порог
    metric: Callable[[UserStats], int]  # какая цифра из статистики сравнивается с порогом
    unit: str  # единица для прогресса: «ответов», «мемов»...


ACHIEVEMENTS: tuple[AchievementDef, ...] = (
    AchievementDef("first_win", "🏆", "First Win", "Выиграй первую игру: 3 из 5 правильных и больше",
                   1, lambda s: s.wins, "побед"),
    AchievementDef("perfect_game", "🎯", "Без права на ошибку", "Пройди игру на 5 из 5",
                   1, lambda s: s.perfect_games, "идеальных игр"),
    AchievementDef("big_brain", "🧠", "Big Brain", "Ответь правильно 20 раз подряд",
                   20, lambda s: s.best_streak, "в серии"),
    AchievementDef("hooper", "🏀", "Hooper", "Дай 10 правильных ответов в MNBA и WNBA",
                   10, lambda s: s.correct_basketball, "ответов"),
    AchievementDef("rap_archivist", "🎤", "Rap Archivist", "Дай 10 правильных ответов в Hip-Hop",
                   10, lambda s: s.correct_hiphop, "ответов"),
    AchievementDef("postirony", "💀", "Постирония", "Дай 10 правильных ответов в «Постирония и брейнрот»",
                   10, lambda s: s.correct_postirony, "ответов"),
    AchievementDef("meme_lord", "🔥", "Meme Lord", "Создай 10 мемов",
                   10, lambda s: s.memes_created, "мемов"),
    AchievementDef("crowd_favorite", "⭐", "Народный любимец", "Набери рейтинг +5 у своего мема",
                   5, lambda s: s.best_meme_rating, "рейтинг"),
)
BY_CODE = {a.code: a for a in ACHIEVEMENTS}


@dataclass(frozen=True)
class AchievementStatus:
    definition: AchievementDef
    earned_at: datetime | None
    progress: int  # текущее значение метрики, не больше цели

    @property
    def earned(self) -> bool:
        return self.earned_at is not None


async def seed_achievements(session: AsyncSession) -> None:
    """Приводит таблицу achievements в соответствие с каталогом (идемпотентно, по code)."""
    existing = {a.code: a for a in await session.scalars(select(Achievement))}
    for definition in ACHIEVEMENTS:
        row = existing.get(definition.code) or Achievement(code=definition.code)
        if row.id is None:
            session.add(row)
        row.name = definition.name
        row.description = definition.description
        row.icon = definition.icon
    await session.flush()


class AchievementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def gather_stats(self, user: User) -> UserStats:
        scalar = self.session.scalar

        finished = (QuizSession.user_id == user.id, QuizSession.finished_at.is_not(None))
        wins = await scalar(
            select(func.count()).select_from(QuizSession).where(
                *finished,
                QuizSession.total_questions > 0,
                QuizSession.correct_count * 100 >= QuizSession.total_questions * WIN_THRESHOLD_PERCENT,
            )
        )
        perfect = await scalar(
            select(func.count()).select_from(QuizSession).where(
                *finished,
                QuizSession.total_questions == QUESTIONS_PER_GAME,
                QuizSession.correct_count == QuizSession.total_questions,
            )
        )

        # Правильные ответы по категориям: JOIN ответов с вопросами и группировка.
        rows = await self.session.execute(
            select(QuizQuestion.category, func.count())
            .select_from(QuizAttempt)
            .join(QuizQuestion, QuizQuestion.id == QuizAttempt.question_id)
            .where(QuizAttempt.user_id == user.id, QuizAttempt.is_correct.is_(True))
            .group_by(QuizQuestion.category)
        )
        correct = dict(rows.all())

        memes_created = await scalar(select(func.count()).select_from(Meme).where(Meme.author_id == user.id))
        best_rating = await scalar(
            select(func.max(Meme.rating)).where(Meme.author_id == user.id, Meme.is_published.is_(True))
        )
        return UserStats(
            wins=wins or 0,
            perfect_games=perfect or 0,
            best_streak=user.best_streak,
            correct_basketball=correct.get("nba", 0) + correct.get("wnba", 0),
            correct_hiphop=correct.get("hiphop", 0),
            correct_postirony=correct.get("postirony", 0),
            memes_created=memes_created or 0,
            best_meme_rating=max(best_rating or 0, 0),
        )

    async def _earned(self, user: User) -> dict[str, datetime]:
        rows = await self.session.execute(
            select(Achievement.code, UserAchievement.earned_at)
            .join(UserAchievement, UserAchievement.achievement_id == Achievement.id)
            .where(UserAchievement.user_id == user.id)
        )
        return {code: earned_at for code, earned_at in rows.all()}

    async def check_and_award(self, user: User) -> list[AchievementDef]:
        """Выдаёт все достижения, условия которых выполнены. Возвращает только НОВЫЕ."""
        stats = await self.gather_stats(user)
        earned = await self._earned(user)
        candidates = [a for a in ACHIEVEMENTS if a.code not in earned and a.metric(stats) >= a.target]
        if not candidates:
            return []

        ids = dict((await self.session.execute(select(Achievement.code, Achievement.id))).all())
        awarded: list[AchievementDef] = []
        for definition in candidates:
            achievement_id = ids.get(definition.code)
            if achievement_id is None:
                logger.warning("Достижение %s не загружено в БД (seed_achievements)", definition.code)
                continue
            try:
                # SAVEPOINT: если параллельная проверка уже выдала достижение, откатится только эта вставка.
                async with self.session.begin_nested():
                    self.session.add(UserAchievement(user_id=user.id, achievement_id=achievement_id))
                    await self.session.flush()
            except IntegrityError:
                continue
            awarded.append(definition)
            logger.info("Выдано достижение %s пользователю id=%s", definition.code, user.id)
        return awarded

    async def overview(self, user: User) -> list[AchievementStatus]:
        """Все достижения с отметкой «получено» и прогрессом (для экрана «Достижения»)."""
        stats = await self.gather_stats(user)
        earned = await self._earned(user)
        return [
            AchievementStatus(a, earned.get(a.code), min(a.metric(stats), a.target)) for a in ACHIEVEMENTS
        ]
