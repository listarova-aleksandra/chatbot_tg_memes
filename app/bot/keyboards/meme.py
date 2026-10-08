"""Клавиатуры генератора мемов."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import MemeCB, MenuCB
from app.services.imgflip_service import MemeTemplate

PAGE_SIZE = 8


def _btn(text: str, action: str, value: str = "", page: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MemeCB(action=action, value=value, page=page).pack())


def _menu_btn(text: str = "⬅️ В меню") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=MenuCB(action="main").pack())


def page_count(total: int) -> int:
    return max(1, -(-total // PAGE_SIZE))  # деление с округлением вверх


def templates_kb(templates: list[MemeTemplate], page: int) -> InlineKeyboardMarkup:
    pages = page_count(len(templates))
    page = min(max(page, 0), pages - 1)
    chunk = templates[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    rows = [[_btn(t.name[:40], "tpl", t.id)] for t in chunk]
    if pages > 1:
        rows.append(
            [
                _btn("◀️", "page", page=max(page - 1, 0)),
                InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=MemeCB(action="noop").pack()),
                _btn("▶️", "page", page=min(page + 1, pages - 1)),
            ]
        )
    rows.append([_btn("📷 Своё фото", "photo"), _btn("🎲 Случайный", "random")])
    rows.append([_menu_btn()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("❌ Отмена", "cancel")]])


def preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📢 Опубликовать", "publish"), _btn("💾 Сохранить", "save")],
            [_btn("🔄 Заново", "redo"), _btn("❌ Отмена", "cancel")],
        ]
    )


def saved_kb(meme_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📢 Опубликовать", "publish_saved", str(meme_id))],
            [InlineKeyboardButton(text="🎨 Ещё мем", callback_data=MenuCB(action="meme").pack()), _menu_btn("🏠 В меню")],
        ]
    )


def done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Ещё мем", callback_data=MenuCB(action="meme").pack()), _menu_btn("🏠 В меню")]
        ]
    )
