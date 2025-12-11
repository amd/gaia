#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Vision-Language Model (VLM) client for extracting text from images.

Handles model loading/unloading and image-to-text extraction via Lemonade server.
"""

import base64
import logging
import os
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Default Lemonade server URL (can be overridden via LEMONADE_BASE_URL env var)
DEFAULT_LEMONADE_URL = "http://localhost:8000/api/v1"

logger = logging.getLogger(__name__)


class VLMClient:
    """
    VLM client for extracting text from images using Lemonade server.

    Handles:
    - Model loading (Qwen2.5-VL-7B-Instruct-GGUF)
    - Image-to-markdown conversion
    - State tracking for VLM processing
    """

    def __init__(
        self,
        vlm_model: str = "Qwen2.5-VL-7B-Instruct-GGUF",
        base_url: Optional[str] = None,
        auto_load: bool = True,
    ):
        """
        Initialize VLM client.

        Args:
            vlm_model: Vision model to use for image extraction
            base_url: Lemonade server API URL (defaults to LEMONADE_BASE_URL env var)
            auto_load: Automatically load VLM model on first use
        """
        # Use provided base_url, fall back to env var, then default
        if base_url is None:
            base_url = os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL)
        from urllib.parse import urlparse

        from gaia.llm.lemonade_client import LemonadeClient

        self.vlm_model = vlm_model
        self.base_url = base_url

        # Parse base_url to extract host and port for LemonadeClient
        parsed = urlparse(base_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 8000

        # Get base server URL (without /api/v1) for user-facing messages
        self.server_url = f"http://{host}:{port}"

        self.client = LemonadeClient(model=vlm_model, host=host, port=port)
        self.auto_load = auto_load
        self.vlm_loaded = False

        logger.info(f"VLM Client initialized: {self.vlm_model} at {self.server_url}")

    def check_availability(self) -> bool:
        """
        Check if VLM model is available on Lemonade server.

        Returns:
            True if model is available, False otherwise
        """
        try:
            models_response = self.client.list_models()
            available_models = [
                m.get("id", "") for m in models_response.get("data", [])
            ]

            if self.vlm_model in available_models:
                logger.info(f"✅ VLM model available: {self.vlm_model}")
                return True
            else:
                logger.warning(f"❌ VLM model not found: {self.vlm_model}")
                logger.warning("")
                logger.warning("📥 To download this model:")
                logger.warning(f"   1. Open Lemonade Model Manager ({self.server_url})")
                logger.warning(f"   2. Search for: {self.vlm_model}")
                logger.warning("   3. Click 'Download' to install the model")
                logger.warning("")
                logger.warning(
                    f"   Available models: {', '.join(available_models[:3])}..."
                )
                return False

        except Exception as e:
            logger.error(f"Failed to check VLM availability: {e}")
            logger.error(
                f"   Make sure Lemonade server is running at {self.server_url}"
            )
            return False

    def _ensure_vlm_loaded(self) -> bool:
        """
        Ensure VLM model is loaded, load it if necessary.

        The model will be automatically downloaded if not available (handled by
        lemonade_client.chat_completions with auto_download=True).

        Returns:
            True if VLM is loaded, False if loading failed
        """
        if self.vlm_loaded:
            return True

        if not self.auto_load:
            logger.warning("VLM not loaded and auto_load=False")
            return False

        try:
            logger.info(f"📥 Loading VLM model: {self.vlm_model}")
            # Load model (auto-download handled by lemonade_client, may take hours)
            self.client.load_model(self.vlm_model, timeout=60, auto_download=True)
            self.vlm_loaded = True
            logger.info(f"✅ VLM model loaded: {self.vlm_model}")
            return True

        except Exception as e:
            logger.error(f"Failed to load VLM model: {e}")
            logger.error(
                f"   Make sure Lemonade server is running at {self.server_url}"
            )
            return False

    def extract_from_image(
        self,
        image_bytes: bytes,
        image_num: int = 1,
        page_num: int = 1,
        prompt: Optional[str] = None,
    ) -> str:
        """
        Extract text from an image using VLM.

        Args:
            image_bytes: Image as PNG/JPEG bytes
            image_num: Image number on page (for logging)
            page_num: Page number (for logging)
            prompt: Custom extraction prompt (optional)

        Returns:
            Extracted text in markdown format
        """
        # Ensure VLM is loaded
        if not self._ensure_vlm_loaded():
            error_msg = "VLM model not available"
            logger.error(error_msg)
            return f"[VLM extraction failed: {error_msg}]"

        # Encode image as base64
        # Note: Image size optimization happens in pdf_utils.py during extraction
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        _image_size_kb = len(image_bytes) / 1024

        # Default prompt for text extraction
        if not prompt:
            prompt = """You are an OCR system. Extract ALL visible text from this image exactly as it appears.

