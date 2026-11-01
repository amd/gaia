#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
PDF image extraction utilities for multi-modal RAG.

Extracts individual images from PDF pages (not whole page conversion).
"""

import io
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


def prepare_image_for_vlm(image_bytes: bytes) -> dict:
    """Downscale and re-encode an image as the PNG or JPEG the VLM is sent.

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
    image_bytes = png_buffer.getvalue()
    image_format = "png"

    # Halving a scan to fit makes small print unreadable, so change encoding.
    MAX_PNG_KB = 300
    if len(image_bytes) / 1024 > MAX_PNG_KB:
        logger.info(f"   Re-encoding as JPEG: {len(image_bytes) / 1024:.0f}KB PNG")
        if img.mode == "RGBA":
            background = Image.new("RGB", img.size, "white")
            background.paste(img, mask=img.getchannel("A"))
            img = background
        jpeg_buffer = io.BytesIO()
        img.save(jpeg_buffer, format="JPEG", quality=88, optimize=True)
        image_bytes = jpeg_buffer.getvalue()
        image_format = "jpeg"

    return {
        "image_bytes": image_bytes,
        "width": img.width,
        "height": img.height,
        "format": image_format,
        "size_kb": len(image_bytes) / 1024,
    }


def extract_images_from_page_pymupdf(
    pdf_path: str, page_num: int, min_cover: float = 0.0, strict: bool = False
) -> List[dict]:
    """
    Extract images using PyMuPDF (more reliable than pypdf for images).

    Args:
        pdf_path: Path to PDF file
        page_num: Page number (1-indexed)
        min_cover: Leave out images covering less than this share of the page
        strict: Raise instead of logging, for a caller that records the page as
            read when nothing comes back

    Returns:
        List of image dicts with bytes, dimensions, etc.
    """
    images = []

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(pdf_path)
        try:
            page = doc[page_num - 1]  # PyMuPDF uses 0-indexed

            image_list = page.get_images()

            for img_index, img_info in enumerate(image_list):
                try:
                    xref = img_info[0]
                    drawn = sum(
                        rect.width * rect.height for rect in page.get_image_rects(xref)
                    )
                    if drawn < min_cover * page.rect.width * page.rect.height:
                        continue

                    # Extract image bytes (PyMuPDF handles decoding)
                    base_image = doc.extract_image(xref)
                    image = prepare_image_for_vlm(base_image["image"])
                    images.append(image)

                    logger.debug(
                        f"Extracted image {img_index + 1}: "
                        f"{image['width']}x{image['height']}, {image['size_kb']:.1f}KB"
                    )

                except Exception as e:
                    if strict:
                        raise
                    logger.warning(
                        f"Failed to extract image {img_index + 1} from page {page_num}: {e}"
                    )
                    continue
        finally:
            doc.close()

    except ImportError:
        if strict:
            raise
        logger.error("PyMuPDF not installed. Install: uv pip install pymupdf")
    except Exception as e:
        if strict:
            raise
        logger.error(f"Error extracting images from page {page_num}: {e}")

    return images


def extract_images_from_page(
    page, page_num: int  # pylint: disable=unused-argument
) -> List[dict]:
    """
    DEPRECATED: Use extract_images_from_page_pymupdf instead.

    This function kept for backwards compatibility but PyMuPDF
    is more reliable for image extraction.
    """
    logger.warning("Using deprecated pypdf image extraction - switch to PyMuPDF")
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
    Get positions of images on PDF page using PyMuPDF.

    Args:
        pdf_path: Path to PDF file
        page_num: Page number (0-indexed)

    Returns:
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
    """
    positions: list[dict] = []

    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.debug("PyMuPDF not available for position detection")
        return positions

    try:
        doc = fitz.open(pdf_path)
        try:
            page = doc[page_num]

            image_list = page.get_images()

            for img_index, img_info in enumerate(image_list):
                # Get image bounding box
                xref = img_info[0]
                image_rects = page.get_image_rects(xref)

                if image_rects:
                    rect = image_rects[0]  # First occurrence
                    bbox = [rect.x0, rect.y0, rect.x1, rect.y1]

                    positions.append(
                        {
                            "image_index": img_index,
                            "bbox": bbox,
                            "position_y": rect.y0,  # Top Y coordinate
                            "width": int(rect.width),
                            "height": int(rect.height),
                        }
                    )
        finally:
            doc.close()

    except Exception as e:
        logger.warning(f"Could not get image positions for page {page_num}: {e}")

    return positions
