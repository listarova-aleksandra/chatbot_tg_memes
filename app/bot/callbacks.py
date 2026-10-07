"""Типизированные callback_data для inline-кнопок.

Когда пользователь нажимает кнопку, Telegram присылает боту короткую строку
(не длиннее 64 байт). CallbackData-класс сам собирает её ("menu:profile")
и разбирает обратно в объект, поэтому строки вручную мы не парсим.
"""

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="menu"):
    action: str  # main, play, meme, community, daily, top, profile, achievements, settings


class SettingsCB(CallbackData, prefix="set"):
    action: str  # notify, category, set_category, reset, reset_yes, help
    value: str = ""
