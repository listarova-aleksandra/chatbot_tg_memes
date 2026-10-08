"""Уровни и опыт: чистые функции без БД и Telegram (их легко тестировать).

Формула: для уровня L нужно суммарно  50 * (L - 1)^2  XP.
    уровень 1:    0 XP     уровень 4:  450 XP
    уровень 2:   50 XP     уровень 5:  800 XP
    уровень 3:  200 XP     уровень 6: 1250 XP
Чем выше уровень, тем больше XP до следующего: прокачка замедляется.
"""

import math

XP_PER_LEVEL_FACTOR = 50

# ---------- Правила начисления XP за викторину (всё в одном месте) ----------
QUESTIONS_PER_GAME = 5
XP_CORRECT = 10  # за любой правильный ответ
XP_DIFFICULTY_BONUS = {1: 0, 2: 2, 3: 5}  # надбавка за средний и сложный вопрос
STREAK_BONUS_FROM = 3  # с третьего правильного ответа подряд...
XP_STREAK_BONUS = 5  # ...добавляется бонус за каждый такой ответ
XP_PERFECT_GAME = 20  # бонус за игру без единой ошибки
XP_MEME_CREATED = 5  # мем создан и сохранён
XP_MEME_PUBLISHED = 5  # мем опубликован в сообществе
RATING_BONUS_THRESHOLD = 5  # рейтинг мема, с которого автор получает бонус...
XP_RATING_BONUS = 10  # ...один раз за мем


def level_for_xp(xp: int) -> int:
    """Уровень по количеству XP: floor(sqrt(xp / 50)) + 1.

    isqrt это целочисленный квадратный корень: без ошибок округления float.
    """
    return math.isqrt(max(xp, 0) // XP_PER_LEVEL_FACTOR) + 1


def xp_for_level(level: int) -> int:
    """Сколько XP нужно набрать суммарно, чтобы достичь уровня `level`."""
    return XP_PER_LEVEL_FACTOR * (max(level, 1) - 1) ** 2


def level_progress(xp: int) -> tuple[int, int]:
    """(XP, набранные на текущем уровне; сколько XP занимает весь текущий уровень)."""
    level = level_for_xp(xp)
    start = xp_for_level(level)
    return max(xp, 0) - start, xp_for_level(level + 1) - start


def answer_xp(difficulty: int, streak_after: int) -> int:
    """XP за ПРАВИЛЬНЫЙ ответ.

    difficulty:   сложность вопроса 1..3
    streak_after: длина серии правильных ответов подряд, включая этот ответ
    Пример: сложный вопрос (+5) при серии из 3 ответов: 10 + 5 + 5 = 20 XP.
    """
    xp = XP_CORRECT + XP_DIFFICULTY_BONUS.get(difficulty, 0)
    if streak_after >= STREAK_BONUS_FROM:
        xp += XP_STREAK_BONUS
    return xp
