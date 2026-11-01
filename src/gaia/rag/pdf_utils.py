#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
PDF image extraction utilities for multi-modal RAG.

Extracts individual images from PDF pages (not whole page conversion).
"""

import hashlib
import io
from typing import List, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)


def prepare_image_for_vlm(image_bytes: bytes) -> dict:
    """Downscale and re-encode an image as the PNG the VLM is sent.

    Returns a dict with ``image_bytes``, ``width``, ``height``, ``format`` and
    ``size_kb``. Raises ``PIL.UnidentifiedImageError`` (an ``OSError``) when
    Pillow cannot decode the bytes.
    """
    from PIL import Image

    return prepare_pil_image_for_vlm(Image.open(io.BytesIO(image_bytes)))


def prepare_pil_image_for_vlm(img) -> dict:
    """:func:`prepare_image_for_vlm` for an already-decoded ``PIL.Image``."""
    from PIL import Image

    # Get dimensions
    width, height = img.size

    # A plain convert("RGB") clips 16-bit samples to 255: the image turns white.
    if img.mode.startswith("I;16"):
        img = img.convert("I").point(lambda v: v * (1 / 257)).convert("L")

    # Convert to RGB if needed
    if img.mode not in ["RGB", "RGBA"]:
        logger.debug(f"Converting {img.mode} to RGB")
        img = img.convert("RGB")

    # Resize if too large
    MAX_DIMENSION = 1600
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        scale = min(MAX_DIMENSION / width, MAX_DIMENSION / height)
        new_width = int(width * scale)
        new_height = int(height * scale)

        logger.info(f"   Resizing: {width}x{height} → {new_width}x{new_height}")
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Save as optimized PNG
    png_buffer = io.BytesIO()
    img.save(png_buffer, format="PNG", optimize=True, compress_level=6)
    png_bytes = png_buffer.getvalue()
    size_kb = len(png_bytes) / 1024

    # Iteratively compress until target size is reached
    MAX_SIZE_KB = 300
    compression_iterations = 0
    MAX_ITERATIONS = 5

    while size_kb > MAX_SIZE_KB and compression_iterations < MAX_ITERATIONS:
        compression_iterations += 1
        logger.info(
            f"   Compressing (iteration {compression_iterations}): {size_kb:.0f}KB → <{MAX_SIZE_KB}KB"
        )

        # Reduce size by 50% each iteration
        img = img.resize(
            (img.width // 2, img.height // 2),
            Image.Resampling.LANCZOS,
        )

        png_buffer = io.BytesIO()
        img.save(
            png_buffer,
            format="PNG",
            optimize=True,
            compress_level=9,
        )
        png_bytes = png_buffer.getvalue()
        size_kb = len(png_bytes) / 1024

    if size_kb <= MAX_SIZE_KB:
        logger.info(
            f"   ✅ Compressed to {size_kb:.0f}KB ({img.width}x{img.height}) in {compression_iterations} iteration(s)"
        )
    else:
        logger.warning(
            f"   ⚠️  Could not compress below {MAX_SIZE_KB}KB after {MAX_ITERATIONS} iterations (final: {size_kb:.0f}KB)"
        )

    return {
        "image_bytes": png_bytes,
        "width": img.width,
        "height": img.height,
        "format": "png",
        "size_kb": size_kb,
    }


class PdfImageExtractionError(RuntimeError):
    """A page's images could not be extracted.

    ``images`` holds whatever did extract, so a caller can still use the
    partial result while reporting the page as degraded.
    """

    def __init__(self, message: str, page_num: int = 0, images=None):
        self.page_num = page_num
        self.images: List[dict] = list(images or [])
        super().__init__(message)


def _open_pdf_page(pdf_path, page_index: int):
    """Open *pdf_path* with PDFium and return ``(document, page)``.

    The caller closes the document. Raises :class:`PdfImageExtractionError`
    naming the file and page when either cannot be opened.
    """
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(pdf_path)
    except pdfium.PdfiumError as e:
        raise PdfImageExtractionError(
            f"could not open {pdf_path} to read page {page_index + 1}: "
            f"{str(e).rstrip('.')}. "
            "The file may be damaged or password-protected; re-save it from a "
            "PDF viewer (or remove the password) and index it again.",
            page_index + 1,
        ) from e
    if not 0 <= page_index < len(pdf):
        pages = len(pdf)
        pdf.close()
        raise PdfImageExtractionError(
            f"page {page_index + 1} is not in {pdf_path} (it has {pages} pages)",
            page_index + 1,
        )
    return pdf, pdf[page_index]


def extract_images_from_page_pdfium(pdf_path: str, page_num: int) -> List[dict]:
    """Extract the embedded images on one PDF page, ready for the VLM.

    Args:
        pdf_path: Path to PDF file
        page_num: Page number (1-indexed)

    Returns:
        One :func:`prepare_image_for_vlm` dict per distinct image; ``[]`` only
        when the page genuinely has no images.

    Raises:
        FileNotFoundError: *pdf_path* does not exist.
        PdfImageExtractionError: the file or page could not be opened, or an
            image on it could not be decoded. ``images`` carries the ones that
            did decode.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    pdf, page = _open_pdf_page(pdf_path, page_num - 1)
    images: List[dict] = []
    failures: List[str] = []
    seen = set()
    try:
        image_objects = page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])
        for img_index, obj in enumerate(image_objects, 1):
            try:
                pil = obj.get_bitmap(render=False).to_pil()
                # One image placed twice is one image to the VLM.
                key = (pil.size, pil.mode, hashlib.sha256(pil.tobytes()).digest())
                if key in seen:
                    continue
                seen.add(key)
                image = prepare_pil_image_for_vlm(pil)
            except (pdfium.PdfiumError, ValueError, OSError) as e:
                failures.append(f"image {img_index}: {e}")
                continue
            images.append(image)
            logger.debug(
                f"Extracted image {img_index}: "
                f"{image['width']}x{image['height']}, {image['size_kb']:.1f}KB"
            )
    finally:
        pdf.close()

    if failures:
        raise PdfImageExtractionError(
            f"{len(failures)} image(s) on page {page_num} of {pdf_path} could not "
            f"be decoded ({'; '.join(failures)})",
            page_num,
            images,
        )
    return images


