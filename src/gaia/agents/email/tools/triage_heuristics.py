# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Pre-LLM triage heuristics for the Email Triage Agent.

Heuristic rules adapted from PR #916 (Inbox Zero prototype). The prototype
operated on MBOX ``X-Gmail-Labels`` headers (human strings like
``"Promotions"``); this module operates on Gmail API v1 ``labelIds``
(system IDs like ``CATEGORY_PROMOTIONS``). The categories are also
re-mapped from PR #916's five-bucket scheme
(``URGENT/NEEDS_RESPONSE/FYI/PROMOTIONAL/PERSONAL``) onto the four-bucket
taxonomy used by the synthetic eval dataset (#848):

  - ``urgent``         — needs the user's attention right now
  - ``actionable``     — requires a reply / decision in the near term
  - ``informational``  — useful context, no action
  - ``low priority``   — newsletters, promotions, low-signal updates

Plus two boolean fields surfaced separately so the eval harness can score
them independently of the four-way classification:

  - ``is_spam``        — Gmail's SPAM label OR keyword heuristics suggest spam
  - ``is_phishing``    — heuristics suggest credential-harvesting (separate
                          from generic spam — the agent should refuse to act
                          on links from a phishing message even if the user
                          says "do as it asks")

The heuristic exists to save an LLM round-trip on obviously-low-priority
mail (newsletters, promotions, automated security alerts that are clearly
informational). Everything that doesn't match a high-confidence keyword
or system-label rule falls through to the LLM. This module never decides
on its own that an email is ``actionable`` or ``urgent`` — those require
the LLM's reading of the body and are intentionally not heuristic-gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

# ---------------------------------------------------------------------------
# Gmail API system label IDs we recognize.
# ---------------------------------------------------------------------------
# The Gmail REST API returns these as opaque strings on every message. The
# user-defined labels look like ``Label_12345`` and cannot be matched by
# keyword — the heuristic fast-path therefore covers ONLY the built-in
# system labels. (Source: Gmail API v1 docs, ``users.messages.list`` →
# ``labelIds`` field.)

LABEL_INBOX = "INBOX"
LABEL_SPAM = "SPAM"
LABEL_TRASH = "TRASH"
LABEL_IMPORTANT = "IMPORTANT"
LABEL_STARRED = "STARRED"
LABEL_UNREAD = "UNREAD"
LABEL_CATEGORY_PROMOTIONS = "CATEGORY_PROMOTIONS"
LABEL_CATEGORY_UPDATES = "CATEGORY_UPDATES"
LABEL_CATEGORY_SOCIAL = "CATEGORY_SOCIAL"
LABEL_CATEGORY_FORUMS = "CATEGORY_FORUMS"
LABEL_CATEGORY_PERSONAL = "CATEGORY_PERSONAL"


# Categories emitted by the heuristic. MUST agree with #848's eval-time
# taxonomy — any drift breaks AC4.
CATEGORY_URGENT = "urgent"
CATEGORY_ACTIONABLE = "actionable"
CATEGORY_INFORMATIONAL = "informational"
CATEGORY_LOW_PRIORITY = "low priority"

ALL_CATEGORIES: tuple[str, ...] = (
    CATEGORY_URGENT,
    CATEGORY_ACTIONABLE,
    CATEGORY_INFORMATIONAL,
    CATEGORY_LOW_PRIORITY,
)


@dataclass(frozen=True)
class HeuristicResult:
    """Outcome of a single heuristic pass over one message.

    ``confident=True`` means the heuristic is willing to commit to the
    category without LLM consultation. ``confident=False`` means the
    heuristic returned a best-guess fallback (e.g., ``informational``)
    and the caller should escalate to the LLM. The ``reason`` field is
    the source-of-truth for verbose-mode logging — it tells the operator
    exactly which rule fired.
    """

    category: str
    is_spam: bool = False
    is_phishing: bool = False
    confident: bool = False
    reason: str = ""
    matched_label_ids: tuple[str, ...] = field(default_factory=tuple)


# Phishing heuristics — *very* conservative. The cost of a false negative
# (real phishing missed) is higher than a false positive (legitimate
# password-reset flagged), but the heuristic must NEVER auto-act on phishing.
# The flag is informational only — the LLM gets the same message and
# decides what to do, with the heuristic flag as a *signal* in its prompt.
_PHISHING_KEYWORD_PAIRS = (
    ("verify your account", "click"),
    ("verify your account", "link"),
    ("suspended", "click"),
    ("suspended", "verify"),
    ("password expires", "click"),
    ("urgent action required", "click"),
    ("confirm your identity", "click"),
)
_PHISHING_SINGLE_PHRASES = (
    "we detected unusual sign-in activity",
    "your account has been compromised",
)


# Spam-keyword fallback for the case where Gmail did NOT flag SPAM but the
# subject screams marketing.
_PROMO_SUBJECT_KEYWORDS = (
    "50% off",
    "sale ends",
    "limited time",
    "special offer",
    "deal of the day",
    "discount code",
    "coupon",
    "newsletter",
    "this week's deals",
)


# Senders that never need a human reply and are almost always informational
# at best, low-priority at worst.
_AUTOMATED_SENDER_KEYWORDS = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "auto-confirm",
    "notifications@",
    "alerts@",
    "store-news",
)


