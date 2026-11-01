"""Настройка логирования.

Добавлен фильтр, который маскирует секреты в тексте логов: токен бота
и параметр `api_key=...`. Даже если какая-то библиотека напечатает URL с ключом,
в лог он не попадёт.
"""

import logging
import re

_SECRET_PATTERNS = [
    # Токен Telegram-бота вида 123456789:AAE...
    (re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"), "<bot-token>"),
    # ?api_key=XXXX или &api_key=XXXX
    (re.compile(r"(api_key=)[^&\s]+", re.IGNORECASE), r"\1<hidden>"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Подменяем уже отформатированное сообщение и сбрасываем аргументы.
        record.msg = redact(record.getMessage())
        record.args = ()
        # Текст трассировки тоже может содержать секрет: например, ошибка скачивания файла
        # печатает URL вида .../file/bot<ТОКЕН>/... Готовим его заранее и маскируем:
        # Formatter использует готовый record.exc_text, а не форматирует исключение заново.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactSecretsFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # SQLAlchemy и aiohttp на уровне INFO слишком болтливы.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
