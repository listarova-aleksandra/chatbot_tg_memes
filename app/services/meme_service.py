"""Мемы пользователя: сохранение, публикация, XP.

Картинка мема в БД не хранится. После отправки готового мема в Telegram бот получает
`file_id` этой фотографии, и по нему её можно показывать снова (лента сообщества, Этап 7).
"""

import logging
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.base import utcnow
from app.database.models import Meme, User
from app.services.gamification import LevelChange, add_xp
from app.services.xp import XP_MEME_CREATED, XP_MEME_PUBLISHED

logger = logging.getLogger(__name__)


class MemeNotPublishableError(Exception):
    """Мем не найден, чужой или уже опубликован."""


@dataclass(frozen=True)
class MemeResult:
    meme: Meme
    xp_awarded: int
    level_change: LevelChange


class MemeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_meme(
        self,
        user: User,
        file_id: str,
        template_name: str | None,
        top_text: str,
        bottom_text: str,
        publish: bool,
    ) -> MemeResult:
        """Сохраняет мем. За создание +5 XP, за публикацию ещё +5."""
        meme = Meme(
            author_id=user.id,
            image_file_id=file_id,
            template_name=(template_name or None) and template_name[:100],
            top_text=top_text,
            bottom_text=bottom_text,
            is_published=publish,
            published_at=utcnow() if publish else None,
        )
        self.session.add(meme)
        await self.session.flush()

        xp = XP_MEME_CREATED + (XP_MEME_PUBLISHED if publish else 0)
        change = add_xp(user, xp)
        logger.info("Мем id=%s создан (опубликован=%s)", meme.id, publish)
        return MemeResult(meme, xp, change)

    async def publish_meme(self, user: User, meme_id: int) -> MemeResult:
        """Публикует ранее сохранённый мем. XP начисляется один раз.

        Один UPDATE с условиями (автор, ещё не опубликован) атомарен: при двойном нажатии
        кнопки строка обновится только в первый раз, второй UPDATE затронет 0 строк.
        """
        result = await self.session.execute(
            update(Meme)
            .where(Meme.id == meme_id, Meme.author_id == user.id, Meme.is_published.is_(False))
            .values(is_published=True, published_at=utcnow())
        )
        if result.rowcount != 1:
            raise MemeNotPublishableError(meme_id)

        meme = await self.session.get(Meme, meme_id)
        await self.session.refresh(meme)  # UPDATE выполнен в обход объекта: перечитываем строку
        change = add_xp(user, XP_MEME_PUBLISHED)
        return MemeResult(meme, XP_MEME_PUBLISHED, change)
