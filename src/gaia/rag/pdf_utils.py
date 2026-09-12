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


def extract_images_from_page_pymupdf(pdf_path: str, page_num: int) -> List[dict]:
    """
    Extract images using PyMuPDF (more reliable than pypdf for images).

    Args:
        pdf_path: Path to PDF file
        page_num: Page number (1-indexed)

    Returns:
        List of image dicts with bytes, dimensions, etc.
    """
    images = []

    try:
        import fitz  # PyMuPDF
        from PIL import Image

        doc = fitz.open(pdf_path)
        try:
            page = doc[page_num - 1]  # PyMuPDF uses 0-indexed

            image_list = page.get_images()

            for img_index, img_info in enumerate(image_list):
                try:
                    xref = img_info[0]

                    # Extract image bytes (PyMuPDF handles decoding)
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    _img_ext = base_image["ext"]  # jpg, png, etc.

                    # Open with PIL for processing
                    img = Image.open(io.BytesIO(image_bytes))

                    # Get dimensions
                    width, height = img.size
                    size_kb = len(image_bytes) / 1024

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

                        logger.info(
                            f"   Resizing: {width}x{height} → {new_width}x{new_height}"
                        )
                        img = img.resize(
                            (new_width, new_height), Image.Resampling.LANCZOS
                        )

                    # Save as optimized PNG
                    png_buffer = io.BytesIO()
                    img.save(png_buffer, format="PNG", optimize=True, compress_level=6)
                    png_bytes = png_buffer.getvalue()
                    size_kb = len(png_bytes) / 1024

                    # Iteratively compress until target size is reached
                    MAX_SIZE_KB = 300
                    compression_iterations = 0
                    MAX_ITERATIONS = 5

                    while (
                        size_kb > MAX_SIZE_KB
                        and compression_iterations < MAX_ITERATIONS
                    ):
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

                    images.append(
                        {
                            "image_bytes": png_bytes,
                            "width": img.width,
                            "height": img.height,
                            "format": "png",
                            "size_kb": size_kb,
                        }
                    )

                    logger.debug(
                        f"Extracted image {img_index + 1}: {img.width}x{img.height}, {size_kb:.1f}KB"
                    )

                except Exception as e:
                    logger.warning(
                        f"Failed to extract image {img_index + 1} from page {page_num}: {e}"
                    )
                    continue
        finally:
            doc.close()

    except ImportError:
        logger.error("PyMuPDF not installed. Install: uv pip install pymupdf")
    except Exception as e:
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
