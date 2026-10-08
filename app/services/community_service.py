"""Сообщество: лента опубликованных мемов, голосование, рейтинг.

Защита от повторного голосования устроена на двух уровнях:
  1. таблица meme_votes имеет UNIQUE(user_id, meme_id): второй голос того же человека за тот
     же мем база отклонит сама, даже если нажатия придут одновременно;
  2. счётчики мема меняются одним SQL-выражением `likes_count = likes_count + 1`, а не
     «прочитал, прибавил в Python, записал». Так параллельные голоса не затирают друг друга.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Meme, MemeVote, User
from app.services.gamification import add_xp
from app.services.xp import RATING_BONUS_THRESHOLD, XP_RATING_BONUS

logger = logging.getLogger(__name__)

POPULAR, NEW = "pop", "new"


class MemeNotFoundError(Exception):
    """Мем не найден или не опубликован."""


class OwnMemeError(Exception):
    """Нельзя голосовать за свой мем."""


class AlreadyVotedError(Exception):
    """Пользователь уже голосовал за этот мем."""


@dataclass(frozen=True)
class FeedItem:
    meme_id: int
    file_id: str
    author_name: str
    author_id: int
    published_at: datetime | None
    likes: int
    dislikes: int
    rating: int
    my_vote: int | None  # +1, -1 или None, если пользователь ещё не голосовал


@dataclass(frozen=True)
class VoteResult:
    item: FeedItem
    bonus_xp: int  # XP, начисленные автору за рейтинг (0, если бонуса не было)
    author_telegram_id: int | None
    author_notify: bool


def _author_name(username: str | None, first_name: str) -> str:
    return f"@{username}" if username else first_name


class CommunityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_published(self) -> int:
        return await self.session.scalar(
            select(func.count()).select_from(Meme).where(Meme.is_published.is_(True))
        ) or 0

    async def get_page(self, sort: str, offset: int, user: User) -> FeedItem | None:
        """Мем на позиции `offset` в выбранной сортировке (позиции с нуля)."""
        if sort == NEW:
            order = (Meme.published_at.desc(), Meme.id.desc())
        else:
            # Популярные: по рейтингу, при равенстве более свежие выше. id делает порядок строгим.
            order = (Meme.rating.desc(), Meme.published_at.desc(), Meme.id.desc())
        row = (
            await self.session.execute(
                select(Meme, User.username, User.first_name)
                .join(User, User.id == Meme.author_id)
                .where(Meme.is_published.is_(True))
                .order_by(*order)
                .offset(max(offset, 0))
                .limit(1)
            )
        ).first()
        return await self._to_item(row, user) if row else None

    async def get_item(self, meme_id: int, user: User) -> FeedItem | None:
        row = (
            await self.session.execute(
                select(Meme, User.username, User.first_name)
                .join(User, User.id == Meme.author_id)
                .where(Meme.id == meme_id, Meme.is_published.is_(True))
            )
        ).first()
        return await self._to_item(row, user) if row else None

    async def _to_item(self, row, user: User) -> FeedItem:
        meme, username, first_name = row
        my_vote = await self.session.scalar(
            select(MemeVote.vote).where(MemeVote.meme_id == meme.id, MemeVote.user_id == user.id)
        )
        return FeedItem(
            meme_id=meme.id,
            file_id=meme.image_file_id,
            author_name=_author_name(username, first_name),
            author_id=meme.author_id,
            published_at=meme.published_at,
            likes=meme.likes_count,
            dislikes=meme.dislikes_count,
            rating=meme.rating,
            my_vote=my_vote,
        )

    async def vote(self, user: User, meme_id: int, value: int) -> VoteResult:
        """Голос за мем: +1 (лайк) или -1 (дизлайк). Голос окончательный."""
        if value not in (1, -1):
            raise ValueError("vote must be 1 or -1")
        meme = await self.session.get(Meme, meme_id)
        if meme is None or not meme.is_published:
            raise MemeNotFoundError(meme_id)
        if meme.author_id == user.id:
            raise OwnMemeError(meme_id)

        try:
            # SAVEPOINT: если вставка нарушит UNIQUE, откатится только она, а не вся транзакция.
            async with self.session.begin_nested():
                self.session.add(MemeVote(meme_id=meme_id, user_id=user.id, vote=value))
                await self.session.flush()
        except IntegrityError as error:
            raise AlreadyVotedError(meme_id) from error

        # Счётчики меняются на стороне БД одним выражением (атомарно).
        await self.session.execute(
            update(Meme)
            .where(Meme.id == meme_id)
            .values(
                likes_count=Meme.likes_count + (1 if value == 1 else 0),
                dislikes_count=Meme.dislikes_count + (1 if value == -1 else 0),
                rating=Meme.rating + value,
            )
        )
        await self.session.refresh(meme)

        bonus_xp, author_telegram_id, author_notify = await self._maybe_award_rating_bonus(meme)
        item = (await self.get_item(meme_id, user))
        assert item is not None
        return VoteResult(item, bonus_xp, author_telegram_id, author_notify)

    async def _maybe_award_rating_bonus(self, meme: Meme) -> tuple[int, int | None, bool]:
        """Автору +10 XP, когда рейтинг мема впервые достиг порога. Бонус выдаётся один раз:
        флаг bonus_awarded меняется условным UPDATE, который сработает только для одного голоса."""
        if meme.rating < RATING_BONUS_THRESHOLD:
            return 0, None, False
        claimed = await self.session.execute(
            update(Meme)
            .where(Meme.id == meme.id, Meme.bonus_awarded.is_(False), Meme.rating >= RATING_BONUS_THRESHOLD)
            .values(bonus_awarded=True)
        )
        if claimed.rowcount != 1:
            return 0, None, False
        author = await self.session.get(User, meme.author_id)
        add_xp(author, XP_RATING_BONUS)
        logger.info("Автор мема id=%s получил бонус за рейтинг", meme.id)
        return XP_RATING_BONUS, author.telegram_id, author.notifications_enabled