Instructions:
1. Extract EVERY word you see - don't skip or paraphrase
2. Preserve exact formatting (headings, bold, bullets, tables)
3. If it's a table, format as markdown table
4. If it's a chart, describe what you see: [CHART: ...]
5. Do NOT add placeholders like "[Insert ...]"  - only extract actual text
6. Do NOT generate or invent content - only extract what you see

Output format: Clean markdown with the ACTUAL text from the image."""

        # Format message with image (OpenAI vision format)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                ],
            }
        ]

        try:
            import time

            start_time = time.time()

            logger.info(
                f"   🔍 VLM extracting from image {image_num} on page {page_num}..."
            )
            logger.debug(
                f"   Image size: {len(image_b64)} chars base64 ({len(image_bytes)} bytes raw)"
            )

            # Call VLM using chat completions endpoint
            response = self.client.chat_completions(
                model=self.vlm_model,
                messages=messages,
                temperature=0.1,  # Low temp for accurate extraction
                max_completion_tokens=2048,  # Allow detailed extraction
                timeout=60,  # VLM may be slower than text models
            )

            elapsed = time.time() - start_time

            # Extract text from response
            if (
                isinstance(response, dict)
                and "choices" in response
                and len(response["choices"]) > 0
            ):
                extracted_text = response["choices"][0]["message"]["content"]
                logger.info(
                    f"   ✅ Extracted {len(extracted_text)} chars from image {image_num} "
                    f"in {elapsed:.2f}s ({len(image_bytes)/1024:.0f}KB image)"
                )
                return extracted_text
            else:
                logger.error(f"Unexpected VLM response format: {response}")
                return "[VLM extraction failed: unexpected response format]"

        except Exception as e:
            logger.error(
                f"VLM extraction failed for page {page_num}, image {image_num}: {e}"
            )
            import traceback

            logger.debug(traceback.format_exc())
            return f"[VLM extraction failed: {str(e)}]"

    def extract_from_page_images(self, images: list, page_num: int) -> list:
        """
        Extract text from multiple images on a page.

        Args:
            images: List of image dicts with 'image_bytes', 'width', 'height', etc.
            page_num: Page number

        Returns:
            List of dicts:
            [
                {
                    "image_num": 1,
                    "text": "extracted markdown",
                    "dimensions": "800x600",
                    "size_kb": 45.2
                },
                ...
            ]
        """
        results = []

        for img_idx, img_data in enumerate(images, 1):
            extracted_text = self.extract_from_image(
                image_bytes=img_data["image_bytes"],
                image_num=img_idx,
                page_num=page_num,
            )

            results.append(
                {
                    "image_num": img_idx,
                    "text": extracted_text,
                    "dimensions": f"{img_data['width']}x{img_data['height']}",
                    "size_kb": img_data["size_kb"],
                }
            )

        return results

    def cleanup(self):
        """
        Cleanup VLM resources.

        Call this after batch processing to mark VLM as unloaded.
        Note: Model remains loaded on server; this just updates local state.
        """
        if self.vlm_loaded:
            logger.info("🧹 VLM processing complete")
            self.vlm_loaded = False

    def __enter__(self):
        """Context manager entry - ensure VLM loaded."""
        self._ensure_vlm_loaded()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup VLM state."""
        self.cleanup()
