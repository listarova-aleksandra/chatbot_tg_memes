"""Сквозные тесты викторины: настоящий Dispatcher + FSM + БД, Telegram поддельный.

Тест играет за пользователя: нажимает кнопки так, как их нажимал бы человек, а ответы
выбирает, заглядывая в данные FSM (там лежат вопросы текущей игры).
"""

from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageCaption,
    EditMessageText,
    SendMessage,
    SendPhoto,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import MenuCB, QuizCB
from app.bot.states.quiz import QuizStates
from app.database.models import QuizAttempt, QuizQuestion, QuizSession, User
from app.database.seed import seed_questions
from tests.conftest import FakeTelegramSession
from tests.updates import callback_update, message_update


class Player:
    """Один игрок (Аня, id=42), который пишет боту и нажимает кнопки."""

    def __init__(self, dp: Dispatcher, bot: Bot, telegram: FakeTelegramSession,
                 session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.dp, self.bot, self.telegram, self.session_factory = dp, bot, telegram, session_factory
        self._update_id = 0

    def _next_id(self) -> int:
        self._update_id += 1
        return self._update_id

    async def send(self, text: str) -> None:
        await self.dp.feed_raw_update(self.bot, message_update(text, self._next_id()))

    async def press(self, callback_data: str, *, photo: bool = False) -> None:
        await self.dp.feed_raw_update(
            self.bot, callback_update(callback_data, self._next_id(), photo=photo)
        )

    def fsm(self) -> FSMContext:
        return self.dp.fsm.get_context(bot=self.bot, chat_id=42, user_id=42)

    async def state(self) -> str | None:
        return await self.fsm().get_state()

    async def data(self) -> dict[str, Any]:
        return await self.fsm().get_data()

    async def question(self) -> QuizQuestion:
        data = await self.data()
        async with self.session_factory() as s:
            return await s.get(QuizQuestion, data["question_ids"][data["index"]])

    async def answer(self, *, correct: bool, photo: bool = False) -> None:
        """Нажимает кнопку с правильным (или неправильным) вариантом текущего вопроса."""
        data, question = await self.data(), await self.question()
        right = data["options"].index(question.correct_answer)
        choice = right if correct else (right + 1) % 4
        await self.press(QuizCB(action="ans", value=str(choice), q=data["index"]).pack(), photo=photo)

    async def next(self, *, photo: bool = False) -> None:
        data = await self.data()
        await self.press(QuizCB(action="next", q=data["index"]).pack(), photo=photo)

    async def start_game(self, category: str = "nba") -> None:
        await self.send("/start")
        await self.press(MenuCB(action="play").pack())
        await self.press(QuizCB(action="cat", value=category).pack())

    async def db_user(self) -> User:
        async with self.session_factory() as s:
            return (await s.execute(select(User))).scalar_one()

    def last_alert(self) -> AnswerCallbackQuery:
        return self.telegram.of_type(AnswerCallbackQuery)[-1]

    def last_text(self) -> str:
        edits = self.telegram.of_type(EditMessageText) + self.telegram.of_type(EditMessageCaption)
        return (edits[-1].text if isinstance(edits[-1], EditMessageText) else edits[-1].caption) or ""


@pytest.fixture
async def player(
    bot: Bot, telegram: FakeTelegramSession, make_dispatcher: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> Player:
    async with session_factory() as s:
        await seed_questions(s)
        await s.commit()
    return Player(make_dispatcher(), bot, telegram, session_factory)


# ---------- Основной сценарий ----------


async def test_full_game_all_correct(player: Player) -> None:
    await player.send("/start")
    await player.press(MenuCB(action="play").pack())
    assert await player.state() == QuizStates.choosing_category.state
    keyboard = player.telegram.of_type(EditMessageText)[-1].reply_markup.inline_keyboard
    labels = [b.text for row in keyboard for b in row]
    assert "🏀 NBA" in labels and "🇷🇺 Русская постирония" in labels and "🎲 Смешанная" in labels

    await player.press(QuizCB(action="cat", value="nba").pack())
    assert await player.state() == QuizStates.answering.state

    for number in range(1, 6):
        assert f"Вопрос {number}/5" in player.last_text()
        # Четыре варианта A-D в тексте и четыре кнопки.
        assert all(f"{letter}. " in player.last_text() for letter in "ABCD")
        await player.answer(correct=True)
        assert "Верно" in player.last_text() and "💡" in player.last_text()
        assert await player.state() == QuizStates.reviewing.state
        await player.next()

    assert await player.state() == QuizStates.finished.state
    result = player.last_text()
    assert "5/5" in result and "100%" in result and "+20 за игру без ошибок" in result
    assert "Новый уровень: <b>1 →" in result

    user = await player.db_user()
    assert user.level >= 2 and user.xp > 0 and user.best_streak == 5
    async with player.session_factory() as s:
        game = (await s.execute(select(QuizSession))).scalar_one()
        assert game.finished_at is not None and game.correct_count == 5 and game.xp_earned == user.xp
        assert await s.scalar(select(func.count()).select_from(QuizAttempt)) == 5

    # Профиль и история отражают игру.
    await player.send("/profile")
    assert "Всего игр: <b>1</b>" in player.telegram.of_type(SendMessage)[-1].text
    await player.send("/history")
    assert "5/5" in player.telegram.of_type(SendMessage)[-1].text

    # «Играть ещё» возвращает к выбору категории.
    await player.press(QuizCB(action="again").pack())
    assert await player.state() == QuizStates.choosing_category.state


async def test_wrong_answer_shows_correct_one_and_gives_no_xp(player: Player) -> None:
    await player.start_game()
    question = await player.question()
    await player.answer(correct=False)
    text = player.last_text()
    assert "Не угадал" in text and question.correct_answer in text
    assert question.explanation.split(".")[0][:20] in text
    assert (await player.db_user()).xp == 0


# ---------- Защита FSM ----------


async def test_double_tap_on_answer_counts_once(player: Player) -> None:
    await player.start_game()
    data = await player.data()
    question = await player.question()
    button = QuizCB(action="ans", value=str(data["options"].index(question.correct_answer)), q=0).pack()
    await player.press(button)
    xp_after_first = (await player.db_user()).xp
    await player.press(button)  # второе нажатие на ту же кнопку

    assert player.last_alert().show_alert is True
    assert (await player.db_user()).xp == xp_after_first
    async with player.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(QuizAttempt)) == 1


async def test_button_of_another_question_is_ignored(player: Player) -> None:
    await player.start_game()
    await player.press(QuizCB(action="ans", value="0", q=3).pack())  # кнопка «чужого» вопроса
    assert player.last_alert().show_alert is True
    assert await player.state() == QuizStates.answering.state
    async with player.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(QuizAttempt)) == 0


