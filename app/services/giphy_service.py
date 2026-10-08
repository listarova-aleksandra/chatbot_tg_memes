"""Giphy API: GIF-реакция в конце игры.

Зачем Giphy в продукте. После игры бот присылает GIF, подходящую к результату:
80%+ правильных ответов, и бот радуется, 40% и меньше, и бот сочувствует (запрос
зависит ещё и от категории игры). Это одна GIF за игру: чат не засоряется.

Экономия запросов: бесплатный ключ ограничен по числу запросов. Поэтому по каждому
поисковому запросу мы кэшируем сразу список из 25 GIF на час и выбираем из него случайную.
Результат: пользователи видят разные GIF, а в API уходит один запрос в час на запрос.

Если ключа нет или сервис не отвечает, игра идёт как обычно, только без GIF.
"""

import asyncio
import io
import logging
import random
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

from app.core.cache import TTLCache
from app.core.exceptions import ApiBadResponseError, ApiError
from app.core.http import ApiClient

logger = logging.getLogger(__name__)

API_URL = "https://api.giphy.com/v1/gifs/search"
BY_ID_URL = "https://api.giphy.com/v1/gifs/{gif_id}"
IMAGE_CACHE_TTL_SECONDS = 60 * 60
MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGE_SIDE = 1024
RESULTS_LIMIT = 25
CACHE_TTL_SECONDS = 60 * 60
EMPTY_CACHE_TTL_SECONDS = 10 * 60  # «ничего не найдено» запоминаем ненадолго
GIPHY_ATTEMPTS = 2  # GIF не критична: ждём меньше, чем для остальных API

# Статусы результата
OK, EMPTY, UNAVAILABLE, DISABLED = "ok", "empty", "unavailable", "disabled"

# Поисковые запросы: настроение -> категория -> запрос (по-английски: так больше результатов).
QUERIES: dict[str, dict[str, str]] = {
    "win": {
        "nba": "basketball dunk celebration",
        "wnba": "basketball celebration",
        "hiphop": "rap mic drop",
        "rnb": "dance vibes",
        "postirony": "absurd dance",
        "internet": "meme celebration",
        "default": "celebration",
    },
    "lose": {
        "nba": "basketball fail",
        "wnba": "basketball fail",
        "hiphop": "sad rapper",
        "rnb": "crying music",
        "postirony": "confused",
        "internet": "facepalm",
        "default": "facepalm",
    },
}


@dataclass(frozen=True)
class GifResult:
    url: str | None
    status: str  # ok / empty / unavailable / disabled


def mood_for_accuracy(accuracy: float) -> str | None:
    """Какая реакция подходит результату: 'win', 'lose' или None (середина: без GIF)."""
    if accuracy >= 80:
        return "win"
    if accuracy <= 40:
        return "lose"
    return None


def build_query(mood: str, category: str | None) -> str:
    table = QUERIES[mood]
    return table.get(category or "", table["default"])


@dataclass(frozen=True)
class GifCandidate:
    """Одна GIF из выдачи Giphy вместе с метаданными, по которым можно судить о ней."""

    gif_id: str
    gif_url: str  # анимация
    still_url: str | None  # неподвижный кадр (для «обычных картинок»)
    title: str
    alt_text: str  # описание GIF; если на ней виден текст, он обычно попадает и сюда
    username: str  # канал, который загрузил GIF


def _safe_url(url: Any) -> str | None:
    """Пересылаем в Telegram и скачиваем только https-ссылки на домен giphy.com."""
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme == "https" and (parsed.hostname or "").endswith("giphy.com"):
        return url
    return None


def _item_to_candidate(item: Any) -> GifCandidate | None:
    try:
        images = item["images"]
        gif_url = _safe_url((images.get("downsized") or images["original"])["url"])
        still = next(
            (u for key in ("downsized_still", "original_still", "fixed_height_still")
             if (u := _safe_url((images.get(key) or {}).get("url")))),
            None,
        )
    except (KeyError, TypeError, AttributeError):
        return None
    if gif_url is None:
        return None
    return GifCandidate(
        gif_id=str(item.get("id", "")),
        gif_url=gif_url,
        still_url=still,
        title=str(item.get("title") or ""),
        alt_text=str(item.get("alt_text") or ""),
        username=str(item.get("username") or "").lower(),
    )