def extract_images_from_page(
    page, page_num: int  # pylint: disable=unused-argument
) -> List[dict]:
    """
    DEPRECATED: Use extract_images_from_page_pdfium instead.

    Kept for backwards compatibility; it extracts nothing.
    """
    logger.warning(
        "extract_images_from_page is deprecated and extracts nothing - "
        "use extract_images_from_page_pdfium"
    )
    return []


class PdfPageInspectionError(RuntimeError):
    """A page's image inventory could not be read (#3551).

    Distinct from "this page has no images". The old code returned ``(False,
    0)`` for both, and that boolean is the only gate on the vision path — so a
    damaged page was silently indexed as blank, indistinguishable from a page
    that genuinely had nothing on it.
    """

    def __init__(self, message: str, page_num: int = 0):
        self.page_num = page_num
        super().__init__(message)


def count_images_in_page(page, page_num: int = 0) -> Tuple[bool, int]:
    """
    Fast check for image presence without extraction.

    Args:
        page: pypdf page object
        page_num: 1-based page number, for the error message

    Returns:
        (has_images: bool, count: int)

    Raises:
        PdfPageInspectionError: the page's resources could not be resolved, so
            whether it has images is unknown. Never reported as "no images" —
            that is the gate on the vision path (#3551).
    """
    count = 0

    try:
        if "/XObject" in page.get("/Resources", {}):
            xobject = page["/Resources"]["/XObject"].get_object()
            for obj_name in xobject:
                obj = xobject[obj_name]
                if obj.get("/Subtype") == "/Image":
                    count += 1
    except Exception as e:  # pylint: disable=broad-except
        raise PdfPageInspectionError(
            f"could not read the image inventory of page {page_num}: {e}",
            page_num,
        ) from e

    return (count > 0, count)


def get_image_positions_on_page(pdf_path: str, page_num: int) -> List[dict]:
    """
    Get positions of images on a PDF page.

    Args:
        pdf_path: Path to PDF file
        page_num: Page number (0-indexed)

    Returns:
        One entry per image placement, in top-left-origin page points:
        [
            {
                "image_index": int,
                "bbox": [x0, y0, x1, y1],
                "position_y": float,  # Y-coordinate for sorting
                "width": int,
                "height": int
            },
            ...
        ]

    Raises:
        FileNotFoundError: *pdf_path* does not exist.
        PdfImageExtractionError: the file or page could not be opened.
    """
    import pypdfium2.raw as pdfium_c

    pdf, page = _open_pdf_page(pdf_path, page_num)
    try:
        page_height = page.get_height()
        positions: List[dict] = []
        image_objects = page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])
        for img_index, obj in enumerate(image_objects):
            # PDFium bounds are bottom-left origin; callers sort top-down.
            left, bottom, right, top = obj.get_bounds()
            y0 = page_height - top
            positions.append(
                {
                    "image_index": img_index,
                    "bbox": [left, y0, right, page_height - bottom],
                    "position_y": y0,
                    "width": int(right - left),
                    "height": int(top - bottom),
                }
            )
        return positions
    finally:
        pdf.close()