@pytest.mark.parametrize("value", ["9", "-1", "abc", ""])
async def test_invalid_option_index_is_rejected(player: Player, value: str) -> None:
    await player.start_game()
    await player.press(QuizCB(action="ans", value=value, q=0).pack())
    assert player.last_alert().show_alert is True
    assert await player.state() == QuizStates.answering.state


async def test_next_is_not_accepted_before_answering(player: Player) -> None:
    await player.start_game()
    await player.press(QuizCB(action="next", q=0).pack())  # шаг «answering», а не «reviewing»
    assert player.last_alert().show_alert is True
    assert (await player.data())["index"] == 0


async def test_buttons_after_restart_are_stale(player: Player) -> None:
    """После перезапуска бота состояния FSM потеряны: старые кнопки дают «игра неактуальна»."""
    await player.send("/start")  # состояния нет (как после рестарта)
    await player.press(QuizCB(action="cat", value="nba").pack())
    assert "неактуальна" in player.last_alert().text
    await player.press(QuizCB(action="ans", value="0", q=0).pack())
    assert "неактуальна" in player.last_alert().text


async def test_invalid_category_is_rejected(player: Player) -> None:
    await player.send("/start")
    await player.press(MenuCB(action="play").pack())
    await player.press(QuizCB(action="cat", value="hacked").pack())
    assert player.last_alert().show_alert is True
    assert await player.state() == QuizStates.choosing_category.state


async def test_category_without_questions(player: Player) -> None:
    async with player.session_factory() as s:
        await s.execute(update(QuizQuestion).where(QuizQuestion.category == "wnba").values(is_active=False))
        await s.commit()
    await player.send("/start")
    await player.press(MenuCB(action="play").pack())
    await player.press(QuizCB(action="cat", value="wnba").pack())
    assert "нет вопросов" in player.last_alert().text
    assert await player.state() == QuizStates.choosing_category.state


async def test_exit_mid_game_clears_state_and_game_stays_unfinished(player: Player) -> None:
    await player.start_game()
    await player.press(MenuCB(action="main").pack())  # кнопка «Выйти из игры»
    assert await player.state() is None
    async with player.session_factory() as s:
        assert (await s.execute(select(QuizSession))).scalar_one().finished_at is None
    await player.press(QuizCB(action="ans", value="0", q=0).pack())  # старая кнопка
    assert "неактуальна" in player.last_alert().text


# ---------- Картинки ----------


@pytest.fixture
async def photo_player(player: Player) -> Player:
    async with player.session_factory() as s:
        await s.execute(update(QuizQuestion).values(image_url="https://example.com/meme.jpg"))
        await s.commit()
    return player


async def test_question_with_image_is_sent_as_photo(photo_player: Player) -> None:
    p = photo_player
    await p.start_game()
    photos = p.telegram.of_type(SendPhoto)
    assert len(photos) == 1 and photos[0].photo == "https://example.com/meme.jpg"
    assert "Вопрос 1/5" in photos[0].caption
    assert p.telegram.of_type(DeleteMessage)  # старое текстовое сообщение (меню) удалено

    # Ответ редактирует подпись, а картинка остаётся.
    deletes = len(p.telegram.of_type(DeleteMessage))
    await p.answer(correct=True, photo=True)
    assert p.telegram.of_type(EditMessageCaption) and "Верно" in p.last_text()
    assert len(p.telegram.of_type(DeleteMessage)) == deletes

    # Следующий вопрос: новое фото (удаляем старое сообщение).
    await p.next(photo=True)
    assert len(p.telegram.of_type(SendPhoto)) == 2


async def test_broken_image_url_falls_back_to_text(photo_player: Player) -> None:
    p = photo_player
    p.telegram.fail_photos = True  # Telegram не смог скачать картинку
    await p.start_game()
    assert await p.state() == QuizStates.answering.state  # игра продолжается
    fallback = p.telegram.of_type(SendMessage)[-1].text
    assert "картинка не загрузилась" in fallback and "Вопрос 1/5" in fallback
