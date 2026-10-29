# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Draw rendered slides as PNG or JPEG images."""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

from .outline import OutlineError

FORMATS = {"png": "png", "jpeg": "jpeg", "jpg": "jpeg"}


def image_format(name: str) -> str:
    """``png`` or ``jpeg`` for a requested format; raises OutlineError otherwise."""
    fmt = FORMATS.get((name or "png").strip().lower().lstrip("."))
    if fmt is None:
        raise OutlineError(
            [f"image_format {name!r} is not supported. Use png (the default) or jpeg."]
        )
    return fmt


def page_count(pdf: Path) -> int:
    import pymupdf

    with pymupdf.open(str(pdf)) as doc:
        return doc.page_count


def save_pages(
    pdf: Path, pages: Sequence[int], outs: Sequence[Path], fmt: str, width: int
) -> List[dict]:
    """Draw 1-based *pages* of *pdf* to *outs*, *width* pixels wide.

    A page that renders as one flat colour is an error: the image would claim
    a slide it does not show.
    """
    import pymupdf

    saved = []
    with pymupdf.open(str(pdf)) as doc:
        for page_no, out in zip(pages, outs):
            page = doc[page_no - 1]
            zoom = width / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            sample = pix.samples[:: max(1, len(pix.samples) // 5000)]
            if len(set(sample)) <= 1:
                raise OutlineError(
                    [
                        f"slide {page_no} rendered as a blank image, so it was not "
                        "saved. Check the deck opens in PowerPoint, then try again."
                    ]
                )
            # The library writes the file, after the caller's check_write.
            if fmt == "jpeg":
                pix.save(str(out), output="jpeg", jpg_quality=92)
            else:
                pix.save(str(out), output="png")
            saved.append(
                {
                    "slide": page_no,
                    "path": str(out),
                    "width": pix.width,
                    "height": pix.height,
                }
            )
    return saved
