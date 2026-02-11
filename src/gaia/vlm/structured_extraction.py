#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Structured VLM Extraction - Enhanced VLM capabilities for structured data.

Extends VLMClient with methods for extracting structured data (tables, timelines,
key-value pairs) from images and documents. General-purpose, not domain-specific.

This is a stopgap solution until Vision SDK M3 is available.

Example:
    from gaia.vlm import StructuredVLMExtractor

    extractor = StructuredVLMExtractor()

    # Extract table
    table = extractor.extract_table(image_bytes)
    # Returns: [{"col1": "val1", "col2": "val2"}, ...]

    # Extract key-value pairs
    data = extractor.extract_key_values(image_bytes, keys=["name", "date", "total"])
    # Returns: {"name": "John", "date": "2024-01-01", "total": 100.50}

    # Extract with custom schema
    data = extractor.extract_structured(image_bytes, schema={...})
"""

import logging
from typing import Any, Dict, List, Optional

from gaia.llm import VLMClient
from gaia.utils import extract_json_from_text

logger = logging.getLogger(__name__)


class StructuredVLMExtractor:
    """
    Enhanced VLM extractor for structured data extraction.

    Simple API: Pass in a document, get back structured JSON.

    Example:
        from gaia.vlm import StructuredVLMExtractor

        extractor = StructuredVLMExtractor()

        # Extract everything with one call
        result = extractor.extract(
            "document.pdf",
            pages="all",
            extract_tables=True,
            extract_timelines=True,
            extract_fields=["name", "date", "total"],
        )

        # Access structured data
        print(result["pages"][0]["tables"])
        print(result["pages"][0]["timelines"])
        print(result["aggregated_data"])
    """

    def __init__(
        self,
        vlm_model: str = "Qwen3-VL-4B-Instruct-GGUF",
        base_url: Optional[str] = None,
    ):
        """
        Initialize structured extractor.

        Args:
            vlm_model: VLM model to use
            base_url: Lemonade server URL
        """
        self.vlm = VLMClient(vlm_model=vlm_model, base_url=base_url)

    def extract(
        self,
        document_path: str,
        pages: str = "all",
        extract_tables: bool = False,
        extract_timelines: bool = False,
        timeline_status_types: Optional[List[str]] = None,
        extract_fields: Optional[List[str]] = None,
        schema: Optional[Dict[str, Any]] = None,
        scale: float = 2.0,
        on_progress: Optional[callable] = None,
    ) -> Dict[str, Any]:
        """
        Extract structured data from document.

        Simple API: Pass in document path, get back structured JSON.

        Args:
            document_path: Path to PDF or image file
            pages: Page range ("all", "1-3", "2")
            extract_tables: Extract tables as structured data
            extract_timelines: Extract timeline/chart data
            timeline_status_types: Status types for timeline (e.g., ["Active", "Idle", "Offline"])
            extract_fields: Specific fields to extract (key-value pairs)
            schema: Custom extraction schema (alternative to extract_fields)
            scale: Image resolution scale for PDFs
            on_progress: Callback function(current_page, total_pages)

        Returns:
            {
              "metadata": {...},
              "pages": [
                {
                  "page": 1,
                  "tables": [...],
                  "timelines": {...},
                  "fields": {...},
                  "raw_text": "..."
                }
              ],
              "aggregated_data": {...}
            }

        Example:
            result = extractor.extract(
                "report.pdf",
                pages="all",
                extract_tables=True,
                extract_timelines=True,
                timeline_status_types=["Active", "Idle", "Offline"]
            )

            # Access data
            totals = result["aggregated_data"]["timeline_totals"]
            table_rows = result["pages"][1]["tables"][0]
        """
        from pathlib import Path

        from gaia.utils import pdf_page_to_image

        doc_path = Path(document_path)
        if not doc_path.exists():
            raise FileNotFoundError(f"Document not found: {document_path}")

        # Determine if PDF or image
        is_pdf = doc_path.suffix.lower() == ".pdf"

        # Get total pages
        if is_pdf:
            import fitz

            doc = fitz.open(str(doc_path))
            total_pages = len(doc)
            doc.close()
        else:
            total_pages = 1

        # Parse page range
        pages_to_process = self._parse_page_range(pages, total_pages)

        # Process pages
        pages_data = []
        aggregated_timeline = {} if extract_timelines else None

        for i, page_num in enumerate(pages_to_process, 1):
            if on_progress:
                on_progress(i, len(pages_to_process))

            # Load page as image (internal)
            if is_pdf:
                image_bytes = pdf_page_to_image(
                    doc_path, page=page_num - 1, scale=scale
                )
            else:
                image_bytes = doc_path.read_bytes()

            if not image_bytes:
                continue

            # Extract data from page
            page_data = {"page": page_num}

            # Tables
            if extract_tables:
                page_data["tables"] = self.extract_table(image_bytes, page_num=page_num)

            # Timelines
            if extract_timelines:
                timeline_data = self.extract_timeline(
                    image_bytes,
                    status_types=timeline_status_types or ["Category1", "Category2"],
                    page_num=page_num,
                )
                page_data["timeline"] = timeline_data

                # Aggregate
                if aggregated_timeline is not None:
                    for status, hours in timeline_data.items():
                        aggregated_timeline[status] = (
                            aggregated_timeline.get(status, 0.0) + hours
                        )

            # Fields or schema
            if extract_fields:
                page_data["fields"] = self.extract_key_values(
                    image_bytes, extract_fields, page_num=page_num
                )
            elif schema:
                page_data["fields"] = self.extract_structured(
                    image_bytes, schema, page_num=page_num
                )

            # Raw text (always included)
            page_data["raw_text"] = self.vlm.extract_from_image(
                image_bytes, page_num=page_num
            )

            pages_data.append(page_data)

        # Build output
        result = {
            "metadata": {
                "source": doc_path.name,
                "total_pages": total_pages,
                "pages_processed": len(pages_data),
            },
            "pages": pages_data,
        }

        if aggregated_timeline is not None:
            result["aggregated_data"] = {"timeline_totals": aggregated_timeline}

        return result

    def _parse_page_range(self, range_str: str, total_pages: int) -> List[int]:
        """Internal: Parse page range string."""
        if range_str.lower() == "all":
            return list(range(1, total_pages + 1))
        if "-" in range_str:
            start, end = range_str.split("-")
            return list(range(int(start.strip()), int(end.strip()) + 1))
        return [int(range_str)]

    def extract_table(
        self,
        image_bytes: bytes,
        table_description: Optional[str] = None,
        page_num: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Extract table from image as structured data.

        Args:
            image_bytes: Image containing table
            table_description: Optional description of what table contains
            page_num: Page number for logging

        Returns:
            List of dicts, one per row: [{"col1": "val1", "col2": "val2"}, ...]
            Empty list if no table found or extraction fails.

        Example:
            table = extractor.extract_table(image_bytes)
            # [
            #   {"name": "John", "age": "30", "city": "Boston"},
            #   {"name": "Jane", "age": "25", "city": "NYC"}
            # ]
        """
        desc = table_description or "a table"

        prompt = f"""This image contains {desc}.

Extract ALL rows from the table and return as JSON array of objects.
Each object should have column names as keys and cell values as values.

Example format:
[
  {{"column1": "value1", "column2": "value2"}},
  {{"column1": "value3", "column2": "value4"}}
]

IMPORTANT:
- Extract EVERY row
- Use exact column names from table headers
- Keep exact text from cells
- Use null for empty cells
- Return ONLY the JSON array, no other text
- If no table found, return []"""

        result = self.vlm.extract_from_image(
            image_bytes=image_bytes,
            page_num=page_num,
            prompt=prompt,
        )

        # Parse JSON
        data = extract_json_from_text(result)

        if isinstance(data, list):
            logger.info(f"Extracted table with {len(data)} rows")
            return data
        else:
            logger.warning(
                f"Table extraction failed or returned non-list: {type(data)}"
            )
            return []

    def extract_key_values(
        self,
        image_bytes: bytes,
        keys: List[str],
        descriptions: Optional[Dict[str, str]] = None,
        page_num: int = 1,
    ) -> Dict[str, Any]:
        """
        Extract specific key-value pairs from image.

        Args:
            image_bytes: Image to extract from
            keys: List of keys to extract (e.g., ["name", "date", "total"])
            descriptions: Optional descriptions for each key
            page_num: Page number for logging

        Returns:
            Dict with extracted values: {"key1": "value1", "key2": "value2", ...}
            Keys not found will have null values.

        Example:
            fields = extractor.extract_key_values(
                image_bytes,
                keys=["invoice_number", "total", "date"]
            )
            # {"invoice_number": "INV-12345", "total": 150.00, "date": "2024-01-15"}
        """
        # Build prompt with key descriptions
        key_specs = []
        for key in keys:
            desc = descriptions.get(key, key) if descriptions else key
            key_specs.append(f'  "{key}": {desc}')

        prompt = f"""Extract these specific fields from the image:

{{
{chr(10).join(key_specs)}
}}

IMPORTANT:
- Extract ONLY these fields
- Use null for fields not found
- Return ONLY the JSON object, no other text
- Keep exact text from image"""

        result = self.vlm.extract_from_image(
            image_bytes=image_bytes,
            page_num=page_num,
            prompt=prompt,
        )

        # Parse JSON
        data = extract_json_from_text(result)

        if isinstance(data, dict):
            logger.info(f"Extracted {len(data)} fields")
            return data
        else:
            logger.warning(f"Key-value extraction failed")
            return {key: None for key in keys}

    def extract_structured(
        self,
        image_bytes: bytes,
        schema: Dict[str, Any],
        page_num: int = 1,
    ) -> Dict[str, Any]:
        """
        Extract data according to custom schema.

        Args:
            image_bytes: Image to extract from
            schema: Schema defining fields to extract
            page_num: Page number for logging

        Schema format:
            {
              "fields": {
                "field_name": {
                  "type": "string|number|date|boolean",
                  "description": "what this field contains",
                  "required": True|False
                }
              }
            }

        Returns:
            Dict with extracted data matching schema

        Example:
            schema = {
              "fields": {
                "patient_name": {"type": "string", "description": "patient full name"},
                "total_cost": {"type": "number", "description": "total billing amount"}
              }
            }

            data = extractor.extract_structured(image_bytes, schema)
            # {"patient_name": "Jane Smith", "total_cost": 1250.00}
        """
        fields = schema.get("fields", {})

        # Build prompt from schema
        field_specs = []
        for field_name, field_spec in fields.items():
            field_type = field_spec.get("type", "string")
            field_desc = field_spec.get("description", field_name)
            req = " (REQUIRED)" if field_spec.get("required") else ""
            field_specs.append(f'  "{field_name}": {field_desc}{req} [{field_type}]')

        prompt = f"""Extract these fields from the image:

{{
{chr(10).join(field_specs)}
}}

IMPORTANT:
- Extract ALL fields you can find
- Use null for fields not visible
- Return numbers as numbers, not strings
- Dates in YYYY-MM-DD format
- Return ONLY the JSON object, no other text"""

        result = self.vlm.extract_from_image(
            image_bytes=image_bytes,
            page_num=page_num,
            prompt=prompt,
        )

        # Parse JSON
        data = extract_json_from_text(result)

        if isinstance(data, dict):
            logger.info(f"Extracted {len(data)} fields from schema")
            return data
        else:
            logger.warning("Structured extraction failed")
            return {}

    def _parse_time_to_hours(self, time_str: str) -> float:
        """
        Convert HH:MM:SS string to decimal hours.

        Args:
            time_str: Time in HH:MM:SS format

        Returns:
            Decimal hours
        """
        try:
            if ":" in time_str:
                parts = time_str.split(":")
                hours = int(parts[0])
                minutes = int(parts[1]) if len(parts) > 1 else 0
                seconds = int(parts[2]) if len(parts) > 2 else 0
                return hours + (minutes / 60.0) + (seconds / 3600.0)
            else:
                # Already a number
                return float(time_str)
        except (ValueError, IndexError):
            logger.warning(f"Failed to parse time: {time_str}")
            return 0.0

    def extract_chart_data(
        self,
        image_bytes: bytes,
        categories: List[str],
        value_format: str = "auto",
        page_num: int = 1,
    ) -> Dict[str, Any]:
        """
        Extract data from charts, timelines, or graphs (general-purpose).

        Args:
            image_bytes: Image containing chart/timeline
            categories: Category names to extract (e.g., ["Q1", "Q2", "Q3"])
            value_format: Format of values to extract:
                - "auto": Let VLM determine format (returns as-is)
                - "time_hms": Time in HH:MM:SS format (returns strings)
                - "time_hms_decimal": HH:MM:SS converted to decimal hours (returns floats)
                - "number": Numeric values (returns numbers)
                - "percentage": Percentages (returns floats 0-100)
            page_num: Page number for logging

        Returns:
            Dict mapping category to extracted value (type depends on value_format)

        Examples:
            # Timeline with time values → decimal hours
            hours = extractor.extract_chart_data(
                image,
                categories=["Active", "Idle", "Offline", "Maintenance"],
                value_format="time_hms_decimal"
            )
            # {"Active": 14.777, "Idle": 0.022, "Offline": 7.654, "Maintenance": 1.547}

            # Bar chart with sales numbers
            sales = extractor.extract_chart_data(
                image,
                categories=["Q1", "Q2", "Q3", "Q4"],
                value_format="number"
            )
            # {"Q1": 150000, "Q2": 175000, "Q3": 200000, "Q4": 225000}

            # Pie chart with percentages
            shares = extractor.extract_chart_data(
                image,
                categories=["Product A", "Product B", "Product C"],
                value_format="percentage"
            )
            # {"Product A": 45.5, "Product B": 32.0, "Product C": 22.5}
        """
        category_list = ", ".join(categories)

        # Build prompts based on format
        if value_format == "time_hms" or value_format == "time_hms_decimal":
            # Extract times as HH:MM:SS strings
            json_template_lines = [f'  "{cat}": "HH:MM:SS"' for cat in categories]
            json_template = "{\n" + ",\n".join(json_template_lines) + "\n}"

            prompt = f"""Extract time values from this chart.

You MUST extract ALL {len(categories)} categories: {category_list}

For each category row, find the time at the RIGHT END (format: HH:MM:SS).

Return THIS EXACT JSON structure:
{json_template}

Replace "HH:MM:SS" with actual times (like "14:46:38").
Use "00:00:00" if not present.
ALL {len(categories)} fields REQUIRED. Return complete JSON only."""

        elif value_format == "number":
            # Extract numeric values
            json_template_lines = [f'  "{cat}": <number>' for cat in categories]
            json_template = "{\n" + ",\n".join(json_template_lines) + "\n}"

            prompt = f"""Extract numeric values from this chart.

You MUST extract ALL {len(categories)} categories: {category_list}

For each category, find the numeric value displayed.

Return THIS EXACT JSON structure:
{json_template}

Replace <number> with actual numbers (no quotes).
Use 0 if not present.
ALL {len(categories)} fields REQUIRED."""

        elif value_format == "percentage":
            # Extract percentages
            json_template_lines = [f'  "{cat}": <number>' for cat in categories]
            json_template = "{\n" + ",\n".join(json_template_lines) + "\n}"

            prompt = f"""Extract percentage values from this chart.

You MUST extract ALL {len(categories)} categories: {category_list}

For each category, find the percentage value.

Return as JSON with numbers (e.g., 45.5 for 45.5%):
{json_template}

Return numbers only, not strings with % symbol.
ALL {len(categories)} fields REQUIRED."""

        else:  # "auto"
            # Let VLM determine format
            json_template_lines = [f'  "{cat}": <value>' for cat in categories]
            json_template = "{\n" + ",\n".join(json_template_lines) + "\n}"

            prompt = f"""Extract values from this chart for: {category_list}

Return JSON:
{json_template}

ALL {len(categories)} fields REQUIRED."""

        result = self.vlm.extract_from_image(
            image_bytes=image_bytes,
            page_num=page_num,
            prompt=prompt,
        )

        # Debug: Log raw VLM response
        logger.debug(f"Timeline VLM raw response: {result[:200]}...")

        # Parse JSON (VLM returns time strings)
        data = extract_json_from_text(result)
        logger.debug(f"Timeline parsed JSON: {data}")

        if isinstance(data, dict):
            # Convert based on format
            if value_format == "time_hms_decimal":
                # Convert HH:MM:SS strings to decimal hours
                converted = {}
                for cat, value in data.items():
                    if isinstance(value, str):
                        converted[cat] = self._parse_time_to_hours(value)
                    elif isinstance(value, (int, float)):
                        converted[cat] = float(value)
                    else:
                        converted[cat] = 0.0
                logger.info(f"Extracted chart data with {len(converted)} categories")
                return converted
            else:
                # Return as-is (strings, numbers, whatever VLM returned)
                logger.info(f"Extracted chart data with {len(data)} categories")
                return data
        else:
            logger.warning("Chart data extraction failed")
            # Return appropriate defaults based on format
            if value_format == "time_hms":
                return {cat: "00:00:00" for cat in categories}
            else:
                return {cat: 0.0 for cat in categories}

    def extract_timeline(
        self,
        image_bytes: bytes,
        status_types: List[str],
        page_num: int = 1,
    ) -> Dict[str, float]:
        """
        Convenience method for timeline extraction (backward compatibility).

        Extracts timeline data and converts to decimal hours.
        This is a wrapper around extract_chart_data() with value_format="time_hms_decimal".

        Args:
            image_bytes: Image containing timeline
            status_types: Status categories (e.g., ["Active", "Idle", "Offline"])
            page_num: Page number

        Returns:
            Dict with decimal hours: {"Active": 14.777, ...}
        """
        return self.extract_chart_data(
            image_bytes,
            categories=status_types,
            value_format="time_hms_decimal",
            page_num=page_num,
        )
