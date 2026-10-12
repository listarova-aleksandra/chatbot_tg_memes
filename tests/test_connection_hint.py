"""Понятные сообщения при проблемах с подключением к PostgreSQL."""

import socket

import asyncpg
import pytest

from app.database.connection_hint import db_connection_hint

URL = "postgresql+asyncpg://memomaster:SuperSecret@localhost:5432/memomaster"


def test_server_down_explains_how_to_start_it():
    text = db_connection_hint(ConnectionRefusedError(1225, "refused"), URL)
    assert "не отвечает" in text
    assert "docker compose up -d" in text
    assert "services.msc" in text
    assert "localhost:5432" in text


def test_wrong_password():
    text = db_connection_hint(asyncpg.InvalidPasswordError("bad"), URL)
    assert "логин или пароль" in text


def test_missing_database():
    text = db_connection_hint(asyncpg.InvalidCatalogNameError("no db"), URL)
    assert "такой базы данных в нём нет" in text


def test_unknown_error_is_still_explained():
    text = db_connection_hint(RuntimeError("странное"), URL)
    assert "RuntimeError" in text and "DATABASE_URL" in text


def test_password_is_never_printed():
    for error in (ConnectionRefusedError(), asyncpg.InvalidPasswordError("x"), RuntimeError("x")):
        assert "SuperSecret" not in db_connection_hint(error, URL)


def test_broken_url_does_not_crash():
    assert "DATABASE_URL" in db_connection_hint(ConnectionRefusedError(), "это не url")


async def test_real_refused_connection_gets_hint():
    """Настоящее подключение к закрытому порту: ловим ровно тот тип ошибки, что был у пользователя."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # сокет закрыт, порт свободен: подключение будет отклонено
    with pytest.raises(OSError) as info:
        await asyncpg.connect(host="127.0.0.1", port=port, user="u", password="p", database="d", timeout=3)
    assert "не отвечает" in db_connection_hint(info.value, URL)
