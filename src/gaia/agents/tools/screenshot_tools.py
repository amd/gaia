# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ScreenshotToolsMixin — cross-platform screenshot capture for GAIA agents."""

import sys
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

        missing = []
        failures = []

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
            missing.append("mss is not installed")
        except Exception as e:
            logger.warning("mss screenshot failed: %s", e)
            failures.append(f"mss failed: {type(e).__name__}: {e}")

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
        except ImportError:
            missing.append("PIL.ImageGrab is not available")
        except Exception as e:
            logger.warning("PIL.ImageGrab screenshot failed: %s", e)
            failures.append(f"PIL.ImageGrab failed: {type(e).__name__}: {e}")

        if failures:
            hint = "A capture backend is installed but could not grab the screen."
            if sys.platform == "darwin":
                hint += (
                    " On macOS, allow Screen Recording for the app running GAIA in "
                    "System Settings > Privacy & Security > Screen Recording."
                )
        else:
            hint = (
                "Install mss (pip install mss), or Pillow for PIL.ImageGrab "
                "on Windows/macOS."
            )
        return {
            "status": "error",
            "error": (
                f"Screenshot capture failed: {'; '.join(failures + missing)}. {hint}"
            ),
        }
