"""Giphy API: GIF-реакция в конце игры.

Зачем Giphy в продукте. После игры бот присылает GIF, подходящую к результату:
80%+ правильных ответов, и бот радуется, 40% и меньше, и бот сочувствует (запрос
зависит ещё и от категории игры). Это одна GIF за игру: чат не засоряется.

Экономия запросов: бесплатный ключ ограничен по числу запросов. Поэтому по каждому
поисковому запросу мы кэшируем сразу список из 25 GIF на час и выбираем из него случайную.
Результат: пользователи видят разные GIF, а в API уходит один запрос в час на запрос.

Если ключа нет или сервис не отвечает, игра идёт как обычно, только без GIF.
"""

import logging
import random
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.core.cache import TTLCache
from app.core.exceptions import ApiBadResponseError, ApiError
from app.core.http import ApiClient

logger = logging.getLogger(__name__)

API_URL = "https://api.giphy.com/v1/gifs/search"
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


def parse_gif_urls(payload: Any) -> list[str]:
    """Достаёт ссылки на GIF из ответа Giphy. Бросает ApiBadResponseError при другой структуре."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ApiBadResponseError("giphy: нет списка data")
    urls: list[str] = []
    for item in payload["data"]:
        try:
            images = item["images"]
            url = (images.get("downsized") or images["original"])["url"]
        except (KeyError, TypeError, AttributeError):
            continue
        parsed = urlparse(url) if isinstance(url, str) else None
        # Пересылаем в Telegram только https-ссылки на домен giphy.com.
        if parsed and parsed.scheme == "https" and (parsed.hostname or "").endswith("giphy.com"):
            urls.append(url)
    return urls


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
