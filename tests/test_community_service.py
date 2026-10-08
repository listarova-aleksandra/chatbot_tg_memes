"""CommunityService: лента, сортировка, голосование, защита от повторов, бонус автору."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import utcnow
from app.database.models import Meme, MemeVote, User
from app.services.community_service import (
    NEW, POPULAR, AlreadyVotedError, CommunityService, MemeNotFoundError, OwnMemeError,
)
from app.services.user_service import UserService
from app.services.xp import RATING_BONUS_THRESHOLD, XP_RATING_BONUS


async def make_user(session: AsyncSession, telegram_id: int, name: str = "User", username: str | None = None) -> User:
    user, _ = await UserService(session).get_or_register(telegram_id, username, name)
    await session.flush()
    return user


async def make_meme(session: AsyncSession, author: User, *, published: bool = True, rating: int = 0,
                    age_minutes: int = 0, file_id: str | None = None) -> Meme:
    meme = Meme(
        author_id=author.id, image_file_id=file_id or f"f{author.id}-{age_minutes}-{rating}", is_published=published,
        published_at=utcnow() - timedelta(minutes=age_minutes) if published else None, rating=rating,
    )
    session.add(meme)
    await session.flush()
    return meme


# ---------- Голосование ----------


async def test_like_and_dislike_update_counters_and_rating(session: AsyncSession) -> None:
    author, a, b = [await make_user(session, i) for i in (1, 2, 3)]
    meme = await make_meme(session, author)
    service = CommunityService(session)
    r1 = await service.vote(a, meme.id, 1)
    r2 = await service.vote(b, meme.id, -1)
    assert (r1.item.likes, r1.item.dislikes, r1.item.rating) == (1, 0, 1)
    assert (r2.item.likes, r2.item.dislikes, r2.item.rating) == (1, 1, 0)
    assert r1.item.my_vote == 1 and r2.item.my_vote == -1


async def test_second_vote_by_same_user_is_rejected_even_if_opposite(session: AsyncSession) -> None:
    author, voter = await make_user(session, 1), await make_user(session, 2)
    meme = await make_meme(session, author)
    service = CommunityService(session)
    await service.vote(voter, meme.id, 1)
    for value in (1, -1):
        with pytest.raises(AlreadyVotedError):
            await service.vote(voter, meme.id, value)
    await session.refresh(meme)
    assert (meme.likes_count, meme.dislikes_count, meme.rating) == (1, 0, 1)  # счётчики не тронуты
    assert await session.scalar(select(func.count()).select_from(MemeVote)) == 1


async def test_cannot_vote_for_own_unpublished_or_missing_meme(session: AsyncSession) -> None:
    author, other = await make_user(session, 1), await make_user(session, 2)
    own = await make_meme(session, author)
    draft = await make_meme(session, author, published=False)
    service = CommunityService(session)
    with pytest.raises(OwnMemeError):
        await service.vote(author, own.id, 1)
    with pytest.raises(MemeNotFoundError):
        await service.vote(other, draft.id, 1)  # не опубликован
    with pytest.raises(MemeNotFoundError):
        await service.vote(other, 99999, 1)
    assert await session.scalar(select(func.count()).select_from(MemeVote)) == 0


@pytest.mark.parametrize("value", [0, 2, -2, 100])
async def test_invalid_vote_value_is_rejected(session: AsyncSession, value: int) -> None:
    author, voter = await make_user(session, 1), await make_user(session, 2)
    meme = await make_meme(session, author)
    with pytest.raises(ValueError):
        await CommunityService(session).vote(voter, meme.id, value)
    assert await session.scalar(select(func.count()).select_from(MemeVote)) == 0


# ---------- Бонус автору ----------


async def test_author_gets_bonus_once_when_rating_reaches_threshold(session: AsyncSession) -> None:
    author = await make_user(session, 1)
    voters = [await make_user(session, 10 + i) for i in range(RATING_BONUS_THRESHOLD + 2)]
    meme = await make_meme(session, author)
    service = CommunityService(session)

    results = [await service.vote(v, meme.id, 1) for v in voters]
    bonuses = [r.bonus_xp for r in results]
    assert bonuses.count(XP_RATING_BONUS) == 1 and bonuses.index(XP_RATING_BONUS) == RATING_BONUS_THRESHOLD - 1
    assert author.xp == XP_RATING_BONUS  # ровно один раз, хотя голосов больше
    assert results[RATING_BONUS_THRESHOLD - 1].author_telegram_id == 1


async def test_bonus_is_not_repeated_after_rating_drops_and_returns(session: AsyncSession) -> None:
    author = await make_user(session, 1)
    voters = [await make_user(session, 10 + i) for i in range(10)]
    meme = await make_meme(session, author)
    service = CommunityService(session)
    for v in voters[:5]:
        await service.vote(v, meme.id, 1)  # рейтинг 5: бонус
    for v in voters[5:8]:
        await service.vote(v, meme.id, -1)  # рейтинг падает до 2
    for v in voters[8:]:
        await service.vote(v, meme.id, 1)  # снова растёт
    assert author.xp == XP_RATING_BONUS


async def test_dislikes_do_not_trigger_bonus_and_notify_flag_is_reported(session: AsyncSession) -> None:
    author = await make_user(session, 1)
    author.notifications_enabled = False
    meme = await make_meme(session, author, rating=RATING_BONUS_THRESHOLD - 1)
    voters = [await make_user(session, 10 + i) for i in range(2)]
    service = CommunityService(session)
    assert (await service.vote(voters[0], meme.id, -1)).bonus_xp == 0
    result = await service.vote(voters[1], meme.id, 1)  # рейтинг 3
    assert result.bonus_xp == 0 and author.xp == 0

    meme2 = await make_meme(session, author, rating=RATING_BONUS_THRESHOLD - 1, age_minutes=5)
    result = await service.vote(voters[0], meme2.id, 1)
    assert result.bonus_xp == XP_RATING_BONUS and result.author_notify is False  # XP есть, уведомлять не надо


# ---------- Лента ----------


async def test_feed_contains_only_published_memes(session: AsyncSession) -> None:
    author = await make_user(session, 1)
    viewer = await make_user(session, 2)
    await make_meme(session, author, published=False)
    service = CommunityService(session)
    assert await service.count_published() == 0 and await service.get_page(POPULAR, 0, viewer) is None
    await make_meme(session, author)
    assert await service.count_published() == 1


async def test_popular_sort_by_rating_then_freshness_and_new_by_date(session: AsyncSession) -> None:
    author, viewer = await make_user(session, 1, "Аня", "anya"), await make_user(session, 2)
    old_best = await make_meme(session, author, rating=9, age_minutes=100)
    mid = await make_meme(session, author, rating=3, age_minutes=50)
    fresh_same = await make_meme(session, author, rating=3, age_minutes=1)
    newest_bad = await make_meme(session, author, rating=-2, age_minutes=0)
    service = CommunityService(session)

    popular = [(await service.get_page(POPULAR, i, viewer)).meme_id for i in range(4)]
    assert popular == [old_best.id, fresh_same.id, mid.id, newest_bad.id]
    new = [(await service.get_page(NEW, i, viewer)).meme_id for i in range(4)]
    assert new == [newest_bad.id, fresh_same.id, mid.id, old_best.id]
    assert await service.get_page(POPULAR, 4, viewer) is None  # за концом ленты


async def test_item_has_author_name_and_viewers_own_vote(session: AsyncSession) -> None:
    named, nameless, viewer = (await make_user(session, 1, "Аня", "anya"), await make_user(session, 2, "Борис"),
                               await make_user(session, 3))
    m1 = await make_meme(session, named, age_minutes=2)
    m2 = await make_meme(session, nameless, age_minutes=1)
    service = CommunityService(session)
    await service.vote(viewer, m1.id, -1)
    assert (await service.get_item(m1.id, viewer)).author_name == "@anya"
    assert (await service.get_item(m2.id, viewer)).author_name == "Борис"  # без username: имя
    assert (await service.get_item(m1.id, viewer)).my_vote == -1
    assert (await service.get_item(m2.id, viewer)).my_vote is None
    assert (await service.get_item(m1.id, named)).my_vote is None  # голос видит только проголосовавший
