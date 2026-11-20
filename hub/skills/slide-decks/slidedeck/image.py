# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read rendered slides from a PDF: page images and where each word sits."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Sequence, Tuple

from .outline import OutlineError

FORMATS = {"png": "png", "jpeg": "jpeg", "jpg": "jpeg"}

#: A word on a page: left, top, right, bottom in points from the top-left, text.
Word = Tuple[float, float, float, float, str]


def image_format(name: str) -> str:
    """``png`` or ``jpeg`` for a requested format; raises OutlineError otherwise."""
    fmt = FORMATS.get((name or "png").strip().lower().lstrip("."))
    if fmt is None:
        raise OutlineError(
            [f"image_format {name!r} is not supported. Use png (the default) or jpeg."]
        )
    return fmt


def open_pdf(pdf: Path) -> Any:
    """The PDF as a pypdfium2 document; the caller closes it."""
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(str(pdf))


def page_count(pdf: Path) -> int:
    doc = open_pdf(pdf)
    try:
        return len(doc)
    finally:
        doc.close()


def page_image(page: Any, scale: float) -> Any:
    """*page* drawn as an RGB Pillow image; *scale* 1.0 is one pixel per point."""
    return page.render(scale=scale).to_pil().convert("RGB")


def page_words(page: Any) -> List[Word]:
    """Every word on *page* in reading order, with the box its glyphs fill."""
    import pypdfium2.raw as pdfium_c

    height = page.get_height()
    textpage = page.get_textpage()
    words: List[Word] = []
    chars: List[str] = []
    box: List[float] = []

    def flush() -> None:
        if chars:
            words.append((box[0], box[1], box[2], box[3], "".join(chars)))
        chars.clear()
        box.clear()

    try:
        joining = False
        for index in range(textpage.count_chars()):
            char = chr(pdfium_c.FPDFText_GetUnicode(textpage.raw, index))
            # U+0002 is PDFium's soft hyphen: the word goes on after the line break.
            if char == "\x02":
                joining = bool(chars)
                continue
            if char.isspace() or char in "\x00\ufffe":
                if not joining:
                    flush()
                continue
            joining = False
            left, bottom, right, top = textpage.get_charbox(index, loose=True)
            y0, y1 = height - top, height - bottom
            if box:
                box[:] = [
                    min(box[0], left),
                    min(box[1], y0),
                    max(box[2], right),
                    max(box[3], y1),
                ]
            else:
                box[:] = [left, y0, right, y1]
            chars.append(char)
        flush()
    finally:
        textpage.close()
    return words


def is_blank(image: Any) -> bool:
    """True when *image* is one flat colour: nothing was drawn on the page."""
    small = image.copy()
    small.thumbnail((256, 256))
    return all(low == high for low, high in small.convert("RGB").getextrema())


def save_pages(
    pdf: Path, pages: Sequence[int], outs: Sequence[Path], fmt: str, width: int
) -> List[dict]:
    """Draw 1-based *pages* of *pdf* to *outs*, *width* pixels wide.

    A page that renders as one flat colour is an error: the image would claim
    a slide it does not show.
    """
    saved = []
    doc = open_pdf(pdf)
    try:
        for page_no, out in zip(pages, outs):
            page = doc[page_no - 1]
            image = page_image(page, width / page.get_width())
            if is_blank(image):
                raise OutlineError(
                    [
                        f"slide {page_no} rendered as a blank image, so it was not "
                        "saved. Check the deck opens in PowerPoint, then try again."
                    ]
                )
            # The library writes the file, after the caller's check_write.
            if fmt == "jpeg":
                image.save(str(out), format="JPEG", quality=92)
            else:
                image.save(str(out), format="PNG")
            saved.append(
                {
                    "slide": page_no,
                    "path": str(out),
                    "width": image.width,
                    "height": image.height,
                }
            )
    finally:
        doc.close()
    return saved
