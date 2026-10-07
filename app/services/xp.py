"""Уровни и опыт: чистые функции без БД и Telegram (их легко тестировать).

Формула: для уровня L нужно суммарно  50 * (L - 1)^2  XP.
    уровень 1:    0 XP     уровень 4:  450 XP
    уровень 2:   50 XP     уровень 5:  800 XP
    уровень 3:  200 XP     уровень 6: 1250 XP
Чем выше уровень, тем больше XP до следующего: прокачка замедляется.
"""

import math

XP_PER_LEVEL_FACTOR = 50


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
