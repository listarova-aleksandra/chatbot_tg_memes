"""Понятные сообщения об ошибках настроек."""

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import ENV_FILE, Settings, format_settings_error

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def error_for(**values: str) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None, **values)  # type: ignore[arg-type]
    return info.value


def test_env_file_path_is_project_root_not_cwd() -> None:
    assert ENV_FILE == PROJECT_ROOT / ".env" and ENV_FILE.is_absolute()


def test_missing_values_without_env_file_explain_how_to_create_it(tmp_path: Path) -> None:
    message = format_settings_error(error_for(), tmp_path / ".env")
    assert "BOT_TOKEN: не задана" in message and "DATABASE_URL: не задана" in message
    assert "copy .env.example .env" in message and "cp .env.example .env" in message  # Windows и macOS
    assert ".env.txt" in message  # типичная ошибка Windows


def test_existing_env_file_gets_a_different_hint(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("BOT_TOKEN=\n", encoding="utf-8")
    message = format_settings_error(error_for(), env)
    assert "найден, но значения" in message and "copy .env.example" not in message


def test_invalid_value_is_described_without_leaking_it(tmp_path: Path) -> None:
    secret_url = "postgresql://user:SuperSecretPassword@host/db"  # неверный драйвер
    message = format_settings_error(error_for(bot_token="1:x", database_url=secret_url), tmp_path / ".env")
    assert "DATABASE_URL" in message and "asyncpg" in message
    assert "SuperSecretPassword" not in message  # значение не печатается


def test_running_the_bot_without_env_prints_friendly_message_and_exits() -> None:
    """Запуск настоящей команды в чистом окружении: нет трассировки, код выхода 1."""
    if (PROJECT_ROOT / ".env").exists():
        pytest.skip("в проекте есть реальный .env")
    result = subprocess.run(
        [sys.executable, "-m", "app.main"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60,
        env={"PATH": "", "SYSTEMROOT": "C:\\Windows"} if sys.platform == "win32" else {"PATH": ""},
    )
    assert result.returncode == 1
    assert "НЕ УДАЛОСЬ ЗАГРУЗИТЬ НАСТРОЙКИ" in result.stderr and "BOT_TOKEN" in result.stderr
    assert "Traceback" not in result.stderr
