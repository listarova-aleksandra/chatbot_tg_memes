"""Тесты ApiClient на настоящем локальном HTTP-сервере (aiohttp).

Сервер имитирует все сбои из требований: HTTP 5xx, 429, 4xx, некорректный JSON,
зависание (timeout) и недоступный хост. Паузы между попытками подменены на «ничего не
делать» и записываются, чтобы проверить логику backoff без реального ожидания.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core.exceptions import (
    ApiAuthError,
    ApiBadResponseError,
    ApiClientError,
    ApiRateLimitedError,
    ApiUnavailableError,
)
from app.core.http import ApiClient, backoff_delay


@dataclass
class Scenario:
    """Что сервер отвечает на очередной запрос: список обработчиков по порядку (последний повторяется)."""

    handlers: list[Callable[[web.Request], web.StreamResponse | None]]
    calls: int = 0
    seen_headers: list[dict[str, str]] = field(default_factory=list)


def ok(payload: object = None) -> Callable:
    return lambda _req: web.json_response({"ok": True} if payload is None else payload)


def status(code: int, **headers: str) -> Callable:
    return lambda _req: web.Response(status=code, headers=headers, text="x")


@pytest.fixture
async def server() -> AsyncIterator[Callable[[Scenario], TestServer]]:
    servers: list[TestServer] = []

    async def start(scenario: Scenario) -> TestServer:
        async def handle(request: web.Request) -> web.StreamResponse:
            index = min(scenario.calls, len(scenario.handlers) - 1)
            scenario.calls += 1
            scenario.seen_headers.append(dict(request.headers))
            return await scenario.handlers[index](request) if asyncio.iscoroutinefunction(
                scenario.handlers[index]
            ) else scenario.handlers[index](request)

        app = web.Application()
        app.router.add_route("*", "/api", handle)
        test_server = TestServer(app)
        await test_server.start_server()
        servers.append(test_server)
        return test_server

    yield start
    for s in servers:
        await s.close()


@pytest.fixture
def delays() -> list[float]:
    return []


@pytest.fixture
async def client(delays: list[float]) -> AsyncIterator[ApiClient]:
    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    api = ApiClient(timeout=0.3, max_attempts=3, sleep=fake_sleep)
    yield api
    await api.close()


async def call(client: ApiClient, srv: TestServer, **kwargs):
    return await client.request_json("test", "GET", str(srv.make_url("/api")), **kwargs)


async def test_success_returns_parsed_json_and_sends_user_agent(server, client) -> None:
    scenario = Scenario([ok({"hello": "мир"})])
    srv = await server(scenario)
    assert await call(client, srv) == {"hello": "мир"}
    assert scenario.calls == 1
    assert scenario.seen_headers[0]["User-Agent"].startswith("memomaster-bot")


async def test_5xx_is_retried_and_then_succeeds(server, client, delays) -> None:
    scenario = Scenario([status(503), status(500), ok()])
    srv = await server(scenario)
    assert await call(client, srv) == {"ok": True}
    assert scenario.calls == 3
    assert len(delays) == 2 and delays[0] < delays[1]  # пауза растёт: 0.5 -> 1


async def test_5xx_forever_raises_unavailable_after_all_attempts(server, client) -> None:
    scenario = Scenario([status(500)])
    srv = await server(scenario)
    with pytest.raises(ApiUnavailableError):
        await call(client, srv)
    assert scenario.calls == 3  # ровно max_attempts, не бесконечно


async def test_429_respects_retry_after_header(server, client, delays) -> None:
    scenario = Scenario([status(429, **{"Retry-After": "2"}), ok()])
    srv = await server(scenario)
    assert await call(client, srv) == {"ok": True}
    assert delays == [2.0]


async def test_retry_after_is_capped(server, client, delays) -> None:
    scenario = Scenario([status(429, **{"Retry-After": "3600"}), ok()])
    srv = await server(scenario)
    await call(client, srv)
    assert delays == [5.0]  # не ждём час


async def test_429_forever_raises_rate_limited(server, client) -> None:
    srv = await server(Scenario([status(429, **{"Retry-After": "0"})]))
    with pytest.raises(ApiRateLimitedError):
        await call(client, srv)


@pytest.mark.parametrize(("code", "error"), [(404, ApiClientError), (400, ApiClientError),
                                              (401, ApiAuthError), (403, ApiAuthError)])
async def test_4xx_is_not_retried(server, client, code, error) -> None:
    scenario = Scenario([status(code)])
    srv = await server(scenario)
    with pytest.raises(error):
        await call(client, srv)
    assert scenario.calls == 1  # повтор бессмысленен


async def test_invalid_json_raises_bad_response_without_retry(server, client) -> None:
    scenario = Scenario([lambda _r: web.Response(text="<html>не json</html>", content_type="text/html")])
    srv = await server(scenario)
    with pytest.raises(ApiBadResponseError):
        await call(client, srv)
    assert scenario.calls == 1


async def test_empty_body_raises_bad_response(server, client) -> None:
    srv = await server(Scenario([lambda _r: web.Response(text="")]))
    with pytest.raises(ApiBadResponseError):
        await call(client, srv)


async def test_timeout_is_retried_then_raises_unavailable(server, client) -> None:
    async def hang(_request: web.Request) -> web.Response:
        await asyncio.sleep(2)  # дольше таймаута клиента (0.3 с)
        return web.json_response({})

    scenario = Scenario([hang])
    srv = await server(scenario)
    with pytest.raises(ApiUnavailableError, match="таймаут"):
        await call(client, srv)
    assert scenario.calls == 3


async def test_unreachable_host_raises_unavailable(client) -> None:
    with pytest.raises(ApiUnavailableError):
        await client.request_json("test", "GET", "http://127.0.0.1:1/api")  # порт закрыт


async def test_per_call_attempts_override(server, client) -> None:
    scenario = Scenario([status(500)])
    srv = await server(scenario)
    with pytest.raises(ApiUnavailableError):
        await call(client, srv, attempts=1)
    assert scenario.calls == 1


def test_backoff_grows_and_is_bounded() -> None:
    values = [backoff_delay(n) for n in (1, 2, 3, 4, 10)]
    assert 0.5 <= values[0] < 0.76 and 1.0 <= values[1] < 1.26 and 2.0 <= values[2] < 2.26
    assert values[3] < 4.26 and values[4] < 4.26  # потолок 4 с
