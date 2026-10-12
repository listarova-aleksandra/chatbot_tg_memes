"""Понятное объяснение ошибки подключения к PostgreSQL.

Самая частая проблема при первом запуске: сервер БД просто не запущен
(Windows: WinError 1225 / Errno 111 «connection refused»). Вместо длинной трассировки
показываем причину и что делать. Пароль в сообщение не попадает.
"""

import asyncpg
from sqlalchemy.engine import make_url


def db_connection_hint(error: BaseException, database_url: str) -> str:
    try:
        url = make_url(database_url)
        target = f"{url.host or 'localhost'}:{url.port or 5432}, база «{url.database}», пользователь «{url.username}»"
    except Exception:
        target = "адрес из DATABASE_URL"

    if isinstance(error, asyncpg.InvalidPasswordError):
        reason = "PostgreSQL отклонил логин или пароль."
        todo = ["Сверьте пользователя и пароль в DATABASE_URL (файл .env) с теми, что заданы в PostgreSQL."]
    elif isinstance(error, asyncpg.InvalidCatalogNameError):
        reason = "Сервер PostgreSQL работает, но такой базы данных в нём нет."
        todo = [
            "Создайте базу и пользователя (команды в README, раздел про PostgreSQL),",
            "либо поправьте имя базы в DATABASE_URL.",
        ]
    elif isinstance(error, OSError):
        reason = "Сервер PostgreSQL не отвечает: он не запущен или слушает другой порт."
        todo = [
            "Если используете Docker:   docker compose up -d   (Docker Desktop должен быть запущен),",
            "   затем проверьте:        docker compose ps",
            "Если PostgreSQL установлен как программа:",
            "   Windows: Win+R → services.msc → служба «postgresql-x64-NN» → «Запустить»;",
            "   macOS (Homebrew): brew services start postgresql@16",
            "Порт и пароль должны совпадать с DATABASE_URL в .env.",
        ]
    else:
        reason = f"Неожиданная ошибка подключения ({type(error).__name__})."
        todo = ["Проверьте DATABASE_URL в .env и что PostgreSQL запущен."]

    lines = ["", "НЕ УДАЛОСЬ ПОДКЛЮЧИТЬСЯ К БАЗЕ ДАННЫХ", reason, f"Подключение: {target}", "", "Что сделать:"]
    lines += [f"  {line}" for line in todo]
    return "\n".join(lines) + "\n"