def classify_category_heuristic(
    subject: str,
    sender: str,
    label_ids: Iterable[str],
) -> HeuristicResult:
    """Classify a single message using fast keyword + label-ID rules.

    Args:
        subject: The message subject line. Already-decoded Unicode (the
            Gmail API's ``payload.headers[].value`` is decoded by Google
            before we receive it).
        sender: The ``From`` header value, raw form
            (``"Alice <alice@example.com>"``).
        label_ids: System label IDs from ``labelIds`` on the message
            payload. User-defined labels (opaque ``Label_*`` ids) are
            ignored — they cannot be classified by name.

    Returns:
        A :class:`HeuristicResult`. When ``confident=False`` the caller
        SHOULD escalate to the LLM; when ``True`` the LLM call can be
        skipped (saves latency on bulk triage).
    """
    label_id_set = set(label_ids)
    subject_lower = (subject or "").lower()
    sender_lower = (sender or "").lower()

    # Phishing is a *signal* layered on top of every category, never a
    # category itself — compute once up front so spam/phishing can co-fire.
    is_phishing = _looks_phishing(subject_lower)

    # 1. Spam — confident, label-driven. Gmail's spam classifier is more
    #    accurate than anything we'd build ad-hoc.
    if LABEL_SPAM in label_id_set:
        return HeuristicResult(
            category=CATEGORY_LOW_PRIORITY,
            is_spam=True,
            is_phishing=is_phishing,
            confident=True,
            reason="Gmail SPAM label set",
            matched_label_ids=(LABEL_SPAM,),
        )

    # 2. Promotions — confident, label-driven.
    if LABEL_CATEGORY_PROMOTIONS in label_id_set:
        return HeuristicResult(
            category=CATEGORY_LOW_PRIORITY,
            confident=True,
            reason="Gmail CATEGORY_PROMOTIONS label set",
            matched_label_ids=(LABEL_CATEGORY_PROMOTIONS,),
        )

    # 3. Social — confident, label-driven. Notifications, "X liked your
    #    post", etc.
    if LABEL_CATEGORY_SOCIAL in label_id_set:
        return HeuristicResult(
            category=CATEGORY_LOW_PRIORITY,
            confident=True,
            reason="Gmail CATEGORY_SOCIAL label set",
            matched_label_ids=(LABEL_CATEGORY_SOCIAL,),
        )

    # 4. Updates — confident, label-driven. Receipts, shipping
    #    confirmations, calendar updates: useful context, no reply needed.
    if LABEL_CATEGORY_UPDATES in label_id_set:
        return HeuristicResult(
            category=CATEGORY_INFORMATIONAL,
            confident=True,
            reason="Gmail CATEGORY_UPDATES label set",
            matched_label_ids=(LABEL_CATEGORY_UPDATES,),
        )

    # 5. Subject-keyword promo fallback — fires when Gmail didn't tag
    #    promotions but the marketing language is unmistakable.
    for kw in _PROMO_SUBJECT_KEYWORDS:
        if kw in subject_lower:
            return HeuristicResult(
                category=CATEGORY_LOW_PRIORITY,
                is_phishing=is_phishing,
                confident=True,
                reason=f"subject contains promotional keyword '{kw}'",
            )

    # 7. Automated-sender fallback — newsletters, alert bots, etc.
    for kw in _AUTOMATED_SENDER_KEYWORDS:
        if kw in sender_lower:
            return HeuristicResult(
                category=CATEGORY_INFORMATIONAL,
                is_phishing=is_phishing,
                confident=True,
                reason=f"sender contains automated-sender keyword '{kw}'",
            )

    # 8. Built-in IMPORTANT / STARRED labels — Gmail has decided this is
    #    significant; we down-rank but do NOT classify (urgent vs.
    #    actionable depends on body, which the LLM reads). Return
    #    confident=False so the caller escalates.
    matched: List[str] = []
    if LABEL_IMPORTANT in label_id_set:
        matched.append(LABEL_IMPORTANT)
    if LABEL_STARRED in label_id_set:
        matched.append(LABEL_STARRED)
    if matched:
        return HeuristicResult(
            category=CATEGORY_ACTIONABLE,
            is_phishing=is_phishing,
            confident=False,
            reason=f"Gmail flagged as {', '.join(matched)} — escalating to LLM",
            matched_label_ids=tuple(matched),
        )

    # 9. No high-confidence heuristic matched — escalate.
    return HeuristicResult(
        category=CATEGORY_INFORMATIONAL,
        is_phishing=is_phishing,
        confident=False,
        reason="no heuristic match — escalating to LLM",
    )


