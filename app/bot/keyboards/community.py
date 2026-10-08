"""Клавиатура ленты сообщества."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import CommunityCB, MenuCB
from app.services.community_service import NEW, POPULAR, FeedItem


def _btn(text: str, **kwargs: str | int) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CommunityCB(**kwargs).pack())


def feed_kb(item: FeedItem, sort: str, page: int, total: int) -> InlineKeyboardMarkup:
    like = f"👍 {item.likes}" + (" ✅" if item.my_vote == 1 else "")
    dislike = f"👎 {item.dislikes}" + (" ✅" if item.my_vote == -1 else "")
    rows = [
        [
            _btn(like, action="vote", sort=sort, page=page, meme_id=item.meme_id, value=1),
            _btn(dislike, action="vote", sort=sort, page=page, meme_id=item.meme_id, value=-1),
        ]
    ]
    if total > 1:
        # Лента зациклена: «назад» с первого мема ведёт к последнему.
        rows.append(
            [
                _btn("◀️", action="view", sort=sort, page=(page - 1) % total),
                _btn(f"{page + 1}/{total}", action="noop"),
                _btn("▶️", action="view", sort=sort, page=(page + 1) % total),
            ]
        )
    rows.append(
        [
            _btn(("✓ " if sort == POPULAR else "") + "🔥 Популярные", action="view", sort=POPULAR),
            _btn(("✓ " if sort == NEW else "") + "🆕 Новые", action="view", sort=NEW),
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCB(action="main").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def empty_feed_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Создать мем", callback_data=MenuCB(action="meme").pack())],
            [InlineKeyboardButton(text="⬅️ В меню", callback_data=MenuCB(action="main").pack())],
        ]
    )
