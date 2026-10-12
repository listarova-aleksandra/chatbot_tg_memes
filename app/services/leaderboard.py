"""Лидерборд: TOP-10 по XP и место конкретного игрока.

Порядок единый и строгий: больше XP выше, при равных XP выше тот, кто зарегистрировался раньше
(меньший id). По тому же правилу считается и место игрока вне топа, поэтому номера в списке
и «твоё место» никогда не противоречат друг другу.
"""

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.names import display_name

TOP_SIZE = 10


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    user_id: int
    name: str
    xp: int
    level: int


@dataclass(frozen=True)
class Leaderboard:
    top: list[LeaderboardEntry]
    my_rank: int | None  # None: у игрока 0 XP, в рейтинге его ещё нет
    my_xp: int
    my_level: int
    total_players: int  # игроков с XP больше нуля

    def me_in_top(self, user_id: int) -> bool:
        return any(entry.user_id == user_id for entry in self.top)


class LeaderboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user: User, limit: int = TOP_SIZE) -> Leaderboard:
        rows = await self.session.execute(
            select(User.id, User.username, User.first_name, User.xp, User.level)
            .where(User.xp > 0)
            .order_by(User.xp.desc(), User.id.asc())
            .limit(limit)
        )
        top = [
            LeaderboardEntry(rank, row.id, display_name(row.username, row.first_name), row.xp, row.level)
            for rank, row in enumerate(rows.all(), start=1)
        ]
        total = await self.session.scalar(select(func.count()).select_from(User).where(User.xp > 0)) or 0

        my_rank = None
        if user.xp > 0:
            # Место = 1 + число игроков, которые стоят выше по правилу сортировки.
            ahead = await self.session.scalar(
                select(func.count())
                .select_from(User)
                .where(or_(User.xp > user.xp, and_(User.xp == user.xp, User.id < user.id)))
            )
            my_rank = (ahead or 0) + 1
        return Leaderboard(top, my_rank, user.xp, user.level, total)
