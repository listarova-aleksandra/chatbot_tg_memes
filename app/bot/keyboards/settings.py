"""Клавиатуры раздела «Настройки»."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import MenuCB, SettingsCB
from app.bot.texts import CATEGORY_LABELS, category_label
from app.database.models import User


def _button(text: str, action: str, value: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text, callback_data=SettingsCB(action=action, value=value).pack()
    )


def _back_to_menu() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCB(action="main").pack())


def settings_kb(user: User) -> InlineKeyboardMarkup:
    notify = "включены" if user.notifications_enabled else "выключены"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(f"🔔 Уведомления: {notify}", "notify")],
            [_button(f"🎯 Категория: {category_label(user.default_category)}", "category")],
            [_button("🗑 Сбросить прогресс", "reset")],
            [_button("❓ Помощь", "help")],
            [_back_to_menu()],
        ]
    )


def category_kb(current: str | None) -> InlineKeyboardMarkup:
    rows = [
        [_button(("✅ " if key == current else "") + label, "set_category", key)]
        for key, label in CATEGORY_LABELS.items()
    ]
    rows.append([_button("Не выбирать", "set_category", "none")])
    rows.append([_button("⬅️ Назад", "back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reset_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("Да, сбросить", "reset_yes"),
                _button("Отмена", "back"),
            ]
        ]
    )


def back_to_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button("⬅️ К настройкам", "back")], [_back_to_menu()]]
    )
