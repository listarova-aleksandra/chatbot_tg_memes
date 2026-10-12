"""Конфигурация приложения.

Все настройки читаются из переменных окружения или из файла `.env`
(библиотека pydantic-settings). Так секреты остаются вне кода и вне Git.
Настройки проверяются при старте: если нет BOT_TOKEN или DATABASE_URL,
приложение сразу падает с понятной ошибкой, а не в середине работы.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# .env ищется в корне проекта, а не в текущей папке терминала: бот запускается одинаково,
# откуда бы ни вызвали `python -m app.main`.
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # лишние переменные в .env не считаем ошибкой
    )

    # --- Обязательные ---
    # SecretStr не печатает значение в логах и repr(): так токен не утечёт случайно.
    bot_token: SecretStr
    database_url: str

    # --- Необязательные внешние API ---
    # Если ключа нет, соответствующая функция просто отключается (graceful degradation).
    giphy_api_key: SecretStr | None = None
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None
    reddit_user_agent: str = "memomaster-bot/0.1 (учебный проект)"

    # --- HTTP-клиент для внешних API ---
    http_timeout_seconds: float = 5.0
    http_max_retries: int = 3

    log_level: str = "INFO"

    @field_validator(
        "giphy_api_key", "reddit_client_id", "reddit_client_secret", mode="before"
    )
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        """`GIPHY_API_KEY=` в .env даёт пустую строку, считаем это «ключа нет»."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def check_async_driver(cls, value: str) -> str:
        """SQLAlchemy async работает только с async-драйверами (asyncpg / aiosqlite)."""
        allowed = ("postgresql+asyncpg://", "sqlite+aiosqlite://")
        if not value.startswith(allowed):
            raise ValueError(
                "DATABASE_URL должен начинаться с 'postgresql+asyncpg://' "
                "(для тестов допускается 'sqlite+aiosqlite://')"
            )
        return value

    @property
    def giphy_enabled(self) -> bool:
        return self.giphy_api_key is not None

    @property
    def reddit_enabled(self) -> bool:
        return self.reddit_client_id is not None and self.reddit_client_secret is not None


@lru_cache
def get_settings() -> Settings:
    """Настройки создаются один раз и переиспользуются (lru_cache)."""
    return Settings()  # type: ignore[call-arg]  # обязательные поля берутся из окружения


def format_settings_error(error: ValidationError, env_file: Path = ENV_FILE) -> str:
    """Понятное объяснение ошибки настроек вместо длинной трассировки.

    Значения переменных не выводятся (в них могут быть секреты), только их имена и причина.
    """
    problems = []
    for item in error.errors():
        name = str(item["loc"][0]).upper() if item["loc"] else "?"
        if item["type"] == "missing":
            problems.append(f"  - {name}: не задана")
        else:
            problems.append(f"  - {name}: {item['msg'].removeprefix('Value error, ')}")

    lines = ["", "НЕ УДАЛОСЬ ЗАГРУЗИТЬ НАСТРОЙКИ", *problems, ""]
    if not env_file.exists():
        lines += [
            f"Файл с настройками не найден: {env_file}",
            "Создайте его из шаблона (в корне проекта):",
            "  Windows (PowerShell):  copy .env.example .env",
            "  macOS / Linux:         cp .env.example .env",
            "Затем откройте .env и впишите BOT_TOKEN (токен от @BotFather).",
            "Если файл есть, но называется .env.txt, переименуйте его: Windows скрывает расширения.",
        ]
    else:
        lines += [
            f"Файл {env_file} найден, но значения в нём не заполнены или неверны.",
            "Откройте его и впишите значения после знака «=» (без кавычек и пробелов).",
        ]
    return "\n".join(lines) + "\n"
