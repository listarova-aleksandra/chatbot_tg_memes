"""Проверка файла с вопросами и загрузки его в БД."""

import json
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import QuizCategory, QuizQuestion
from app.database.seed import QuestionData, load_questions, seed_questions
from app.bot.texts_quiz import format_answer, format_question
from app.services.quiz_service import AnswerResult

QUESTION_CATEGORIES = {c.value for c in QuizCategory if c is not QuizCategory.MIXED}


def test_questions_file_is_valid_and_large_enough() -> None:
    questions = load_questions()  # внутри проверяются все правила (варианты, длина, категория)
    assert 30 <= len(questions) <= 50
    counts = Counter(q.category for q in questions)
    assert set(counts) == QUESTION_CATEGORIES  # представлены все категории
    # В игре 5 вопросов: в каждой категории их должно быть не меньше.
    assert min(counts.values()) >= 5


def test_every_question_fits_telegram_photo_caption_limit() -> None:
    """У фото-сообщения подпись не длиннее 1024 символов: проверяем худший случай."""
    for data in load_questions():
        question = QuizQuestion(**data.model_dump(exclude={"image_url"}), id=1)
        options = [question.correct_answer, *question.wrong_answers]
        assert len(format_question(question, options, 0, 5)) <= 1024, data.slug
        result = AnswerResult(False, question.correct_answer, question.explanation, 0, 0)
        assert len(format_answer(question, 0, 5, options[1], result)) <= 1024, data.slug


@pytest.mark.parametrize(
    "patch",
    [
        {"wrong_answers": ["a", "b"]},  # не три неправильных
        {"wrong_answers": ["a", "b", "правильный"]},  # дубль правильного ответа
        {"category": "mixed"},  # «смешанная» не категория вопроса
        {"category": "unknown"},
        {"difficulty": 4},
        {"correct_answer": "x" * 100},  # слишком длинный вариант
    ],
)
def test_question_validation_rejects_bad_data(patch: dict) -> None:
    base = {
        "slug": "s", "category": "nba", "difficulty": 1, "question": "?",
        "correct_answer": "правильный", "wrong_answers": ["a", "b", "c"], "explanation": "e",
    }
    with pytest.raises(ValueError):
        QuestionData.model_validate({**base, **patch})


def test_duplicate_slugs_rejected(tmp_path: Path) -> None:
    item = {
        "slug": "same", "category": "nba", "difficulty": 1, "question": "?",
        "correct_answer": "a", "wrong_answers": ["b", "c", "d"], "explanation": "e",
    }
    path = tmp_path / "q.json"
    path.write_text(json.dumps([item, item]), encoding="utf-8")
    with pytest.raises(ValueError, match="уникальны"):
        load_questions(path)


async def test_seed_is_idempotent_and_deactivates_removed(
    session: AsyncSession, tmp_path: Path
) -> None:
    count = await seed_questions(session)
    await seed_questions(session)  # повторный запуск не создаёт дублей
    assert await session.scalar(select(func.count()).select_from(QuizQuestion)) == count

    # Вопрос исчез из файла: в БД остаётся, но становится неактивным.
    items = json.loads(Path("app/content/questions.json").read_text(encoding="utf-8"))
    path = tmp_path / "q.json"
    path.write_text(json.dumps(items[1:]), encoding="utf-8")
    await seed_questions(session, path)
    removed = await session.scalar(select(QuizQuestion).where(QuizQuestion.slug == items[0]["slug"]))
    assert removed is not None and removed.is_active is False
