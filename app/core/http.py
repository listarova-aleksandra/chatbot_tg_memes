"""HTTP-клиент для внешних API: timeout, retry, разбор ошибок.

Все три сервиса (Imgflip, Giphy, Reddit) ходят в сеть только через `ApiClient`.
Так логика повторов написана один раз и не смешивается с Telegram-хендлерами.

async/await: пока бот ждёт ответ внешнего API (`await session.request(...)`), event loop
обслуживает других пользователей. Поэтому медленный Giphy не «вешает» бота для остальных.

Retry (повтор запроса). Многие сбои временные: сервер перегружен, сеть моргнула.
Поэтому такие запросы повторяются, но с растущей паузой (0.5 с → 1 с → 2 с, плюс случайная
добавка «jitter», чтобы много клиентов не ломились одновременно). Повторяем только то,
что может исправиться само:
    таймаут, обрыв соединения, HTTP 5xx, HTTP 429 (с учётом заголовка Retry-After)
Не повторяем:
    HTTP 401/403 (неверный ключ), другие 4xx (запрос неверный), невалидный JSON.
"""

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from app.core.exceptions import (
    ApiAuthError,
    ApiBadResponseError,
    ApiClientError,
    ApiError,
    ApiRateLimitedError,
    ApiUnavailableError,
)

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER_SECONDS = 5.0  # дольше ждать ответа 429 мы не готовы (пользователь ждёт)


class _RetryableError(Exception):
    """Внутренняя: сбой, который имеет смысл повторить. Несёт итоговую ошибку на случай,
    если попытки закончатся."""

    def __init__(self, error: ApiError, retry_after: float | None = None) -> None:
        self.error = error
        self.retry_after = retry_after


def backoff_delay(attempt: int) -> float:
    """Пауза перед следующей попыткой: 0.5, 1, 2, 4 с (не больше) плюс случайные до 0.25 с."""
    return min(0.5 * 2 ** (attempt - 1), 4.0) + random.uniform(0, 0.25)


class ApiClient:
    def __init__(
        self,
        timeout: float = 5.0,
        max_attempts: int = 3,
        user_agent: str = "memomaster-bot/0.1",
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.user_agent = user_agent
        self._sleep = sleep  # подменяется в тестах, чтобы не ждать по-настоящему
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        # Одна сессия на всё приложение: она переиспользует соединения (быстрее).
        # trust_env=True: уважать переменные HTTP_PROXY/HTTPS_PROXY (нужно за прокси).
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(trust_env=True)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def request_json(
        self,
        service: str,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        attempts: int | None = None,
    ) -> Any:
        """Выполняет запрос и возвращает разобранный JSON. При неудаче бросает ApiError.

        service: имя сервиса для логов ("giphy"). В логи не попадают URL с параметрами:
                 в них лежат ключи API.
        """
        total_attempts = attempts or self.max_attempts
        for attempt in range(1, total_attempts + 1):
            try:
                return await self._request_once(service, method, url, params, headers, data)
            except _RetryableError as failure:
                if attempt == total_attempts:
                    logger.error("%s: все %s попытки не удались: %s", service, total_attempts, failure.error)
                    raise failure.error from None
                delay = failure.retry_after if failure.retry_after is not None else backoff_delay(attempt)
                logger.warning(
                    "%s: попытка %s/%s не удалась (%s), повтор через %.1f с",
                    service, attempt, total_attempts, failure.error, delay,
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")  # цикл всегда возвращает значение или бросает ошибку

    async def _request_once(
        self,
        service: str,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        data: dict[str, Any] | None,
    ) -> Any:
        all_headers = {"User-Agent": self.user_agent, **(headers or {})}
        try:
            async with self._get_session().request(
                method,
                url,
                params=params,
                headers=all_headers,
                data=data,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as response:
                status = response.status
                if status == 429:
                    raise _RetryableError(
                        ApiRateLimitedError(f"{service}: HTTP 429"),
                        retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                    )
                if status >= 500:
                    raise _RetryableError(ApiUnavailableError(f"{service}: HTTP {status}"))
                if status in (401, 403):
                    raise ApiAuthError(f"{service}: HTTP {status}, проверьте ключ API")
                if status >= 400:
                    raise ApiClientError(f"{service}: HTTP {status}")
                body = await response.text()
        except _RetryableError:
            raise
        except TimeoutError as error:  # asyncio.TimeoutError в Python 3.11+ это TimeoutError
            raise _RetryableError(ApiUnavailableError(f"{service}: таймаут")) from error
        except aiohttp.ClientError as error:
            raise _RetryableError(
                ApiUnavailableError(f"{service}: ошибка соединения ({type(error).__name__})")
            ) from error

        try:
            return json.loads(body)
        except ValueError as error:  # json.JSONDecodeError является ValueError
            raise ApiBadResponseError(f"{service}: ответ не является корректным JSON") from error


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After бывает числом секунд (даты не поддерживаем: вернём None -> обычная пауза)."""
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return None
