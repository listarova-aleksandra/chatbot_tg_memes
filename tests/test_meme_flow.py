"""Сквозные тесты генератора мемов: настоящий Dispatcher + FSM + БД, Telegram поддельный."""

import io
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageCaption, SendMessage, SendPhoto
from aiogram.types import BufferedInputFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import MemeCB, MenuCB
from app.bot.states.meme import MemeStates
from app.core.cache import TTLCache
from app.core.exceptions import ApiUnavailableError
from app.database.models import Meme
from app.services import meme_renderer as mr
from app.services.imgflip_service import ImgflipService
from tests.conftest import FakeApiClient, FakeTelegramSession
from tests.test_imgflip_service import meme as imgflip_meme, response as imgflip_response
from tests.test_quiz_flow import Player
from tests.updates import photo_update, sticker_update

pytestmark = pytest.mark.skipif(mr.find_font_path() is None, reason="нет системного шрифта с кириллицей")


def png(width: int = 400, height: int = 300) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (30, 90, 160)).save(out, format="PNG")
    return out.getvalue()


class MemePlayer(Player):
    async def send_photo(self, file_id: str = "user-photo", size: int = 1000) -> None:
        await self.dp.feed_raw_update(self.bot, photo_update(self._next_id(), file_id, size))

    async def send_sticker(self) -> None:
        await self.dp.feed_raw_update(self.bot, sticker_update(self._next_id()))

    def sent_texts(self) -> list[str]:
        return [m.text for m in self.telegram.of_type(SendMessage)]

    def last_preview(self) -> SendPhoto:
        return [p for p in self.telegram.of_type(SendPhoto) if isinstance(p.photo, BufferedInputFile)][-1]

    async def memes(self) -> list[Meme]:
        async with self.session_factory() as s:
            return list((await s.execute(select(Meme))).scalars())


def make(bot: Bot, telegram: FakeTelegramSession, session_factory: Any, make_dispatcher: Any,
         templates: int = 12, download: Any = None) -> MemePlayer:
    client = FakeApiClient(imgflip_response(*[imgflip_meme(i) for i in range(1, templates + 1)]))
    client.image_bytes = png() if download is None else download
    imgflip = ImgflipService(client, TTLCache())  # type: ignore[arg-type]
    return MemePlayer(make_dispatcher(imgflip=imgflip), bot, telegram, session_factory)


def keyboard_labels(message: Any) -> list[str]:
    return [b.text for row in message.reply_markup.inline_keyboard for b in row]


async def to_preview(p: MemePlayer, top: str = "верхний текст", bottom: str = "нижний текст") -> None:
    await p.send("/meme")
    await p.press(MemeCB(action="tpl", value="1").pack())
    await p.send(top)
    await p.send(bottom)


# ---------- Основной сценарий ----------


async def test_full_flow_template_to_publication(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)

    await p.send("/start")
    await p.press(MenuCB(action="meme").pack())
    assert await p.state() == MemeStates.choosing_template.state
    labels = keyboard_labels(p.telegram.of_type(__import__("aiogram").methods.EditMessageText)[-1])
    assert "Template 1" in labels and "📷 Своё фото" in labels and "🎲 Случайный" in labels

    await p.press(MemeCB(action="tpl", value="1").pack())
    assert await p.state() == MemeStates.waiting_top.state
    assert "Шаг 2 из 3" in p.telegram.of_type(SendPhoto)[-1].caption  # шаблон показан картинкой

    await p.send("Когда ты в 2026")
    assert await p.state() == MemeStates.waiting_bottom.state
    await p.send("а мем всё ещё про Дрейка")
    assert await p.state() == MemeStates.preview.state

    preview = p.last_preview()
    image = Image.open(io.BytesIO(preview.photo.data))
    assert image.format == "JPEG" and image.size == (400, 300)  # готовая картинка, не оригинал
    assert keyboard_labels(preview) == ["📢 Опубликовать", "💾 Сохранить", "🔄 Заново", "❌ Отмена"]

    await p.press(MemeCB(action="publish").pack(), photo=True)
    assert await p.state() is None
    assert "Мем опубликован" in p.last_text() and "+10 XP" in p.last_text()
    [meme] = await p.memes()
    assert meme.is_published and (meme.top_text, meme.bottom_text) == ("Когда ты в 2026", "а мем всё ещё про Дрейка")
    assert meme.template_name == "Template 1" and meme.image_file_id.startswith("fid-")
    assert (await p.db_user()).xp == 10

    # Подпись отредактирована (картинка осталась), новое сообщение не отправлено.
    assert p.telegram.of_type(EditMessageCaption)


