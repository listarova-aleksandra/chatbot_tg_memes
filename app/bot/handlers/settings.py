"""Настройки: уведомления, категория по умолчанию, сброс прогресса, помощь."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.callbacks import MenuCB, SettingsCB
from app.bot.helpers import show_screen
from app.bot.keyboards.settings import (
    back_to_settings_kb,
    category_kb,
    reset_confirm_kb,
    settings_kb,
)
from app.database.models import User
from app.services.user_service import UserService

router = Router(name="settings")


@router.message(Command("settings"))
@router.callback_query(MenuCB.filter(F.action == "settings"))
@router.callback_query(SettingsCB.filter(F.action == "back"))
async def open_settings(event: Message | CallbackQuery, user: User) -> None:
    await show_screen(event, texts.format_settings(user), settings_kb(user))


@router.callback_query(SettingsCB.filter(F.action == "notify"))
async def toggle_notifications(
    callback: CallbackQuery, user: User, session: AsyncSession
) -> None:
    await UserService(session).toggle_notifications(user)
    await show_screen(callback, texts.format_settings(user), settings_kb(user))


@router.callback_query(SettingsCB.filter(F.action == "category"))
async def choose_category(callback: CallbackQuery, user: User) -> None:
    await show_screen(callback, texts.CHOOSE_CATEGORY, category_kb(user.default_category))


@router.callback_query(SettingsCB.filter(F.action == "set_category"))
async def set_category(
    callback: CallbackQuery, callback_data: SettingsCB, user: User, session: AsyncSession
) -> None:
    # callback_data попадает в хендлер благодаря фильтру SettingsCB.filter(...).
    category = None if callback_data.value == "none" else callback_data.value
    if not await UserService(session).set_default_category(user, category):
        await callback.answer("Неизвестная категория", show_alert=True)
        return
    await show_screen(callback, texts.format_settings(user), settings_kb(user))


@router.callback_query(SettingsCB.filter(F.action == "reset"))
async def ask_reset_confirmation(callback: CallbackQuery) -> None:
    # Опасное действие требует подтверждения: сначала только показываем вопрос.
    await show_screen(callback, texts.RESET_CONFIRM, reset_confirm_kb())


@router.callback_query(SettingsCB.filter(F.action == "reset_yes"))
async def confirm_reset(callback: CallbackQuery, user: User, session: AsyncSession) -> None:
    await UserService(session).reset_progress(user)
    await show_screen(callback, texts.RESET_DONE, back_to_settings_kb())


@router.callback_query(SettingsCB.filter(F.action == "help"))
async def show_help(callback: CallbackQuery) -> None:
    await show_screen(callback, texts.HELP, back_to_settings_kb())
