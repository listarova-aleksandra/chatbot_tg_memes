"""Генератор мемов: шаблон или своё фото → верхний текст → нижний текст → превью → публикация.

Состояния FSM описаны в app/bot/states/meme.py. Данные сценария лежат в state.data:
    source          "template" или "photo"
    template_id/name/url   выбранный шаблон
    file_id         file_id фото пользователя (для source == "photo")
    top_text, bottom_text  введённые тексты
    preview_file_id file_id готового мема (после отправки превью)
"""

import asyncio
import logging
import math
import random

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, texts_meme
from app.bot.callbacks import MemeCB, MenuCB
from app.bot.helpers import show_screen
from app.bot.keyboards.main_menu import main_menu_kb
from app.bot.keyboards.meme import PAGE_SIZE, cancel_kb, done_kb, preview_kb, saved_kb, templates_kb
from app.bot.states.meme import MemeStates
from app.core.exceptions import ApiError
from app.database.models import User
from app.services.imgflip_service import ImgflipService, MemeTemplate
from app.services.meme_renderer import MemeImageError, MemeTextError, build_meme, clean_text
from app.services.meme_service import MemeNotPublishableError, MemeService

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5_000_000

router = Router(name="meme")


# ---------- Шаг 1: выбор основы ----------


@router.message(Command("meme"))
@router.callback_query(MenuCB.filter(F.action == "meme"))
async def open_templates(event: Message | CallbackQuery, state: FSMContext, imgflip: ImgflipService) -> None:
    await state.clear()
    await state.set_state(MemeStates.choosing_template)
    await _show_templates(event, imgflip, page=0)


async def _show_templates(event: Message | CallbackQuery, imgflip: ImgflipService, page: int) -> None:
    result = await imgflip.get_templates()
    text = texts_meme.CHOOSE_TEMPLATE + (texts_meme.TEMPLATES_FALLBACK_NOTE if result.is_fallback else "")
    await show_screen(event, text, templates_kb(result.templates, page))


@router.callback_query(MemeStates.choosing_template, MemeCB.filter(F.action == "page"))
async def change_page(callback: CallbackQuery, callback_data: MemeCB, imgflip: ImgflipService) -> None:
    await _show_templates(callback, imgflip, callback_data.page)


@router.callback_query(MemeStates.choosing_template, MemeCB.filter(F.action == "noop"))
async def page_indicator(callback: CallbackQuery) -> None:
    await callback.answer()  # кнопка «2/5» только показывает номер страницы


@router.callback_query(MemeStates.choosing_template, MemeCB.filter(F.action == "tpl"))
async def pick_template(
    callback: CallbackQuery, callback_data: MemeCB, state: FSMContext, imgflip: ImgflipService
) -> None:
    templates = (await imgflip.get_templates()).templates
    template = next((t for t in templates if t.id == callback_data.value), None)
    if template is None:
        await callback.answer(texts_meme.STALE, show_alert=True)
        return
    await _use_template(callback, state, imgflip, template)


@router.callback_query(MemeStates.choosing_template, MemeCB.filter(F.action == "random"))
async def pick_random_template(callback: CallbackQuery, state: FSMContext, imgflip: ImgflipService) -> None:
    template = random.choice((await imgflip.get_templates()).templates)
    await _use_template(callback, state, imgflip, template)


async def _use_template(
    callback: CallbackQuery, state: FSMContext, imgflip: ImgflipService, template: MemeTemplate
) -> None:
    # Картинку проверяем сразу: если шаблон не скачать, не заставляем писать тексты зря.
    try:
        await imgflip.get_template_image(template)
    except ApiError:
        await callback.answer(texts_meme.TEMPLATE_DOWNLOAD_FAILED, show_alert=True)
        return

    await state.set_data(
        {"source": "template", "template_id": template.id, "template_name": template.name, "template_url": template.url}
    )
    await state.set_state(MemeStates.waiting_top)
    await show_screen(callback, texts_meme.ASK_TOP, cancel_kb(), media=template.url)


@router.callback_query(MemeStates.choosing_template, MemeCB.filter(F.action == "photo"))
async def choose_own_photo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(MemeStates.waiting_photo)
    await show_screen(callback, texts_meme.ASK_PHOTO, cancel_kb())


@router.message(MemeStates.waiting_photo, F.photo)
async def receive_photo(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]  # последний элемент списка это самый большой размер
    if photo.file_size and photo.file_size > MAX_UPLOAD_BYTES:
        await message.answer(texts_meme.PHOTO_TOO_BIG, reply_markup=cancel_kb())
        return
    await state.set_data({"source": "photo", "file_id": photo.file_id, "template_name": None})
    await state.set_state(MemeStates.waiting_top)
    await message.answer(texts_meme.ASK_TOP, reply_markup=cancel_kb())


@router.message(MemeStates.waiting_photo)
async def not_a_photo(message: Message) -> None:
    await message.answer(texts_meme.NOT_A_PHOTO, reply_markup=cancel_kb())


@router.message(MemeStates.choosing_template)
async def text_instead_of_button(message: Message) -> None:
    await message.answer("Выбери основу кнопкой выше 👆 или нажми /cancel")


# ---------- Шаги 2 и 3: тексты ----------


def _read_text(message: Message) -> str | None:
    """Очищенный текст из сообщения. «-» означает «пропустить». None: текст не подошёл."""
    raw = (message.text or "").strip()
    return "" if raw == "-" else clean_text(raw)


