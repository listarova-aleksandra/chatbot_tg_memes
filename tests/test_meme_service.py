"""MemeService: сохранение, публикация, XP, защита от повторной публикации."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Meme, User
from app.services.meme_service import MemeNotPublishableError, MemeService
from app.services.user_service import UserService
from app.services.xp import XP_MEME_CREATED, XP_MEME_PUBLISHED


@pytest.fixture
async def user(session: AsyncSession) -> User:
    created, _ = await UserService(session).get_or_register(1, "anya", "Аня")
    return created


async def test_saved_meme_gives_create_xp_and_is_not_public(session: AsyncSession, user: User) -> None:
    result = await MemeService(session).create_meme(user, "file1", "Drake", "верх", "низ", publish=False)
    assert result.xp_awarded == XP_MEME_CREATED == 5 and user.xp == 5
    meme = result.meme
    assert meme.id and not meme.is_published and meme.published_at is None
    assert (meme.author_id, meme.image_file_id, meme.top_text, meme.bottom_text) == (user.id, "file1", "верх", "низ")


async def test_published_meme_gives_both_bonuses(session: AsyncSession, user: User) -> None:
    result = await MemeService(session).create_meme(user, "f", None, "", "низ", publish=True)
    assert result.xp_awarded == XP_MEME_CREATED + XP_MEME_PUBLISHED == 10
    assert result.meme.is_published and result.meme.published_at is not None and user.xp == 10
    assert result.meme.template_name is None  # своё фото: шаблона нет


async def test_level_up_is_reported(session: AsyncSession, user: User) -> None:
    user.xp = 45
    result = await MemeService(session).create_meme(user, "f", None, "а", "б", publish=False)
    assert result.level_change.leveled_up and user.level == 2  # 45 + 5 = 50 XP


async def test_publish_saved_meme_once(session: AsyncSession, user: User) -> None:
    service = MemeService(session)
    saved = await service.create_meme(user, "f", None, "а", "б", publish=False)
    published = await service.publish_meme(user, saved.meme.id)
    assert published.meme.is_published and published.xp_awarded == 5 and user.xp == 10
    with pytest.raises(MemeNotPublishableError):  # двойное нажатие: XP второй раз не даётся
        await service.publish_meme(user, saved.meme.id)
    assert user.xp == 10


async def test_cannot_publish_someone_elses_or_missing_meme(session: AsyncSession, user: User) -> None:
    other, _ = await UserService(session).get_or_register(2, None, "Y")
    service = MemeService(session)
    theirs = await service.create_meme(other, "f", None, "а", "б", publish=False)
    with pytest.raises(MemeNotPublishableError):
        await service.publish_meme(user, theirs.meme.id)
    with pytest.raises(MemeNotPublishableError):
        await service.publish_meme(user, 99999)
    assert not (await session.get(Meme, theirs.meme.id)).is_published
    assert user.xp == 0


async def test_profile_counts_created_memes(session: AsyncSession, user: User) -> None:
    service = MemeService(session)
    await service.create_meme(user, "a", None, "а", "б", publish=False)
    await service.create_meme(user, "b", None, "а", "б", publish=True)
    stats = await UserService(session).get_profile_stats(user)
    assert stats.memes_created == 2
    assert await session.scalar(select(func.count()).select_from(Meme).where(Meme.is_published)) == 1
