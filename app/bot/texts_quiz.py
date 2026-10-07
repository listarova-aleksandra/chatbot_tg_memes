"""Тексты викторины (HTML). Контент вопросов экранируется на всякий случай."""

from html import escape

from app.bot.keyboards.quiz import LETTERS
from app.bot.texts import category_label
from app.database.models import QuizQuestion
from app.services.quiz_service import AnswerResult, GameHistoryItem, GameResult

CHOOSE_CATEGORY = "🎮 <b>Викторина</b>\nВыбери категорию. В игре 5 вопросов:"

NO_QUESTIONS = "В этой категории пока нет вопросов 😔 Выбери другую."

STALE_GAME = "Эта игра уже неактуальна. Начни новую: /play"


def question_header(question: QuizQuestion, index: int, total: int) -> str:
    stars = "⭐" * question.difficulty
    return f"🧠 <b>Вопрос {index + 1}/{total}</b> · {category_label(question.category)} · {stars}"


def format_question(question: QuizQuestion, options: list[str], index: int, total: int) -> str:
    lines = [f"{LETTERS[i]}. {escape(option)}" for i, option in enumerate(options)]
    return f"{question_header(question, index, total)}\n\n{escape(question.question)}\n\n" + "\n".join(lines)


def format_answer(
    question: QuizQuestion, index: int, total: int, selected: str, result: AnswerResult
) -> str:
    head = question_header(question, index, total)
    if result.is_correct:
        verdict = f"✅ <b>Верно!</b> +{result.xp_awarded} XP"
        if result.streak >= 3:
            verdict += f"\n🔥 Серия: {result.streak} подряд"
    else:
        verdict = (
            "❌ <b>Не угадал.</b>\n"
            f"Твой ответ: {escape(selected)}\n"
            f"Правильный ответ: <b>{escape(result.correct_answer)}</b>"
        )
    return f"{head}\n\n{escape(question.question)}\n\n{verdict}\n\n💡 {escape(result.explanation)}"


def verdict_for(accuracy: float) -> str:
    if accuracy == 100:
        return "Идеально! Ты мем-босс 👑"
    if accuracy >= 60:
        return "Неплохо, чувствуется насмотренность 😎"
    if accuracy >= 40:
        return "Есть куда расти, но вайб ловишь 🙂"
    return "Сегодня не твой день, зато завтра реванш 💪"


def format_result(result: GameResult) -> str:
    lines = [
        "🏁 <b>Игра окончена!</b>",
        category_label(result.category),
        "",
        f"✅ Правильных ответов: <b>{result.correct}/{result.total}</b> ({result.accuracy:.0f}%)",
        f"✨ Получено XP: <b>+{result.xp_earned}</b>",
    ]
    if result.perfect_bonus:
        lines.append(f"   в том числе +{result.perfect_bonus} за игру без ошибок")
    change = result.level_change
    if change.leveled_up:
        lines.append(f"⭐ Новый уровень: <b>{change.old_level} → {change.new_level}</b> 🎉")
    else:
        lines.append(f"⭐ Уровень: {change.new_level}")
    lines += ["", verdict_for(result.accuracy)]
    return "\n".join(lines)


def format_history(items: list[GameHistoryItem]) -> str:
    if not items:
        return "📜 <b>История игр</b>\n\nПока пусто. Сыграй первую игру!"
    lines = ["📜 <b>История игр</b> (последние 5)\n"]
    for item in items:
        date = item.started_at.strftime("%d.%m %H:%M")
        lines.append(
            f"{date} · {category_label(item.category)} · {item.correct}/{item.total} · +{item.xp_earned} XP"
        )
    return "\n".join(lines)
