"""Reddit: OAuth-токен, фильтр безопасности, кэш, сбои, мем дня."""

import base64

import pytest

from app.core.cache import TTLCache
from app.core.exceptions import ApiAuthError, ApiBadResponseError, ApiUnavailableError
from app.services.reddit_service import (
    DISABLED, EMPTY, OK, UNAVAILABLE, RedditService, is_safe_meme, parse_memes,
)
from tests.conftest import FakeApiClient

TOKEN = {"access_token": "tok123", "expires_in": 86400, "token_type": "bearer"}


def post(**overrides) -> dict:
    base = {
        "title": "Смешной мем", "url": "https://i.redd.it/abc.jpg", "permalink": "/r/memes/comments/1/x/",
        "subreddit": "memes", "score": 5000, "over_18": False, "is_video": False, "is_self": False,
        "stickied": False, "spoiler": False, "removed_by_category": None, "author": "someone",
        "post_hint": "image",
    }
    return {**base, **overrides}


def listing(*posts: dict) -> dict:
    return {"data": {"children": [{"kind": "t3", "data": p} for p in posts]}}


def service(client: FakeApiClient, creds: bool = True) -> RedditService:
    return RedditService(client, TTLCache(), "id" if creds else None, "secret" if creds else None, "ua")  # type: ignore[arg-type]


# ---------- Фильтр безопасности ----------


def test_good_post_passes() -> None:
    assert is_safe_meme(post())


@pytest.mark.parametrize(
    "override",
    [
        {"over_18": True}, {"over_18": None}, {"over_18": "true"},  # NSFW или неизвестно: отбрасываем
        {"is_video": True}, {"is_self": True}, {"stickied": True}, {"spoiler": True},
        {"removed_by_category": "moderator"}, {"removed_by_category": "deleted"},
        {"author": "[deleted]"}, {"author": None},
        {"post_hint": "link"}, {"post_hint": None},
        {"url": "https://i.redd.it/abc.gif"}, {"url": "http://i.redd.it/abc.jpg"},
        {"url": "https://evil.example.com/abc.jpg"}, {"url": "https://i.redd.it@evil.com/abc.jpg"},
        {"url": None}, {"url": 5}, {"title": ""}, {"title": "   "}, {"title": None},
        {"permalink": None}, {"score": 10}, {"score": None}, {"score": "9999"},
    ],
)
def test_unsafe_or_broken_posts_are_rejected(override: dict) -> None:
    assert not is_safe_meme(post(**override))


def test_non_dict_posts_are_rejected() -> None:
    assert not any(is_safe_meme(x) for x in (None, "text", 5, [], {}))


def test_parse_memes_filters_and_builds_full_permalink() -> None:
    memes = parse_memes(listing(post(), post(over_18=True), post(url="https://i.redd.it/2.png", title="Второй")))
    assert [m.title for m in memes] == ["Смешной мем", "Второй"]
    assert memes[0].permalink == "https://www.reddit.com/r/memes/comments/1/x/"


@pytest.mark.parametrize("payload", [None, {}, {"data": {}}, {"data": {"children": "x"}}, []])
def test_parse_memes_rejects_bad_structure(payload) -> None:
    with pytest.raises(ApiBadResponseError):
        parse_memes(payload)


def test_parse_memes_empty_listing_is_ok() -> None:
    assert parse_memes(listing()) == []


# ---------- Сервис ----------


async def test_disabled_without_credentials_does_not_call_api() -> None:
    client = FakeApiClient(TOKEN)
    result = await service(client, creds=False).get_meme_of_the_day()
    assert result.status == DISABLED and client.calls == []


async def test_meme_of_the_day_picks_highest_score_and_uses_oauth() -> None:
    client = FakeApiClient(
        TOKEN,
        listing(post(title="A", score=1000)),
        listing(post(title="B", score=9000, url="https://i.redd.it/b.png")),
        listing(post(title="C", score=3000, url="https://i.redd.it/c.png")),
    )
    result = await service(client).get_meme_of_the_day()
    assert result.status == OK and result.meme.title == "B"

    token_call, first_listing = client.calls[0], client.calls[1]
    assert token_call["method"] == "POST" and token_call["data"] == {"grant_type": "client_credentials"}
    assert token_call["headers"]["Authorization"] == "Basic " + base64.b64encode(b"id:secret").decode()
    assert first_listing["headers"]["Authorization"] == "Bearer tok123"
    assert first_listing["url"].startswith("https://oauth.reddit.com/r/")
    assert first_listing["headers"]["User-Agent"] == "ua"


async def test_token_and_results_are_cached() -> None:
    client = FakeApiClient(TOKEN, listing(post()), listing(post(url="https://i.redd.it/2.jpg")), listing())
    svc = service(client)
    first = await svc.get_meme_of_the_day()
    calls_after_first = len(client.calls)
    assert calls_after_first == 4  # 1 токен + 3 сабреддита
    second = await svc.get_meme_of_the_day()
    assert second.meme == first.meme and len(client.calls) == calls_after_first  # мем дня из кэша


async def test_token_is_reused_for_all_subreddits() -> None:
    client = FakeApiClient(TOKEN, listing(), listing(), listing())
    await service(client).get_meme_of_the_day()
    assert sum(1 for c in client.calls if c["method"] == "POST") == 1


async def test_no_safe_posts_gives_empty_not_error() -> None:
    client = FakeApiClient(TOKEN, listing(post(over_18=True)), listing(), listing(post(score=1)))
    result = await service(client).get_meme_of_the_day()
    assert result.status == EMPTY and result.meme is None


async def test_unavailable_stops_early_and_is_remembered() -> None:
    client = FakeApiClient(TOKEN, ApiUnavailableError("down"))
    svc = service(client)
    assert (await svc.get_meme_of_the_day()).status == UNAVAILABLE
    assert len(client.calls) == 2  # токен + один сабреддит: на остальные время не тратим
    assert (await svc.get_meme_of_the_day()).status == UNAVAILABLE
    assert len(client.calls) == 2  # второй пользователь получил отказ сразу, без обращений к API


async def test_token_failure_is_unavailable() -> None:
    result = await service(FakeApiClient(ApiAuthError("bad keys"))).get_meme_of_the_day()
    assert result.status == UNAVAILABLE


async def test_bad_token_response_is_handled() -> None:
    result = await service(FakeApiClient({"error": "invalid_grant"})).get_meme_of_the_day()
    assert result.status == UNAVAILABLE


async def test_auth_error_on_listing_drops_cached_token() -> None:
    client = FakeApiClient(TOKEN, ApiAuthError("expired"), TOKEN, listing(post()), listing(), listing())
    svc = service(client)
    assert (await svc.get_meme_of_the_day()).status == UNAVAILABLE
    svc.cache.delete("reddit:down")  # имитируем, что предохранитель уже истёк
    assert (await svc.get_meme_of_the_day()).status == OK
    assert sum(1 for c in client.calls if c["method"] == "POST") == 2  # токен получен заново


async def test_one_bad_subreddit_does_not_break_the_others() -> None:
    client = FakeApiClient(TOKEN, {"garbage": 1}, listing(post(title="ok", url="https://i.redd.it/z.jpg")), listing())
    result = await service(client).get_meme_of_the_day()
    assert result.status == OK and result.meme.title == "ok"
