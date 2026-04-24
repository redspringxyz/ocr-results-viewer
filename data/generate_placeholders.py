"""Regenerate the placeholder WebP page images used by ``outputs.json``.

The production viewer data comes from ``benchmarks/export_viewer_data.py`` in
the source ``modal/ocr-benchmark`` repo, which reads the real 144-DPI corpus
on Modal, downscales pages to ~960 px, and writes real WebPs alongside the
model outputs. This file exists so the committed placeholder set is
reproducible from source without needing the Modal corpus. Running it is not
required for day-to-day viewer work.

Usage (from this repo's root)::

    uvx --with pillow python data/generate_placeholders.py

Or with a local venv that already has Pillow::

    python data/generate_placeholders.py
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUTPUTS_JSON = HERE / "outputs.json"
PAGES_DIR = HERE / "pages"

WIDTH = 800
HEIGHT = 1040
BG = (248, 246, 240)
FG = (40, 38, 32)
ACCENT = (139, 46, 46)
BORDER = (200, 196, 186)


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(page: dict, dest: Path) -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle((24, 24, WIDTH - 24, HEIGHT - 24), outline=BORDER, width=2)

    title_font = _load_font(30)
    meta_font = _load_font(20)
    big_font = _load_font(180)
    small_font = _load_font(14)

    draw.text((48, 56), "PLACEHOLDER PAGE", fill=ACCENT, font=title_font)
    y = 100
    lines = [
        f"id:       {page['id']}",
        f"pdf:      {page.get('pdf', '-')}",
        f"page no:  {page.get('page', '-')}",
        f"category: {page.get('primary_code', '-')}",
    ]
    for line in lines:
        draw.text((48, y), line, fill=FG, font=meta_font)
        y += 30

    page_no_str = f"p{page.get('page', '?')}"
    try:
        bbox = draw.textbbox((0, 0), page_no_str, font=big_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        tw, th = draw.textsize(page_no_str, font=big_font)
    draw.text(((WIDTH - tw) / 2, (HEIGHT - th) / 2 - 20), page_no_str, fill=FG, font=big_font)

    footer = "Regenerate via `modal/ocr-benchmark` -> benchmarks/export_viewer_data.py"
    draw.text((48, HEIGHT - 60), footer, fill=(100, 96, 86), font=small_font)

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="WEBP", quality=70, method=6)


def main() -> None:
    with OUTPUTS_JSON.open() as f:
        data = json.load(f)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    for page in data["pages"]:
        image_rel = page.get("image")
        if not image_rel:
            continue
        dest = HERE.parent / image_rel if image_rel.startswith("data/") else PAGES_DIR / Path(image_rel).name
        render(page, dest)
        size_kb = dest.stat().st_size / 1024
        print(f"wrote {dest.relative_to(HERE.parent.parent)} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
