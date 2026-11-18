# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Helpers to ingest uploaded media into VLM and RAG pipelines.

Provides small, testable wrappers around `VLMClient` and `RAGSDK` so
messaging adapters can hand off downloaded files for processing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from gaia.chat.sdk import AgentSDK
from gaia.llm.vlm_client import VLMClient

log = logging.getLogger(__name__)


def ingest_image_to_vlm(image_path: str, vlm_model: str = None) -> Dict[str, Any]:
    """Run VLM extraction on an image file and return results.

    Returns dict with keys: status, text, model, error (if any)
    """
    if not os.path.exists(image_path):
        return {"status": "error", "error": "file_not_found", "path": image_path}

    try:
        client = VLMClient(vlm_model=vlm_model) if vlm_model else VLMClient()
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        text = client.extract_from_image(image_bytes)
        return {"status": "success", "text": text, "model": client.vlm_model}
    except Exception as e:  # pragma: no cover - integration depends on Lemonade
        log.exception("VLM ingestion failed")
        return {"status": "error", "error": str(e), "path": image_path}


def ingest_document_to_rag(file_path: str, session: AgentSDK) -> bool:
    """Index a document into ``session`` so that chat's replies can draw on it.

    Returns True when the document was indexed.
    """
    if session.rag_enabled:
        return session.add_document(file_path)
    return session.enable_rag(documents=[file_path])
