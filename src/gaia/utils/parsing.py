# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Parsing utilities for GAIA agents.

Provides utilities for extracting structured data from LLM outputs,
document conversion, and field change detection.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract a JSON object from text that may contain surrounding content.

    LLMs often return JSON embedded in explanatory text. This function
    handles nested JSON objects correctly using balanced brace counting,
    unlike simple regex approaches.

    Args:
        text: Text potentially containing a JSON object.

    Returns:
        Parsed JSON as a dict, or None if no valid JSON found.

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
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Look for JSON object in response (handles nested braces properly)
    try:
        # Find first {
        start = text.find("{")
        if start == -1:
            logger.debug("No JSON object found in text")
            return None

        # Count braces to find matching close
        brace_count = 0
        for i, char in enumerate(text[start:], start):
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    json_str = text[start : i + 1]
                    return json.loads(json_str)

        logger.debug(f"Failed to find matching brace in text: {text[:200]}...")
        return None

    except json.JSONDecodeError as e:
        logger.debug(f"JSON decode error: {e}")
        return None


def pdf_page_to_image(
    path: Union[str, Path],
    page: int = 0,
    scale: float = 2.0,
) -> Optional[bytes]:
    """
    Convert a PDF page to PNG image bytes.

    Uses PyMuPDF (fitz) for high-quality rendering. The scale parameter
    controls resolution - higher values produce larger, sharper images
    suitable for OCR or VLM processing.

    Args:
        path: Path to the PDF file.
        page: Page number to convert (0-indexed, default: first page).
        scale: Resolution multiplier (default: 2.0 for 2x resolution).

    Returns:
        PNG image as bytes, or None if conversion failed.

    Raises:
        ImportError hint if PyMuPDF is not installed.

    Example:
        from gaia.utils import pdf_page_to_image

        # Convert first page at 2x resolution
        image_bytes = pdf_page_to_image("form.pdf")

        # Convert third page at 3x resolution for better OCR
        image_bytes = pdf_page_to_image("document.pdf", page=2, scale=3.0)
    """
    doc = None
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        if page >= len(doc):
            logger.warning(f"Page {page} not in PDF (has {len(doc)} pages)")
            return None

        pdf_page = doc[page]
        # Render at specified scale for better quality
        mat = fitz.Matrix(scale, scale)
        pix = pdf_page.get_pixmap(matrix=mat)
        return pix.tobytes("png")

    except ImportError:
        logger.error("PyMuPDF required for PDF processing: pip install pymupdf")
        return None
    except Exception as e:
        logger.error(f"Failed to convert PDF to image: {e}")
        return None
    finally:
        if doc:
            doc.close()


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
