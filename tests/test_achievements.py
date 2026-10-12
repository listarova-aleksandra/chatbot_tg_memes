"""Достижения: каталог, статистика, выдача, прогресс."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import utcnow
from app.database.models import (
    Achievement, Meme, QuizAttempt, QuizQuestion, QuizSession, User, UserAchievement,
)
from app.services.achievements import (
    ACHIEVEMENTS, BY_CODE, AchievementService, UserStats, seed_achievements,
)
from app.services.user_service import UserService


@pytest.fixture
async def user(session: AsyncSession) -> User:
    await seed_achievements(session)
    created, _ = await UserService(session).get_or_register(1, "anya", "Аня")
    await session.flush()
    return created


async def add_game(session: AsyncSession, user: User, correct: int, total: int = 5, finished: bool = True,
                   category: str = "nba") -> None:
    """Игра: `correct` правильных ответов из `total`; каждый ответ на свой вопрос нужной категории."""
    quiz = QuizSession(user_id=user.id, category=category, total_questions=total, correct_count=correct,
                       finished_at=utcnow() if finished else None)
    session.add(quiz)
    await session.flush()
    for number in range(total):
        question = QuizQuestion(slug=f"q{quiz.id}-{number}", question="?", category=category, correct_answer="a",
                                wrong_answers=["b", "c", "d"], explanation="e")
        session.add(question)
        await session.flush()
        session.add(QuizAttempt(session_id=quiz.id, user_id=user.id, question_id=question.id,
                                selected_answer="a", is_correct=number < correct))
    await session.flush()


async def codes(session: AsyncSession, user: User) -> set[str]:
    return {a.code for a in await AchievementService(session).check_and_award(user)}


# ---------- Каталог ----------


def test_catalog_has_eight_unique_achievements_with_valid_targets() -> None:
    assert len(ACHIEVEMENTS) == 8 and len(BY_CODE) == 8
    assert all(a.target > 0 and a.icon and a.name and a.description for a in ACHIEVEMENTS)


def test_metrics_read_the_right_stat() -> None:
    stats = UserStats(wins=1, perfect_games=2, best_streak=3, correct_basketball=4, correct_hiphop=5,
                      correct_postirony=6, memes_created=7, best_meme_rating=8)
    assert [BY_CODE[c].metric(stats) for c in
            ("first_win", "perfect_game", "big_brain", "hooper", "rap_archivist", "postirony", "meme_lord",
             "crowd_favorite")] == [1, 2, 3, 4, 5, 6, 7, 8]


async def test_seed_is_idempotent_and_updates_texts(session: AsyncSession) -> None:
    await seed_achievements(session)
    row = (await session.scalars(select(Achievement).where(Achievement.code == "meme_lord"))).one()
    row.name = "старое название"
    await seed_achievements(session)
    await seed_achievements(session)
    assert await session.scalar(select(func.count()).select_from(Achievement)) == 8
    assert (await session.get(Achievement, row.id)).name == "Meme Lord"


# ---------- Выдача ----------


async def test_new_user_has_no_achievements(session: AsyncSession, user: User) -> None:
    assert await codes(session, user) == set()


@pytest.mark.parametrize(("correct", "wins"), [(2, False), (3, True), (5, True)])
async def test_first_win_threshold_is_three_of_five(session: AsyncSession, user: User, correct: int, wins: bool) -> None:
    await add_game(session, user, correct)
    assert ("first_win" in await codes(session, user)) is wins


async def test_unfinished_game_is_not_a_win(session: AsyncSession, user: User) -> None:
    await add_game(session, user, 5, finished=False)
    assert await codes(session, user) == set()


async def test_perfect_game_needs_full_game_of_five(session: AsyncSession, user: User) -> None:
    await add_game(session, user, 3, total=3)  # 3/3, но игра короткая (в категории мало вопросов)
    assert "perfect_game" not in await codes(session, user)
    await add_game(session, user, 4)
    assert "perfect_game" not in await codes(session, user)
    await add_game(session, user, 5)
    assert "perfect_game" in await codes(session, user)


async def test_awarded_once_and_only_new_are_returned(session: AsyncSession, user: User) -> None:
    await add_game(session, user, 5)
    first = await codes(session, user)
    assert first == {"first_win", "perfect_game"}
    assert await codes(session, user) == set()  # повторная проверка ничего не выдаёт
    assert await session.scalar(select(func.count()).select_from(UserAchievement)) == 2


async def test_category_achievements_count_correct_answers_only(session: AsyncSession, user: User) -> None:
    await add_game(session, user, 5, category="hiphop")
    assert "rap_archivist" not in await codes(session, user)  # 5 из 10
    await add_game(session, user, 4, category="hiphop")
    assert "rap_archivist" not in await codes(session, user)  # 9
    await add_game(session, user, 1, category="hiphop")
    assert "rap_archivist" in await codes(session, user)  # 10 правильных (неправильные не считаются)


async def test_hooper_counts_both_mnba_and_wnba(session: AsyncSession, user: User) -> None:
    await add_game(session, user, 5, category="nba")
    await add_game(session, user, 4, category="wnba")
    assert "hooper" not in await codes(session, user)  # 9
    await add_game(session, user, 1, category="wnba")
    assert "hooper" in await codes(session, user)


async def test_postirony_and_other_categories_do_not_mix(session: AsyncSession, user: User) -> None:
    await add_game(session, user, 5, category="postirony")
    await add_game(session, user, 5, category="internet")  # другая категория
    assert "postirony" not in await codes(session, user)
    await add_game(session, user, 5, category="postirony")
    assert "postirony" in await codes(session, user)


async def test_big_brain_uses_best_streak(session: AsyncSession, user: User) -> None:
    user.best_streak = 19
    assert "big_brain" not in await codes(session, user)
    user.best_streak = 20
    assert "big_brain" in await codes(session, user)


async def test_meme_lord_counts_created_memes_boundary(session: AsyncSession, user: User) -> None:
    for i in range(9):
        session.add(Meme(author_id=user.id, image_file_id=f"m{i}"))
    await session.flush()
    assert "meme_lord" not in await codes(session, user)
    session.add(Meme(author_id=user.id, image_file_id="m9"))
    assert "meme_lord" in await codes(session, user)


async def test_crowd_favorite_needs_published_meme_with_rating_five(session: AsyncSession, user: User) -> None:
    session.add(Meme(author_id=user.id, image_file_id="draft", is_published=False, rating=50))  # не опубликован
    session.add(Meme(author_id=user.id, image_file_id="low", is_published=True, rating=4))
    assert "crowd_favorite" not in await codes(session, user)
    session.add(Meme(author_id=user.id, image_file_id="top", is_published=True, rating=5))
    assert "crowd_favorite" in await codes(session, user)


async def test_missing_catalog_rows_do_not_crash(session: AsyncSession) -> None:
    """Если seed_achievements не вызывался, проверка не падает, а просто ничего не выдаёт."""
    created, _ = await UserService(session).get_or_register(5, None, "X")
    await add_game(session, created, 5)
    assert await AchievementService(session).check_and_award(created) == []


async def test_parallel_award_does_not_duplicate_or_report_twice(session: AsyncSession, user: User) -> None:
    """Параллельная проверка уже выдала достижение: вставка отклоняется, и повторно оно не сообщается."""
    await add_game(session, user, 5)

    class StaleService(AchievementService):
        async def _earned(self, u: User) -> dict:
            return {}  # «устаревшее чтение»: сервис не знает, что достижения уже выданы

    await AchievementService(session).check_and_award(user)
    assert await StaleService(session).check_and_award(user) == []
    assert await session.scalar(select(func.count()).select_from(UserAchievement)) == 2


# ---------- Прогресс ----------


async def test_overview_shows_progress_capped_at_target(session: AsyncSession, user: User) -> None:
    await add_game(session, user, 5, category="nba")
    for i in range(3):
        session.add(Meme(author_id=user.id, image_file_id=f"m{i}"))
    await session.flush()
    service = AchievementService(session)
    await service.check_and_award(user)
    status = {s.definition.code: s for s in await service.overview(user)}
    assert status["first_win"].earned and status["first_win"].earned_at is not None
    assert status["hooper"].progress == 5 and not status["hooper"].earned
    assert status["meme_lord"].progress == 3
    assert status["big_brain"].progress == 0
    user.best_streak = 99
    assert {s.definition.code: s for s in await service.overview(user)}["big_brain"].progress == 20  # не больше цели


async def test_reset_progress_removes_achievements_but_memes_can_reearn_meme_lord(session: AsyncSession, user: User) -> None:
    for i in range(10):
        session.add(Meme(author_id=user.id, image_file_id=f"m{i}"))
    await add_game(session, user, 5)
    service = AchievementService(session)
    assert await codes(session, user) == {"first_win", "perfect_game", "meme_lord"}

    await UserService(session).reset_progress(user)
    assert await session.scalar(select(func.count()).select_from(UserAchievement)) == 0
    # Мемы при сбросе остаются, поэтому «Meme Lord» вернётся при следующей проверке, а игровые нет.
    assert await codes(session, user) == {"meme_lord"}
