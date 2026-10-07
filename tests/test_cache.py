"""Тесты TTL-кэша."""

import pytest

from app.core.cache import TTLCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_get_returns_value_until_ttl_expires() -> None:
    clock = FakeClock()
    cache = TTLCache(clock=clock)
    cache.set("k", "value", ttl_seconds=60)
    assert cache.get("k") == "value"
    clock.now += 59
    assert cache.get("k") == "value"
    clock.now += 1  # ровно 60 с: запись устарела
    assert cache.get("k") is None
    assert len(cache) == 0  # устаревшая запись удалена


def test_missing_key_is_none_and_empty_list_is_cacheable() -> None:
    cache = TTLCache()
    assert cache.get("nope") is None
    cache.set("empty", [], ttl_seconds=10)
    assert cache.get("empty") == []  # пустой результат API тоже кэшируется


def test_none_cannot_be_stored() -> None:
    with pytest.raises(ValueError):
        TTLCache().set("k", None, ttl_seconds=10)


def test_set_overwrites_and_resets_ttl() -> None:
    clock = FakeClock()
    cache = TTLCache(clock=clock)
    cache.set("k", 1, 10)
    clock.now += 9
    cache.set("k", 2, 10)
    clock.now += 9
    assert cache.get("k") == 2


def test_delete() -> None:
    cache = TTLCache()
    cache.set("k", 1, 10)
    cache.delete("k")
    cache.delete("absent")  # удаление отсутствующего ключа не ошибка
    assert cache.get("k") is None


def test_eviction_drops_expired_first_then_oldest() -> None:
    clock = FakeClock()
    cache = TTLCache(max_items=3, clock=clock)
    cache.set("old", 1, 1000)
    cache.set("short", 2, 5)
    cache.set("c", 3, 1000)
    clock.now += 10  # "short" устарел
    cache.set("d", 4, 1000)  # место есть за счёт устаревшего
    assert cache.get("old") == 1 and cache.get("short") is None and len(cache) == 3
    cache.set("e", 5, 1000)  # устаревших нет: вытесняется самый старый ("old")
    assert cache.get("old") is None and cache.get("e") == 5 and len(cache) == 3
