"""Reddit API: «Мем дня».

Авторизация. Reddit требует OAuth2 даже для чтения. Мы используем самый простой вариант,
«application-only» (без входа пользователя):
  1. создаём приложение на https://www.reddit.com/prefs/apps (тип «script»);
  2. меняем client_id и client_secret на токен:
       POST https://www.reddit.com/api/v1/access_token   (Basic Auth: id:secret)
       тело: grant_type=client_credentials
  3. с токеном читаем посты: GET https://oauth.reddit.com/r/<sub>/top  (Authorization: Bearer ...)
Токен живёт около суток, поэтому кэшируем его, а не запрашиваем на каждый вызов.
Обязателен осмысленный заголовок User-Agent: без него Reddit может ответить 429.

Безопасность контента. Посты проходят строгий фильтр (`is_safe_meme`): без NSFW, видео,
закреплённых, удалённых, спойлеров, не-картинок и «непопулярных» постов. Всё, что сомнительно,
отбрасывается. Лимиты: ~100 запросов в минуту; мы кэшируем посты на 30 минут,
а выбранный «мем дня» на сутки, так что реальных запросов единицы в час.

Если ключей нет или Reddit недоступен, бот показывает запасной вариант (см. хендлер).
"""

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.cache import TTLCache
from app.core.exceptions import (
    ApiAuthError,
    ApiBadResponseError,
    ApiError,
    ApiRateLimitedError,
    ApiUnavailableError,
)
from app.core.http import ApiClient

logger = logging.getLogger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
SUBREDDITS = ("memes", "dankmemes", "me_irl")
MIN_SCORE = 500
POSTS_LIMIT = 25
POSTS_TTL_SECONDS = 30 * 60
DOWN_TTL_SECONDS = 2 * 60  # после сбоя 2 минуты не пытаемся снова (см. get_meme_of_the_day)
REDDIT_ATTEMPTS = 2  # пользователь ждёт ответа: повторов меньше, чем по умолчанию
TOKEN_TTL_MARGIN = 60  # обновляем токен чуть раньше конца срока
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
IMAGE_HOSTS = ("i.redd.it", "i.imgur.com")  # картинки принимаем только с известных хостов

OK, EMPTY, UNAVAILABLE, DISABLED = "ok", "empty", "unavailable", "disabled"


@dataclass(frozen=True)
class RedditMeme:
    title: str
    image_url: str
    permalink: str  # полная ссылка на пост
    subreddit: str
    score: int


@dataclass(frozen=True)
class MemeOfDayResult:
    meme: RedditMeme | None
    status: str  # ok / empty / unavailable / disabled


def is_safe_meme(post: Any, min_score: int = MIN_SCORE) -> bool:
    """Проходит ли пост в пользовательский поток. Любое сомнение, и пост отбрасывается."""
    if not isinstance(post, dict):
        return False
    url = post.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    host = url.split("/")[2] if url.count("/") >= 2 else ""
    return (
        post.get("over_18") is False  # None или True: отбрасываем
        and not post.get("is_video")
        and not post.get("is_self")
        and not post.get("stickied")
        and not post.get("spoiler")
        and not post.get("removed_by_category")  # удалён модераторами/автором
        and post.get("author") not in (None, "[deleted]")
        and post.get("post_hint") == "image"
        and host in IMAGE_HOSTS
        and url.lower().split("?")[0].endswith(IMAGE_EXTENSIONS)
        and isinstance(post.get("title"), str)
        and bool(post["title"].strip())
        and isinstance(post.get("permalink"), str)
        and isinstance(post.get("score"), int)
        and post["score"] >= min_score
    )


def parse_memes(payload: Any, min_score: int = MIN_SCORE) -> list[RedditMeme]:
    """Безопасные посты из ответа листинга. Бросает ApiBadResponseError при другой структуре."""
    try:
        children = payload["data"]["children"]
    except (KeyError, TypeError):
        raise ApiBadResponseError("reddit: неожиданная структура листинга") from None
    if not isinstance(children, list):
        raise ApiBadResponseError("reddit: children не список")

    memes = []
    for child in children:
        post = child.get("data") if isinstance(child, dict) else None
        if is_safe_meme(post, min_score):
            memes.append(
                RedditMeme(
                    title=post["title"].strip(),
                    image_url=post["url"],
                    permalink="https://www.reddit.com" + post["permalink"],
                    subreddit=str(post.get("subreddit", "")),
                    score=post["score"],
                )
            )
    return memes


