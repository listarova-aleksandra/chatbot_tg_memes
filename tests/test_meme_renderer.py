"""Рендерер мемов: очистка текста, раскладка, защита от плохих картинок."""

import io

import pytest
from PIL import Image

from app.services import meme_renderer as mr
from app.services.meme_renderer import (
    MemeImageError, MemeTextError, blank_canvas, build_meme, clean_text, layout_text, open_source_image,
    render_meme, wrap_text,
)

pytestmark = pytest.mark.skipif(mr.find_font_path() is None, reason="нет системного шрифта с кириллицей")


def png(width: int, height: int, color=(40, 80, 160), fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format=fmt)
    return out.getvalue()


# ---------- Очистка текста ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  привет   мир  ", "привет мир"),
        ("строка\nс переносом\tи табом", "строка с переносом и табом"),
        ("смешно 😂😂 очень", "смешно очень"),
        ("👍", ""),  # одни эмодзи: поле пустое
        ("с​нулевой‍шириной", "снулевойшириной"),  # невидимые символы удалены
        ("a\x00b\x07c", "abc"),  # управляющие символы
        ("Ёлка, №1! 100% $5", "Ёлка, №1! 100% $5"),  # обычная пунктуация и символы остаются
        ("", ""),
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_clean_text_length_limit() -> None:
    assert clean_text("а" * 100) == "а" * 100
    with pytest.raises(MemeTextError):
        clean_text("а" * 101)
    with pytest.raises(MemeTextError):
        clean_text("а" * 10_000)  # отсекается сразу


def test_clean_text_limit_counts_after_cleaning() -> None:
    assert clean_text("а" * 100 + "😂" * 50) == "а" * 100  # эмодзи не считаются


# ---------- Раскладка ----------


def test_wrap_breaks_by_words_and_never_exceeds_width() -> None:
    font = mr.load_font(40)
    lines = wrap_text("раз два три четыре пять шесть семь восемь девять десять", font, 300)
    assert len(lines) > 1 and all(font.getlength(line) <= 300 for line in lines)
    assert " ".join(lines) == "раз два три четыре пять шесть семь восемь девять десять"  # ничего не потеряно


def test_wrap_splits_a_word_wider_than_the_line() -> None:
    font = mr.load_font(40)
    lines = wrap_text("Ж" * 50, font, 200)
    assert all(font.getlength(line) <= 200 for line in lines)
    assert "".join(lines) == "Ж" * 50


@pytest.mark.parametrize(
    "text",
    ["ок", "Когда NBA лучше сериала", "а" * 100, "слово " * 16, "Ж" * 100, "Привет, " + "длинноеслово" * 5],
)
@pytest.mark.parametrize("size", [(300, 300), (800, 600), (1024, 400)])
def test_layout_always_fits_the_block(text: str, size: tuple[int, int]) -> None:
    width, height = size
    max_w, max_h = width * 0.92, height * mr.BLOCK_HEIGHT_RATIO
    layout = layout_text(clean_text(text), max_w, max_h, max_size=max(18, height // 7))
    font = mr.load_font(layout.size)
    assert layout.height + 2 * layout.stroke <= max_h
    assert all(font.getlength(line) + 2 * layout.stroke <= max_w for line in layout.lines)


def test_short_text_gets_bigger_font_than_long_text() -> None:
    short = layout_text("ПРИВЕТ", 700, 200, 120)
    long = layout_text("ОЧЕНЬ ДЛИННЫЙ ТЕКСТ " * 5, 700, 200, 120)
    assert short.size > long.size


def test_impossible_text_is_truncated_with_ellipsis() -> None:
    layout = layout_text("СЛОВО " * 16, 120, 40, 60)  # крошечная область
    assert layout.lines[-1].endswith("…") and layout.height + 2 * layout.stroke <= 40


def test_empty_text_has_no_layout() -> None:
    assert layout_text("", 500, 100, 60) is None


# ---------- Рендер ----------


def brightness_band(data: bytes, y0: float, y1: float) -> int:
    """Сколько почти белых пикселей в горизонтальной полосе (доли высоты y0..y1)."""
    image = Image.open(io.BytesIO(data)).convert("L")
    width, height = image.size
    band = image.crop((0, int(height * y0), width, int(height * y1)))
    return sum(1 for v in band.tobytes() if v > 240)


def test_text_is_drawn_at_top_and_bottom_and_middle_stays_clean() -> None:
    data = render_meme(blank_canvas("local-dark"), "ВЕРХ", "НИЗ")
    assert brightness_band(data, 0.0, 0.3) > 200
    assert brightness_band(data, 0.7, 1.0) > 200
    assert brightness_band(data, 0.35, 0.65) == 0  # середина картинки не закрыта текстом


def test_only_bottom_text_leaves_top_clean() -> None:
    data = render_meme(blank_canvas("local-dark"), "", "ТОЛЬКО НИЗ")
    assert brightness_band(data, 0.0, 0.3) == 0 and brightness_band(data, 0.7, 1.0) > 200


def test_render_requires_some_text() -> None:
    with pytest.raises(MemeTextError):
        render_meme(blank_canvas("local-dark"), "", "")


def test_big_image_is_downscaled_and_result_is_jpeg() -> None:
    data = build_meme(png(3000, 2000), "x", "верх", "низ")
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG" and max(image.size) == 1024
    assert image.size == (1024, 683)  # пропорции сохранены


def test_small_image_is_not_upscaled() -> None:
    image = Image.open(io.BytesIO(build_meme(png(200, 100), "x", "а", "б")))
    assert image.size == (200, 100)


def test_blank_template_is_used_without_image() -> None:
    data = build_meme(None, "local-blue", "а", "б")
    assert Image.open(io.BytesIO(data)).size == (800, 600)


def test_extreme_input_does_not_crash() -> None:
    build_meme(png(64, 64), "x", "Ж" * 100, "А" * 100)
    build_meme(png(5000, 40), "x", "текст", "текст")  # очень вытянутая картинка
    build_meme(png(40, 5000), "x", "текст", "текст")


# ---------- Защита от плохих картинок ----------


def test_garbage_and_wrong_formats_are_rejected() -> None:
    for data in (b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"x" * 10):
        with pytest.raises(MemeImageError):
            open_source_image(data)
    with pytest.raises(MemeImageError, match="только JPEG"):
        open_source_image(png(100, 100, fmt="BMP"))
    with pytest.raises(MemeImageError):
        open_source_image(png(100, 100, fmt="GIF"))


def test_too_large_and_too_small_images_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mr, "MAX_SOURCE_PIXELS", 5_000)
    with pytest.raises(MemeImageError, match="слишком большое"):
        open_source_image(png(100, 100))
    monkeypatch.undo()
    with pytest.raises(MemeImageError, match="слишком маленькое"):
        open_source_image(png(4, 4))


def test_truncated_file_is_rejected() -> None:
    data = png(300, 300)
    with pytest.raises(MemeImageError):
        open_source_image(data[: len(data) // 2])


def test_exif_orientation_is_applied() -> None:
    image = Image.new("RGB", (100, 50), "red")
    exif = Image.Exif()
    exif[0x0112] = 6  # «повернуть на 90°»: так снимают телефоны
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif)
    assert open_source_image(out.getvalue()).size == (50, 100)


def test_font_supports_cyrillic() -> None:
    font = mr.load_font(40)
    assert mr._supports_cyrillic(font)
