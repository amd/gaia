# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ScreenshotToolsMixin — cross-platform screenshot capture for GAIA agents."""

from datetime import datetime
from pathlib import Path
from typing import Dict

from gaia.logger import get_logger

logger = get_logger(__name__)


class ScreenshotToolsMixin:
    """
    Mixin providing screenshot capture tools.

    Tools provided:
    - take_screenshot: Capture a screenshot and save to file

    Tries mss first (cross-platform), falls back to PIL.ImageGrab (Windows).
    """

    def register_screenshot_tools(self) -> None:
        """Register screenshot tools into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        @tool
        def take_screenshot(output_path: str = "") -> Dict:
            """Capture a screenshot of the current screen and save it to a file.

            Args:
                output_path: File path to save the screenshot (PNG).
                             If empty, saves to ~/.gaia/screenshots/screenshot_<timestamp>.png

            Returns:
                Dictionary with status, file_path, width, height
            """
            return self._take_screenshot(output_path)

    def _take_screenshot(self, output_path: str = "") -> Dict:
        """Take a screenshot using mss or PIL.ImageGrab."""
        # Determine output path
        if not output_path:
            screenshots_dir = Path.home() / ".gaia" / "screenshots"
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(screenshots_dir / f"screenshot_{ts}.png")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Try mss first (cross-platform, no display server required on Linux)
        try:
            import mss
            import mss.tools

            with mss.mss() as sct:
                monitor = sct.monitors[0]  # Full screen (all monitors combined)
                img = sct.grab(monitor)
                mss.tools.to_png(img.rgb, img.size, output=str(out))
            return {
                "status": "success",
                "file_path": str(out),
                "width": img.size[0],
                "height": img.size[1],
                "method": "mss",
            }
        except ImportError:
            pass
        except Exception as e:
            logger.debug("mss screenshot failed: %s", e)

        # Fall back to PIL.ImageGrab (Windows / macOS)
        try:
            from PIL import ImageGrab

            img = ImageGrab.grab()
            img.save(str(out), "PNG")
            return {
                "status": "success",
                "file_path": str(out),
                "width": img.width,
                "height": img.height,
                "method": "PIL.ImageGrab",
            }
        except Exception as e:
            logger.debug("PIL.ImageGrab screenshot failed: %s", e)

        return {
            "status": "error",
            "error": (
                "Screenshot capture failed. Install mss (pip install mss) or "
                "ensure PIL.ImageGrab is available (Pillow on Windows/macOS)."
            ),
        }