def seconds_until_midnight_utc(now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max((midnight - now).total_seconds(), 60.0)


class RedditService:
    name = "reddit"

    def __init__(
        self,
        client: ApiClient | None,
        cache: TTLCache,
        client_id: str | None,
        client_secret: str | None,
        user_agent: str,
    ) -> None:
        self.client = client
        self.cache = cache
        self._client_id = client_id
        self._client_secret = client_secret
        self._user_agent = user_agent

    @property
    def enabled(self) -> bool:
        return bool(self._client_id and self._client_secret) and self.client is not None

    async def _get_token(self) -> str:
        cached = self.cache.get("reddit:token")
        if cached is not None:
            return cached
        # HTTP Basic Auth: заголовок "Authorization: Basic base64(client_id:client_secret)".
        credentials = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        payload = await self.client.request_json(  # type: ignore[union-attr]
            self.name,
            "POST",
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {credentials}", "User-Agent": self._user_agent},
            attempts=REDDIT_ATTEMPTS,
        )
        try:
            token = payload["access_token"]
            expires_in = float(payload.get("expires_in", 3600))
        except (KeyError, TypeError, ValueError):
            raise ApiBadResponseError("reddit: в ответе нет access_token") from None
        if not isinstance(token, str) or not token:
            raise ApiBadResponseError("reddit: пустой access_token")
        self.cache.set("reddit:token", token, max(expires_in - TOKEN_TTL_MARGIN, 60))
        return token

    async def _fetch_subreddit(self, subreddit: str) -> list[RedditMeme]:
        key = f"reddit:posts:{subreddit}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        token = await self._get_token()
        try:
            payload = await self.client.request_json(  # type: ignore[union-attr]
                self.name,
                "GET",
                f"{API_BASE}/r/{subreddit}/top",
                params={"t": "day", "limit": POSTS_LIMIT, "raw_json": 1},
                headers={"Authorization": f"Bearer {token}", "User-Agent": self._user_agent},
                attempts=REDDIT_ATTEMPTS,
            )
        except ApiAuthError:
            self.cache.delete("reddit:token")  # токен мог устареть: в следующий раз получим новый
            raise
        memes = parse_memes(payload)
        self.cache.set(key, memes, POSTS_TTL_SECONDS)
        return memes

    async def get_meme_of_the_day(self) -> MemeOfDayResult:
        """Лучший безопасный мем дня. Никогда не бросает исключений."""
        if not self.enabled:
            return MemeOfDayResult(None, DISABLED)

        daily_key = f"reddit:daily:{datetime.now(UTC).date().isoformat()}"
        cached = self.cache.get(daily_key)
        if cached is not None:
            return MemeOfDayResult(cached, OK)  # весь день один и тот же мем у всех

        # «Предохранитель»: если Reddit только что не отвечал, сразу отказываем, не заставляя
        # каждого следующего пользователя ждать таймауты и повторы.
        if self.cache.get("reddit:down") is not None:
            return MemeOfDayResult(None, UNAVAILABLE)

        candidates: list[RedditMeme] = []
        failures = 0
        for subreddit in SUBREDDITS:
            try:
                candidates.extend(await self._fetch_subreddit(subreddit))
            except ApiError as error:
                failures += 1
                logger.warning("Reddit r/%s недоступен: %s", subreddit, error)
                if isinstance(error, ApiAuthError | ApiUnavailableError | ApiRateLimitedError):
                    break  # проблема общая для всех сабреддитов: не тратим время на остальные

        if not candidates:
            # Запросы упали, это недоступность; запросы прошли, но подходящих постов нет.
            if failures:
                self.cache.set("reddit:down", True, DOWN_TTL_SECONDS)
                return MemeOfDayResult(None, UNAVAILABLE)
            return MemeOfDayResult(None, EMPTY)

        best = max(candidates, key=lambda meme: meme.score)
        self.cache.set(daily_key, best, seconds_until_midnight_utc())
        return MemeOfDayResult(best, OK)
