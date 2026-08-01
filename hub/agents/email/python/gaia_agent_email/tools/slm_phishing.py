# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SLM phishing detection (SpecificAI small language model).

Fail-safe, build-once wrapper around a ``LemonadeEmbeddingClassifier`` —
a binary phishing SLM whose labels are ``"False"`` (not phishing) and
``"True"`` (phishing).

When wired (``use_slm`` and a usable classifier), the SLM runs **first**:
a usable result is the sole phishing decision and phishing heuristics are
not run. Any failure (classifier unavailable, prediction error, empty or
unexpected label) returns ``None`` so the caller falls back to
``detect_phishing``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from gaia_agent_email.tools.slm_common import (
    format_slm_input,
    get_classifier,
    resolve_base_url,
)

from gaia.logger import get_logger

log = get_logger(__name__)

# Binary labels.
_LABEL_PHISHING = "True"
_LABEL_NOT_PHISHING = "False"


def get_slm_phishing_classifier(config: Any) -> Optional[Any]:
    """Return the cached phishing classifier, or ``None`` when unavailable."""
    return get_classifier(
        model=getattr(config, "slm_phishing_model", None),
        checkpoint=getattr(config, "slm_phishing_checkpoint", None),
        base_url=resolve_base_url(config),
        task="phishing",
    )


def classify_phishing_slm(
    clf: Any, *, subject: str, sender: str, body: str
) -> Optional[bool]:
    """Classify phishing with the SLM.

    Returns ``True`` when the model predicts the phishing label (``"True"``),
    ``False`` for the not-phishing label (``"False"``), and ``None`` when the
    model could not produce a usable answer (empty labels, an unexpected
    label, or any exception) — in which case the caller runs
    ``detect_phishing``.
    """
    if clf is None:
        return None
    text = format_slm_input(subject=subject, sender=sender, body=body)
    try:
        prediction = clf.predict_one(text)
    except Exception as exc:  # fail safe — caller falls back to heuristics
        log.warning(
            "email SLM phishing prediction failed (%s: %s) — falling back to heuristic",
            type(exc).__name__,
            exc,
        )
        return None

    labels = getattr(prediction, "predicted_labels", None) or []
    if not labels:
        return None
    label = str(labels[0])
    if label == _LABEL_PHISHING:
        return True
    if label == _LABEL_NOT_PHISHING:
        return False
    log.warning(
        "email SLM phishing returned unexpected label %r — falling back to heuristic",
        label,
    )
    return None


def make_slm_phishing_classifier(
    config: Any,
) -> Optional[Callable[..., Optional[bool]]]:
    """Build a phishing-SLM callable bound to the cached classifier.

    Returns ``None`` when the phishing SLM is disabled or unavailable, so the
    caller can treat "no classifier" as "use the heuristic only".
    """
    clf = get_slm_phishing_classifier(config)
    if clf is None:
        return None

    def _classifier(*, subject: str, sender: str, body: str) -> Optional[bool]:
        return classify_phishing_slm(clf, subject=subject, sender=sender, body=body)

    return _classifier


__all__ = [
    "classify_phishing_slm",
    "get_slm_phishing_classifier",
    "make_slm_phishing_classifier",
]
