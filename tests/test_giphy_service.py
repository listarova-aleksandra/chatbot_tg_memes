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


# ---------- Кандидаты, картинки, закрепление ----------

import io as _io  # noqa: E402

from PIL import Image as _Image  # noqa: E402

from app.services.giphy_service import GifCandidate, parse_candidates, still_to_jpeg  # noqa: E402


def full_item(**kw) -> dict:
    return {
        "id": "abc", "title": "Caitlin Clark GIF by WNBA", "alt_text": "a basketball player", "username": "WNBA",
        "images": {
            "downsized": {"url": "https://media.giphy.com/media/abc/giphy.gif"},
            "downsized_still": {"url": "https://media.giphy.com/media/abc/giphy_s.gif"},
        },
        **kw,
    }


def test_parse_candidates_reads_metadata_and_validates_hosts() -> None:
    bad_still = full_item(id="x")
    bad_still["images"]["downsized_still"] = {"url": "https://evil.example.com/s.gif"}
    result = parse_candidates({"data": [full_item(), bad_still, {"images": {}}, "мусор"]})
    assert len(result) == 2
    first = result[0]
    assert (first.gif_id, first.alt_text, first.username) == ("abc", "a basketball player", "wnba")  # канал в нижнем регистре
    assert first.still_url.endswith("giphy_s.gif") and result[1].still_url is None  # чужая ссылка отброшена


def test_parse_candidates_tolerates_missing_alt_text() -> None:
    item = full_item()
    del item["alt_text"]
    assert parse_candidates({"data": [item]})[0].alt_text == ""


def animated_gif() -> bytes:
    frames = [_Image.new("RGB", (80, 60), color) for color in ("red", "blue", "green")]
    out = _io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return out.getvalue()


def test_still_to_jpeg_takes_first_frame_and_downscales() -> None:
    jpeg = still_to_jpeg(animated_gif())
    image = _Image.open(_io.BytesIO(jpeg))
    assert image.format == "JPEG" and image.size == (80, 60)
    assert image.getpixel((40, 30))[0] > 200  # первый кадр красный
    big = _io.BytesIO()
    _Image.new("RGB", (3000, 1500)).save(big, format="PNG")
    assert max(_Image.open(_io.BytesIO(still_to_jpeg(big.getvalue()))).size) == 1024


def test_still_to_jpeg_rejects_garbage() -> None:
    with pytest.raises(ApiBadResponseError):
        still_to_jpeg(b"not an image")


async def test_get_image_jpeg_downloads_converts_and_caches() -> None:
    client = FakeApiClient()
    client.image_bytes = animated_gif()
    service = make(client)
    url = "https://media.giphy.com/media/abc/giphy_s.gif"
    first = await service.get_image_jpeg(url)
    second = await service.get_image_jpeg(url)
    assert first == second and _Image.open(_io.BytesIO(first)).format == "JPEG"
    assert sum(1 for c in client.calls if c["method"] == "GET-bytes") == 1  # скачано один раз


async def test_get_image_jpeg_returns_none_on_failure_foreign_host_or_disabled() -> None:
    url = "https://media.giphy.com/media/abc/giphy_s.gif"
    client = FakeApiClient()
    client.image_bytes = ApiUnavailableError("down")
    assert await make(client).get_image_jpeg(url) is None
    client.image_bytes = b"garbage"
    assert await make(client).get_image_jpeg(url) is None
    client.calls.clear()
    assert await make(client).get_image_jpeg("https://evil.example.com/x.gif") is None
    assert client.calls == []  # на чужой хост бот не ходит
    assert await make(client, key=None).get_image_jpeg(url) is None


async def test_get_candidate_by_id() -> None:
    client = FakeApiClient({"data": full_item()})
    candidate = await make(client).get_candidate("abc")
    assert candidate.gif_id == "abc" and client.calls[0]["url"].endswith("/gifs/abc")
    assert await make(FakeApiClient({"data": []})).get_candidate("zzz") is None
