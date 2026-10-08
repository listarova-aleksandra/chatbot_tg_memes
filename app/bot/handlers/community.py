"""Сообщество: лента мемов, лайки и дизлайки (/community и кнопка в меню).

FSM здесь не нужен: лента не «процесс из шагов», а просто просмотр. Всё, что нужно знать
(сортировка, номер страницы, id мема), лежит в callback_data кнопок.
"""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts_community as t
from app.bot.callbacks import CommunityCB, MenuCB
from app.bot.helpers import show_screen
from app.bot.keyboards.community import empty_feed_kb, feed_kb
from app.database.models import User
from app.services.community_service import (
    POPULAR,
    AlreadyVotedError,
    CommunityService,
    FeedItem,
    MemeNotFoundError,
    OwnMemeError,
)

logger = logging.getLogger(__name__)

router = Router(name="community")


@router.message(Command("community"))
@router.callback_query(MenuCB.filter(F.action == "community"))
async def open_feed(event: Message | CallbackQuery, user: User, session: AsyncSession) -> None:
    await _show_page(event, user, CommunityService(session), POPULAR, 0)


@router.callback_query(CommunityCB.filter(F.action == "view"))
async def change_page(
    callback: CallbackQuery, callback_data: CommunityCB, user: User, session: AsyncSession
) -> None:
    sort = callback_data.sort
    # Смена сортировки возвращает к началу ленты, листание сохраняет сортировку.
    await _show_page(callback, user, CommunityService(session), sort, callback_data.page)


@router.callback_query(CommunityCB.filter(F.action == "noop"))
async def page_indicator(callback: CallbackQuery) -> None:
    await callback.answer()  # кнопка «3/17» только показывает номер


@router.callback_query(CommunityCB.filter(F.action == "vote"))
async def vote(
    callback: CallbackQuery, callback_data: CommunityCB, user: User, session: AsyncSession
) -> None:
    service = CommunityService(session)
    try:
        result = await service.vote(user, callback_data.meme_id, callback_data.value)
    except AlreadyVotedError:
        await callback.answer(t.ALREADY_VOTED, show_alert=True)
        return
    except OwnMemeError:
        await callback.answer(t.OWN_MEME, show_alert=True)
        return
    except MemeNotFoundError:
        await callback.answer(t.MEME_GONE, show_alert=True)
        return
    except ValueError:  # подделанный callback_data с другим значением голоса
        await callback.answer(t.MEME_GONE, show_alert=True)
        return

    await _render(
        callback, user, result.item, callback_data.sort, callback_data.page, await service.count_published(),
        toast=t.VOTED_UP if callback_data.value == 1 else t.VOTED_DOWN,
    )

    if result.bonus_xp and result.author_notify and result.author_telegram_id:
        try:  # уведомление автору: не получилось отправить (бот заблокирован), не страшно
            await callback.bot.send_message(result.author_telegram_id, t.format_author_bonus(result.bonus_xp))
        except TelegramAPIError as error:
            logger.info("Не удалось уведомить автора мема: %s", error)


async def _show_page(
    event: Message | CallbackQuery, user: User, service: CommunityService, sort: str, page: int
) -> None:
    total = await service.count_published()
    if total == 0:
        await show_screen(event, t.EMPTY_FEED, empty_feed_kb())
        return
    page = min(max(page, 0), total - 1)
    item = await service.get_page(sort, page, user)
    assert item is not None
    await _render(event, user, item, sort, page, total)


async def _render(
    event: Message | CallbackQuery,
    user: User,
    item: FeedItem,
    sort: str,
    page: int,
    total: int,
    toast: str | None = None,
) -> None:
    text = t.format_item(item, sort, min(page, total - 1), total, is_own=item.author_id == user.id)
    kb = feed_kb(item, sort, min(page, total - 1), total)

    # Фото в фото меняется на месте (edit_media): без удаления и «мигания» сообщения.
    if isinstance(event, CallbackQuery) and isinstance(event.message, Message) and event.message.photo:
        try:
            await event.message.edit_media(
                InputMediaPhoto(media=item.file_id, caption=text, parse_mode="HTML"), reply_markup=kb
            )
            await event.answer(toast)
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                await event.answer(toast)
                return
            logger.debug("edit_media не удался (%s), отправляю заново", error)
    await show_screen(event, text, kb, media=item.file_id)
