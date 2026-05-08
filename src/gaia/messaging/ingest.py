"""Helpers to ingest uploaded media into VLM and RAG pipelines.

Provides small, testable wrappers around `VLMClient` and `RAGSDK` so
messaging adapters can hand off downloaded files for processing.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from gaia.llm.vlm_client import VLMClient
from gaia.rag.sdk import RAGSDK, RAGConfig

log = logging.getLogger(__name__)


def ingest_image_to_vlm(image_path: str, vlm_model: str = None) -> Dict[str, Any]:
    """Run VLM extraction on an image file and return results.

    Returns dict with keys: status, text, model, error (if any)
    """
    if not os.path.exists(image_path):
        return {"status": "error", "error": "file_not_found", "path": image_path}

    try:
        client = VLMClient(vlm_model=vlm_model) if vlm_model else VLMClient()
        image_bytes = open(image_path, "rb").read()
        text = client.extract_from_image(image_bytes)
        return {"status": "success", "text": text, "model": client.vlm_model}
    except Exception as e:  # pragma: no cover - integration depends on Lemonade
        log.exception("VLM ingestion failed")
        return {"status": "error", "error": str(e), "path": image_path}


def ingest_document_to_rag(
    file_path: str, config: RAGConfig | None = None
) -> Dict[str, Any]:
    """Index a document into the RAG index via RAGSDK.index_document.

    Returns whatever `RAGSDK.index_document` returns (a dict of stats).
    """
    cfg = config or RAGConfig()
    rag = RAGSDK(cfg)
    result = rag.index_document(file_path)
    return result