def _looks_phishing(subject_lower: str) -> bool:
    """Conservative phishing flag based on keyword pairs.

    Returns True only when at least one paired indicator OR a singleton
    high-signal phrase fires. Single common words like ``"verify"`` on
    their own are not enough — too many false positives for legitimate
    account-management mail.
    """
    for required, also in _PHISHING_KEYWORD_PAIRS:
        if required in subject_lower and also in subject_lower:
            return True
    for phrase in _PHISHING_SINGLE_PHRASES:
        if phrase in subject_lower:
            return True
    return False


def group_by_category(triage_results: list[dict]) -> dict:
    """Group an iterable of triage results into category buckets.

    Used by the read-tools' ``triage_inbox`` summary view. Each item must
    have ``id`` and ``category`` keys; ``is_spam`` / ``is_phishing`` flags
    if present are surfaced into separate ``spam`` / ``phishing`` buckets
    on top of the category bucket so the user sees both views.

    Adapted from PR #916's ``group_by_category`` with the new taxonomy.
    """
    buckets: dict[str, list[str]] = {cat: [] for cat in ALL_CATEGORIES}
    spam: list[str] = []
    phishing: list[str] = []
    for item in triage_results:
        msg_id = item.get("id")
        if msg_id is None:
            continue
        cat = item.get("category", CATEGORY_INFORMATIONAL)
        buckets.setdefault(cat, []).append(msg_id)
        if item.get("is_spam"):
            spam.append(msg_id)
        if item.get("is_phishing"):
            phishing.append(msg_id)
    return {
        "groups": buckets,
        "spam": spam,
        "phishing": phishing,
        "total": sum(len(v) for v in buckets.values()),
    }


# Backwards-compat alias for callers that imported the PR #916 spelling.
# Kept private — new code should use the canonical names.
_classify = classify_category_heuristic


__all__ = [
    "ALL_CATEGORIES",
    "CATEGORY_URGENT",
    "CATEGORY_ACTIONABLE",
    "CATEGORY_INFORMATIONAL",
    "CATEGORY_LOW_PRIORITY",
    "HeuristicResult",
    "classify_category_heuristic",
    "group_by_category",
    # System label ID constants — exported so callers can match without
    # repeating string literals.
    "LABEL_INBOX",
    "LABEL_SPAM",
    "LABEL_TRASH",
    "LABEL_IMPORTANT",
    "LABEL_STARRED",
    "LABEL_UNREAD",
    "LABEL_CATEGORY_PROMOTIONS",
    "LABEL_CATEGORY_UPDATES",
    "LABEL_CATEGORY_SOCIAL",
    "LABEL_CATEGORY_FORUMS",
    "LABEL_CATEGORY_PERSONAL",
]
