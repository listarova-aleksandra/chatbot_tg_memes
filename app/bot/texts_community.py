"""Тексты сообщества (HTML)."""

from html import escape

from app.services.community_service import POPULAR, FeedItem

EMPTY_FEED = (
    "🔥 <b>Сообщество</b>\n\n"
    "Пока здесь пусто: никто ещё не опубликовал мем. Будь первым! 🎨"
)

ALREADY_VOTED = "Ты уже голосовал за этот мем"
OWN_MEME = "За свой мем голосовать нельзя 🙂"
MEME_GONE = "Этот мем уже недоступен"
VOTED_UP = "Лайк принят 👍"
VOTED_DOWN = "Дизлайк принят 👎"


def format_item(item: FeedItem, sort: str, page: int, total: int, is_own: bool) -> str:
    sort_label = "🔥 Популярные" if sort == POPULAR else "🆕 Новые"
    date = item.published_at.strftime("%d.%m.%Y") if item.published_at else ""
    lines = [
        f"<b>Мем {page + 1} из {total}</b> · {sort_label}",
        f"👤 {escape(item.author_name)} · {date}" + (" (твой мем)" if is_own else ""),
        "",
        f"👍 {item.likes}   👎 {item.dislikes}   ⭐ Рейтинг: <b>{item.rating:+d}</b>",
    ]
    return "\n".join(lines)


def format_author_bonus(xp: int) -> str:
    return f"🔥 Твой мем набрал высокий рейтинг в сообществе! Бонус: +{xp} XP"
