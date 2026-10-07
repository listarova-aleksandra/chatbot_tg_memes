"""Тесты бизнес-логики викторины (без Telegram)."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import QuizAttempt, QuizQuestion, QuizSession, User
from app.database.seed import seed_questions
from app.services.quiz_service import NoQuestionsError, QuizService, StaleAnswerError
from app.services.user_service import UserService
from app.services.xp import XP_PERFECT_GAME, answer_xp


# ---------- Правила XP ----------


@pytest.mark.parametrize(
    ("difficulty", "streak", "expected"),
    [(1, 1, 10), (2, 1, 12), (3, 1, 15), (1, 2, 10), (1, 3, 15), (3, 5, 20)],
)
def test_answer_xp(difficulty: int, streak: int, expected: int) -> None:
    assert answer_xp(difficulty, streak) == expected


# ---------- Фикстуры ----------


@pytest.fixture
async def user(session: AsyncSession) -> User:
    await seed_questions(session)
    created, _ = await UserService(session).get_or_register(1, "anya", "Аня")
    return created


async def _question(session: AsyncSession, qid: int) -> QuizQuestion:
    return await QuizService(session).get_question(qid)


async def _answer(service: QuizService, user: User, game, session: AsyncSession, i: int, correct: bool):
    question = await _question(session, game.question_ids[i])
    wrong = question.wrong_answers[0]
    return await service.submit_answer(
        user, game.session_id, question, question.correct_answer if correct else wrong
    )


# ---------- Подбор вопросов ----------


async def test_start_game_picks_five_distinct_questions_of_category(
    session: AsyncSession, user: User
) -> None:
    game = await QuizService(session).start_game(user, "nba")
    assert len(game.question_ids) == 5 and len(set(game.question_ids)) == 5
    rows = await session.scalars(select(QuizQuestion).where(QuizQuestion.id.in_(game.question_ids)))
    assert {q.category for q in rows} == {"nba"}


async def test_mixed_category_uses_all_categories(session: AsyncSession, user: User) -> None:
    seen: set[str] = set()
    service = QuizService(session)
    for _ in range(10):
        game = await service.start_game(user, "mixed")
        rows = await session.scalars(select(QuizQuestion).where(QuizQuestion.id.in_(game.question_ids)))
        seen |= {q.category for q in rows}
    assert len(seen) >= 4


async def test_unseen_questions_are_preferred(session: AsyncSession, user: User) -> None:
    """В NBA 7 вопросов: после игры из 5 вторая должна начаться с двух ещё не виденных."""
    service = QuizService(session)
    first = await service.start_game(user, "nba")
    for i in range(5):
        await _answer(service, user, first, session, i, correct=True)
    second = await service.start_game(user, "nba")
    unseen = set(await session.scalars(select(QuizQuestion.id).where(QuizQuestion.category == "nba"))) - set(
        first.question_ids
    )
    assert unseen <= set(second.question_ids)  # оба новых вопроса попали в игру


async def test_no_questions_raises(session: AsyncSession) -> None:
    user, _ = await UserService(session).get_or_register(1, None, "X")  # вопросы не загружены
    with pytest.raises(NoQuestionsError):
        await QuizService(session).start_game(user, "nba")


async def test_inactive_questions_are_not_used(session: AsyncSession, user: User) -> None:
    for q in await session.scalars(select(QuizQuestion).where(QuizQuestion.category == "wnba")):
        q.is_active = False
    await session.flush()
    with pytest.raises(NoQuestionsError):
        await QuizService(session).start_game(user, "wnba")


def test_options_contain_all_four_answers() -> None:
    q = QuizQuestion(correct_answer="a", wrong_answers=["b", "c", "d"])
    options = QuizService.make_options(q)
    assert sorted(options) == ["a", "b", "c", "d"]


# ---------- Проверка ответа, XP, серия ----------


async def test_correct_answer_gives_xp_and_updates_stats(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    question = await _question(session, game.question_ids[0])
    result = await _answer(service, user, game, session, 0, correct=True)

    assert result.is_correct and result.xp_awarded == answer_xp(question.difficulty, 1)
    assert user.xp == result.xp_awarded and user.correct_streak == 1
    quiz = await session.get(QuizSession, game.session_id)
    assert quiz.correct_count == 1 and quiz.xp_earned == result.xp_awarded


async def test_wrong_answer_gives_no_xp_and_resets_streak(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    await _answer(service, user, game, session, 0, correct=True)
    await _answer(service, user, game, session, 1, correct=True)
    xp_before = user.xp
    result = await _answer(service, user, game, session, 2, correct=False)

    assert not result.is_correct and result.xp_awarded == 0
    assert user.xp == xp_before
    assert user.correct_streak == 0 and user.best_streak == 2


async def test_streak_bonus_from_third_correct_answer(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    results = [await _answer(service, user, game, session, i, correct=True) for i in range(3)]
    assert [r.streak for r in results] == [1, 2, 3]
    # Бонус серии (+5) есть только у третьего ответа.
    q = [await _question(session, qid) for qid in game.question_ids[:3]]
    assert [r.xp_awarded for r in results] == [
        answer_xp(q[0].difficulty, 1), answer_xp(q[1].difficulty, 2), answer_xp(q[2].difficulty, 3)
    ]
    assert results[2].xp_awarded >= 15


async def test_answering_same_question_twice_does_not_double_xp(
    session: AsyncSession, user: User
) -> None:
    """Двойное нажатие: второй ответ отклоняется, XP не удваивается."""
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    await _answer(service, user, game, session, 0, correct=True)
    xp_after_first = user.xp
    with pytest.raises(StaleAnswerError):
        await _answer(service, user, game, session, 0, correct=True)
    assert user.xp == xp_after_first
    count = await session.scalar(select(func.count()).select_from(QuizAttempt))
    assert count == 1


async def test_cannot_answer_in_someone_elses_or_unknown_game(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    other, _ = await UserService(session).get_or_register(2, None, "Y")
    game = await service.start_game(user, "nba")
    question = await _question(session, game.question_ids[0])
    with pytest.raises(StaleAnswerError):
        await service.submit_answer(other, game.session_id, question, "x")  # чужая игра
    with pytest.raises(StaleAnswerError):
        await service.submit_answer(user, 99999, question, "x")  # такой игры нет


async def test_unknown_answer_text_counts_as_wrong(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    question = await _question(session, game.question_ids[0])
    result = await service.submit_answer(user, game.session_id, question, "что-то странное")
    assert result.is_correct is False and result.xp_awarded == 0


# ---------- Конец игры ----------


async def test_perfect_game_gets_bonus_and_levels_up(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    for i in range(5):
        await _answer(service, user, game, session, i, correct=True)
    xp_before_finish = user.xp
    result = await service.finish_game(user, game.session_id, level_before=1)

    assert (result.correct, result.total, result.accuracy) == (5, 5, 100.0)
    assert result.perfect_bonus == XP_PERFECT_GAME
    assert user.xp == xp_before_finish + XP_PERFECT_GAME
    assert result.xp_earned == user.xp  # игрок был с нуля, поэтому всё XP получено в этой игре
    assert result.level_change.leveled_up and result.level_change.new_level == user.level >= 2


async def test_imperfect_game_has_no_bonus(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    for i in range(5):
        await _answer(service, user, game, session, i, correct=i != 4)
    result = await service.finish_game(user, game.session_id, level_before=1)
    assert result.perfect_bonus == 0 and result.correct == 4 and result.accuracy == 80.0


async def test_finishing_twice_is_rejected(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    game = await service.start_game(user, "nba")
    await service.finish_game(user, game.session_id, 1)
    with pytest.raises(StaleAnswerError):
        await service.finish_game(user, game.session_id, 1)


async def test_history_lists_only_finished_games_latest_first(session: AsyncSession, user: User) -> None:
    service = QuizService(session)
    first = await service.start_game(user, "nba")
    await _answer(service, user, first, session, 0, correct=True)
    await service.finish_game(user, first.session_id, 1)
    await service.start_game(user, "hiphop")  # не завершена: в историю не попадёт
    second = await service.start_game(user, "rnb")
    await service.finish_game(user, second.session_id, 1)

    history = await service.recent_games(user)
    assert [h.category for h in history] == ["rnb", "nba"]
    assert history[1].correct == 1 and history[1].total == 5

    stats = await UserService(session).get_profile_stats(user)
    assert stats.games == 2 and stats.total_answers == 1
