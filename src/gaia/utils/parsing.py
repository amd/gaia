# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Parsing utilities for GAIA agents.

Provides utilities for extracting structured data from LLM outputs,
document conversion, and field change detection.
"""

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from gaia.logger import get_logger

logger = get_logger(__name__)

#: PDFium is not thread-safe, even across documents, and pypdfium2 releases the
#: GIL. Hold this from opening a document until it and every page, bitmap and
#: object handle from it are closed.
PDFIUM_LOCK = threading.RLock()


class PdfRenderError(RuntimeError):
    """A PDF page could not be rendered to an image."""


def _extract_balanced_json(
    text: str, start: int, open_ch: str, close_ch: str
) -> Tuple[Optional[Union[Dict[str, Any], List[Any]]], Optional[int]]:
    """Parse the balanced JSON value opening at ``text[start]``.

    Counts only the outer bracket type (inner brackets of the same type are
    themselves balanced), ignoring brackets inside JSON string values and
    handling escaped characters within them.

    Returns ``(value, end)`` where ``end`` is the index of the matching close
    bracket, or ``None`` when the opener is never closed. ``value`` is ``None``
    when the balanced span is not valid JSON.
    """
    depth = 0
    in_string = False
    escaped = False
    for i, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1]), i
                except json.JSONDecodeError as e:
                    logger.debug(f"JSON decode error: {e}")
                    return None, i
    logger.debug(f"Failed to find matching {close_ch!r} in text: {text[:200]}...")
    return None, None


def extract_json_from_text(text: str) -> Optional[Union[Dict[str, Any], List[Any]]]:
    """
    Extract a JSON object or array from text that may contain surrounding content.

    LLMs often return JSON embedded in explanatory text. This function
    handles nested JSON correctly using balanced bracket counting,
    unlike simple regex approaches.

    Args:
        text: Text potentially containing a JSON object or array.

    Returns:
        Parsed JSON as a dict or list, or None if no valid JSON found.

    Example:
        from gaia.utils import extract_json_from_text

        # LLM output with surrounding text
        llm_output = '''Here is the extracted data:
        {"name": "John", "address": {"city": "Boston", "zip": "02101"}}
        I hope this helps!'''

        data = extract_json_from_text(llm_output)
        # Returns: {"name": "John", "address": {"city": "Boston", "zip": "02101"}}
    """
    if not text:
        return None

    # Try parsing entire response as JSON first
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass

    # Look for a JSON object or array in the response, trying whichever
    # opens first (a top-level array's first "{" is an inner row object —
    # scanning only "{" would silently drop every row after the first).
    pairs = {"{": "}", "[": "]"}
    next_start = {open_ch: text.find(open_ch) for open_ch in pairs}
    if all(start == -1 for start in next_start.values()):
        logger.debug("No JSON object or array found in text")
        return None
    while any(start != -1 for start in next_start.values()):
        open_ch = min(
            (ch for ch, start in next_start.items() if start != -1),
            key=next_start.__getitem__,
        )
        start = next_start[open_ch]
        parsed, end = _extract_balanced_json(text, start, open_ch, pairs[open_ch])
        if parsed is not None:
            return parsed
        # Resume after an invalid span (e.g. a "{field}" placeholder) but never
        # inside it, so a malformed object can't yield one of its fragments.
        next_start[open_ch] = -1 if end is None else text.find(open_ch, end + 1)
    return None


def pdf_page_to_image(
    path: Union[str, Path],
    page: int = 0,
    scale: float = 2.0,
) -> bytes:
    """
    Convert a PDF page to PNG image bytes.

    Renders with PDFium (pypdfium2). The scale parameter controls resolution:
    1.0 is 72 DPI (one pixel per PDF point); higher values produce larger,
    sharper images suitable for OCR or VLM processing.

    Args:
        path: Path to the PDF file.
        page: Page number to convert (0-indexed, default: first page).
        scale: Resolution multiplier (default: 2.0 for 2x resolution).

    Returns:
        PNG image as bytes.

    Raises:
        FileNotFoundError: *path* does not exist.
        PdfRenderError: the file could not be opened (damaged, encrypted), the
            page does not exist, or rendering failed.
        ImportError: pypdfium2 is not installed.

    Example:
        from gaia.utils import pdf_page_to_image

        # Convert first page at 2x resolution
        image_bytes = pdf_page_to_image("form.pdf")

        # Convert third page at 3x resolution for better OCR
        image_bytes = pdf_page_to_image("document.pdf", page=2, scale=3.0)
    """
    import io

    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ImportError(
            "pypdfium2 is required to render PDF pages. Install it with: "
            'pip install "amd-gaia[rag]"'
        ) from e

    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as e:
            raise PdfRenderError(
                f"could not open {path}: {str(e).rstrip('.')}. The file may be "
                "damaged or password-protected; re-save it from a PDF viewer and "
                "try again."
            ) from e
        try:
            if not 0 <= page < len(pdf):
                raise PdfRenderError(
                    f"page {page} is not in {path} (it has {len(pdf)} pages)"
                )
            try:
                pdf_page = pdf[page]
                try:
                    bitmap = pdf_page.render(scale=scale)
                    try:
                        # to_pil() shares the bitmap's buffer; copy before closing.
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    pdf_page.close()
            except pdfium.PdfiumError as e:
                raise PdfRenderError(
                    f"could not render page {page} of {path}: {e}"
                ) from e
        finally:
            pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def detect_field_changes(
    old_data: Dict[str, Any],
    new_data: Dict[str, Any],
    fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Detect changes between two dictionaries.

    Compares field values and returns a list of changes. Useful for
    tracking record updates in database-backed agents.

    Args:
        old_data: The original/existing data.
        new_data: The new/updated data.
        fields: Specific fields to compare. If None, compares all keys
               present in either dict.

    Returns:
        List of change dicts with keys: field, old, new.

    Example:
        from gaia.utils import detect_field_changes

        old = {"phone": "555-1234", "email": "old@test.com", "name": "John"}
        new = {"phone": "555-9999", "email": "old@test.com", "name": "John"}

        changes = detect_field_changes(old, new, ["phone", "email"])
        # Returns: [{"field": "phone", "old": "555-1234", "new": "555-9999"}]
    """
    changes = []

    # If no fields specified, compare all keys from both dicts
    if fields is None:
        fields = list(set(old_data.keys()) | set(new_data.keys()))

    for field in fields:
        old_val = old_data.get(field)
        new_val = new_data.get(field)

        # Skip if both are empty/None
        if not old_val and not new_val:
            continue

        # Detect change (normalize to string for comparison)
        if str(old_val or "").strip() != str(new_val or "").strip():
            changes.append(
                {
                    "field": field,
                    "old": old_val,
                    "new": new_val,
                }
            )

    return changes


def validate_required_fields(
    data: Dict[str, Any],
    required_fields: List[str],
) -> Tuple[bool, List[str]]:
    """
    Validate that required fields are present and non-empty.

    Args:
        data: The data dict to validate.
        required_fields: List of field names that must be present.

    Returns:
        Tuple of (is_valid, missing_fields).
        is_valid is True if all required fields are present.
        missing_fields lists any fields that are missing or empty.

    Example:
        from gaia.utils import validate_required_fields

        data = {"name": "John", "email": ""}
        is_valid, missing = validate_required_fields(data, ["name", "email", "phone"])
        # Returns: (False, ["email", "phone"])
    """
    missing = []
    for field in required_fields:
        value = data.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)

    return (len(missing) == 0, missing)
