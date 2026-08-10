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
  classifier artifacts can't be resolved. Any such failure returns ``None`` so
  the caller falls back to the existing heuristic + LLM flow — this addon must
  never break the current path — while logging at ERROR and recording the reason
  in :func:`slm_build_errors`, since the failure only happens to someone who
  explicitly asked for the classifiers.
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

from gaia.llm.lemonade_client import DEFAULT_LEMONADE_URL
from gaia.logger import get_logger

log = get_logger(__name__)

# Process-wide cache of built classifiers keyed by (model, checkpoint, base_url).
# ``None`` is cached too, so a known-failed build is not retried on every email.
_CACHE: dict[Tuple[str, str, str], Optional[Any]] = {}
_CACHE_LOCK = threading.Lock()

# Why each failed build failed, keyed by task. Cached ``None`` alone makes the
# SLM path silently inert; this keeps the reason retrievable after the log line
# has scrolled away.
_BUILD_ERRORS: dict[str, str] = {}


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
    otherwise ``LEMONADE_BASE_URL``, otherwise core's ``DEFAULT_LEMONADE_URL``.
    Chat-style URLs ending in ``/api/v1`` or ``/v1`` are stripped to the server
    root — ``LemonadeEmbeddingClassifier`` appends those paths itself.
    """
    base_url = getattr(config, "base_url", None)
    if not base_url:
        base_url = os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL)
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
            reason = f"{type(exc).__name__}: {exc}"
            _BUILD_ERRORS[task] = reason
            # ERROR, not WARNING: only an operator who asked for the SLM gets here.
            log.error(
                "email SLM %s classifier unavailable (%s) — every message falls "
                "back to the heuristic + LLM flow. Check that Lemonade answers "
                "at %s and that model %r / checkpoint %r can be loaded, or turn "
                "the classifiers off (GAIA_EMAIL_USE_SLM=false). Setup: "
                "docs/guides/email.mdx, 'Small language model classifiers'.",
                task,
                reason,
                base_url,
                model,
                checkpoint,
            )

        _CACHE[key] = classifier
        return classifier


def slm_build_errors() -> dict[str, str]:
    """Return ``task -> reason`` for every classifier build that has failed.

    Lets a caller report *why* the SLM path is inert instead of only observing
    that classifications came back from the heuristic + LLM flow.
    """
    with _CACHE_LOCK:
        return dict(_BUILD_ERRORS)


def _reset_cache_for_tests() -> None:
    """Clear the process-wide classifier cache (test-only helper)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _BUILD_ERRORS.clear()


__all__ = [
    "format_slm_input",
    "get_classifier",
    "resolve_base_url",
    "slm_build_errors",
]
