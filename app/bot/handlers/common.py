"""Базовые хендлеры: /start, /menu, /help, /cancel и переход в главное меню."""

from aiogram import F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts
from app.bot.callbacks import MenuCB
from app.bot.commands import COMMANDS
from app.bot.helpers import show_screen
from app.bot.keyboards.main_menu import back_to_menu_kb, main_menu_kb
from app.bot.states.meme import MemeStates
from app.database.models import User

router = Router(name="common")

# Команды меню, кроме /cancel: у него своё сообщение «Отменено».
_MENU_COMMANDS = [c.command for c in COMMANDS if c.command != "cancel"]


@router.message(StateFilter(MemeStates), Command(*_MENU_COMMANDS))
async def leave_meme_flow_on_command(message: Message, state: FSMContext) -> object:
    """Команда посреди создания мема (/top, /profile...) выходит из сценария.

    Без этого бот ждал бы текст или фото и ответил бы на /top «пришли текст».
    UNHANDLED говорит aiogram «я событие не обработал, ищи дальше», поэтому саму команду
    затем выполняет её обычный хендлер.
    """
    await state.clear()
    return UNHANDLED


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
