"""Тесты UserService: регистрация, статистика, настройки, сброс."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.base import utcnow
from app.database.models import (
    Achievement,
    Meme,
    QuizAttempt,
    QuizQuestion,
    QuizSession,
    User,
    UserAchievement,
)
from app.services.user_service import ProfileStats, UserService


async def test_register_new_user(session: AsyncSession) -> None:
    user, created = await UserService(session).get_or_register(100, "anya", "Аня")
    assert created is True
    assert (user.telegram_id, user.username, user.first_name) == (100, "anya", "Аня")


async def test_second_call_returns_same_user_and_updates_profile(session: AsyncSession) -> None:
    service = UserService(session)
    first, _ = await service.get_or_register(100, "anya", "Аня")
    second, created = await service.get_or_register(100, "new_nick", "Анна")
    assert created is False
    assert second.id == first.id
    assert (second.username, second.first_name) == ("new_nick", "Анна")


async def test_new_user_has_defaults(session: AsyncSession) -> None:
    user, _ = await UserService(session).get_or_register(1, None, "X")
    await session.flush()
    assert (user.xp, user.level, user.correct_streak) == (0, 1, 0)
    assert user.notifications_enabled is True
    assert user.default_category is None


async def test_registration_race_does_not_create_duplicate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Гонка: два апдейта от нового человека, оба не нашли его в БД, и второй INSERT
    нарушает UNIQUE. Сервис должен не упасть, а вернуть уже созданного пользователя.

    Реальную конкуренцию на SQLite не воспроизвести, поэтому имитируем её:
    первый поиск «не находит» пользователя, хотя он уже есть в БД.
    """
    async with session_factory() as s:
        s.add(User(telegram_id=777, username="x", first_name="X"))
        await s.commit()

    class StaleReadService(UserService):
        first_lookup = True

        async def _find(self, telegram_id: int) -> User | None:
            if self.first_lookup:
                self.first_lookup = False
                return None  # «устаревшее чтение»
            return await super()._find(telegram_id)

    async with session_factory() as s:
        user, created = await StaleReadService(s).get_or_register(777, "x", "X")
        await s.commit()
        assert created is False
        assert user.telegram_id == 777
        assert await s.scalar(select(func.count()).select_from(User)) == 1


async def test_profile_stats_for_new_user_are_zero(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.get_or_register(1, None, "X")
    stats = await service.get_profile_stats(user)
    assert stats == ProfileStats(0, 0, 0, 0, 0)
    assert stats.accuracy == 0.0  # без деления на ноль


async def _add_game(session: AsyncSession, user: User, answers: list[bool]) -> None:
    """Создаёт завершённую игру: на каждый ответ свой вопрос (на один вопрос в игре один ответ)."""
    quiz = QuizSession(user_id=user.id, category="nba", total_questions=len(answers))
    session.add(quiz)
    await session.flush()
    for number, ok in enumerate(answers):
        question = QuizQuestion(
            slug=f"q{quiz.id}-{number}", question="?", category="nba",
            correct_answer="a", wrong_answers=["b", "c", "d"], explanation="e",
        )
        session.add(question)
        await session.flush()
        session.add(
            QuizAttempt(
                session_id=quiz.id, user_id=user.id, question_id=question.id,
                selected_answer="a", is_correct=ok,
            )
        )
    quiz.finished_at = utcnow()
    await session.flush()


async def test_profile_stats_count_games_answers_and_memes(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.get_or_register(1, None, "X")
    await _add_game(session, user, [True, True, False, True])
    session.add_all(
        [
            Meme(author_id=user.id, image_file_id="a", is_published=True, rating=3),
            Meme(author_id=user.id, image_file_id="b", is_published=True, rating=-1),
            Meme(author_id=user.id, image_file_id="c", is_published=False, rating=50),
        ]
    )
    await session.flush()

    stats = await service.get_profile_stats(user)
    assert stats.games == 1
    assert (stats.correct_answers, stats.total_answers) == (3, 4)
    assert stats.accuracy == 75.0
    assert stats.memes_created == 3
    assert stats.memes_rating == 2  # непубликованный мем в рейтинг не входит


async def test_toggle_notifications(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.get_or_register(1, None, "X")
    assert await service.toggle_notifications(user) is False
    assert await service.toggle_notifications(user) is True


async def test_set_default_category_validates_input(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.get_or_register(1, None, "X")
    assert await service.set_default_category(user, "nba") is True
    assert user.default_category == "nba"
    assert await service.set_default_category(user, "'; DROP TABLE users;--") is False
    assert user.default_category == "nba"  # некорректное значение не применилось
    assert await service.set_default_category(user, None) is True
    assert user.default_category is None


async def test_reset_progress_clears_progress_but_keeps_memes(session: AsyncSession) -> None:
    service = UserService(session)
    user, _ = await service.get_or_register(1, None, "X")
    other, _ = await service.get_or_register(2, None, "Y")
    await _add_game(session, user, [True, False])
    await _add_game(session, other, [True])
    achievement = Achievement(code="first_win", name="n", description="d")
    session.add_all([achievement, Meme(author_id=user.id, image_file_id="m")])
    await session.flush()
    session.add_all(
        [
            UserAchievement(user_id=user.id, achievement_id=achievement.id),
            UserAchievement(user_id=other.id, achievement_id=achievement.id),
        ]
    )
    user.xp, user.level, user.correct_streak, user.best_streak = 300, 3, 4, 9
    await session.flush()

    await service.reset_progress(user)
    await session.flush()

    assert (user.xp, user.level, user.correct_streak, user.best_streak) == (0, 1, 0, 0)
    count = lambda model, col: session.scalar(  # noqa: E731
        select(func.count()).select_from(model).where(col == user.id)
    )
    assert await count(QuizSession, QuizSession.user_id) == 0
    assert await count(QuizAttempt, QuizAttempt.user_id) == 0
    assert await count(UserAchievement, UserAchievement.user_id) == 0
    assert await count(Meme, Meme.author_id) == 1  # мемы остаются
    # Данные другого пользователя не тронуты.
    assert await session.scalar(
        select(func.count()).select_from(QuizSession).where(QuizSession.user_id == other.id)
    ) == 1
