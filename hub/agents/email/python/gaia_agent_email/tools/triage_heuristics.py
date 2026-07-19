# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Pre-LLM triage heuristics for the Email Triage Agent.

Heuristic rules adapted from PR #916 (Inbox Zero prototype). Schema 2.0 (#1615).
Five-bucket taxonomy: URGENT, NEEDS_RESPONSE, FYI, PROMOTIONAL, PERSONAL.

  - ``URGENT``          -- needs the user's attention right now
  - ``NEEDS_RESPONSE``  -- requires a reply / decision in the near term
  - ``FYI``             -- useful context, no action
  - ``PROMOTIONAL``     -- newsletters, promotions, low-signal updates
  - ``PERSONAL``        -- personal correspondence, no action needed

Plus two boolean fields surfaced separately so the eval harness can score
them independently of the five-way classification:

  - ``is_spam``        -- content-based spam detection (#1906). The heuristic
                          only commits ``True`` for a narrow set of
                          high-confidence, content-derived sender patterns
                          (auto-generated anonymous addresses, freemail-domain
                          impersonation) -- patterns expected to generalize
                          beyond any one corpus, not memorized literal
                          strings. Everything else within ``PROMOTIONAL``
                          (where spam exclusively lives) is left
                          ``spam_confident=False`` for the LLM to judge from
                          the actual content -- mass-market junk (pharma,
                          prize scams) reads very differently from a
                          plausible, if aggressive, marketing solicitation,
                          and that distinction needs real reading
                          comprehension, not more keyword rules.
  - ``is_phishing``    -- heuristics suggest credential-harvesting (separate
                          from generic spam -- the agent should refuse to act
                          on links from a phishing message even if the user
                          says "do as it asks")

The heuristic exists to save an LLM round-trip on obviously-low-priority
mail (newsletters, promotions, automated security alerts that are clearly
FYI/PROMOTIONAL). Everything that doesn't match a high-confidence keyword
or system-label rule falls through to the LLM. This module never decides
on its own that an email is ``NEEDS_RESPONSE`` or ``URGENT`` -- those require
the LLM's reading of the body and are intentionally not heuristic-gated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List

# ---------------------------------------------------------------------------
# Gmail API system label IDs we recognize.
# ---------------------------------------------------------------------------
# The Gmail REST API returns these as opaque strings on every message. The
# user-defined labels look like ``Label_12345`` and cannot be matched by
# keyword -- the heuristic fast-path therefore covers ONLY the built-in
# system labels. (Source: Gmail API v1 docs, ``users.messages.list`` →
# ``labelIds`` field.)

LABEL_INBOX = "INBOX"
LABEL_TRASH = "TRASH"
LABEL_IMPORTANT = "IMPORTANT"
LABEL_STARRED = "STARRED"
LABEL_UNREAD = "UNREAD"
LABEL_CATEGORY_PROMOTIONS = "CATEGORY_PROMOTIONS"
LABEL_CATEGORY_UPDATES = "CATEGORY_UPDATES"
LABEL_CATEGORY_SOCIAL = "CATEGORY_SOCIAL"
LABEL_CATEGORY_FORUMS = "CATEGORY_FORUMS"
LABEL_CATEGORY_PERSONAL = "CATEGORY_PERSONAL"


# Categories emitted by the heuristic. MUST agree with the contract's
# EmailCategory enum -- any drift breaks AC4. Schema 2.0 (#1615).
# Precedence: URGENT > NEEDS_RESPONSE > PROMOTIONAL > PERSONAL > FYI(default)
CATEGORY_URGENT = "URGENT"
CATEGORY_NEEDS_RESPONSE = "NEEDS_RESPONSE"
CATEGORY_FYI = "FYI"
CATEGORY_PROMOTIONAL = "PROMOTIONAL"
CATEGORY_PERSONAL = "PERSONAL"

ALL_CATEGORIES: tuple[str, ...] = (
    CATEGORY_URGENT,
    CATEGORY_NEEDS_RESPONSE,
    CATEGORY_FYI,
    CATEGORY_PROMOTIONAL,
    CATEGORY_PERSONAL,
)


def default_action_for(category: str) -> str:
    """Return the default suggested_action for a triage category.

    Precedence: URGENT/NEEDS_RESPONSE -> reply, PROMOTIONAL -> archive, others -> none.
    """
    if category in (CATEGORY_URGENT, CATEGORY_NEEDS_RESPONSE):
        return "reply"
    if category == CATEGORY_PROMOTIONAL:
        return "archive"
    return "none"  # FYI, PERSONAL, or unknown


@dataclass(frozen=True)
class HeuristicResult:
    """Outcome of a single heuristic pass over one message.

    ``confident=True`` means the heuristic is willing to commit to the
    category without LLM consultation. ``confident=False`` means the
    heuristic returned a best-guess fallback (e.g., ``informational``)
    and the caller should escalate to the LLM. The ``reason`` field is
    the source-of-truth for verbose-mode logging -- it tells the operator
    exactly which rule fired.
    """

    category: str
    is_spam: bool = False
    spam_confident: bool = True
    is_phishing: bool = False
    confident: bool = False
    reason: str = ""
    matched_label_ids: tuple[str, ...] = field(default_factory=tuple)


# Phishing heuristics -- *very* conservative. The cost of a false negative
# (real phishing missed) is higher than a false positive (legitimate
# password-reset flagged), but the heuristic must NEVER auto-act on phishing.
# The flag is informational only -- the LLM gets the same message and
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
# NOTE: "newsletter" is intentionally omitted -- company-internal newsletters
# (e.g. all-hands recaps, digest emails from colleagues) appear with the word
# "newsletter" in the subject and should be classified by the LLM, not killed
# as low-priority by the heuristic. External marketing newsletters are caught
# by the CATEGORY_PROMOTIONS label before this list fires.
_PROMO_SUBJECT_KEYWORDS = (
    "50% off",
    "sale ends",
    "limited time",
    "special offer",
    "deal of the day",
    "discount code",
    "coupon",
    "this week's deals",
)


# Spam sender signals (#1906) -- provider-agnostic, content-derived, and
# deliberately narrow: each pattern generalizes beyond this one corpus
# (auto-generated anonymous local-parts, freemail-brand domain impersonation)
# rather than memorizing specific spam-campaign domain strings. The signal
# only ever commits is_spam=True once _spam_fields confirms category is
# confidently PROMOTIONAL (never from an unresolved or non-PROMOTIONAL
# category, even on a signal hit) -- so most of this corpus's spam, which
# rarely also matches a confident-PROMOTIONAL heuristic branch, still falls
# through to the LLM. That's intentional: this prefilter exists to skip an
# LLM round-trip on the rare unambiguous case, not to carry recall on its
# own; see classify_category_heuristic and _spam_fields.
_ANON_SENDER_PATTERN = re.compile(r"^contact\.\d+@", re.IGNORECASE)
_FREEMAIL_BRANDS = ("hotmail", "gmail", "yahoo", "outlook")


def _spam_sender_signal(sender: str) -> bool:
    """High-confidence, content-derived spam sender signal.

    True only for a narrow set of patterns expected to generalize: an
    auto-generated anonymous local-part (``contact.1234@...``), or a sender
    domain that impersonates a freemail brand without being that brand's
    real domain.

    The freemail check is registrable-domain-based, not a literal domain
    allowlist: a real provider's leftmost domain label is the brand name
    exactly (``hotmail.com``, ``hotmail.co.uk``, ``hotmail.fr`` -- any TLD/
    ccTLD suffix), while an impersonation domain mixes the brand with other
    characters in that same label (``hotmail-secure.cc``). A hardcoded list
    of "real" domains would need every ccTLD variant (``yahoo.co.uk``,
    ``outlook.de``, ...) to avoid flagging legitimate international mail --
    this generalizes without one.
    """
    sender = sender or ""
    if _ANON_SENDER_PATTERN.match(sender):
        return True
    match = re.search(r"@([\w.-]+)", sender)
    domain = match.group(1).rstrip(">").lower() if match else ""
    leading_label = domain.split(".", 1)[0] if domain else ""
    if leading_label and any(
        brand in leading_label and leading_label != brand for brand in _FREEMAIL_BRANDS
    ):
        return True
    return False


def _spam_fields(category: str, spam_signal: bool) -> tuple[bool, bool]:
    """Resolve ``(is_spam, spam_confident)`` for a HeuristicResult.

    Spam exclusively lives in PROMOTIONAL in the eval corpus, so a confident
    non-PROMOTIONAL category trusts ``is_spam=False`` outright -- the category
    gate is checked FIRST, before the sender signal, so a mechanically
    spam-shaped sender address (e.g. an auto-generated ``contact.NNNN@``
    ticketing address) on an otherwise-legitimate UPDATES/PERSONAL email
    can't get confidently mis-flagged with no LLM recourse. Within
    PROMOTIONAL, the sender signal commits ``True``; otherwise the heuristic
    is not confident and the caller should escalate to the LLM.
    """
    if category != CATEGORY_PROMOTIONAL:
        return False, True
    if spam_signal:
        return True, True
    return False, False


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

# Subject-line patterns that signal genuine urgency even from automated senders.
# When any of these appear in the subject, the automated-sender heuristic must
# NOT commit confident=informational -- the LLM must read the body to decide.
# These cover the primary miss class (#1266): DevOps/IT alerts with [SEV1],
# credential-rotation advisories, and compliance deadlines sent by noreply/alerts@.
_URGENT_SUBJECT_PATTERNS = (
    "[sev1]",
    "[sev0]",
    "rotate credentials",
    "compliance acknowledgment",
    "compliance sign-off",
    "owner needed",
    "response needed",
    "action required",
    "requires exec review",
    "prod incident",
    "security advisory",
    "respond by",
    "due by eod",
    "due today",
    "within 4 hours",
    "within 24 hours",
    "within 1 hour",
)


# Content signals that a message carries a genuine deadline, commitment, or
# consequence for the user — i.e. it needs ATTENTION/ACTION even when Gmail
# filed it under a low-priority category (PROMOTIONS / SOCIAL / UPDATES).
# #2113: a membership notice with a this-week attendance requirement and a
# personal-finance "budget exceeded" alert were both confidently buried
# (archive / informational) because the category label short-circuited before
# any body read. When one of these fires we VETO the confident low-priority
# short-circuit and escalate to the LLM (which then reads the full body).
#
# Precision-first: these are obligation/consequence phrases, deliberately NOT
# marketing-urgency ("sale ends", "limited time", "last chance") — those must
# still archive confidently. Kept narrow so routine receipts/newsletters are
# not dragged into the LLM.
_COMMITMENT_SIGNAL_PATTERNS = (
    # Deadlines / required responses
    "action required",
    "response required",
    "responses are required",
    "respond by",
    "reply by",
    "rsvp by",
    "register by",
    "renew by",
    "confirm by",
    "due by",
    "payment due",
    "past due",
    "past-due",
    "overdue",
    "final notice",
    "final reminder",
    "deadline",
    "no later than",
    # Attendance / mandatory commitments
    "attendance is required",
    "attendance required",
    "mandatory attendance",
    "required to attend",
    "must attend",
    "confirm your attendance",
    # Consequences for non-compliance
    "failure to",
    "non-compliance",
    "noncompliance",
    "will be suspended",
    "will be cancelled",
    "will be canceled",
    "will be terminated",
    "will be revoked",
    "will be charged",
    "late fee",
    "avoid a late fee",
    "loss of access",
    # Personal-finance alerts (budget/balance)
    "budget exceeded",
    "over budget",
    "you have exceeded",
    "you've exceeded",
    "exceeded your",
    "overdrawn",
)


def _has_commitment_signal(subject_lower: str, body_lower: str) -> bool:
    """True when subject or body carries a deadline/commitment/consequence.

    The body channel is what makes this distinct from #1266's subject-only
    urgent veto — the #2113 misses (attendance requirement, budget alert)
    lived in the body, invisible to a subject-only check.
    """
    for pattern in _COMMITMENT_SIGNAL_PATTERNS:
        if pattern in subject_lower or pattern in body_lower:
            return True
    return False


def classify_category_heuristic(
    subject: str,
    sender: str,
    label_ids: Iterable[str],
    body: str = "",
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
            ignored -- they cannot be classified by name.
        body: Plain-text body or snippet (any length; empty string
            disables body-level signals). Callers that have already decoded
            the full body should pass it; callers in the fast bulk-triage
            path may pass ``msg["snippet"]`` to avoid an extra decode.

    Returns:
        A :class:`HeuristicResult`. When ``confident=False`` the caller
        SHOULD escalate to the LLM; when ``True`` the LLM call can be
        skipped (saves latency on bulk triage).
    """
    label_id_set = set(label_ids)
    subject_lower = (subject or "").lower()
    sender_lower = (sender or "").lower()
    body_lower = (body or "").lower()

    # #2113: a deadline/commitment/consequence signal vetoes the confident
    # low-priority label short-circuit below (PROMOTIONS / SOCIAL / UPDATES),
    # so a real obligation buried under a promotions label reaches the LLM
    # instead of being confidently archived / filed informational.
    commitment_signal = _has_commitment_signal(subject_lower, body_lower)

    # Phishing is a *signal* layered on top of every category, never a
    # category itself -- compute once up front so spam/phishing can co-fire.
    # detect_phishing covers subject + sender-domain + body; the body channel
    # uses whatever text the caller provides (snippet or full decode).
    is_phishing = detect_phishing(subject, sender, body)
    spam_signal = _spam_sender_signal(sender)

    # 1. Promotions -- confident, label-driven. Vetoed by a commitment
    #    signal (#2113): a deadline/consequence in the body means the LLM
    #    must read it rather than confidently archiving it.
    if LABEL_CATEGORY_PROMOTIONS in label_id_set:
        is_spam, spam_confident = _spam_fields(CATEGORY_PROMOTIONAL, spam_signal)
        if commitment_signal:
            return HeuristicResult(
                category=CATEGORY_PROMOTIONAL,
                is_spam=is_spam,
                spam_confident=spam_confident,
                is_phishing=is_phishing,
                confident=False,
                reason=(
                    "Gmail CATEGORY_PROMOTIONS label set but body has a "
                    "deadline/commitment signal -- escalating to LLM"
                ),
                matched_label_ids=(LABEL_CATEGORY_PROMOTIONS,),
            )
        return HeuristicResult(
            category=CATEGORY_PROMOTIONAL,
            is_spam=is_spam,
            spam_confident=spam_confident,
            confident=True,
            reason="Gmail CATEGORY_PROMOTIONS label set",
            matched_label_ids=(LABEL_CATEGORY_PROMOTIONS,),
        )

    # 3. Social -- confident, label-driven. Notifications, "X liked your
    #    post", etc. Same commitment veto as promotions (#2113).
    if LABEL_CATEGORY_SOCIAL in label_id_set:
        is_spam, spam_confident = _spam_fields(CATEGORY_PROMOTIONAL, spam_signal)
        if commitment_signal:
            return HeuristicResult(
                category=CATEGORY_PROMOTIONAL,
                is_spam=is_spam,
                spam_confident=spam_confident,
                is_phishing=is_phishing,
                confident=False,
                reason=(
                    "Gmail CATEGORY_SOCIAL label set but body has a "
                    "deadline/commitment signal -- escalating to LLM"
                ),
                matched_label_ids=(LABEL_CATEGORY_SOCIAL,),
            )
        return HeuristicResult(
            category=CATEGORY_PROMOTIONAL,
            is_spam=is_spam,
            spam_confident=spam_confident,
            confident=True,
            reason="Gmail CATEGORY_SOCIAL label set",
            matched_label_ids=(LABEL_CATEGORY_SOCIAL,),
        )

    # 4. Updates -- confident, label-driven. Receipts, shipping
    #    confirmations, calendar updates: useful context, no reply needed.
    #    A commitment signal (e.g. a "budget exceeded" alert) vetoes the
    #    confident=FYI short-circuit so it isn't filed informational (#2113).
    if LABEL_CATEGORY_UPDATES in label_id_set:
        is_spam, spam_confident = _spam_fields(CATEGORY_FYI, spam_signal)
        if commitment_signal:
            return HeuristicResult(
                category=CATEGORY_FYI,
                is_spam=is_spam,
                spam_confident=spam_confident,
                is_phishing=is_phishing,
                confident=False,
                reason=(
                    "Gmail CATEGORY_UPDATES label set but body has a "
                    "deadline/commitment signal -- escalating to LLM"
                ),
                matched_label_ids=(LABEL_CATEGORY_UPDATES,),
            )
        return HeuristicResult(
            category=CATEGORY_FYI,
            is_spam=is_spam,
            spam_confident=spam_confident,
            confident=True,
            reason="Gmail CATEGORY_UPDATES label set",
            matched_label_ids=(LABEL_CATEGORY_UPDATES,),
        )

    # 5. Personal -- confident, label-driven.
    if LABEL_CATEGORY_PERSONAL in label_id_set:
        is_spam, spam_confident = _spam_fields(CATEGORY_PERSONAL, spam_signal)
        return HeuristicResult(
            category=CATEGORY_PERSONAL,
            is_spam=is_spam,
            spam_confident=spam_confident,
            is_phishing=is_phishing,
            confident=True,
            reason="Gmail CATEGORY_PERSONAL label set",
            matched_label_ids=(LABEL_CATEGORY_PERSONAL,),
        )

    # 6. Subject-keyword promo fallback -- fires when Gmail didn't tag
    #    promotions but the marketing language is unmistakable.
    for kw in _PROMO_SUBJECT_KEYWORDS:
        if kw in subject_lower:
            is_spam, spam_confident = _spam_fields(CATEGORY_PROMOTIONAL, spam_signal)
            return HeuristicResult(
                category=CATEGORY_PROMOTIONAL,
                is_spam=is_spam,
                spam_confident=spam_confident,
                is_phishing=is_phishing,
                confident=True,
                reason=f"subject contains promotional keyword '{kw}'",
            )

    # 7. Automated-sender fallback -- newsletters, alert bots, etc.
    #    Exception: if the subject contains high-urgency signals (e.g. [SEV1],
    #    "rotate credentials", "compliance due by EOD"), the heuristic MUST
    #    NOT commit confident=informational -- these are DevOps/IT alerts that
    #    require a human to act and must be reviewed by the LLM (#1266).
    for kw in _AUTOMATED_SENDER_KEYWORDS:
        if kw in sender_lower:
            is_spam, spam_confident = _spam_fields(CATEGORY_FYI, spam_signal)
            if _subject_has_urgent_signal(subject_lower):
                return HeuristicResult(
                    category=CATEGORY_FYI,
                    is_spam=is_spam,
                    spam_confident=spam_confident,
                    is_phishing=is_phishing,
                    confident=False,
                    reason=(
                        f"sender contains automated-sender keyword '{kw}' but "
                        "subject has urgent signal -- escalating to LLM"
                    ),
                )
            return HeuristicResult(
                category=CATEGORY_FYI,
                is_spam=is_spam,
                spam_confident=spam_confident,
                is_phishing=is_phishing,
                confident=True,
                reason=f"sender contains automated-sender keyword '{kw}'",
            )

    # 8. Built-in IMPORTANT / STARRED labels -- Gmail has decided this is
    #    significant; we down-rank but do NOT classify (urgent vs.
    #    actionable depends on body, which the LLM reads). Return
    #    confident=False so the caller escalates.
    matched: List[str] = []
    if LABEL_IMPORTANT in label_id_set:
        matched.append(LABEL_IMPORTANT)
    if LABEL_STARRED in label_id_set:
        matched.append(LABEL_STARRED)
    if matched:
        is_spam, spam_confident = _spam_fields(CATEGORY_NEEDS_RESPONSE, spam_signal)
        return HeuristicResult(
            category=CATEGORY_NEEDS_RESPONSE,
            is_spam=is_spam,
            spam_confident=spam_confident,
            is_phishing=is_phishing,
            confident=False,
            reason=f"Gmail flagged as {', '.join(matched)} -- escalating to LLM",
            matched_label_ids=tuple(matched),
        )

    # 9. No high-confidence heuristic matched -- escalate. Category is
    # unresolved (FYI is a placeholder the LLM will override) and could turn
    # out to be anything, including PROMOTIONAL -- committing is_spam=True
    # from the sender signal here would risk confidently mis-flagging a
    # message that later resolves to a non-PROMOTIONAL category, with no LLM
    # recourse. Always escalate; the same LLM call already happening for
    # category resolves is_spam too.
    return HeuristicResult(
        category=CATEGORY_FYI,
        is_spam=False,
        spam_confident=False,
        is_phishing=is_phishing,
        confident=False,
        reason="no heuristic match -- escalating to LLM",
    )


def _subject_has_urgent_signal(subject_lower: str) -> bool:
    """Return True when the subject contains a high-urgency indicator.

    Used to suppress the automated-sender confident=informational path for
    DevOps/IT alerts whose subjects unambiguously signal urgency. These cases
    must fall through to the LLM rather than being silently classified as
    informational (#1266).
    """
    return any(pattern in subject_lower for pattern in _URGENT_SUBJECT_PATTERNS)


def _looks_phishing(subject_lower: str) -> bool:
    """Conservative phishing flag based on keyword pairs.

    Returns True only when at least one paired indicator OR a singleton
    high-signal phrase fires. Single common words like ``"verify"`` on
    their own are not enough -- too many false positives for legitimate
    account-management mail.
    """
    for required, also in _PHISHING_KEYWORD_PAIRS:
        if required in subject_lower and also in subject_lower:
            return True
    for phrase in _PHISHING_SINGLE_PHRASES:
        if phrase in subject_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Multi-signal phishing detector (#1271)
#
# ``detect_phishing`` augments the subject-only ``_looks_phishing`` with
# sender-domain and body signals for use by the block/quarantine tool and
# the precision CI gate.  The design is precision-first: each signal is
# high-specificity and conservative.  Recall is secondary -- it is better
# to miss a phishing message than to flag a legit onboarding email.
# ---------------------------------------------------------------------------

# Sender-domain analysis constants.
# ---------------------------------------------------------------------------
# Registrable SLDs (second-level domains) known to belong to legitimate
# organisations.  A sender whose SLD exactly matches one of these is
# *never* flagged by the domain signal, regardless of TLD.
_LEGIT_SENDER_SLDS: frozenset[str] = frozenset(
    {
        "github",
        "slack",
        "amazon",
        "google",
        "dropbox",
        "microsoft",
        "apple",
        "stripe",
        "uber",
        "netflix",
        "ups",
        "twitter",
        "linkedin",
        "notion",
        "etsy",
        "zoom",
        "namecheap",
        "figma",
        "okta",
        "airbnb",
        "workday",
        "docusign",
        "coinbase",
        "heroku",
        "atlassian",
        "spotify",
        "fedex",
        "usps",
        "dhl",
        "paypal",
    }
)

# TLDs that are routinely abused by phishing campaigns but rarely used by
# legitimate large-scale senders.  Flagged when the SLD is NOT in the
# allowlist above.
_SUSPICIOUS_TLDS: frozenset[str] = frozenset({"tk", "xyz", "ml", "ga", "cf"})

# Brand keywords that should appear in the SLD of a legitimate sender but,
# when found inside a *non-canonical* domain (e.g. as a substring of a
# longer SLD), indicate impersonation.
#
# Short brands (<= _SHORT_BRAND_MAX_LEN chars, e.g. "irs", "dhl", "hsbc",
# "uber") are matched on a WORD-TOKEN boundary, never as a raw substring --
# otherwise "irs" fires inside legit domains like ``firstservice.com`` /
# ``firstalert.com`` and "uber" inside ``uberflip.com``. Longer brands are
# matched as substrings (their collision risk against real words is
# negligible). See ``_domain_has_impersonation_brand``.
_SHORT_BRAND_MAX_LEN = 4

_IMPERSONATION_BRANDS: tuple[str, ...] = (
    "paypal",
    "paypa",
    "microsoft",
    "micros0ft",
    "amazon",
    "amaz0n",
    "google",
    "g00gle",
    "apple",
    "netflix",
    "facebook",
    "twitter",
    "instagram",
    "linkedin",
    "linkedln",  # common typo impersonation
    "adobe",
    "spotify",
    "steam",
    "coinbase",
    "docusign",
    "chase",
    "citibank",
    "wellsfargo",
    "dhl",
    "irs",
    "uber",
    "dropbox",
    "outlook",
    "hsbc",
    "bankofamerica",
)

# Body-level keyword pairs (same structure as subject pairs).  Each tuple
# fires when BOTH terms appear in the body.  Precision-trap cases like
# "verify your email" + "click" appear in legitimate onboarding mail but
# "verify your account" + "banking" is almost always credential-harvesting.
_PHISHING_BODY_KEYWORD_PAIRS: tuple[tuple[str, str], ...] = (
    ("verify your account", "banking"),
    ("verify your account", "transfer"),
    ("transfer to a protected account", "compromised"),
    ("your funds will be frozen", "verify"),
    ("cryptocurrency", "frozen"),
    ("your balance", "transfer to a protected"),
    ("security deposit", "click here"),
)

# Body-level single phrases that are high-signal on their own.
_PHISHING_BODY_SINGLE_PHRASES: tuple[str, ...] = (
    "transfer to a protected account",
    "your funds will be frozen",
    "cryptocurrency holdings will be frozen",
)

# Suffix patterns in a domain's SLD that strongly suggest impersonation.
# Only fires when a brand keyword is also present in the full domain.
_IMPERSONATION_SLD_SUFFIXES: tuple[str, ...] = (
    "alert",
    "secure",
    "verify",
    "service",
    "support",
    "team",
    "helpdesk",
)

# Number-substitution pattern: a letter-digit-letter run in a domain word
# (e.g. ``amaz0n``, ``micros0ft``, ``g00gle``).
_NUM_SUB_RE = re.compile(r"[a-z][01][a-z]")

# Splits an SLD into alpha-only word tokens (hyphens and digits are
# separators), so short brands match whole tokens, not substrings.
_SLD_TOKEN_RE = re.compile(r"[^a-z]+")


def _domain_has_impersonation_brand(full_domain: str, sld: str) -> bool:
    """Return True when a known brand keyword is present in the domain.

    Short brands (``<= _SHORT_BRAND_MAX_LEN``) match only as a standalone
    word token of the SLD -- never as a substring -- so e.g. ``"irs"`` does
    not fire inside ``firstservice`` / ``firstalert`` and ``"uber"`` does
    not fire inside ``uberflip``. Longer brands match as a substring of the
    hyphen-collapsed domain (so ``dropbox-security-alert`` still matches
    ``dropbox``).
    """
    domain_no_hyphens = full_domain.replace("-", "")
    sld_tokens = set(_SLD_TOKEN_RE.split(sld))
    for brand in _IMPERSONATION_BRANDS:
        if len(brand) <= _SHORT_BRAND_MAX_LEN:
            if brand in sld_tokens:
                return True
        elif brand in domain_no_hyphens:
            return True
    return False


def _suspicious_sender_domain(sender_lower: str) -> bool:
    """Return True when the sender domain shows impersonation signals.

    Three-tier check (all precision-first):

    1. If the SLD is in the trusted allowlist → never flag.
    2. If TLD is in the suspicious set → flag (unknown org using a spam TLD).
    3. For .com / .net / .org: flag only when a brand keyword appears in the
       domain AND (the SLD contains a digit-substitution OR a suspicious
       suffix pattern). Short brands must match on a word-token boundary
       (see ``_domain_has_impersonation_brand``).

    Ham that would otherwise be flagged (e.g. docusign.net, coinbase.com,
    firstservice.com) is protected by the allowlist + token-boundary check.
    """
    match = re.search(r"@([\w.\-]+)", sender_lower)
    if not match:
        return False
    full_domain = match.group(1)
    parts = full_domain.split(".")
    # Need at least two labels for a meaningful SLD / TLD split.
    if len(parts) < 2:
        return False
    sld = parts[-2]
    tld = parts[-1]

    # Tier 1: trusted allowlist.
    if sld in _LEGIT_SENDER_SLDS:
        return False

    # Tier 2: suspicious TLD regardless of SLD.
    if tld in _SUSPICIOUS_TLDS:
        return True

    # Tier 3: common TLD (.com / .net) -- require brand + impersonation signal.
    if not _domain_has_impersonation_brand(full_domain, sld):
        return False

    # Digit substitution in the SLD (e.g. amaz0n, micros0ft).
    if _NUM_SUB_RE.search(sld):
        return True

    # Suspicious suffix appended to brand name in the SLD
    # (e.g. dropbox-security-alert → sld = 'dropbox-security-alert').
    return any(
        sld.endswith(sfx) or ("-" + sfx) in sld for sfx in _IMPERSONATION_SLD_SUFFIXES
    )


def _looks_phishing_body(body_lower: str) -> bool:
    """High-signal body-only phishing indicators.

    Only fires on phrases that are vanishingly rare in legitimate mail
    (e.g. "transfer to a protected account", "funds will be frozen") or
    paired indicators that together identify credential-harvesting.
    """
    for phrase in _PHISHING_BODY_SINGLE_PHRASES:
        if phrase in body_lower:
            return True
    for required, also in _PHISHING_BODY_KEYWORD_PAIRS:
        if required in body_lower and also in body_lower:
            return True
    return False


def detect_phishing(
    subject: str,
    sender: str,
    body: str,
) -> bool:
    """Multi-signal phishing detector used by both the heuristic triage path and
    the quarantine tool.

    Evaluates three independent channels -- subject keyword pairs, suspicious
    sender domain, and body-level signals -- and returns ``True`` when any one
    channel fires.  Each channel is conservative (precision-first).

    ``classify_category_heuristic`` calls this function to set ``is_phishing``
    on every triage result; the quarantine tool calls it again before acting.
    The function is deterministic and LLM-free.

    Args:
        subject: The decoded message subject (any case; normalised internally).
        sender:  The raw ``From`` header value (any case; normalised internally).
        body:    The decoded plain-text body (any case; normalised internally).

    Returns:
        ``True`` if any phishing signal fires; ``False`` otherwise.
    """
    subject_lower = (subject or "").lower()
    sender_lower = (sender or "").lower()
    body_lower = (body or "").lower()

    return (
        _looks_phishing(subject_lower)
        or _suspicious_sender_domain(sender_lower)
        or _looks_phishing_body(body_lower)
    )


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
        cat = item.get("category", CATEGORY_FYI)
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
# Kept private -- new code should use the canonical names.
_classify = classify_category_heuristic


__all__ = [
    "ALL_CATEGORIES",
    "CATEGORY_URGENT",
    "CATEGORY_NEEDS_RESPONSE",
    "CATEGORY_FYI",
    "CATEGORY_PROMOTIONAL",
    "CATEGORY_PERSONAL",
    "HeuristicResult",
    "classify_category_heuristic",
    "default_action_for",
    "detect_phishing",
    "group_by_category",
    # System label ID constants -- exported so callers can match without
    # repeating string literals.
    "LABEL_INBOX",
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
