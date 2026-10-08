"""Базовые хендлеры: /start, /menu, /help, /cancel и переход в главное меню."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.callbacks import MenuCB
from app.bot.helpers import show_screen
from app.bot.keyboards.main_menu import back_to_menu_kb, main_menu_kb
from app.database.models import User

router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message, user: User, is_new_user: bool, state: FSMContext) -> None:
    # `user` и `is_new_user` кладёт в хендлер UserMiddleware (регистрация уже выполнена).
    await state.clear()  # /start всегда выходит из любого незаконченного сценария
    await message.answer(texts.welcome(user.first_name, is_new_user), reply_markup=main_menu_kb())


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.MAIN_MENU, reply_markup=main_menu_kb())


@router.callback_query(MenuCB.filter(F.action == "main"))
async def open_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_screen(callback, texts.MAIN_MENU, main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP, reply_markup=back_to_menu_kb())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Сейчас нечего отменять 🙂", reply_markup=main_menu_kb())
        return
    await state.clear()
    await message.answer("Отменено ✅", reply_markup=main_menu_kb())


# Разделы, которые будут реализованы на следующих этапах. Пока кнопка не молчит,
# а показывает всплывающее уведомление. Эти заглушки удаляются по мере реализации.
STUB_ACTIONS = {"community", "top", "achievements"}


@router.callback_query(MenuCB.filter(F.action.in_(STUB_ACTIONS)))
async def section_stub(callback: CallbackQuery) -> None:
    await callback.answer(texts.COMING_SOON, show_alert=True)
