"""Сквозные тесты ленты сообщества: Dispatcher + БД, Telegram поддельный."""

from typing import Any

from aiogram.methods import EditMessageMedia, EditMessageText, SendMessage, SendPhoto
from sqlalchemy import func, select

from app.bot.callbacks import CommunityCB, MenuCB
from app.database.models import Meme, MemeVote, User
from app.services.xp import RATING_BONUS_THRESHOLD, XP_RATING_BONUS
from tests.test_community_service import make_meme, make_user
from tests.test_quiz_flow import Player


class FeedPlayer(Player):
    """Зритель (Аня, telegram id 42) листает ленту."""

    async def seed(self, fn: Any) -> Any:
        async with self.session_factory() as s:
            result = await fn(s)
            await s.commit()
            return result

    def labels(self, method: Any) -> list[str]:
        markup = method.reply_markup
        return [b.text for row in markup.inline_keyboard for b in row]

    def last_media(self) -> EditMessageMedia:
        return self.telegram.of_type(EditMessageMedia)[-1]

    async def vote(self, meme_id: int, value: int, sort: str = "pop", page: int = 0) -> None:
        await self.press(CommunityCB(action="vote", sort=sort, page=page, meme_id=meme_id, value=value).pack(), photo=True)

    async def view(self, sort: str, page: int) -> None:
        await self.press(CommunityCB(action="view", sort=sort, page=page).pack(), photo=True)

    async def votes_count(self) -> int:
        async with self.session_factory() as s:
            return await s.scalar(select(func.count()).select_from(MemeVote))


async def feed_player(bot, telegram, session_factory, make_dispatcher) -> FeedPlayer:
    p = FeedPlayer(make_dispatcher(), bot, telegram, session_factory)
    await p.send("/start")  # регистрируем зрителя (telegram id 42)
    return p


async def seed_memes(p: FeedPlayer) -> dict[str, int]:
    async def fn(s):
        author = await make_user(s, 7, "Борис", "boris")
        best = await make_meme(s, author, rating=8, age_minutes=30, file_id="file-best")
        newest = await make_meme(s, author, rating=1, age_minutes=1, file_id="file-new")
        return {"author": author.id, "best": best.id, "newest": newest.id}
    return await p.seed(fn)


# ---------- Лента ----------


