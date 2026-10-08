"""Тексты генератора мемов (HTML)."""

from app.services.gamification import LevelChange
from app.services.meme_renderer import MAX_TEXT_LEN

CHOOSE_TEMPLATE = (
    "🖼 <b>Создать мем</b>\n"
    "Шаг 1 из 3: выбери основу.\n\n"
    "📷 <b>Своё фото</b>: лучше всего. Скриншот из свежего мема или твоя фотка.\n"
    "Или выбери готовый шаблон из списка."
)

TEMPLATES_FALLBACK_NOTE = (
    "\n\n⚠️ Imgflip сейчас не отвечает, поэтому показаны запасные шаблоны (цветной фон). "
    "Свои фото работают как обычно."
)

ASK_PHOTO = "📷 Пришли фото (именно как фото, не файлом). Я наложу на него текст."

NOT_A_PHOTO = "Мне нужно именно фото 🙂 Пришли картинку или нажми «Отмена»."

PHOTO_TOO_BIG = "Файл слишком большой. Пришли фото поменьше (до 5 МБ)."

ASK_TOP = (
    f"✏️ Шаг 2 из 3: пришли <b>верхний текст</b> (до {MAX_TEXT_LEN} символов).\n"
    "Если сверху текст не нужен, отправь «-»."
)

ASK_BOTTOM = (
    f"✏️ Шаг 3 из 3: пришли <b>нижний текст</b> (до {MAX_TEXT_LEN} символов).\n"
    "Если снизу текст не нужен, отправь «-»."
)

NEED_TEXT_MESSAGE = "Пришли текст сообщением (или «-», чтобы пропустить). Выйти: /cancel"

NEED_ANY_TEXT = "Нужен хотя бы один текст: сверху или снизу. Напиши нижний текст."

PREVIEW = (
    "👀 <b>Вот твой мем!</b> Что делаем?\n\n"
    "📢 Опубликовать в сообществе: +10 XP\n"
    "💾 Сохранить у себя: +5 XP"
)

TEMPLATE_DOWNLOAD_FAILED = (
    "😕 Не получилось загрузить картинку шаблона (сервис не отвечает). "
    "Выбери другой шаблон или пришли своё фото."
)

IMAGE_PROBLEM = "😕 Не получилось обработать картинку. Пришли другое фото (JPEG, PNG или WebP)."

STALE = "Этот сценарий уже неактуален. Начни заново: /meme"

ALREADY_PUBLISHED = "Этот мем уже опубликован или недоступен."


def format_created(published: bool, xp: int, change: LevelChange) -> str:
    head = "📢 <b>Мем опубликован в сообществе!</b>" if published else "💾 <b>Мем сохранён!</b>"
    text = f"{head}\n✨ +{xp} XP"
    if change.leveled_up:
        text += f"\n⭐ Новый уровень: <b>{change.old_level} → {change.new_level}</b> 🎉"
    return text


def format_published(xp: int, change: LevelChange) -> str:
    return format_created(True, xp, change)
