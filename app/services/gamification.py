"""Начисление XP и смена уровня.

Сюда на Этапе 8 добавятся достижения и лидерборд.
"""

from dataclasses import dataclass

from app.database.models import User
from app.services.xp import level_for_xp


@dataclass(frozen=True)
class LevelChange:
    old_level: int
    new_level: int

    @property
    def leveled_up(self) -> bool:
        return self.new_level > self.old_level


def add_xp(user: User, amount: int) -> LevelChange:
    """Добавляет XP пользователю и пересчитывает его уровень.

    Это единственное место, где меняются `xp` и `level`: так они не расходятся.
    Изменения попадут в БД при commit (его делает DbSessionMiddleware).
    """
    old_level = user.level
    user.xp += max(amount, 0)
    user.level = level_for_xp(user.xp)
    return LevelChange(old_level=old_level, new_level=user.level)
