"""«Мем дня» из Reddit (/daily и кнопка в меню).

Если Reddit не настроен или не отвечает, вместо мема показывается факт из локальной базы:
раздел не ломается, а пользователь понимает, что произошло.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import MenuCB
from app.bot.helpers import show_screen
from app.bot.keyboards.main_menu import back_to_menu_kb, daily_kb
from app.services.quiz_service import QuizService
from app.services.reddit_service import RedditService

router = Router(name="daily")


# `reddit` попадает в хендлер по имени из Dispatcher(...) (внедрение зависимостей aiogram).
@router.message(Command("daily"))
@router.callback_query(MenuCB.filter(F.action == "daily"))
async def show_daily(event: Message | CallbackQuery, reddit: RedditService, session: AsyncSession) -> None:
    result = await reddit.get_meme_of_the_day()
    if result.meme is not None:
        await show_screen(
            event, texts.format_daily(result.meme), daily_kb(result.meme.permalink), media=result.meme.image_url
        )
        return
    fact = await QuizService(session).random_fact()
    await show_screen(event, texts.format_daily_fallback(result.status, fact), back_to_menu_kb())