def parse_candidates(payload: Any) -> list[GifCandidate]:
    """Разбирает выдачу Giphy. Бросает ApiBadResponseError при другой структуре."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ApiBadResponseError("giphy: нет списка data")
    return [c for item in payload["data"] if (c := _item_to_candidate(item)) is not None]


def parse_gif_urls(payload: Any) -> list[str]:
    """Ссылки на GIF из выдачи (для реакций в конце игры)."""
    return [c.gif_url for c in parse_candidates(payload)]


def still_to_jpeg(data: bytes) -> bytes:
    """Первый кадр GIF как обычная JPEG-картинка. Бросает ApiBadResponseError, если не получилось."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > 25_000_000:
            raise ApiBadResponseError("giphy: изображение слишком большое")
        image.seek(0)  # у анимации берём первый кадр
        image = image.convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=88)
        return out.getvalue()
    except ApiBadResponseError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, EOFError) as error:
        raise ApiBadResponseError("giphy: не удалось прочитать изображение") from error


class GiphyService:
    name = "giphy"

    def __init__(self, client: ApiClient | None, cache: TTLCache, api_key: str | None) -> None:
        self.client = client
        self.cache = cache
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key) and self.client is not None

    async def search(self, query: str) -> list[str]:
        """Ссылки на GIF по запросу (из кэша или из API). Бросает ApiError при сбое."""
        key = f"giphy:search:{query}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        payload = await self.client.request_json(  # type: ignore[union-attr]
            self.name,
            "GET",
            API_URL,
            params={"api_key": self._api_key, "q": query, "limit": RESULTS_LIMIT, "rating": "pg"},
            attempts=GIPHY_ATTEMPTS,
        )
        urls = parse_gif_urls(payload)
        self.cache.set(key, urls, CACHE_TTL_SECONDS if urls else EMPTY_CACHE_TTL_SECONDS)
        return urls

    async def search_candidates(self, query: str) -> list[GifCandidate]:
        """Выдача Giphy с метаданными (из кэша или из API). Бросает ApiError при сбое."""
        key = f"giphy:cand:{query}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        payload = await self.client.request_json(  # type: ignore[union-attr]
            self.name,
            "GET",
            API_URL,
            params={"api_key": self._api_key, "q": query, "limit": RESULTS_LIMIT, "rating": "pg"},
            attempts=GIPHY_ATTEMPTS,
        )
        candidates = parse_candidates(payload)
        self.cache.set(key, candidates, CACHE_TTL_SECONDS if candidates else EMPTY_CACHE_TTL_SECONDS)
        return candidates

    async def get_candidate(self, gif_id: str) -> GifCandidate | None:
        """Конкретная GIF по id (когда запись «закреплена» вручную). Бросает ApiError при сбое."""
        payload = await self.client.request_json(  # type: ignore[union-attr]
            self.name,
            "GET",
            BY_ID_URL.format(gif_id=gif_id),
            params={"api_key": self._api_key},
            attempts=GIPHY_ATTEMPTS,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        return _item_to_candidate(data) if isinstance(data, dict) else None

    async def get_image_jpeg(self, still_url: str) -> bytes | None:
        """Неподвижная картинка как JPEG для отправки обычным фото. None, если не удалось
        (тогда бот попробует отправить ссылку как есть или покажет вопрос без картинки)."""
        if _safe_url(still_url) is None or not self.enabled:
            return None
        key = f"giphy:jpeg:{still_url}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        try:
            raw = await self.client.request_bytes(self.name, still_url, max_bytes=MAX_IMAGE_BYTES)  # type: ignore[union-attr]
            # Pillow работает с пикселями синхронно: выносим в поток, чтобы не блокировать бота.
            jpeg = await asyncio.to_thread(still_to_jpeg, raw)
        except ApiError as error:
            logger.warning("Не удалось подготовить картинку Giphy: %s", error)
            return None
        self.cache.set(key, jpeg, IMAGE_CACHE_TTL_SECONDS)
        return jpeg

    async def get_reaction(self, mood: str, category: str | None) -> GifResult:
        """Случайная GIF-реакция. Никогда не бросает исключений."""
        if not self.enabled:
            return GifResult(None, DISABLED)
        try:
            urls = await self.search(build_query(mood, category))
        except ApiError as error:
            logger.warning("Giphy недоступен: %s", error)
            return GifResult(None, UNAVAILABLE)
        if not urls:
            return GifResult(None, EMPTY)
        return GifResult(random.choice(urls), OK)
