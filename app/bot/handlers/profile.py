"""Профиль пользователя: /profile и кнопка «Профиль»."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, texts_quiz
from app.bot.callbacks import MenuCB
from app.bot.helpers import show_screen
from app.bot.keyboards.main_menu import back_to_menu_kb, profile_kb
from app.database.models import User
from app.services.quiz_service import QuizService
from app.services.user_service import UserService

router = Router(name="profile")


# Два декоратора на одной функции: и команда, и кнопка ведут на один экран.
@router.message(Command("profile"))
@router.callback_query(MenuCB.filter(F.action == "profile"))
async def show_profile(event: Message | CallbackQuery, user: User, session: AsyncSession) -> None:
    stats = await UserService(session).get_profile_stats(user)
    await show_screen(event, texts.format_profile(user, stats), profile_kb())


@router.message(Command("history"))
@router.callback_query(MenuCB.filter(F.action == "history"))
async def show_history(event: Message | CallbackQuery, user: User, session: AsyncSession) -> None:
    games = await QuizService(session).recent_games(user)
    await show_screen(event, texts_quiz.format_history(games), back_to_menu_kb())
