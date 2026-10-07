"""Giphy: поиск, кэш, пустой результат, сбои, отключение без ключа."""

import pytest

from app.core.cache import TTLCache
from app.core.exceptions import ApiBadResponseError, ApiUnavailableError
from app.services.giphy_service import (
    DISABLED, EMPTY, OK, UNAVAILABLE, GiphyService, build_query, mood_for_accuracy, parse_gif_urls,
)
from tests.conftest import FakeApiClient


def gif(url: str) -> dict:
    return {"images": {"downsized": {"url": url}, "original": {"url": url}}}


GOOD = {"data": [gif("https://media1.giphy.com/media/a/giphy.gif"), gif("https://media2.giphy.com/media/b/giphy.gif")]}


def make(client: FakeApiClient, key: str | None = "KEY") -> GiphyService:
    return GiphyService(client, TTLCache(), key)  # type: ignore[arg-type]


def test_mood_for_accuracy() -> None:
    assert mood_for_accuracy(100) == mood_for_accuracy(80) == "win"
    assert mood_for_accuracy(40) == mood_for_accuracy(0) == "lose"
    assert mood_for_accuracy(60) is None  # середина: GIF не нужна


def test_query_depends_on_category_with_default() -> None:
    assert "basketball" in build_query("win", "nba")
    assert build_query("lose", "mixed") == "facepalm"  # неизвестная категория: запрос по умолчанию
    assert build_query("win", None) == "celebration"


def test_parse_urls_keeps_only_giphy_https_and_skips_broken_items() -> None:
    payload = {"data": [
        gif("https://media.giphy.com/ok.gif"),
        gif("http://media.giphy.com/not-https.gif"),
        gif("https://evil.example.com/x.gif"),
        gif("https://evilgiphy.com.example.org/x.gif"),
        {"images": {}},
        "мусор",
        {"no_images": 1},
    ]}
    assert parse_gif_urls(payload) == ["https://media.giphy.com/ok.gif"]


@pytest.mark.parametrize("payload", [None, [], {}, {"data": "x"}, {"data": None}])
def test_parse_rejects_unexpected_structure(payload) -> None:
    with pytest.raises(ApiBadResponseError):
        parse_gif_urls(payload)


async def test_reaction_ok_returns_one_of_urls_and_hides_key_from_nothing_but_params() -> None:
    client = FakeApiClient(GOOD)
    result = await make(client).get_reaction("win", "nba")
    assert result.status == OK and result.url in {u["images"]["downsized"]["url"] for u in GOOD["data"]}
    assert client.calls[0]["params"]["rating"] == "pg" and client.calls[0]["params"]["q"] == build_query("win", "nba")


async def test_second_request_is_served_from_cache_without_calling_api() -> None:
    client = FakeApiClient(GOOD)
    service = make(client)
    await service.get_reaction("win", "nba")
    await service.get_reaction("win", "nba")
    await service.get_reaction("win", "nba")
    assert len(client.calls) == 1  # API вызван один раз


async def test_empty_result_is_not_an_error_and_is_cached() -> None:
    client = FakeApiClient({"data": []})
    service = make(client)
    assert (await service.get_reaction("win", "nba")).status == EMPTY
    assert (await service.get_reaction("win", "nba")).status == EMPTY
    assert len(client.calls) == 1


@pytest.mark.parametrize("failure", [ApiUnavailableError("down"), ApiBadResponseError("bad")])
async def test_api_failure_returns_unavailable_without_raising(failure: Exception) -> None:
    result = await make(FakeApiClient(failure)).get_reaction("lose", "hiphop")
    assert result.status == UNAVAILABLE and result.url is None


async def test_garbage_json_structure_is_handled() -> None:
    result = await make(FakeApiClient({"unexpected": True})).get_reaction("win", None)
    assert result.status == UNAVAILABLE


async def test_without_api_key_service_is_disabled_and_does_not_call_api() -> None:
    client = FakeApiClient(GOOD)
    result = await make(client, key=None).get_reaction("win", "nba")
    assert result.status == DISABLED and client.calls == []


async def test_failure_is_not_cached_so_service_recovers() -> None:
    client = FakeApiClient(ApiUnavailableError("down"), GOOD)
    service = make(client)
    assert (await service.get_reaction("win", "nba")).status == UNAVAILABLE
    assert (await service.get_reaction("win", "nba")).status == OK  # API ожил, и GIF пошла
