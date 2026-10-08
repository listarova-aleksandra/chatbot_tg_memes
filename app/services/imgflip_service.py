"""Imgflip API: список популярных шаблонов мемов.

Endpoint `GET https://api.imgflip.com/get_memes` отдаёт топ-100 шаблонов без ключа.
Картинки мы рисуем сами (Pillow, Этап 6), а Imgflip нужен как источник шаблонов.

Цепочка: вызывающий код → ImgflipService → TTLCache → ApiClient → Imgflip.
Если Imgflip недоступен, `get_templates()` не падает, а возвращает запасные шаблоны.
"""

import logging
from dataclasses import dataclass
from typing import Any

from app.core.cache import TTLCache
from app.core.exceptions import ApiBadResponseError, ApiError
from app.core.http import ApiClient

logger = logging.getLogger(__name__)

API_URL = "https://api.imgflip.com/get_memes"
CACHE_KEY = "imgflip:templates"
CACHE_TTL_SECONDS = 6 * 60 * 60  # список популярных шаблонов меняется редко
IMAGE_CACHE_TTL_SECONDS = 60 * 60
MAX_IMAGE_BYTES = 5_000_000
IMAGE_HOST_PREFIX = "https://i.imgflip.com/"  # картинки принимаем только с этого хоста
MAX_NAME_LEN = 60


@dataclass(frozen=True)
class MemeTemplate:
    id: str
    name: str
    url: str | None  # None: пустой фон, который рисует сам бот (запасной шаблон)
    width: int
    height: int
    box_count: int  # сколько текстовых полей у шаблона в Imgflip


# Запасные шаблоны на случай недоступности Imgflip: просто цветной фон без чужих картинок.
FALLBACK_TEMPLATES = [
    MemeTemplate("local-dark", "Тёмный фон", None, 800, 600, 2),
    MemeTemplate("local-blue", "Синий фон", None, 800, 600, 2),
    MemeTemplate("local-red", "Красный фон", None, 800, 600, 2),
]


@dataclass(frozen=True)
class TemplatesResult:
    templates: list[MemeTemplate]
    is_fallback: bool  # True: Imgflip недоступен, показаны запасные шаблоны


def parse_templates(payload: Any) -> list[MemeTemplate]:
    """Разбирает ответ Imgflip. Бросает ApiBadResponseError, если структура неожиданная.

    Берём только шаблоны с 1–2 текстовыми полями (у нас верхний и нижний текст),
    с картинкой на i.imgflip.com и разумным названием.
    """
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ApiBadResponseError("imgflip: success != true")
    memes = (payload.get("data") or {}).get("memes")
    if not isinstance(memes, list):
        raise ApiBadResponseError("imgflip: нет списка memes")

    templates: list[MemeTemplate] = []
    seen_names: set[str] = set()
    for item in memes:
        try:
            template = MemeTemplate(
                id=str(item["id"]),
                name=str(item["name"]).strip(),
                url=str(item["url"]),
                width=int(item["width"]),
                height=int(item["height"]),
                box_count=int(item["box_count"]),
            )
        except (KeyError, TypeError, ValueError):
            continue  # один испорченный элемент не ломает весь список
        if (
            template.box_count in (1, 2)
            and template.url is not None
            and template.url.startswith(IMAGE_HOST_PREFIX)
            and 0 < len(template.name) <= MAX_NAME_LEN
            and template.name not in seen_names
        ):
            seen_names.add(template.name)
            templates.append(template)

    if not templates:
        raise ApiBadResponseError("imgflip: пустой список шаблонов")
    return templates


class ImgflipService:
    name = "imgflip"

    def __init__(self, client: ApiClient, cache: TTLCache) -> None:
        self.client = client
        self.cache = cache

    async def fetch_templates(self) -> list[MemeTemplate]:
        """Шаблоны из кэша или из API. Бросает ApiError при сбое."""
        cached = self.cache.get(CACHE_KEY)
        if cached is not None:
            return cached
        payload = await self.client.request_json(self.name, "GET", API_URL)
        templates = parse_templates(payload)
        self.cache.set(CACHE_KEY, templates, CACHE_TTL_SECONDS)
        return templates

    async def get_templates(self) -> TemplatesResult:
        """Безопасная версия: при любой ошибке отдаёт запасные шаблоны, не бросая исключений."""
        try:
            return TemplatesResult(await self.fetch_templates(), is_fallback=False)
        except ApiError as error:
            logger.warning("Imgflip недоступен (%s), используем запасные шаблоны", error)
            return TemplatesResult(list(FALLBACK_TEMPLATES), is_fallback=True)

    async def get_template_image(self, template: MemeTemplate) -> bytes | None:
        """Байты картинки шаблона (из кэша или с сервера). None для запасных шаблонов без картинки.

        Бросает ApiError, если скачать не удалось: вызывающий код сообщит пользователю.
        """
        if template.url is None:
            return None
        key = f"imgflip:image:{template.id}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        data = await self.client.request_bytes(self.name, template.url, max_bytes=MAX_IMAGE_BYTES)
        self.cache.set(key, data, IMAGE_CACHE_TTL_SECONDS)
        return data
