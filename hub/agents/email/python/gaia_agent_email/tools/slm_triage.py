# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SLM triage-category classification (SpecificAI small language model).

Mirrors ``llm_triage.py`` but backed by a ``LemonadeEmbeddingClassifier`` whose
output labels ARE the five triage categories
(``triage_heuristics.ALL_CATEGORIES``). This runs BEFORE the LLM classifier:
when a heuristic is not confident, the SLM gets first crack at the category, and
if it produces a usable label the (slower) LLM classify call is skipped entirely
for that message.

``classify_email_slm`` returns the same-shaped ``{category, confidence, source}``
mapping the LLM path uses for its category fields, or ``None`` when the SLM
could not produce a usable answer — the caller then falls back to the LLM. It
never raises into the caller: any prediction error, empty label set, or a label
outside the taxonomy is treated as "did not work" (fail safe).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from gaia_agent_email.tools.slm_common import (
    format_slm_input,
    get_classifier,
    resolve_base_url,
)
from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES

from gaia.logger import get_logger

log = get_logger(__name__)


def get_slm_triage_classifier(config: Any) -> Optional[Any]:
    """Return the cached triage classifier, or ``None`` when unavailable."""
    return get_classifier(
        model=getattr(config, "slm_triage_model", None),
        checkpoint=getattr(config, "slm_triage_checkpoint", None),
        base_url=resolve_base_url(config),
        task="triage",
    )


def classify_email_slm(
    clf: Any,
    *,
    subject: str,
    sender: str,
    body: str,
    message_id: str = "",
) -> Optional[dict[str, Any]]:
    """Classify one email's category with the SLM.

    Returns ``{"category", "confidence", "source": "slm"}`` on success, or
    ``None`` when the SLM did not produce a usable answer (empty labels, a label
    outside ``ALL_CATEGORIES``, or any exception). The caller falls back to the
    LLM classifier on ``None``.
    """
    if clf is None:
        return None
    text = format_slm_input(subject=subject, sender=sender, body=body)
    try:
        prediction = clf.predict_one(text)
    except Exception as exc:  # fail safe — fall back to the LLM
        log.warning(
            "email SLM triage prediction failed for message %s (%s: %s) — "
            "falling back to LLM",
            message_id,
            type(exc).__name__,
            exc,
        )
        return None

    labels = getattr(prediction, "predicted_labels", None) or []
    if not labels:
        return None
    category = str(labels[0])
    if category not in ALL_CATEGORIES:
        log.warning(
            "email SLM triage returned label %r outside the taxonomy %s for "
            "message %s — falling back to LLM",
            category,
            ALL_CATEGORIES,
            message_id,
        )
        return None

    confidences = getattr(prediction, "predicted_confidences", None) or {}
    confidence = confidences.get(category)
    log.debug(
        "slm_triage message=%s category=%s confidence=%s",
        message_id,
        category,
        confidence,
    )
    return {"category": category, "confidence": confidence, "source": "slm"}


def make_slm_classifier(
    config: Any,
) -> Optional[Callable[..., Optional[Mapping[str, Any]]]]:
    """Build a classifier callable bound to the cached SLM.

    The returned callable mirrors ``llm_triage.make_llm_classifier``'s keyword
    signature ``(*, subject, sender, body, message_id="") -> Optional[Mapping]``.
    Returns ``None`` when the triage SLM is disabled or unavailable.
    """
    clf = get_slm_triage_classifier(config)
    if clf is None:
        return None

    def _classifier(
        *, subject: str, sender: str, body: str, message_id: str = ""
    ) -> Optional[Mapping[str, Any]]:
        return classify_email_slm(
            clf,
            subject=subject,
            sender=sender,
            body=body,
            message_id=message_id,
        )

    return _classifier


__all__ = [
    "classify_email_slm",
    "get_slm_triage_classifier",
    "make_slm_classifier",
]
