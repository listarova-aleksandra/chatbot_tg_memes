"""Достижения и лидерборд: /achievements, /top и кнопки меню."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_gamification as t
from app.bot.callbacks import MenuCB
from app.bot.helpers import show_screen
from app.bot.keyboards.main_menu import back_to_menu_kb
from app.database.models import User
from app.services.achievements import AchievementService
from app.services.leaderboard import LeaderboardService

router = Router(name="gamification")


@router.message(Command("top"))
@router.callback_query(MenuCB.filter(F.action == "top"))
async def show_leaderboard(event: Message | CallbackQuery, user: User, session: AsyncSession) -> None:
    board = await LeaderboardService(session).get(user)
    await show_screen(event, t.format_leaderboard(board, user.id), back_to_menu_kb())


@router.message(Command("achievements"))
@router.callback_query(MenuCB.filter(F.action == "achievements"))
async def show_achievements(event: Message | CallbackQuery, user: User, session: AsyncSession) -> None:
    statuses = await AchievementService(session).overview(user)
    await show_screen(event, t.format_achievements(statuses), back_to_menu_kb())
