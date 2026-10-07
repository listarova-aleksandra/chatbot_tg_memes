"""Викторина: хендлеры, управляемые FSM (состояния см. app/bot/states/quiz.py).

Каждый хендлер привязан к состоянию: `QuizStates.answering` в декораторе означает
«выполнять только если пользователь сейчас на этом шаге». Данные текущей игры лежат
в `state.data`:
    session_id     id игры в БД
    question_ids   вопросы этой игры (по порядку)
    index          номер текущего вопроса (с 0)
    options        перемешанные варианты текущего вопроса
    level_before   уровень на старте игры (чтобы в конце показать «уровень вырос»)
"""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, texts_quiz
from app.bot.callbacks import MenuCB, QuizCB
from app.bot.helpers import show_screen
from app.bot.keyboards.quiz import answer_kb, category_kb, finished_kb, next_kb
from app.bot.states.quiz import QuizStates
from app.database.models import QuizCategory, User
from app.services.giphy_service import UNAVAILABLE, GiphyService, mood_for_accuracy
from app.services.quiz_service import GameResult, NoQuestionsError, QuizService, StaleAnswerError

logger = logging.getLogger(__name__)

router = Router(name="quiz")


async def _stale(callback: CallbackQuery) -> None:
    """Нажата кнопка старой игры (или бот перезапускался, и состояние потерялось)."""
    await callback.answer(texts_quiz.STALE_GAME, show_alert=True)


# ---------- Шаг 1: выбор категории ----------


@router.message(Command("play"))
@router.callback_query(MenuCB.filter(F.action == "play"))
@router.callback_query(QuizCB.filter(F.action == "again"))
async def choose_category(event: Message | CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await state.set_state(QuizStates.choosing_category)
    await show_screen(event, texts_quiz.CHOOSE_CATEGORY, category_kb(user.default_category))


# ---------- Шаг 2: старт игры, первый вопрос ----------


@router.callback_query(QuizStates.choosing_category, QuizCB.filter(F.action == "cat"))
async def start_game(
    callback: CallbackQuery,
    callback_data: QuizCB,
    state: FSMContext,
    user: User,
    session: AsyncSession,
) -> None:
    # Значение пришло из callback_data: проверяем, что это настоящая категория.
    if callback_data.value not in {c.value for c in QuizCategory}:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    service = QuizService(session)
    try:
        game = await service.start_game(user, callback_data.value)
    except NoQuestionsError:
        await callback.answer(texts_quiz.NO_QUESTIONS, show_alert=True)
        return

    await state.set_data(
        {
            "session_id": game.session_id,
            "question_ids": game.question_ids,
            "index": 0,
            "level_before": user.level,
        }
    )
    await _show_question(callback, state, service, index=0)


async def _show_question(
    callback: CallbackQuery, state: FSMContext, service: QuizService, index: int
) -> None:
    data = await state.get_data()
    question = await service.get_question(data["question_ids"][index])
    options = service.make_options(question)

    await state.update_data(index=index, options=options)
    await state.set_state(QuizStates.answering)
    await show_screen(
        callback,
        texts_quiz.format_question(question, options, index, len(data["question_ids"])),
        answer_kb(len(options), index),
        photo=question.image_url,
    )


# ---------- Шаг 3: ответ ----------


@router.callback_query(QuizStates.answering, QuizCB.filter(F.action == "ans"))
async def handle_answer(
    callback: CallbackQuery,
    callback_data: QuizCB,
    state: FSMContext,
    user: User,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    options: list[str] = data.get("options", [])
    choice = int(callback_data.value) if callback_data.value.isdigit() else -1

    # Кнопка от другого вопроса или невозможный номер варианта: игнорируем.
    if callback_data.q != data["index"] or not 0 <= choice < len(options):
        await _stale(callback)
        return

    # Состояние меняем до обращения к БД: повторное нажатие уже не пройдёт фильтр
    # `QuizStates.answering`. От гонки двух одновременных нажатий защищает UNIQUE в БД.
    await state.set_state(QuizStates.reviewing)

    service = QuizService(session)
    index = data["index"]
    question = await service.get_question(data["question_ids"][index])
    selected = options[choice]
    try:
        result = await service.submit_answer(user, data["session_id"], question, selected)
    except StaleAnswerError:
        await _stale(callback)
        return

    total = len(data["question_ids"])
    await show_screen(
        callback,
        texts_quiz.format_answer(question, index, total, selected, result),
        next_kb(index, is_last=index + 1 >= total),
        keep_photo=True,
    )


# ---------- Шаг 4: дальше или итог ----------


@router.callback_query(QuizStates.reviewing, QuizCB.filter(F.action == "next"))
async def next_step(
    callback: CallbackQuery,
    callback_data: QuizCB,
    state: FSMContext,
    user: User,
    session: AsyncSession,
    giphy: GiphyService,
) -> None:
    data = await state.get_data()
    if callback_data.q != data["index"]:
        await _stale(callback)
        return

    service = QuizService(session)
    next_index = data["index"] + 1
    if next_index < len(data["question_ids"]):
        await _show_question(callback, state, service, next_index)
        return

    try:
        result = await service.finish_game(user, data["session_id"], data["level_before"])
    except StaleAnswerError:
        await _stale(callback)
        return
    await state.set_state(QuizStates.finished)
    await show_screen(callback, texts_quiz.format_result(result), finished_kb())
    # GIF отправляется ПОСЛЕ итогового экрана: даже если Giphy тормозит, игрок уже видит результат.
    await _send_reaction_gif(callback, giphy, result)


async def _send_reaction_gif(callback: CallbackQuery, giphy: GiphyService, result: GameResult) -> None:
    """GIF-реакция на результат игры. Любые сбои Giphy не мешают игре."""
    mood = mood_for_accuracy(result.accuracy)
    if mood is None or not giphy.enabled:
        return
    gif = await giphy.get_reaction(mood, result.category)
    chat_id = callback.from_user.id  # игра идёт в личном чате, id чата равен id пользователя
    if gif.status == UNAVAILABLE:
        await callback.bot.send_message(chat_id, texts.GIF_UNAVAILABLE)
    elif gif.url is not None:
        try:
            await callback.bot.send_animation(chat_id, gif.url)
        except TelegramBadRequest as error:
            logger.warning("Telegram не принял GIF: %s", error)


# ---------- Всё остальное: устаревшие кнопки ----------
# Этот хендлер последний. Сюда попадает любая кнопка викторины, которая не подошла
# по состоянию (старое сообщение, перезапуск бота, повторное нажатие).


@router.callback_query(QuizCB.filter())
async def stale_quiz_button(callback: CallbackQuery) -> None:
    await _stale(callback)