async def test_empty_feed(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    await p.press(MenuCB(action="community").pack())
    shown = telegram.of_type(EditMessageText)[-1]
    assert "Пока здесь пусто" in shown.text and "🎨 Создать мем" in p.labels(shown)


async def test_feed_opens_with_popular_first_as_photo(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    await seed_memes(p)
    await p.send("/community")
    photo = telegram.of_type(SendPhoto)[-1]
    assert photo.photo == "file-best"  # показан по file_id: картинки на диске нет
    assert "Мем 1 из 2" in photo.caption and "Популярные" in photo.caption and "@boris" in photo.caption
    assert "Рейтинг: <b>+8</b>" in photo.caption and "(твой мем)" not in photo.caption
    labels = p.labels(photo)
    assert "👍 0" in labels and "👎 0" in labels and "✓ 🔥 Популярные" in labels and "🆕 Новые" in labels


async def test_navigation_wraps_around_and_sorting_switches(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    await seed_memes(p)
    await p.send("/community")
    await p.view("pop", 1)
    assert p.last_media().media.media == "file-new" and "Мем 2 из 2" in p.last_media().media.caption
    await p.view("pop", 0)  # «вперёд» со второго: по кругу к первому
    assert p.last_media().media.media == "file-best"
    await p.view("new", 0)  # переключили сортировку: самый свежий
    assert p.last_media().media.media == "file-new" and "Новые" in p.last_media().media.caption
    await p.view("pop", 99)  # страница за пределами не ломает ленту
    assert p.last_media().media.media == "file-new"  # последняя (рейтинг 1)


async def test_opening_from_menu_button_replaces_menu_with_photo(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    await seed_memes(p)
    await p.press(MenuCB(action="community").pack())
    assert telegram.of_type(SendPhoto)[-1].photo == "file-best"


# ---------- Голосование ----------


async def test_like_updates_caption_in_place_and_marks_vote(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    ids = await seed_memes(p)
    await p.send("/community")
    await p.vote(ids["best"], 1)
    media = p.last_media()
    assert "👍 1" in media.media.caption and "<b>+9</b>" in media.media.caption
    assert "👍 1 ✅" in p.labels(media)
    assert "Лайк принят" in p.last_alert().text
    async with p.session_factory() as s:
        meme = await s.get(Meme, ids["best"])
        assert (meme.likes_count, meme.rating) == (1, 9)


async def test_repeated_and_opposite_vote_are_rejected(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    ids = await seed_memes(p)
    await p.send("/community")
    await p.vote(ids["best"], 1)
    for value in (1, -1, 1):
        await p.vote(ids["best"], value)
        assert p.last_alert().show_alert is True and "уже голосовал" in p.last_alert().text
    assert await p.votes_count() == 1
    async with p.session_factory() as s:
        assert (await s.get(Meme, ids["best"])).rating == 9


async def test_cannot_vote_for_own_meme(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)

    async def fn(s):
        me = (await s.execute(select(User).where(User.telegram_id == 42))).scalar_one()
        return (await make_meme(s, me, file_id="mine")).id
    meme_id = await p.seed(fn)
    await p.send("/community")
    assert "(твой мем)" in telegram.of_type(SendPhoto)[-1].caption
    await p.vote(meme_id, 1)
    assert "свой мем" in p.last_alert().text and await p.votes_count() == 0


async def test_vote_applies_by_meme_id_even_if_feed_moved(bot, telegram, session_factory, make_dispatcher) -> None:
    """Кнопка из старого сообщения голосует за тот мем, который на ней нарисован."""
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    ids = await seed_memes(p)
    await p.send("/community")
    await p.vote(ids["newest"], -1, page=0)  # на экране «лучший», а кнопка от другого мема
    async with p.session_factory() as s:
        assert (await s.get(Meme, ids["newest"])).dislikes_count == 1
        assert (await s.get(Meme, ids["best"])).dislikes_count == 0


async def test_invalid_vote_value_or_unpublished_meme_handled_gracefully(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)
    ids = await seed_memes(p)

    async def draft(s):
        author = (await s.get(Meme, ids["best"])).author_id
        from app.database.models import User as U
        return (await make_meme(s, await s.get(U, author), published=False)).id
    draft_id = await p.seed(draft)

    await p.vote(ids["best"], 5)  # подделанное значение голоса
    assert p.last_alert().show_alert is True
    await p.vote(draft_id, 1)  # не опубликован
    assert p.last_alert().show_alert is True and await p.votes_count() == 0
    assert not any("пошло не так" in (m.text or "") for m in telegram.of_type(SendMessage))


# ---------- Бонус автору ----------


async def test_author_is_notified_and_gets_bonus_once(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)

    async def fn(s):
        author = await make_user(s, 7, "Борис", "boris")
        meme = await make_meme(s, author, rating=RATING_BONUS_THRESHOLD - 1, file_id="almost")
        return meme.id, author.id
    meme_id, author_id = await p.seed(fn)

    await p.send("/community")
    await p.vote(meme_id, 1)  # рейтинг достиг порога
    dm = [m for m in telegram.of_type(SendMessage) if m.chat_id == 7]
    assert len(dm) == 1 and f"+{XP_RATING_BONUS} XP" in dm[0].text
    async with p.session_factory() as s:
        assert (await s.get(User, author_id)).xp == XP_RATING_BONUS


async def test_no_dm_when_author_disabled_notifications_but_xp_still_awarded(bot, telegram, session_factory, make_dispatcher) -> None:
    p = await feed_player(bot, telegram, session_factory, make_dispatcher)

    async def fn(s):
        author = await make_user(s, 7, "Борис")
        author.notifications_enabled = False
        meme = await make_meme(s, author, rating=RATING_BONUS_THRESHOLD - 1)
        return meme.id, author.id
    meme_id, author_id = await p.seed(fn)
    await p.send("/community")
    await p.vote(meme_id, 1)
    assert [m for m in telegram.of_type(SendMessage) if m.chat_id == 7] == []
    async with p.session_factory() as s:
        assert (await s.get(User, author_id)).xp == XP_RATING_BONUS
