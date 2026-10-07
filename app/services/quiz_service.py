"""Логика викторины: подбор вопросов, проверка ответов, XP, итоги, история.

Сервис не знает про Telegram. Он получает значения (пользователь, id игры, выбранный
ответ) и возвращает результаты. Поэтому его проверяют обычными тестами без бота.
"""

import logging
import random
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import utcnow
from app.database.models import QuizAttempt, QuizCategory, QuizQuestion, QuizSession, User
from app.services.gamification import LevelChange, add_xp
from app.services.xp import QUESTIONS_PER_GAME, XP_PERFECT_GAME, answer_xp

logger = logging.getLogger(__name__)


class NoQuestionsError(Exception):
    """В выбранной категории нет активных вопросов."""


class StaleAnswerError(Exception):
    """Игра не найдена или завершена, либо на этот вопрос уже отвечали (двойное нажатие)."""


@dataclass(frozen=True)
class QuizGame:
    session_id: int
    question_ids: list[int]


@dataclass(frozen=True)
class AnswerResult:
    is_correct: bool
    correct_answer: str
    explanation: str
    xp_awarded: int
    streak: int  # серия правильных ответов подряд после этого ответа


@dataclass(frozen=True)
class GameResult:
    category: str
    correct: int
    total: int
    xp_earned: int
    perfect_bonus: int
    level_change: LevelChange

    @property
    def accuracy(self) -> float:
        return self.correct / self.total * 100 if self.total else 0.0


@dataclass(frozen=True)
class GameHistoryItem:
    category: str
    correct: int
    total: int
    xp_earned: int
    started_at: datetime


class QuizService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- Начало игры ----------

    async def start_game(self, user: User, category: str) -> QuizGame:
        """Подбирает вопросы и создаёт запись об игре в БД."""
        questions = await self._pick_questions(user, category)
        if not questions:
            raise NoQuestionsError(category)

        quiz = QuizSession(user_id=user.id, category=category, total_questions=len(questions))
        self.session.add(quiz)
        await self.session.flush()  # flush выполняет INSERT и заполняет quiz.id
        logger.info("Игра id=%s: категория=%s, вопросов=%s", quiz.id, category, len(questions))
        return QuizGame(session_id=quiz.id, question_ids=[q.id for q in questions])

    async def _pick_questions(self, user: User, category: str) -> list[QuizQuestion]:
        """Случайные вопросы, на которые пользователь ещё не отвечал; если их мало, добираем старыми."""
        base = select(QuizQuestion).where(QuizQuestion.is_active.is_(True))
        if category != QuizCategory.MIXED:
            base = base.where(QuizQuestion.category == category)

        seen_ids = select(QuizAttempt.question_id).where(QuizAttempt.user_id == user.id)
        unseen = await self.session.scalars(
            base.where(QuizQuestion.id.not_in(seen_ids))
            .order_by(func.random())
            .limit(QUESTIONS_PER_GAME)
        )
        picked = list(unseen)

        if len(picked) < QUESTIONS_PER_GAME:
            already = [q.id for q in picked]
            rest = await self.session.scalars(
                base.where(QuizQuestion.id.not_in(already))
                .order_by(func.random())
                .limit(QUESTIONS_PER_GAME - len(picked))
            )
            picked.extend(rest)

        random.shuffle(picked)  # новые и старые вопросы перемешиваем между собой
        return picked

    async def get_question(self, question_id: int) -> QuizQuestion:
        question = await self.session.get(QuizQuestion, question_id)
        if question is None:
            raise StaleAnswerError(f"question {question_id} not found")
        return question

    @staticmethod
    def make_options(question: QuizQuestion) -> list[str]:
        """Четыре варианта в случайном порядке (правильный не всегда первый)."""
        options = [question.correct_answer, *question.wrong_answers]
        random.shuffle(options)
        return options

    # ---------- Ответ ----------

    async def submit_answer(
        self, user: User, session_id: int, question: QuizQuestion, selected_answer: str
    ) -> AnswerResult:
        quiz = await self._get_open_game(user, session_id)

        is_correct = selected_answer == question.correct_answer
        streak_after = user.correct_streak + 1 if is_correct else 0
        xp = answer_xp(question.difficulty, streak_after) if is_correct else 0

        attempt = QuizAttempt(
            session_id=quiz.id,
            user_id=user.id,
            question_id=question.id,
            selected_answer=selected_answer,
            is_correct=is_correct,
            xp_awarded=xp,
        )
        try:
            # Сначала записываем ответ. Если на этот вопрос в этой игре уже есть ответ
            # (двойное нажатие), БД отклонит вставку (UNIQUE), и XP начислен не будет.
            async with self.session.begin_nested():
                self.session.add(attempt)
                await self.session.flush()
        except IntegrityError as error:
            raise StaleAnswerError("question already answered") from error

        user.correct_streak = streak_after
        user.best_streak = max(user.best_streak, streak_after)
        if is_correct:
            quiz.correct_count += 1
        quiz.xp_earned += xp
        add_xp(user, xp)

        return AnswerResult(
            is_correct=is_correct,
            correct_answer=question.correct_answer,
            explanation=question.explanation,
            xp_awarded=xp,
            streak=streak_after,
        )

    # ---------- Конец игры ----------

    async def finish_game(self, user: User, session_id: int, level_before: int) -> GameResult:
        quiz = await self._get_open_game(user, session_id)

        # Бонус за идеальную игру: все вопросы полного набора отвечены правильно.
        perfect = quiz.total_questions == QUESTIONS_PER_GAME and (
            quiz.correct_count == quiz.total_questions
        )
        bonus = XP_PERFECT_GAME if perfect else 0
        if bonus:
            quiz.xp_earned += bonus
            add_xp(user, bonus)

        quiz.finished_at = utcnow()
        await self.session.flush()
        return GameResult(
            category=quiz.category,
            correct=quiz.correct_count,
            total=quiz.total_questions,
            xp_earned=quiz.xp_earned,
            perfect_bonus=bonus,
            level_change=LevelChange(old_level=level_before, new_level=user.level),
        )

    async def _get_open_game(self, user: User, session_id: int) -> QuizSession:
        """Игра должна существовать, принадлежать этому пользователю и быть не завершена."""
        quiz = await self.session.get(QuizSession, session_id)
        if quiz is None or quiz.user_id != user.id or quiz.finished_at is not None:
            raise StaleAnswerError(f"game {session_id} is not open")
        return quiz

    # ---------- История ----------

    async def recent_games(self, user: User, limit: int = 5) -> list[GameHistoryItem]:
        rows = await self.session.scalars(
            select(QuizSession)
            .where(QuizSession.user_id == user.id, QuizSession.finished_at.is_not(None))
            .order_by(QuizSession.started_at.desc(), QuizSession.id.desc())
            .limit(limit)
        )
        return [
            GameHistoryItem(g.category, g.correct_count, g.total_questions, g.xp_earned, g.started_at)
            for g in rows
        ]

    async def random_fact(self) -> QuizQuestion | None:
        """Случайный локальный вопрос: его объяснение показывается как «факт дня»,
        когда Reddit недоступен."""
        return await self.session.scalar(
            select(QuizQuestion)
            .where(QuizQuestion.is_active.is_(True), QuizQuestion.source == "local")
            .order_by(func.random())
            .limit(1)
        )