@router.message(MemeStates.waiting_top, F.text, ~F.text.startswith("/"))
async def receive_top(message: Message, state: FSMContext) -> None:
    try:
        top = _read_text(message)
    except MemeTextError as error:
        await message.answer(f"{error}. Попробуй короче.", reply_markup=cancel_kb())
        return
    await state.update_data(top_text=top)
    await state.set_state(MemeStates.waiting_bottom)
    await message.answer(texts_meme.ASK_BOTTOM, reply_markup=cancel_kb())


@router.message(MemeStates.waiting_bottom, F.text, ~F.text.startswith("/"))
async def receive_bottom(message: Message, state: FSMContext, bot: Bot, imgflip: ImgflipService) -> None:
    try:
        bottom = _read_text(message)
    except MemeTextError as error:
        await message.answer(f"{error}. Попробуй короче.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    if not (data.get("top_text") or bottom):
        await message.answer(texts_meme.NEED_ANY_TEXT, reply_markup=cancel_kb())
        return
    await state.update_data(bottom_text=bottom)
    await _render_preview(message, state, bot, imgflip)


@router.message(MemeStates.waiting_top)
@router.message(MemeStates.waiting_bottom)
async def need_text(message: Message) -> None:
    # Стикер, фото, неизвестная команда и т.п.
    await message.answer(texts_meme.NEED_TEXT_MESSAGE, reply_markup=cancel_kb())


# ---------- Превью ----------


async def _render_preview(message: Message, state: FSMContext, bot: Bot, imgflip: ImgflipService) -> None:
    data = await state.get_data()
    try:
        if data["source"] == "template":
            template = MemeTemplate(data["template_id"], data["template_name"], data["template_url"], 0, 0, 2)
            image_bytes = await imgflip.get_template_image(template)
        else:
            buffer = await bot.download(data["file_id"])
            image_bytes = buffer.read()
        # Работа с пикселями это CPU, а не ожидание сети. В отдельном потоке она не блокирует
        # event loop, и бот продолжает отвечать другим пользователям.
        jpeg = await asyncio.to_thread(
            build_meme, image_bytes, data.get("template_id", ""), data.get("top_text", ""), data["bottom_text"]
        )
    except ApiError:
        await state.clear()
        await message.answer(texts_meme.TEMPLATE_DOWNLOAD_FAILED, reply_markup=done_kb())
        return
    except (MemeImageError, TelegramBadRequest) as error:
        logger.warning("Не удалось подготовить картинку для мема: %s", error)
        await state.set_state(MemeStates.waiting_photo)
        await message.answer(texts_meme.IMAGE_PROBLEM, reply_markup=cancel_kb())
        return

    sent = await message.answer_photo(
        BufferedInputFile(jpeg, filename="meme.jpg"), caption=texts_meme.PREVIEW, reply_markup=preview_kb()
    )
    # Telegram вернул file_id отправленного фото: по нему мем можно показывать снова.
    await state.update_data(preview_file_id=sent.photo[-1].file_id)
    await state.set_state(MemeStates.preview)


@router.callback_query(MemeStates.preview, MemeCB.filter(F.action.in_({"publish", "save"})))
async def save_meme(
    callback: CallbackQuery, callback_data: MemeCB, state: FSMContext, user: User, session: AsyncSession
) -> None:
    data = await state.get_data()
    if not data.get("preview_file_id"):
        # Одновременное двойное нажатие: первый обработчик уже сохранил мем и очистил данные.
        await callback.answer(texts_meme.STALE, show_alert=True)
        return
    publish = callback_data.action == "publish"
    await state.clear()  # сначала выходим из состояния: повторное нажатие уже не пройдёт фильтр

    result = await MemeService(session).create_meme(
        user, data["preview_file_id"], data.get("template_name"), data.get("top_text", ""), data["bottom_text"], publish
    )
    text = texts_meme.format_created(publish, result.xp_awarded, result.level_change)
    kb = done_kb() if publish else saved_kb(result.meme.id)
    await show_screen(callback, text, kb, keep_media=True)


@router.callback_query(MemeStates.preview, MemeCB.filter(F.action == "redo"))
async def redo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(top_text="", bottom_text="", preview_file_id=None)
    await state.set_state(MemeStates.waiting_top)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)  # старое превью остаётся без кнопок
        await callback.message.answer(texts_meme.ASK_TOP, reply_markup=cancel_kb())
    await callback.answer()


@router.callback_query(MemeCB.filter(F.action == "publish_saved"))
async def publish_saved(
    callback: CallbackQuery, callback_data: MemeCB, user: User, session: AsyncSession
) -> None:
    if not callback_data.value.isdigit():
        await callback.answer(texts_meme.ALREADY_PUBLISHED, show_alert=True)
        return
    try:
        result = await MemeService(session).publish_meme(user, int(callback_data.value))
    except MemeNotPublishableError:
        await callback.answer(texts_meme.ALREADY_PUBLISHED, show_alert=True)
        return
    await show_screen(
        callback, texts_meme.format_published(result.xp_awarded, result.level_change), done_kb(), keep_media=True
    )


# ---------- Отмена и устаревшие кнопки ----------


@router.callback_query(MemeCB.filter(F.action == "cancel"))
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show_screen(callback, texts.MAIN_MENU, main_menu_kb())


@router.callback_query(MemeCB.filter())
async def stale_button(callback: CallbackQuery) -> None:
    await callback.answer(texts_meme.STALE, show_alert=True)
