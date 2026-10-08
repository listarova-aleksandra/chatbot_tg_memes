"""Рисование мема: картинка + верхний и нижний текст (Pillow).

Задача учебная, поэтому без графического редактора: один алгоритм, который всегда даёт
читаемый результат.
  1. Картинка проверяется (формат, размер) и уменьшается до 1024 px по большей стороне.
  2. Текст очищается (без эмодзи и управляющих символов), переводится в ВЕРХНИЙ РЕГИСТР.
  3. Для каждого блока подбирается самый крупный шрифт, при котором текст, разбитый на
     строки по словам, помещается в отведённую область. Слишком длинное слово режется по
     символам, а если не помещается даже мелкий шрифт, текст обрезается с многоточием.
  4. Текст рисуется белым с чёрной обводкой (читается на любом фоне).

Функции здесь «чистые» и синхронные: они не знают про Telegram. Из бота их вызывают через
`asyncio.to_thread`, чтобы тяжёлая работа с пикселями не блокировала остальных пользователей.
"""

import io
import logging
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_SIDE = 1024  # результат не больше 1024 px по большей стороне
MAX_SOURCE_PIXELS = 25_000_000  # исходник больше 25 Мпикс отклоняем ещё до декодирования
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_TEXT_LEN = 100
KEEP_SYMBOLS = "№°"  # «прочие символы» (категория So), которые есть в шрифтах: остальные это эмодзи
MAX_RAW_TEXT_LEN = 300  # слишком длинный ввод отсекаем сразу, не тратя время на очистку
BLOCK_HEIGHT_RATIO = 0.30  # текстовый блок занимает не больше 30% высоты картинки
SIDE_MARGIN_RATIO = 0.04
ELLIPSIS = "…"

FONTS_DIR = Path(__file__).resolve().parent.parent / "content" / "fonts"
# Системные шрифты с кириллицей. Свой шрифт можно положить в app/content/fonts/: он важнее.
SYSTEM_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",  # macOS: классический «мемный»
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/impact.ttf",  # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

BLANK_COLORS = {  # запасные шаблоны без картинки: градиент сверху вниз
    "local-dark": ((18, 18, 28), (70, 70, 110)),
    "local-blue": ((10, 40, 90), (60, 140, 220)),
    "local-red": ((90, 10, 20), (220, 70, 80)),
}
DEFAULT_COLORS = BLANK_COLORS["local-dark"]


class MemeTextError(ValueError):
    """Текст не подходит (пустой, слишком длинный)."""


class MemeImageError(ValueError):
    """Картинка не подходит (не изображение, неподдерживаемый формат, слишком большая)."""


# ---------- Текст ----------


def clean_text(raw: str) -> str:
    """Проверяет и очищает пользовательский текст. Пустая строка допустима (поле пропущено).

    Удаляются эмодзи и прочие символы, которых нет в шрифтах (иначе вместо них
    нарисуются квадратики), управляющие и невидимые символы. Пробелы схлопываются.
    """
    if len(raw) > MAX_RAW_TEXT_LEN:
        raise MemeTextError(f"Слишком длинный текст: максимум {MAX_TEXT_LEN} символов")
    text = unicodedata.normalize("NFC", raw)
    kept = []
    for char in text:
        category = unicodedata.category(char)
        if char in "\n\r\t":
            kept.append(" ")
        elif char in KEEP_SYMBOLS:
            kept.append(char)
        elif category[0] == "C" or category in ("So", "Mn", "Me", "Zl", "Zp"):
            continue  # управляющие, невидимые, эмодзи, комбинируемые знаки
        elif category == "Zs":
            kept.append(" ")
        else:
            kept.append(char)
    cleaned = " ".join("".join(kept).split())
    if len(cleaned) > MAX_TEXT_LEN:
        raise MemeTextError(f"Слишком длинный текст: максимум {MAX_TEXT_LEN} символов")
    return cleaned


# ---------- Шрифт ----------


def _supports_cyrillic(font: ImageFont.FreeTypeFont) -> bool:
    """Шрифт умеет рисовать кириллицу, если «Ж» не выглядит как «пустой квадрат» (notdef)."""
    def render(char: str) -> bytes:
        canvas = Image.new("L", (80, 80), 0)
        ImageDraw.Draw(canvas).text((10, 10), char, font=font, fill=255)
        return canvas.tobytes()

    try:
        # У отсутствующего символа шрифт рисует одинаковый «пустой квадрат» (notdef).
        return render("Ж") != render("\uffff") and render("Ж") != render("\ue000")
    except Exception:
        return False


@lru_cache(maxsize=1)
def find_font_path() -> str | None:
    """Первый подходящий шрифт: сначала из app/content/fonts, потом системные."""
    candidates = sorted(str(p) for p in FONTS_DIR.glob("*") if p.suffix.lower() in (".ttf", ".otf"))
    candidates += SYSTEM_FONT_CANDIDATES
    for path in candidates:
        if not Path(path).exists():
            continue
        try:
            if _supports_cyrillic(ImageFont.truetype(path, 40)):
                logger.info("Шрифт для мемов: %s", path)
                return path
        except OSError:
            continue
    logger.warning("Не найден системный шрифт с кириллицей, используется шрифт Pillow по умолчанию")
    return None


@lru_cache(maxsize=128)
def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)  # запасной вариант: латиница без кириллицы


# ---------- Раскладка текста ----------


