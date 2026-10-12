"""Лидерборд: порядок, место игрока, пустые и граничные случаи."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.leaderboard import TOP_SIZE, LeaderboardService
from app.services.user_service import UserService


async def make(session: AsyncSession, telegram_id: int, xp: int, username: str | None = None,
               name: str = "Игрок") -> User:
    user, _ = await UserService(session).get_or_register(telegram_id, username, name)
    user.xp = xp
    await session.flush()
    return user


async def test_empty_board_and_zero_xp_user_has_no_rank(session: AsyncSession) -> None:
    me = await make(session, 1, 0)
    board = await LeaderboardService(session).get(me)
    assert board.top == [] and board.my_rank is None and board.total_players == 0


async def test_order_by_xp_desc_with_names_and_medals_ranks(session: AsyncSession) -> None:
    a = await make(session, 1, 300, username="anya")
    b = await make(session, 2, 900, name="Борис")
    c = await make(session, 3, 500, username="vera")
    await make(session, 4, 0, name="Новичок")  # без XP в рейтинг не попадает
    board = await LeaderboardService(session).get(a)
    assert [(e.rank, e.name, e.xp) for e in board.top] == [(1, "Борис", 900), (2, "@vera", 500), (3, "@anya", 300)]
    assert board.my_rank == 3 and board.total_players == 3 and board.me_in_top(a.id)
    assert not board.me_in_top(99999)
    assert b.id != c.id


async def test_ties_are_ordered_by_registration_and_rank_matches_list(session: AsyncSession) -> None:
    users = [await make(session, i, 100) for i in range(1, 6)]  # у всех поровну
    service = LeaderboardService(session)
    board = await service.get(users[0])
    assert [e.user_id for e in board.top] == [u.id for u in users]  # раньше зарегистрирован, выше
    for position, user in enumerate(users, start=1):
        assert (await service.get(user)).my_rank == position


async def test_top_is_limited_and_outside_user_gets_real_rank(session: AsyncSession) -> None:
    users = [await make(session, i, 1000 - i * 10) for i in range(1, 26)]  # 25 игроков
    me = users[17]  # 18-й по XP
    board = await LeaderboardService(session).get(me)
    assert len(board.top) == TOP_SIZE == 10
    assert not board.me_in_top(me.id) and board.my_rank == 18 and board.total_players == 25
    assert [e.rank for e in board.top] == list(range(1, 11))


async def test_my_rank_agrees_with_full_ordering_for_everyone(session: AsyncSession) -> None:
    xps = [50, 700, 700, 20, 300, 300, 300, 1, 999, 0, 75]
    users = [await make(session, i + 1, xp) for i, xp in enumerate(xps)]
    service = LeaderboardService(session)
    ordered = sorted((u for u in users if u.xp > 0), key=lambda u: (-u.xp, u.id))
    for position, user in enumerate(ordered, start=1):
        assert (await service.get(user)).my_rank == position
    assert (await service.get(users[9])).my_rank is None  # xp == 0
