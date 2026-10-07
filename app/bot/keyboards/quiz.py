"""Клавиатуры викторины."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import MenuCB, QuizCB
from app.bot.texts import CATEGORY_LABELS
from app.database.models import QuizCategory

LETTERS = "ABCD"


def _button(text: str, **kwargs: str | int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=QuizCB(**kwargs).pack())


def _menu_button(text: str = "⬅️ В меню") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MenuCB(action="main").pack())


def category_kb(default_category: str | None) -> InlineKeyboardMarkup:
    """Категории по одной в строке. Категория по умолчанию из настроек отмечена звёздочкой."""
    rows = [
        [_button(("⭐ " if key == default_category else "") + label, action="cat", value=key)]
        for key, label in CATEGORY_LABELS.items()
        if key != QuizCategory.MIXED
    ]
    mixed = CATEGORY_LABELS[QuizCategory.MIXED]
    rows.append(
        [_button(("⭐ " if default_category == QuizCategory.MIXED else "") + mixed, action="cat", value=QuizCategory.MIXED)]
    )
    rows.append([_menu_button()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def answer_kb(options_count: int, question_index: int) -> InlineKeyboardMarkup:
    """Кнопки A B C D в один ряд (сами варианты написаны в тексте вопроса)."""
    letters_row = [
        _button(LETTERS[i], action="ans", value=str(i), q=question_index)
        for i in range(options_count)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[letters_row, [_menu_button("✖️ Выйти из игры")]])


def next_kb(question_index: int, is_last: bool) -> InlineKeyboardMarkup:
    label = "📊 Результат" if is_last else "➡️ Дальше"
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(label, action="next", q=question_index)]]
    )


def finished_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button("🔄 Играть ещё", action="again"), _menu_button("🏠 В меню")]]
    )
