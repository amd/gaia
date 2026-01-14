#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
HTML Viewer for summarizer JSON output
"""

import json
import webbrowser
from pathlib import Path
from typing import Any, Dict

# Template path - will be loaded from file
TEMPLATE_PATH = Path(__file__).parent / "templates" / "summary_report.html"


class HTMLViewer:
    """Generate and open HTML viewer for summary JSON"""

    @staticmethod
    def create_viewer(
        json_data: Dict[str, Any], json_path: Path, html_path: Path = None
    ) -> Path:
        """
        Create an HTML viewer for the summary JSON

        Args:
            json_data: The summary JSON data
            json_path: Path to the JSON file (for display in HTML)
            html_path: Path where to save the HTML file (optional, defaults to json_path with .html extension)

        Returns:
            Path to the created HTML file
        """
        # Ensure json_path is a Path object
        if isinstance(json_path, str):
            json_path = Path(json_path)

        # Determine HTML path if not provided
        if html_path is None:
            html_path = json_path.with_suffix(".html")
        elif isinstance(html_path, str):
            html_path = Path(html_path)

        # Load template from file
        if not TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"HTML template not found: {TEMPLATE_PATH}")

        html_template = TEMPLATE_PATH.read_text(encoding="utf-8")

        # Convert JSON to string with proper escaping
        json_str = json.dumps(json_data, indent=2)

        # Replace the placeholder with actual JSON data
        html_content = html_template.replace("{{JSON_DATA}}", json_str)

        # Add a script to set the JSON file path for display (use JSON path, not HTML path)
        json_absolute_path = json_path.resolve()
        # Escape backslashes for JavaScript string
        json_absolute_path_escaped = str(json_absolute_path).replace("\\", "\\\\")
        json_path_script = f"""
    <script>
        window.jsonFilePath = '{json_absolute_path_escaped}';
    </script>
</head>"""
        html_content = html_content.replace("</head>", json_path_script)

        # Write HTML file
        html_path.write_text(html_content, encoding="utf-8")

        return html_path

    @staticmethod
    def open_viewer(html_path: Path, auto_open: bool = True) -> bool:
        """
        Open the HTML viewer in the default browser

        Args:
            html_path: Path to the HTML file
            auto_open: Whether to automatically open the browser

        Returns:
            True if successfully opened, False otherwise
        """
        if not auto_open:
            return False

        # Ensure html_path is a Path object
        if isinstance(html_path, str):
            html_path = Path(html_path)

        try:
            # Use file:// protocol for local files
            file_url = html_path.absolute().as_uri()

            # Open in default browser
            webbrowser.open(file_url)
            return True

        except Exception as e:
            print(f"⚠️  Could not open browser automatically: {e}")
            print(f"    You can manually open: {html_path}")
            return False

    @staticmethod
    def create_and_open(
        json_data: Dict[str, Any], json_path: Path, auto_open: bool = True
    ) -> Path:
        """
        Create HTML viewer and optionally open it

        Args:
            json_data: The summary JSON data
            json_path: Path to the JSON file
            auto_open: Whether to automatically open the browser

        Returns:
            Path to the created HTML file
        """
        # Ensure json_path is a Path object
        if isinstance(json_path, str):
            json_path = Path(json_path)

        # Create HTML file with same name as JSON
        html_path = json_path.with_suffix(".html")
        html_path = HTMLViewer.create_viewer(json_data, json_path, html_path)

        # Open if requested
        if auto_open:
            HTMLViewer.open_viewer(html_path, auto_open=True)

        return html_path
