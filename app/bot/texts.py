"""Все тексты бота в одном месте (формат HTML).

Любые данные пользователя (имя, ник) перед вставкой в HTML экранируются,
иначе имя вида "<b>" сломало бы разметку сообщения.
"""

from html import escape

from app.database.models import QuizCategory, QuizQuestion, User
from app.services.reddit_service import RedditMeme
from app.services.user_service import ProfileStats
from app.services.xp import level_progress

CATEGORY_LABELS: dict[str, str] = {
    QuizCategory.POSTIRONY: "🇷🇺 Постирония и брейнрот",
    QuizCategory.NBA: "🏀 NBA",
    QuizCategory.WNBA: "🏀 WNBA",
    QuizCategory.HIPHOP: "🎤 Hip-Hop",
    QuizCategory.RNB: "🎶 R&B",
    QuizCategory.INTERNET: "🌐 Интернет-культура",
    QuizCategory.MIXED: "🎲 Смешанная",
}

GIF_UNAVAILABLE = "🎞 GIF-сервис временно не отвечает. Продолжаем без него."

MAIN_MENU = "📋 <b>Главное меню</b>\nВыбирай, чем займёмся 👇"

COMING_SOON = "🚧 Этот раздел появится на следующем этапе разработки"

GENERIC_ERROR = "Что-то пошло не так 😵 Попробуй ещё раз или вернись в /menu"

HELP = (
    "❓ <b>Помощь</b>\n\n"
    "<b>МемоМастер</b>: викторина по мемам, генератор мемов и сообщество.\n\n"
    "<b>Команды</b>\n"
    "/start — начать и открыть меню\n"
    "/menu — главное меню\n"
    "/play — сыграть в викторину\n"
    "/meme — создать мем\n"
    "/community — лента сообщества\n"
    "/daily — мем дня\n"
    "/profile — твой профиль\n"
    "/history — история игр\n"
    "/settings — настройки\n"
    "/cancel — выйти из текущего действия\n"
    "/help — эта справка\n\n"
    "<b>Как копится опыт</b>\n"
    "✅ правильный ответ: +10 XP (за сложные вопросы больше)\n"
    "🔥 серия из 3+ правильных подряд: бонус\n"
    "🖼 создал мем: +5 XP, опубликовал: ещё +5 XP\n"
    "⭐ мем набрал рейтинг +5: бонус +10 XP автору\n\n"
    "Уровень растёт вместе с XP: уровень 2 — с 50 XP, 3 — с 200, 4 — с 450."
)


def welcome(first_name: str, is_new: bool) -> str:
    name = escape(first_name)
    if is_new:
        return (
            f"Привет, <b>{name}</b>! 👋\n\n"
            "Добро пожаловать в <b>МемоМастер</b>: угадывай мемы, делай свои "
            "и собирай лайки сообщества.\n"
            "Профиль создан, можно играть 🎮\n\n"
            "Выбирай раздел 👇"
        )
    return f"С возвращением, <b>{name}</b>! 😎\n\nВыбирай раздел 👇"


def progress_bar(current: int, total: int, width: int = 10) -> str:
    filled = width if total <= 0 else min(width, round(width * current / total))
    return "▰" * filled + "▱" * (width - filled)


def format_profile(user: User, stats: ProfileStats) -> str:
    into_level, level_span = level_progress(user.xp)
    return (
        f"👤 <b>{escape(user.first_name)}</b>\n\n"
        f"⭐ Уровень: <b>{user.level}</b>\n"
        f"✨ XP: <b>{user.xp}</b>\n"
        f"{progress_bar(into_level, level_span)} {into_level}/{level_span} до следующего уровня\n\n"
        f"🎯 Всего игр: <b>{stats.games}</b>\n"
        f"✅ Правильных ответов: <b>{stats.correct_answers}</b>\n"
        f"📊 Accuracy: <b>{stats.accuracy:.0f}%</b>\n"
        f"🔥 Лучшая серия: <b>{user.best_streak}</b>\n\n"
        f"🖼 Создано мемов: <b>{stats.memes_created}</b>\n"
        f"👍 Рейтинг мемов: <b>{stats.memes_rating}</b>"
    )


def category_label(category: str | None) -> str:
    if category is None:
        return "не выбрана"
    return CATEGORY_LABELS.get(category, category)


def format_settings(user: User) -> str:
    notify = "включены" if user.notifications_enabled else "выключены"
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"🔔 Уведомления: <b>{notify}</b>\n"
        f"🎯 Категория по умолчанию: <b>{category_label(user.default_category)}</b>"
    )


CHOOSE_CATEGORY = "🎯 <b>Категория по умолчанию</b>\nВыбери, с чего начинать игру:"

RESET_CONFIRM = (
    "🗑 <b>Сбросить прогресс?</b>\n\n"
    "Обнулятся XP, уровень, история игр и достижения. "
    "Мемы, опубликованные в сообществе, останутся.\n\n"
    "Это действие нельзя отменить."
)

RESET_DONE = "✅ Прогресс сброшен. Начинаем с чистого листа!"


DAILY_TITLE = "📅 <b>Мем дня</b>"


def format_daily(meme: RedditMeme) -> str:
    title = escape(meme.title[:200])
    return f"{DAILY_TITLE} · r/{escape(meme.subreddit)}\n\n{title}\n\n👍 {meme.score}"


_DAILY_FALLBACK_REASON = {
    "disabled": "Раздел пока не настроен (нет ключей Reddit).",
    "unavailable": "Reddit временно не отвечает. Продолжаем без него.",
    "empty": "Сегодня подходящих мемов не нашлось: мы показываем только безопасные посты.",
}


def format_daily_fallback(status: str, fact: QuizQuestion | None) -> str:
    """Запасной экран «Мема дня»: причина и факт из локальной базы вместо мема."""
    text = f"{DAILY_TITLE}\n\n{_DAILY_FALLBACK_REASON.get(status, _DAILY_FALLBACK_REASON['unavailable'])}"
    if fact is not None:
        text += (
            "\n\nВместо мема держи факт из мем-культуры:\n"
            f"❓ {escape(fact.question)}\n"
            f"💡 <b>{escape(fact.correct_answer)}.</b> {escape(fact.explanation)}"
        )
    return text
