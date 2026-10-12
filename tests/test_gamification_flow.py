"""Сквозные тесты достижений и лидерборда через бота."""

from typing import Any

import pytest
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import MemeCB, MenuCB
from app.database.models import Meme, User, UserAchievement
from app.database.seed import seed_questions
from app.services.achievements import seed_achievements
from app.services.meme_service import MemeService
from tests.test_community_service import make_meme, make_user
from tests.test_meme_flow import make as make_meme_player, to_preview
from tests.test_quiz_flow import Player


@pytest.fixture
async def player(bot, telegram, make_dispatcher, session_factory: async_sessionmaker[AsyncSession]) -> Player:
    async with session_factory() as s:
        await seed_questions(s)
        await seed_achievements(s)
        await s.commit()
    return Player(make_dispatcher(), bot, telegram, session_factory)


def screen(p: Player) -> str:
    """Текст последнего показанного экрана (по времени): либо отправленное, либо отредактированное сообщение."""
    for call in reversed(p.telegram.calls):
        if isinstance(call, EditMessageText | SendMessage):
            return call.text
    raise AssertionError("бот ничего не показал")


async def play_perfect_game(p: Player, category: str = "nba") -> None:
    await p.start_game(category)
    for _ in range(5):
        await p.answer(correct=True)
        await p.next()


# ---------- Достижения ----------


async def test_achievements_screen_shows_locked_progress(player: Player) -> None:
    await player.send("/start")
    await player.send("/achievements")
    text = screen(player)
    assert "Достижения</b> · 0/8" in text and "🔒" in text and "✅" not in text
    assert "0/10 мемов" in text and "0/20 в серии" in text
    await player.press(MenuCB(action="achievements").pack())  # то же самое кнопкой меню
    assert "0/8" in screen(player)


async def test_perfect_game_announces_new_achievements_once(player: Player) -> None:
    await play_perfect_game(player)
    result = screen(player)
    assert "Новые достижения!" in result and "First Win" in result and "Без права на ошибку" in result

    await player.send("/achievements")
    text = screen(player)
    assert "· 2/8" in text and text.count("✅") == 2 and "получено" in text

    # Вторая идеальная игра: уже полученные достижения не объявляются повторно, но набралось
    # 10 правильных ответов в MNBA, поэтому открывается новое («Hooper»).
    await play_perfect_game(player)
    second = screen(player)
    assert "Hooper" in second and "First Win" not in second and "Без права на ошибку" not in second
    async with player.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(UserAchievement)) == 3

    await play_perfect_game(player, "hiphop")  # третья игра: ничего нового
    assert "достижени" not in screen(player).lower()


async def test_bad_game_gives_no_achievements(player: Player) -> None:
    await player.start_game("nba")
    for _ in range(5):
        await player.answer(correct=False)
        await player.next()
    assert "достижени" not in screen(player).lower()


async def test_profile_shows_achievement_counter(player: Player) -> None:
    await play_perfect_game(player)
    await player.send("/profile")
    assert "Достижения: <b>2/8</b>" in screen(player)


async def test_meme_lord_is_announced_when_saving_the_tenth_meme(bot, telegram, session_factory, make_dispatcher) -> None:
    p = make_meme_player(bot, telegram, session_factory, make_dispatcher)
    async with session_factory() as s:
        await seed_achievements(s)
        await p.send("/start")
        user = (await s.execute(select(User))).scalar_one()
        for i in range(9):
            await MemeService(s).create_meme(user, f"m{i}", None, "а", "б", publish=False)
        await s.commit()
    await to_preview(p)
    await p.press(MemeCB(action="save").pack(), photo=True)
    assert "Meme Lord" in p.last_text() and "Новое достижение" in p.last_text()


async def test_author_gets_crowd_favorite_in_dm(bot, telegram, session_factory, make_dispatcher) -> None:
    from app.bot.callbacks import CommunityCB
    from app.services.xp import RATING_BONUS_THRESHOLD

    p = Player(make_dispatcher(), bot, telegram, session_factory)
    async with session_factory() as s:
        await seed_achievements(s)
        author = await make_user(s, 7, "Борис")
        meme = await make_meme(s, author, rating=RATING_BONUS_THRESHOLD - 1)
        meme_id = meme.id
        await s.commit()
    await p.send("/start")
    await p.press(CommunityCB(action="vote", meme_id=meme_id, value=1).pack(), photo=True)
    dm = [m for m in telegram.of_type(SendMessage) if m.chat_id == 7]
    assert len(dm) == 1 and "Народный любимец" in dm[0].text and "+10 XP" in dm[0].text


# ---------- Лидерборд ----------


async def seed_players(p: Player, count: int, me_xp: int) -> None:
    async with p.session_factory() as s:
        for i in range(count):
            user = await make_user(s, 1000 + i, f"Игрок{i}", username=f"user{i}")
            user.xp, user.level = 5000 - i * 100, 3
        me = (await s.execute(select(User).where(User.telegram_id == 42))).scalar_one()
        me.xp = me_xp
        await s.commit()


async def test_empty_leaderboard(player: Player) -> None:
    await player.send("/start")
    await player.send("/top")
    text = screen(player)
    assert "Рейтинг пока пуст" in text and "вне рейтинга" in text


async def test_top_ten_and_my_place_when_outside(player: Player) -> None:
    await player.send("/start")
    await seed_players(player, count=14, me_xp=777)
    await player.send("/top")
    text = screen(player)
    lines = text.splitlines()
    assert lines[2].startswith("🥇 @user0 — 5000 XP") and lines[3].startswith("🥈") and lines[4].startswith("🥉")
    import re
    assert sum(1 for line in lines if re.match(r"^(🥇|🥈|🥉|\d+\.) ", line)) == 10  # ровно десять строк рейтинга
    assert "10. @user9" in text and "@user10" not in text
    assert "Твоё место: <b>15</b> из 15" in text and "777 XP" in text and "👈 ты" not in text


async def test_me_inside_top_is_marked_without_extra_place_line(player: Player) -> None:
    await player.send("/start")
    await seed_players(player, count=5, me_xp=4750)  # между user2 (4800) и user3 (4700)
    await player.press(MenuCB(action="top").pack())
    text = screen(player)
    assert "👈 ты" in text and "Твоё место" not in text
    assert [line for line in text.splitlines() if "👈" in line][0].startswith("4.")


async def test_names_in_leaderboard_are_html_escaped(player: Player) -> None:
    await player.send("/start")
    async with player.session_factory() as s:
        evil = await make_user(s, 555, "<b>Хакер</b>")
        evil.xp = 100
        await s.commit()
    await player.send("/top")
    text = screen(player)
    assert "<b>Хакер</b>" not in text and "&lt;b&gt;" in text