def _split_long_word(word: str, font: ImageFont.ImageFont, max_width: float) -> list[str]:
    """Слово шире строки режется на куски по символам."""
    parts, current = [], ""
    for char in word:
        if current and font.getlength(current + char) > max_width:
            parts.append(current)
            current = char
        else:
            current += char
    return parts + [current] if current else parts


def wrap_text(text: str, font: ImageFont.ImageFont, max_width: float) -> list[str]:
    """Жадный перенос по словам: слова добавляются в строку, пока она помещается."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        pieces = [word] if font.getlength(word) <= max_width else _split_long_word(word, font, max_width)
        for piece in pieces:
            candidate = f"{current} {piece}".strip() if current else piece
            if current and font.getlength(candidate) > max_width:
                lines.append(current)
                current = piece
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class TextLayout:
    lines: list[str]
    size: int  # размер шрифта
    line_height: int
    stroke: int

    @property
    def height(self) -> int:
        return self.line_height * len(self.lines)


def layout_text(text: str, max_width: float, max_height: float, max_size: int) -> TextLayout | None:
    """Подбирает самый крупный шрифт, при котором текст помещается в область.

    Перебор идёт от крупного размера к мелкому. Если не помещается даже на минимальном
    размере, лишние строки отбрасываются, а в последней ставится многоточие.
    """
    text = text.upper()
    if not text:
        return None
    min_size = max(10, max_size // 5)
    layout = None
    for size in range(max_size, min_size - 1, -2):
        font = load_font(size)
        stroke = max(2, size // 12)
        lines = wrap_text(text, font, max_width - 2 * stroke)
        line_height = int(size * 1.12)
        layout = TextLayout(lines, size, line_height, stroke)
        if layout.height + 2 * stroke <= max_height:
            return layout

    # Даже самый мелкий шрифт не вмещает текст: обрезаем.
    assert layout is not None
    fit = max(1, int((max_height - 2 * layout.stroke) // layout.line_height))
    lines = layout.lines[:fit]
    font = load_font(layout.size)
    last = lines[-1]
    while last and font.getlength(last + ELLIPSIS) > max_width - 2 * layout.stroke:
        last = last[:-1]
    lines[-1] = last.rstrip() + ELLIPSIS
    return TextLayout(lines, layout.size, layout.line_height, layout.stroke)


def _draw_block(draw: ImageDraw.ImageDraw, layout: TextLayout, center_x: float, top: float) -> None:
    font = load_font(layout.size)
    y = top + layout.stroke
    for line in layout.lines:
        draw.text(
            (center_x, y),
            line,
            font=font,
            fill="white",
            stroke_width=layout.stroke,
            stroke_fill="black",
            anchor="mt",  # m = по центру горизонтально, t = от верхней точки строки
        )
        y += layout.line_height


# ---------- Картинка ----------


def open_source_image(data: bytes) -> Image.Image:
    """Проверяет и открывает исходную картинку. Бросает MemeImageError, если она не подходит."""
    try:
        probe = Image.open(io.BytesIO(data))
        if probe.format not in ALLOWED_FORMATS:
            raise MemeImageError("Поддерживаются только JPEG, PNG и WebP")
        width, height = probe.size
        if width * height > MAX_SOURCE_PIXELS:
            raise MemeImageError("Изображение слишком большое")
        if width < 16 or height < 16:
            raise MemeImageError("Изображение слишком маленькое")
        probe.verify()  # проверяет целостность файла, но после verify() картинку надо открыть заново
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)  # поворот по EXIF (фото с телефона)
        return image.convert("RGB")
    except MemeImageError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, ValueError) as error:
        raise MemeImageError("Не удалось прочитать изображение") from error


def blank_canvas(template_id: str, width: int = 800, height: int = 600) -> Image.Image:
    """Градиентный фон для запасных шаблонов (когда Imgflip недоступен)."""
    top, bottom = BLANK_COLORS.get(template_id, DEFAULT_COLORS)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        mix = y / (height - 1)
        draw.line([(0, y), (width, y)], fill=tuple(int(t + (b - t) * mix) for t, b in zip(top, bottom)))
    return image


def render_meme(base: Image.Image, top_text: str, bottom_text: str) -> bytes:
    """Рисует текст на картинке и возвращает JPEG. Тексты должны быть уже очищены `clean_text`."""
    if not (top_text or bottom_text):
        raise MemeTextError("Нужен хотя бы один текст")

    image = base.copy()
    image.thumbnail((MAX_SIDE, MAX_SIDE))  # уменьшает с сохранением пропорций, увеличивать не будет
    width, height = image.size
    margin = width * SIDE_MARGIN_RATIO
    max_width = width - 2 * margin
    max_height = height * BLOCK_HEIGHT_RATIO
    max_size = max(18, int(height / 7))

    draw = ImageDraw.Draw(image)
    top_layout = layout_text(top_text, max_width, max_height, max_size)
    bottom_layout = layout_text(bottom_text, max_width, max_height, max_size)
    if top_layout:
        _draw_block(draw, top_layout, width / 2, margin / 2)
    if bottom_layout:
        _draw_block(draw, bottom_layout, width / 2, height - bottom_layout.height - 2 * bottom_layout.stroke - margin / 2)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def build_meme(image_bytes: bytes | None, template_id: str, top_text: str, bottom_text: str) -> bytes:
    """Весь путь «байты картинки → готовый JPEG». Вызывается из бота в отдельном потоке.

    image_bytes=None означает запасной шаблон без картинки: рисуется градиентный фон.
    """
    base = open_source_image(image_bytes) if image_bytes is not None else blank_canvas(template_id)
    return render_meme(base, top_text, bottom_text)
