"""Импорт всех моделей в одном месте.

Alembic и Base.metadata «видят» только те модели, которые были импортированы.
Поэтому все модели собраны здесь, а env.py импортирует этот пакет.
"""

from app.database.models.achievement import Achievement, UserAchievement
from app.database.models.meme import Meme, MemeVote
from app.database.models.quiz import QuizAttempt, QuizCategory, QuizQuestion, QuizSession
from app.database.models.user import User

__all__ = [
    "Achievement",
    "Meme",
    "MemeVote",
    "QuizAttempt",
    "QuizCategory",
    "QuizQuestion",
    "QuizSession",
    "User",
    "UserAchievement",
]
