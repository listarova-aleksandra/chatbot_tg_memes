"""Клавиатуры главного меню."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import MenuCB


def _button(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MenuCB(action=action).pack())


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("🎮 Играть", "play"), _button("🖼 Создать мем", "meme")],
            [_button("🔥 Сообщество", "community"), _button("📅 Мем дня", "daily")],
            [_button("🏆 Топ", "top"), _button("👤 Профиль", "profile")],
            [_button("🏅 Достижения", "achievements"), _button("⚙️ Настройки", "settings")],
        ]
    )


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button("⬅️ В меню", "main")]])


def profile_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("📜 История игр", "history")],
            [_button("⬅️ В меню", "main")],
        ]
    )


def daily_kb(permalink: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть на Reddit", url=permalink)],
            [_button("⬅️ В меню", "main")],
        ]
    )