async def test_save_then_publish_later(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await to_preview(p)
    await p.press(MemeCB(action="save").pack(), photo=True)
    [meme] = await p.memes()
    assert not meme.is_published and (await p.db_user()).xp == 5
    assert "+5 XP" in p.last_text()

    publish = MemeCB(action="publish_saved", value=str(meme.id)).pack()
    await p.press(publish, photo=True)
    assert (await p.memes())[0].is_published and (await p.db_user()).xp == 10
    await p.press(publish, photo=True)  # повторное нажатие
    assert p.last_alert().show_alert is True and (await p.db_user()).xp == 10


async def test_double_tap_on_publish_creates_one_meme(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await to_preview(p)
    await p.press(MemeCB(action="publish").pack(), photo=True)
    await p.press(MemeCB(action="publish").pack(), photo=True)
    assert len(await p.memes()) == 1 and (await p.db_user()).xp == 10
    assert p.last_alert().show_alert is True


async def test_own_photo_flow(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    telegram.file_bytes = png(600, 900)
    await p.send("/meme")
    await p.press(MemeCB(action="photo").pack())
    assert await p.state() == MemeStates.waiting_photo.state
    await p.send_photo()
    assert await p.state() == MemeStates.waiting_top.state
    await p.send("-")  # пропустили верхний текст
    await p.send("только низ")
    image = Image.open(io.BytesIO(p.last_preview().photo.data))
    assert image.size == (600, 900)
    await p.press(MemeCB(action="save").pack(), photo=True)
    [meme] = await p.memes()
    assert meme.template_name is None and meme.top_text == "" and meme.bottom_text == "ТОЛЬКО НИЗ".lower()


# ---------- Ввод пользователя ----------


async def test_both_texts_empty_is_rejected(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await to_preview(p, top="-", bottom="-")
    assert await p.state() == MemeStates.waiting_bottom.state
    assert "хотя бы один текст" in p.sent_texts()[-1]
    await p.send("😂😂😂")  # одни эмодзи: после очистки пусто
    assert await p.state() == MemeStates.waiting_bottom.state


async def test_too_long_text_is_rejected_and_state_kept(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await p.send("/meme")
    await p.press(MemeCB(action="tpl", value="1").pack())
    await p.send("а" * 101)
    assert "максимум 100" in p.sent_texts()[-1]
    assert await p.state() == MemeStates.waiting_top.state
    await p.send("а" * 100)  # ровно на границе принимается
    assert await p.state() == MemeStates.waiting_bottom.state


async def test_sticker_and_unknown_command_are_not_accepted_as_text(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await p.send("/meme")
    await p.press(MemeCB(action="tpl", value="1").pack())
    await p.send_sticker()
    assert "Пришли текст" in p.sent_texts()[-1]
    await p.send("/unknowncommand")
    assert "Пришли текст" in p.sent_texts()[-1]
    assert await p.state() == MemeStates.waiting_top.state


async def test_text_instead_of_photo_and_oversized_photo(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await p.send("/meme")
    await p.press(MemeCB(action="photo").pack())
    await p.send("вот фото, честно")
    assert "именно фото" in p.sent_texts()[-1]
    await p.send_photo(size=9_000_000)
    assert "слишком большой" in p.sent_texts()[-1]
    assert await p.state() == MemeStates.waiting_photo.state


async def test_unreadable_image_asks_for_another_photo(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    telegram.file_bytes = b"this is not an image"
    await p.send("/meme")
    await p.press(MemeCB(action="photo").pack())
    await p.send_photo()
    await p.send("а")
    await p.send("б")
    assert "Не получилось обработать картинку" in p.sent_texts()[-1]
    assert await p.state() == MemeStates.waiting_photo.state
    assert await p.memes() == []


# ---------- Навигация ----------


async def test_redo_returns_to_top_text_and_new_preview_replaces_old(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await to_preview(p, "первый", "вариант")
    await p.press(MemeCB(action="redo").pack(), photo=True)
    assert await p.state() == MemeStates.waiting_top.state
    await p.send("второй")
    await p.send("вариант")
    assert await p.state() == MemeStates.preview.state
    await p.press(MemeCB(action="publish").pack(), photo=True)
    [meme] = await p.memes()
    assert meme.top_text == "второй"  # в БД второй вариант


async def test_cancel_clears_state_and_old_buttons_become_stale(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await to_preview(p)
    await p.press(MemeCB(action="cancel").pack(), photo=True)
    assert await p.state() is None and await p.memes() == []
    await p.press(MemeCB(action="publish").pack(), photo=True)  # старая кнопка
    assert "неактуален" in p.last_alert().text
    assert await p.memes() == [] and (await p.db_user()).xp == 0


async def test_command_menu_and_cancel_exit_the_flow(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await p.send("/meme")
    await p.press(MemeCB(action="tpl", value="1").pack())
    await p.send("/cancel")
    assert await p.state() is None
    await p.send("обычный текст")  # вне сценария это не верхний текст
    assert await p.state() is None and await p.memes() == []


async def test_template_pagination(bot, telegram, session_factory, make_dispatcher) -> None:
    from aiogram.methods import EditMessageText

    p = make(bot, telegram, session_factory, make_dispatcher, templates=20)
    await p.send("/start")
    await p.press(MenuCB(action="meme").pack())
    first = telegram.of_type(EditMessageText)[-1]
    assert "Template 1" in keyboard_labels(first) and "Template 9" not in keyboard_labels(first)
    assert "1/3" in keyboard_labels(first)
    await p.press(MemeCB(action="page", page=1).pack())
    second = telegram.of_type(EditMessageText)[-1]
    assert "Template 9" in keyboard_labels(second) and "2/3" in keyboard_labels(second)
    await p.press(MemeCB(action="page", page=99).pack())  # страница за границами не ломает
    assert "3/3" in keyboard_labels(telegram.of_type(EditMessageText)[-1])


async def test_random_template(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher)
    await p.send("/meme")
    await p.press(MemeCB(action="random").pack())
    assert await p.state() == MemeStates.waiting_top.state


# ---------- Сбои Imgflip ----------


async def test_imgflip_down_shows_fallback_templates_and_flow_still_works(bot, telegram, session_factory, make_dispatcher) -> None:
    from aiogram.methods import EditMessageText

    imgflip = ImgflipService(FakeApiClient(ApiUnavailableError("down")), TTLCache())  # type: ignore[arg-type]
    p = MemePlayer(make_dispatcher(imgflip=imgflip), bot, telegram, session_factory)
    await p.send("/start")
    await p.press(MenuCB(action="meme").pack())
    shown = telegram.of_type(EditMessageText)[-1]
    assert "Imgflip сейчас не отвечает" in shown.text and "Тёмный фон" in keyboard_labels(shown)

    await p.press(MemeCB(action="tpl", value="local-dark").pack())
    await p.send("верх")
    await p.send("низ")
    image = Image.open(io.BytesIO(p.last_preview().photo.data))
    assert image.size == (800, 600)  # нарисован на градиентном фоне
    await p.press(MemeCB(action="publish").pack(), photo=True)
    assert len(await p.memes()) == 1


async def test_template_image_download_failure_keeps_user_on_selection(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make(bot, telegram, session_factory, make_dispatcher, download=ApiUnavailableError("down"))
    await p.send("/meme")
    await p.press(MemeCB(action="tpl", value="1").pack())
    assert "Не получилось загрузить картинку шаблона" in p.last_alert().text and p.last_alert().show_alert
    assert await p.state() == MemeStates.choosing_template.state
    await p.press(MemeCB(action="photo").pack())  # можно продолжить со своим фото
    assert await p.state() == MemeStates.waiting_photo.state


async def test_concurrent_duplicate_tap_after_data_cleared_is_handled_gracefully(bot, telegram, session_factory, make_dispatcher) -> None:
    """Второй обработчик успел пройти фильтр состояния, а данные уже очищены первым."""
    p = make(bot, telegram, session_factory, make_dispatcher)
    await p.fsm().set_state(MemeStates.preview)  # состояние есть, данных сценария нет
    await p.press(MemeCB(action="publish").pack(), photo=True)
    assert p.last_alert().show_alert is True and "неактуален" in p.last_alert().text
    assert await p.memes() == [] and not any("пошло не так" in (t or "") for t in p.sent_texts())
