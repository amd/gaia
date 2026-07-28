# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared construction + caching for the SpecificAI SLM classifiers.

With ``use_slm`` enabled the email agent runs two small-language-model
classifiers (phishing and triage-category classification), each backed by a
GGUF encoder served from the SAME local Lemonade server as the chat model (no
cloud path — AC3 holds). Both are ``LemonadeEmbeddingClassifier`` instances
from ``specific-ai-tools``.

Two properties this module guarantees for the whole SLM feature:

- **Fail safe.** Building a classifier can fail for many benign reasons —
  Lemonade is unreachable, the model or checkpoint cannot be loaded, or the
  classifier artifacts can't be resolved. Any such failure returns ``None``
  (logged once) so the caller falls back to the existing heuristic + LLM flow.
  This addon must never break the current path.
- **Build once.** The classifier is expensive to construct (it loads the GGUF
  encoder into Lemonade and pulls the classifier / tokenizer artifacts), so
  instances are cached process-wide keyed by ``(model, checkpoint, base_url)``
  and shared by the agent-loop path and the stateless REST service alike.

``specific_ai_tools`` imports are deferred into the builder so a construction
failure is a quiet fallback, never an import-time crash of this module.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Optional, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

# Default Lemonade server root — ``LemonadeEmbeddingClassifier`` appends
# ``/v1/...`` and ``/api/v1/...`` itself. Matches agent resolution after
# stripping a trailing ``/api/v1`` or ``/v1`` from chat-style base URLs.
_DEFAULT_LEMONADE_BASE_URL = "http://localhost:13305"

# Process-wide cache of built classifiers keyed by (model, checkpoint, base_url).
# ``None`` is cached too, so a known-failed build is not retried on every email.
_CACHE: dict[Tuple[str, str, str], Optional[Any]] = {}
_CACHE_LOCK = threading.Lock()


def format_slm_input(*, subject: str, sender: str, body: str) -> str:
    """Format one email the way the SLMs were trained.

    ::

        From: alice@example.com
        Subject: Hello

        Body text here.
    """
    return (
        f"From: {sender or ''}\n"
        f"Subject: {subject or ''}\n"
        f"\n"
        f"{body or ''}"
    )


def _lemonade_server_root(base_url: str) -> str:
    """Normalize to Lemonade server root (no trailing ``/api/v1`` or ``/v1``)."""
    url = (base_url or "").strip().rstrip("/")
    for suffix in ("/api/v1", "/v1"):
        if url.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


def resolve_base_url(config: Any) -> str:
    """Return the Lemonade server root the SLM encoder should talk to.

    Mirrors ``EmailTriageAgent.__init__``: an explicit ``config.base_url`` wins,
    otherwise ``LEMONADE_BASE_URL``, otherwise the local default. Chat-style
    URLs ending in ``/api/v1`` or ``/v1`` are stripped to the server root.
    """
    base_url = getattr(config, "base_url", None)
    if not base_url:
        base_url = os.getenv("LEMONADE_BASE_URL", _DEFAULT_LEMONADE_BASE_URL)
    return _lemonade_server_root(base_url)


def get_classifier(
    *,
    model: Optional[str],
    checkpoint: Optional[str],
    base_url: str,
    task: str,
) -> Optional[Any]:
    """Build (or return the cached) ``LemonadeEmbeddingClassifier``.

    Returns ``None`` — never raises — when the task is unconfigured or the
    build fails, so callers fall back to the current flow. ``task`` is only used
    for log messages. ``base_url`` must be the Lemonade server root.
    """
    if not model or not checkpoint:
        return None

    key = (model, checkpoint, base_url)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]

        classifier: Optional[Any] = None
        try:
            from specific_ai_tools.embedding_heads import (  # noqa: WPS433
                LemonadeEmbeddingClassifier,
            )

            classifier = LemonadeEmbeddingClassifier(
                lemonade_model_name=model,
                checkpoint=checkpoint,
                lemonade_base_url=base_url,
            )
            log.info(
                "email SLM %s classifier ready (model=%s, base_url=%s)",
                task,
                model,
                base_url,
            )
        except Exception as exc:  # fail safe — any build failure → LLM/heuristic
            log.warning(
                "email SLM %s classifier unavailable (model=%s): %s: %s — "
                "falling back to the heuristic + LLM flow",
                task,
                model,
                type(exc).__name__,
                exc,
            )

        _CACHE[key] = classifier
        return classifier


def _reset_cache_for_tests() -> None:
    """Clear the process-wide classifier cache (test-only helper)."""
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = ["format_slm_input", "get_classifier", "resolve_base_url"]
