"""Тесты формулы уровней."""

import pytest

from app.services.xp import level_for_xp, level_progress, xp_for_level


@pytest.mark.parametrize(
    ("xp", "level"),
    [(0, 1), (49, 1), (50, 2), (199, 2), (200, 3), (449, 3), (450, 4), (800, 5), (1250, 6)],
)
def test_level_for_xp(xp: int, level: int) -> None:
    assert level_for_xp(xp) == level


def test_negative_xp_is_level_one() -> None:
    assert level_for_xp(-100) == 1


def test_xp_for_level_is_inverse_of_level_for_xp() -> None:
    for level in range(1, 30):
        threshold = xp_for_level(level)
        assert level_for_xp(threshold) == level
        if level > 1:
            assert level_for_xp(threshold - 1) == level - 1


def test_level_progress() -> None:
    assert level_progress(0) == (0, 50)
    assert level_progress(120) == (70, 150)  # уровень 2: от 50 до 200 XP
    assert level_progress(200) == (0, 250)  # ровно начало уровня 3: от 200 до 450
