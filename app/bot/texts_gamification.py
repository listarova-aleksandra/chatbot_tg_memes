"""Тексты достижений и лидерборда (HTML). Имена игроков экранируются."""

from collections.abc import Sequence
from html import escape

from app.services.achievements import ACHIEVEMENTS, AchievementDef, AchievementStatus
from app.services.leaderboard import Leaderboard

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def format_new_achievements(achievements: Sequence[AchievementDef]) -> str:
    """Блок «новое достижение» для добавления в конец итогового сообщения. Пусто, если нет новых."""
    if not achievements:
        return ""
    title = "🏅 <b>Новое достижение!</b>" if len(achievements) == 1 else "🏅 <b>Новые достижения!</b>"
    lines = [f"{a.icon} <b>{escape(a.name)}</b>: {escape(a.description)}" for a in achievements]
    return "\n\n" + title + "\n" + "\n".join(lines)


def format_achievements(statuses: Sequence[AchievementStatus]) -> str:
    earned = sum(1 for s in statuses if s.earned)
    lines = [f"🏅 <b>Достижения</b> · {earned}/{len(ACHIEVEMENTS)}", ""]
    for status in statuses:
        a = status.definition
        if status.earned:
            date = status.earned_at.strftime("%d.%m.%Y") if status.earned_at else ""
            lines.append(f"✅ {a.icon} <b>{escape(a.name)}</b>\n    {escape(a.description)} · получено {date}")
        else:
            lines.append(
                f"🔒 {a.icon} <b>{escape(a.name)}</b>\n"
                f"    {escape(a.description)} · {status.progress}/{a.target} {a.unit}"
            )
    return "\n".join(lines)


def format_leaderboard(board: Leaderboard, user_id: int) -> str:
    lines = ["🏆 <b>Топ игроков</b>", ""]
    if not board.top:
        lines.append("Рейтинг пока пуст: никто ещё не набрал XP. Сыграй в викторину и стань первым! 🎮")
    for entry in board.top:
        place = MEDALS.get(entry.rank, f"{entry.rank}.")
        mark = "  👈 ты" if entry.user_id == user_id else ""
        lines.append(f"{place} {escape(entry.name)} — {entry.xp} XP · ур. {entry.level}{mark}")

    lines.append("")
    if board.my_rank is None:
        lines.append("📍 Ты пока вне рейтинга: набери первый XP в викторине.")
    elif not board.me_in_top(user_id):
        lines.append("…")
        lines.append(
            f"📍 Твоё место: <b>{board.my_rank}</b> из {board.total_players} — "
            f"{board.my_xp} XP · ур. {board.my_level}"
        )
    return "\n".join(lines)
