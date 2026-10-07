"""Простой кэш в памяти с TTL (временем жизни записи).

Схема работы сервисов:   Handler → Service → Cache → внешний API
  1. сервис ищет ответ в кэше по ключу;
  2. если запись есть и не устарела, возвращает её (внешний API НЕ вызывается);
  3. если нет, обращается к API и кладёт результат в кэш на `ttl` секунд.

Почему не Redis: у нас один процесс бота, и внешний сервис ради кэша только усложнил бы
запуск. Минус: кэш пропадает при перезапуске и не общий для нескольких процессов.
Интерфейс get/set совпадает с Redis, поэтому замена потребует изменить только этот класс.

Значение None означает «в кэше нет», поэтому None туда класть нельзя (пустой список можно).
"""

import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class TTLCache:
    def __init__(self, max_items: int = 500, clock: Callable[[], float] = time.monotonic) -> None:
        self._data: dict[str, tuple[float, Any]] = {}  # ключ -> (когда истекает, значение)
        self._max_items = max_items
        # clock можно подменить в тестах, чтобы «перемотать» время, не ожидая по-настоящему.
        self._clock = clock

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._data[key]  # запись устарела
            return None
        logger.debug("cache hit: %s", key)
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        if value is None:
            raise ValueError("None нельзя хранить в кэше: он означает «нет записи»")
        if key not in self._data and len(self._data) >= self._max_items:
            self._evict()
        self._data[key] = (self._clock() + ttl_seconds, value)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def _evict(self) -> None:
        """Место кончилось: сначала выбрасываем устаревшее, потом самую старую запись."""
        now = self._clock()
        for key in [k for k, (expires_at, _) in self._data.items() if expires_at <= now]:
            del self._data[key]
        if len(self._data) >= self._max_items:
            del self._data[next(iter(self._data))]  # словарь хранит порядок вставки

    def __len__(self) -> int:
        return len(self._data)
