# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia_agent_email.tools.triage_heuristics``.

Critical behaviour pinned by this suite:

1. The heuristic operates on Gmail API system label IDs
   (``CATEGORY_PROMOTIONS``, ...), NOT on the human label names.
2. The heuristic emits the schema-2.0 five-bucket taxonomy (#1615)
   (``URGENT / NEEDS_RESPONSE / FYI / PROMOTIONAL / PERSONAL``), NOT
   the retired #848 four-bucket scheme.
3. ``is_spam`` is content-based, provider-agnostic (#1906): the heuristic
   commits ``True`` only for a narrow, mechanical sender-pattern signal
   (auto-generated anonymous local-parts, freemail-domain impersonation);
   everything else is left ``spam_confident=False`` for the LLM to judge
   from actual content -- the heuristic never asserts a content-based
   spam/not-spam judgment call itself.
4. ``is_phishing`` is a SEPARATE boolean; it fires independently of spam.
5. ``confident=False`` results MUST escalate to the LLM.
"""

from __future__ import annotations

import ast
import inspect

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.tools import triage_heuristics as triage_heuristics_module
from gaia_agent_email.tools.triage_heuristics import (
    ALL_CATEGORIES,
    CATEGORY_FYI,
    CATEGORY_NEEDS_RESPONSE,
    CATEGORY_PERSONAL,
    CATEGORY_PROMOTIONAL,
    CATEGORY_URGENT,
    LABEL_CATEGORY_PERSONAL,
    LABEL_CATEGORY_PROMOTIONS,
    LABEL_CATEGORY_SOCIAL,
    LABEL_CATEGORY_UPDATES,
    LABEL_IMPORTANT,
    LABEL_INBOX,
    LABEL_STARRED,
    classify_category_heuristic,
    default_action_for,
    detect_phishing,
    group_by_category,
)

# ---------------------------------------------------------------------------
# System-label-ID matching
# ---------------------------------------------------------------------------


class TestSystemLabelIDs:
    """The heuristic MUST match Gmail API system label IDs, not human names."""

    def test_promotions_label_marks_low_priority(self):
        result = classify_category_heuristic(
            subject="50% off — but heuristic should match the LABEL not the keyword",
            sender="store@example.com",
            label_ids=[LABEL_INBOX, LABEL_CATEGORY_PROMOTIONS],
        )
        assert result.category == CATEGORY_PROMOTIONAL
        assert result.is_spam is False
        assert result.confident is True
        # #2744: the reason is a fact about the message, never the raw
        # Gmail label constant that triggered it.
        assert result.reason == "Looks like a promotional email"

    def test_social_label_marks_low_priority(self):
        result = classify_category_heuristic(
            subject="Alice mentioned you in a post",
            sender="notify@social-network.example",
            label_ids=[LABEL_INBOX, LABEL_CATEGORY_SOCIAL],
        )
        assert result.category == CATEGORY_PROMOTIONAL
        assert result.confident is True

    def test_updates_label_marks_informational(self):
        result = classify_category_heuristic(
            subject="Your shipment has been delivered",
            sender="orders@store.example",
            label_ids=[LABEL_INBOX, LABEL_CATEGORY_UPDATES],
        )
        assert result.category == CATEGORY_FYI
        assert result.confident is True
        # #2744: a fact about the message, never the raw label constant.
        assert result.reason == "Looks like an automated update"

    def test_human_label_name_does_NOT_match(self):
        """
        Pin the regression that broke PR #916's heuristic on live Gmail:
        ``"Promotions"`` (human name) MUST NOT trigger the
        ``CATEGORY_PROMOTIONS`` branch. Live Gmail returns the system ID;
        the human name only appears in MBOX exports.
        """
        result = classify_category_heuristic(
            subject="Your weekly newsletter",
            sender="news@example.com",
            label_ids=["Promotions", "INBOX"],  # human name — should NOT fire
        )
        # "newsletter" was deliberately dropped from the promo-keyword
        # fallback (#1266) to avoid killing internal company newsletters,
        # so this falls all the way through to the no-match escalation --
        # never the CATEGORY_PROMOTIONS branch either way.
        assert result.confident is False
        assert "CATEGORY_PROMOTIONS" not in result.reason

    def test_user_defined_label_does_not_match_heuristic(self):
        """
        User-defined labels are opaque ``Label_*`` IDs and cannot be
        keyword-matched. The heuristic must fall through to the
        no-match path (confident=False).
        """
        result = classify_category_heuristic(
            subject="Re: project update",
            sender="colleague@company.example",
            label_ids=[LABEL_INBOX, "Label_12345"],
        )
        assert (
            result.confident is False
        ), f"User-defined label triggered confident heuristic: {result!r}"


# ---------------------------------------------------------------------------
# Category taxonomy alignment with #848
# ---------------------------------------------------------------------------


class TestTaxonomy:
    """Categories MUST be the schema-2.0 five-bucket scheme (#1615)."""

    def test_categories_are_exactly_the_five_we_expect(self):
        assert set(ALL_CATEGORIES) == {
            "URGENT",
            "NEEDS_RESPONSE",
            "FYI",
            "PROMOTIONAL",
            "PERSONAL",
        }

    @pytest.mark.parametrize(
        "label_ids,expected",
        [
            ([LABEL_CATEGORY_PROMOTIONS], CATEGORY_PROMOTIONAL),
            ([LABEL_CATEGORY_SOCIAL], CATEGORY_PROMOTIONAL),
            ([LABEL_CATEGORY_UPDATES], CATEGORY_FYI),
            ([LABEL_CATEGORY_PERSONAL], CATEGORY_PERSONAL),
        ],
    )
    def test_emitted_category_is_one_of_the_five(self, label_ids, expected):
        result = classify_category_heuristic(
            subject="x", sender="x@example.com", label_ids=label_ids
        )
        assert result.category == expected
        assert result.category in ALL_CATEGORIES

    def test_schema_2_category_strings_are_emitted(self):
        """Schema 2.0 taxonomy (URGENT/NEEDS_RESPONSE/FYI/PROMOTIONAL/PERSONAL) must be emitted."""
        new_taxonomy = {"URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"}
        for label in [
            [LABEL_CATEGORY_PROMOTIONS],
            [LABEL_CATEGORY_UPDATES],
            [],  # no labels — fallback
        ]:
            result = classify_category_heuristic(
                subject="x", sender="x@example.com", label_ids=label
            )
            assert result.category in new_taxonomy


# ---------------------------------------------------------------------------
# Spam / phishing as separate booleans
# ---------------------------------------------------------------------------


class TestSpamPhishingFlags:
    def test_anon_sender_pattern_flags_spam_confidently(self):
        """Auto-generated anonymous local-part (contact.NNNN@) is a mechanical
        sender-format signal, not a content judgment -- the heuristic may
        commit it without LLM consultation (#1906)."""
        result = classify_category_heuristic(
            subject="50% off everything",
            sender="contact.4821@dealsnow.biz",
            label_ids=[],
        )
        assert result.is_spam is True
        assert result.spam_confident is True

    def test_freemail_impersonation_flags_spam_confidently(self):
        """A sender domain that contains a freemail brand name but isn't the
        real domain (e.g. hotmail-secure.cc vs hotmail.com) is a mechanical
        impersonation signal (#1906) -- committed only once category is
        confidently PROMOTIONAL (the sender signal alone can't override an
        unresolved or non-PROMOTIONAL category)."""
        result = classify_category_heuristic(
            subject="50% off everything",
            sender="user@hotmail-secure.cc",
            label_ids=[],
        )
        assert result.category == CATEGORY_PROMOTIONAL
        assert result.is_spam is True
        assert result.spam_confident is True

    def test_real_freemail_domain_does_not_false_positive(self):
        """The real hotmail.com/gmail.com/etc. domains must not match the
        impersonation pattern just because they contain the brand name."""
        result = classify_category_heuristic(
            subject="50% off everything",
            sender="someone@gmail.com",
            label_ids=[],
        )
        assert result.is_spam is False
        assert result.spam_confident is False  # PROMOTIONAL, no signal -> LLM

    def test_international_freemail_ccTLD_does_not_false_positive(self):
        """A real freemail provider's ccTLD variant (yahoo.co.uk, hotmail.fr,
        outlook.de) must not be confidently flagged spam just because it
        isn't the .com form -- this signal must generalize beyond a
        hardcoded domain allowlist (regression: legitimate international
        PERSONAL mail was being flagged with no LLM recourse)."""
        for sender in (
            "grandma@yahoo.co.uk",
            "bob@hotmail.fr",
            "x@outlook.de",
            "someone@googlemail.com",
        ):
            result = classify_category_heuristic(
                subject="Hope you're doing well",
                sender=sender,
                label_ids=[],
            )
            assert result.is_spam is False, f"false positive for {sender}"

    def test_freemail_impersonation_still_fires_regardless_of_tld(self):
        """An impersonation domain (brand mixed with other characters in the
        leading label) must still be caught, on any TLD -- confirms the
        registrable-domain check didn't just get looser. Category must be
        confidently PROMOTIONAL for the signal to commit."""
        result = classify_category_heuristic(
            subject="50% off everything",
            sender="user@hotmail-secure.co.uk",
            label_ids=[],
        )
        assert result.category == CATEGORY_PROMOTIONAL
        assert result.is_spam is True
        assert result.spam_confident is True

    def test_promotional_without_spam_signal_escalates_to_llm(self):
        """Most PROMOTIONAL mail (real or merely aggressive marketing) needs
        the LLM's actual reading of content to separate spam from legitimate
        marketing -- the heuristic does not guess (#1906)."""
        result = classify_category_heuristic(
            subject="50% off everything",
            sender="sales@legitcompany.example",
            label_ids=[],
        )
        assert result.category == CATEGORY_PROMOTIONAL
        assert result.confident is True  # category is confident...
        assert result.is_spam is False
        assert result.spam_confident is False  # ...but spam is not

    def test_non_promotional_category_trusts_is_spam_false(self):
        """Spam exclusively lives in PROMOTIONAL in this corpus/design; a
        confidently non-PROMOTIONAL category trusts is_spam=False outright,
        with no LLM round-trip needed just for spam."""
        result = classify_category_heuristic(
            subject="Re: budget review",
            sender="noreply@company.example",
            label_ids=[],
        )
        assert result.category == CATEGORY_FYI
        assert result.is_spam is False
        assert result.spam_confident is True

    def test_non_promotional_category_overrides_spam_sender_signal(self):
        """The category gate is checked BEFORE the sender signal: a
        mechanically spam-shaped sender (auto-generated contact.NNNN@
        address) on an otherwise-legitimate UPDATES email must NOT be
        confidently flagged spam with no LLM recourse -- the sender pattern
        alone isn't enough to override a confident non-PROMOTIONAL category
        (regression: this previously fired before the category check)."""
        result = classify_category_heuristic(
            subject="Your order shipped",
            sender="contact.12345@billing.company.com",
            label_ids=[LABEL_CATEGORY_UPDATES],
        )
        assert result.category == CATEGORY_FYI
        assert result.is_spam is False
        assert result.spam_confident is True

    def test_personal_category_overrides_spam_sender_signal(self):
        """Same category-gate-first guarantee for the PERSONAL label path."""
        result = classify_category_heuristic(
            subject="Hi",
            sender="contact.999@family.example",
            label_ids=[LABEL_CATEGORY_PERSONAL],
        )
        assert result.category == CATEGORY_PERSONAL
        assert result.is_spam is False
        assert result.spam_confident is True

    def test_unresolved_category_with_no_spam_signal_escalates(self):
        """When no heuristic matches at all (category unresolved, going to
        the LLM anyway), spam confidence cannot be assumed from category --
        always escalate, regardless of the sender signal, since the
        eventual category could turn out non-PROMOTIONAL."""
        result = classify_category_heuristic(
            subject="Re: meeting at 3pm",
            sender="alice@company.example",
            label_ids=[],
        )
        assert result.confident is False
        assert result.is_spam is False
        assert result.spam_confident is False

    def test_unresolved_category_with_spam_signal_still_escalates(self):
        """Even a confident sender-signal hit must not commit is_spam=True
        while category is unresolved -- the message could resolve to a
        non-PROMOTIONAL category, and there'd be no LLM recourse to correct
        a wrongly-committed spam flag."""
        result = classify_category_heuristic(
            subject="Can we reschedule?",
            sender="contact.42@family.example",
            label_ids=[],
        )
        assert result.confident is False
        assert result.is_spam is False
        assert result.spam_confident is False

    def test_phishing_keyword_pair_flags_phishing(self):
        result = classify_category_heuristic(
            subject="Verify your account immediately - click here",
            sender="security@bank.example",
            label_ids=[LABEL_INBOX],
        )
        # No high-confidence category match; phishing flag is informational.
        assert result.is_phishing is True

    def test_single_word_does_not_flag_phishing(self):
        # "verify" alone is too common (legit password reset, etc.) to fire.
        result = classify_category_heuristic(
            subject="Please verify your new email subscription",
            sender="newsletter@example.com",
            label_ids=[LABEL_INBOX],
        )
        assert result.is_phishing is False

    def test_phishing_fires_independently_of_spam(self):
        """Phishing detection is content-based and fires regardless of is_spam."""
        result = classify_category_heuristic(
            subject="Verify your account - click here urgently",
            sender="x@scam.example",
            label_ids=[LABEL_INBOX],
        )
        assert result.is_spam is False
        assert result.is_phishing is True


# ---------------------------------------------------------------------------
# Escalation behavior
# ---------------------------------------------------------------------------


class TestEscalation:
    def test_no_match_returns_not_confident(self):
        result = classify_category_heuristic(
            subject="Re: meeting at 3pm",
            sender="alice@company.example",
            label_ids=[LABEL_INBOX],
        )
        assert result.confident is False
        # #2744: describes the absence of a signal, never the classifier's
        # own escalation step.
        assert result.reason == "No clear signal from the sender or subject"

    def test_important_or_starred_escalate_with_actionable_hint(self):
        result = classify_category_heuristic(
            subject="Re: budget review",
            sender="ceo@company.example",
            label_ids=[LABEL_INBOX, LABEL_IMPORTANT],
        )
        assert result.confident is False  # LLM still has the final say
        assert result.category == CATEGORY_NEEDS_RESPONSE
        assert LABEL_IMPORTANT in result.matched_label_ids

    def test_starred_only_also_escalates(self):
        result = classify_category_heuristic(
            subject="Recipe", sender="x@example.com", label_ids=[LABEL_STARRED]
        )
        assert result.confident is False
        assert LABEL_STARRED in result.matched_label_ids


# ---------------------------------------------------------------------------
# group_by_category — bucketed view used by the triage tool's summary
# ---------------------------------------------------------------------------


class TestGroupByCategory:
    def test_basic_bucketing(self):
        items = [
            {"id": "m1", "category": CATEGORY_URGENT},
            {"id": "m2", "category": CATEGORY_NEEDS_RESPONSE},
            {"id": "m3", "category": CATEGORY_FYI},
            {"id": "m4", "category": CATEGORY_PROMOTIONAL},
            {"id": "m5", "category": CATEGORY_PROMOTIONAL},
        ]
        out = group_by_category(items)
        assert out["groups"][CATEGORY_URGENT] == ["m1"]
        assert out["groups"][CATEGORY_NEEDS_RESPONSE] == ["m2"]
        assert out["groups"][CATEGORY_FYI] == ["m3"]
        assert out["groups"][CATEGORY_PROMOTIONAL] == ["m4", "m5"]
        assert out["total"] == 5

    def test_spam_and_phishing_are_separate_lists(self):
        items = [
            {"id": "m1", "category": CATEGORY_PROMOTIONAL, "is_spam": True},
            {"id": "m2", "category": CATEGORY_FYI, "is_phishing": True},
            {"id": "m3", "category": CATEGORY_PROMOTIONAL},
        ]
        out = group_by_category(items)
        assert "m1" in out["spam"]
        assert "m2" in out["phishing"]
        # Spam/phishing items still appear in their categories — the
        # buckets are AND-views, not exclusive.
        assert "m1" in out["groups"][CATEGORY_PROMOTIONAL]
        assert "m2" in out["groups"][CATEGORY_FYI]

    def test_missing_id_skipped(self):
        items = [{"category": CATEGORY_URGENT}]  # no id
        out = group_by_category(items)
        assert out["total"] == 0


# ---------------------------------------------------------------------------
# Automated-sender urgent-subject override (#1266)
#
# The heuristic was committing confident=True / informational for emails from
# `alerts@` / `noreply@` senders, even when the subject line carried
# unambiguous urgent signals ([SEV1], "rotate credentials within N hours",
# "compliance acknowledgment due by EOD"). These must escalate to the LLM
# rather than being silently swallowed as informational.
# ---------------------------------------------------------------------------


class TestAutomatedSenderUrgentSubjectEscalation:
    """Automated-sender keyword heuristic must NOT fire confident=True
    when the subject contains high-urgency indicators. The LLM must read
    the body to make the final call."""

    def test_sev1_subject_from_alerts_sender_escalates(self):
        """[SEV1] in the subject from an automated sender must NOT be
        committed as informational — it must escalate to the LLM."""
        result = classify_category_heuristic(
            subject="[SEV1] API latency above SLA - owner needed",
            sender="DevOps Bot <alerts@acme-corp.example.com>",
            label_ids=["INBOX"],
        )
        assert (
            result.confident is False
        ), f"[SEV1] subject from alerts@ should escalate; got confident=True: {result!r}"

    def test_rotate_credentials_subject_from_noreply_escalates(self):
        """'rotate credentials within N hours' from noreply@ must escalate."""
        result = classify_category_heuristic(
            subject="Security advisory: rotate credentials within 4 hours",
            sender="IT Systems <noreply@acme-corp.example.com>",
            label_ids=["INBOX"],
        )
        assert (
            result.confident is False
        ), f"'rotate credentials' subject should escalate; got confident=True: {result!r}"

    def test_compliance_eod_from_automated_sender_escalates(self):
        """'compliance acknowledgment due by EOD' from automated sender must escalate."""
        result = classify_category_heuristic(
            subject="Compliance acknowledgment due by EOD",
            sender="DevOps Bot <alerts@acme-corp.example.com>",
            label_ids=["INBOX"],
        )
        assert (
            result.confident is False
        ), f"'compliance ... EOD' should escalate; got confident=True: {result!r}"

    def test_plain_automated_notification_without_urgency_stays_informational(self):
        """Non-urgent automated notifications (e.g., build passed) still get
        the informational confident heuristic — only urgent subjects escape."""
        result = classify_category_heuristic(
            subject="Build #4219 passed — all tests green",
            sender="CI Bot <noreply@ci.example.com>",
            label_ids=["INBOX"],
        )
        # A plain passing-build message has no urgency keywords; the heuristic
        # CAN commit confidently to informational here.
        assert result.category == CATEGORY_FYI
        assert result.confident is True

    def test_incident_prod_down_from_alerts_escalates(self):
        """'incident' / 'prod ... down' subjects from alerts@ must escalate."""
        result = classify_category_heuristic(
            subject="Prod incident report requires exec review",
            sender="DevOps Bot <alerts@acme-corp.example.com>",
            label_ids=["INBOX"],
        )
        assert (
            result.confident is False
        ), f"'prod incident ... requires exec review' should escalate; got: {result!r}"


# ---------------------------------------------------------------------------
# Newsletter keyword false-positive fix (#1266)
#
# The `newsletter` keyword in _PROMO_SUBJECT_KEYWORDS was firing confident
# low-priority on legitimate company newsletter/digest subjects like
# "All-hands recap and recording - newsletter" or "Benefits enrollment
# reminder - newsletter" sent by real colleagues, which the ground truth
# labels as informational. The keyword is too broad — removing it from the
# promo list means these fall through to the LLM, which can distinguish
# a marketing newsletter from a company one.
# ---------------------------------------------------------------------------


class TestNewsletterKeywordFalsePositive:
    def test_company_allhands_with_newsletter_in_subject_escalates(self):
        """'All-hands recap and recording - newsletter' from a real colleague
        should NOT be confidently committed to low priority — it should
        escalate so the LLM can determine it's an informational company update."""
        result = classify_category_heuristic(
            subject="All-hands recap and recording - newsletter",
            sender="HR Team <hr@acme-corp.example.com>",
            label_ids=["INBOX"],
        )
        # The heuristic should NOT commit confidently to low priority here.
        # Either it escalates (confident=False) or it stays low priority but
        # with a non-newsletter-keyword reason (label-based is fine). The key
        # requirement is: a human-sender company update with 'newsletter' in
        # its subject MUST NOT be confidently killed as low-priority by the
        # newsletter keyword alone.
        if result.confident and result.category == CATEGORY_PROMOTIONAL:
            assert "newsletter" not in result.reason.lower(), (
                "'newsletter' keyword should not confidently kill a company-sent "
                f"digest: {result!r}"
            )

    def test_marketing_newsletter_from_external_sender_stays_low_priority(self):
        """A genuine external marketing newsletter should still be low priority."""
        result = classify_category_heuristic(
            subject="This week's newsletter: 5 tips for productivity",
            sender="marketing@external-company.example.com",
            label_ids=["INBOX", LABEL_CATEGORY_PROMOTIONS],
        )
        # CATEGORY_PROMOTIONS label guarantees low priority regardless of subject.
        assert result.category == CATEGORY_PROMOTIONAL
        assert result.confident is True


# ---------------------------------------------------------------------------
# Multi-signal phishing detector (#1271)
#
# ``detect_phishing`` combines subject keyword pairs, suspicious sender-domain
# analysis, and high-signal body phrases. These tests pin the precision-first
# behaviour — especially the short-brand token-boundary rule that prevents
# "irs"/"uber" from firing inside legitimate domains like firstservice.com.
# ---------------------------------------------------------------------------


class TestDetectPhishingSubjectChannel:
    def test_subject_keyword_pair_flags(self):
        assert detect_phishing(
            subject="Verify your account immediately - click here",
            sender="security@bank.example",
            body="Please log in.",
        )

    def test_benign_subject_does_not_flag(self):
        assert not detect_phishing(
            subject="Re: lunch tomorrow?",
            sender="alice@company.example",
            body="Want to grab lunch at noon?",
        )


class TestDetectPhishingSenderDomainChannel:
    def test_number_substitution_domain_flags(self):
        # amaz0n with a 0-for-o substitution.
        assert detect_phishing(
            subject="Account notice",
            sender="noreply@amaz0n-security.net",
            body="Review your account.",
        )

    def test_suspicious_tld_flags(self):
        # Unknown org on a .tk domain.
        assert detect_phishing(
            subject="Hello",
            sender="x@company-helpdesk.tk",
            body="Update needed.",
        )

    def test_brand_plus_alert_suffix_flags(self):
        assert detect_phishing(
            subject="Notice",
            sender="helpdesk@dropbox-security-alert.com",
            body="Security breach.",
        )

    def test_legit_allowlisted_sld_never_flags(self):
        # docusign.net is allowlisted even though the TLD is .net.
        assert not detect_phishing(
            subject="Please review and sign the NDA",
            sender="dse_na4@docusign.net",
            body="Review the document.",
        )


class TestDetectPhishingShortBrandTokenBoundary:
    """Short brands (<= 4 chars) must match only on a word-token boundary,
    never as a raw substring — otherwise they fire inside unrelated words."""

    def test_irs_does_not_fire_inside_firstservice(self):
        # 'firstservice' contains the substring 'irs' (f-IRS-t...) but is a
        # legitimate property-management company — must NOT flag.
        assert not detect_phishing(
            subject="Your community newsletter",
            sender="news@firstservice.com",
            body="Monthly community update.",
        )

    def test_irs_does_not_fire_inside_firstalert(self):
        assert not detect_phishing(
            subject="Battery reminder",
            sender="alerts@firstalert.com",
            body="Test your smoke detectors.",
        )

    def test_uber_does_not_fire_inside_uberflip(self):
        assert not detect_phishing(
            subject="Weekly analytics",
            sender="reports@uberflip.com",
            body="Your content hub analytics are ready.",
        )

    def test_irs_fires_as_standalone_token(self):
        # 'irs-gov-refunds' has 'irs' as a standalone token → flag.
        assert detect_phishing(
            subject="Tax refund available",
            sender="refund@irs-gov-refunds.com",
            body="Verify your account and banking details to receive your refund.",
        )


class TestDetectPhishingBodyChannel:
    def test_body_pair_flags_even_with_clean_subject_and_sender(self):
        # Subject + sender are unremarkable; only the body betrays it.
        assert detect_phishing(
            subject="Important notice",
            sender="info@some-host.com",
            body="To receive your refund you must verify your account and "
            "provide your banking details.",
        )

    def test_high_signal_body_phrase_flags(self):
        assert detect_phishing(
            subject="Security notice",
            sender="info@some-host.com",
            body="Hackers have your credentials. Transfer to a protected "
            "account immediately.",
        )


# ---------------------------------------------------------------------------
# default_action_for helper (schema 2.0, #1615)
# ---------------------------------------------------------------------------


class TestDefaultActionFor:
    """``default_action_for`` derives suggested_action from the category."""

    def test_urgent_suggests_reply(self):
        assert default_action_for("URGENT") == "reply"

    def test_needs_response_suggests_reply(self):
        assert default_action_for("NEEDS_RESPONSE") == "reply"

    def test_promotional_suggests_archive(self):
        assert default_action_for("PROMOTIONAL") == "archive"

    def test_fyi_suggests_none(self):
        assert default_action_for("FYI") == "none"

    def test_personal_suggests_none(self):
        assert default_action_for("PERSONAL") == "none"

    def test_unknown_category_suggests_none(self):
        # Graceful fallback — unknown input yields "none", not an exception.
        assert default_action_for("BOGUS_CATEGORY") == "none"


# ---------------------------------------------------------------------------
# User-facing rationale must never leak classifier internals (#2744)
#
# ``HeuristicResult.reason`` used to be documented as a verbose-mode
# logging detail only, but it is rendered verbatim as the attention card's
# "why" line (``pre_scan_inbox_impl`` -> ``rationale`` -> the attention
# envelope's ``why`` field). A raw Gmail label constant (``CATEGORY_``), an
# internal control-flow term ("escalating", "LLM", "heuristic"), or a
# ``--`` code-style separator reads as classifier internals to a user, not
# a fact about their message.
# ---------------------------------------------------------------------------

_BANNED_SUBSTRINGS = ("category_", "escalating", "llm", "heuristic")
_BANNED_SEPARATOR = " -- "


def _reason_violations(text: str) -> list:
    """Return which banned tokens (if any) appear in a rendered reason."""
    lowered = text.lower()
    hits = [token for token in _BANNED_SUBSTRINGS if token in lowered]
    if _BANNED_SEPARATOR in text:
        hits.append(repr(_BANNED_SEPARATOR))
    return hits


def _reason_literals_from_source() -> list:
    """Statically collect every ``reason=`` string literal passed to a
    ``HeuristicResult(...)`` call in the module source.

    A *static* scan -- not just exercising the branches a test author
    thought to drive at runtime -- so a new branch added later is caught
    by this guard the moment its literal lands, even before any test
    happens to exercise it (#2744's own ask: "a guard that catches a
    future regression, not just a fix for today's strings").
    """
    source = inspect.getsource(triage_heuristics_module)
    tree = ast.parse(source)
    literals: list = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "HeuristicResult":
            continue
        for kw in node.keywords:
            if kw.arg != "reason":
                continue
            value = kw.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literals.append(value.value)
            elif isinstance(value, ast.JoinedStr):
                # f-string: the interpolated {...} parts are opaque at
                # parse time, but any literal text around them still is
                # -- and still must be checked.
                literals.extend(
                    piece.value
                    for piece in value.values
                    if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
                )
            # Anything else (e.g. a helper-function call, for a reason
            # that must vary at runtime) isn't scannable statically -- it
            # is covered by its own exhaustive runtime test instead (see
            # TestEveryEmittedReasonIsClean below).
    return literals


class TestReasonLiteralsNeverLeakClassifierInternals:
    """Static guard (#2744): every ``reason=`` string literal already in
    the source must read as a fact about the message, not a step the
    classifier took. Fails the moment a new branch's literal reuses a
    banned token, even before any test happens to exercise that branch."""

    def test_scan_finds_the_known_reason_literals(self):
        """Sanity check on the scan itself, so a change to
        ``classify_category_heuristic`` that stops using ``HeuristicResult(
        reason=...)`` (breaking the scan) fails loudly here instead of
        silently disabling the guard below."""
        literals = _reason_literals_from_source()
        assert len(literals) >= 13, (
            "expected the AST scan to find every reason= literal in "
            f"triage_heuristics.py -- got {literals!r}; either a branch "
            "was removed or the scan itself broke"
        )

    def test_no_banned_tokens_in_source_literals(self):
        literals = _reason_literals_from_source()
        offenders = {text: _reason_violations(text) for text in literals}
        offenders = {text: hits for text, hits in offenders.items() if hits}
        assert (
            not offenders
        ), f"classifier internals leaked into reason text: {offenders}"


class TestEveryEmittedReasonIsClean:
    """Behavioral guard (#2744): drive ``classify_category_heuristic``
    through every branch -- including the one dynamic reason (the
    IMPORTANT/STARRED escalation, built from a helper rather than a
    literal, so the static scan above can't see it) -- and check what it
    actually emits at runtime, not just what the source text says."""

    @staticmethod
    def _all_reasons() -> list:
        reasons = []

        # CATEGORY_PROMOTIONS -- confident, and commitment-veto (#2113).
        reasons.append(
            classify_category_heuristic(
                subject="50% off",
                sender="deals@shop.example",
                label_ids=[LABEL_CATEGORY_PROMOTIONS],
            ).reason
        )
        reasons.append(
            classify_category_heuristic(
                subject="Membership renewal",
                sender="news@club.example",
                label_ids=[LABEL_CATEGORY_PROMOTIONS],
                body=(
                    "Attendance is required. Failure to attend will "
                    "result in suspension."
                ),
            ).reason
        )

        # CATEGORY_SOCIAL -- confident, and commitment-veto.
        reasons.append(
            classify_category_heuristic(
                subject="Alice liked your post",
                sender="notify@social.example",
                label_ids=[LABEL_CATEGORY_SOCIAL],
            ).reason
        )
        reasons.append(
            classify_category_heuristic(
                subject="Group event",
                sender="notify@social.example",
                label_ids=[LABEL_CATEGORY_SOCIAL],
                body="RSVP by Friday or your reserved spot will be cancelled.",
            ).reason
        )

        # CATEGORY_UPDATES -- confident, and commitment-veto. This is
        # #2744's own live example (Gmail CATEGORY_UPDATES + a deadline
        # in the body).
        reasons.append(
            classify_category_heuristic(
                subject="Your shipment has been delivered",
                sender="orders@store.example",
                label_ids=[LABEL_CATEGORY_UPDATES],
            ).reason
        )
        reasons.append(
            classify_category_heuristic(
                subject="Your monthly spending summary",
                sender="alerts@bank.example",
                label_ids=[LABEL_CATEGORY_UPDATES],
                body="Heads up: you have exceeded your budget for this month.",
            ).reason
        )

        # CATEGORY_PERSONAL -- confident (no commitment veto on this branch).
        reasons.append(
            classify_category_heuristic(
                subject="Hi",
                sender="mom@family.example",
                label_ids=[LABEL_CATEGORY_PERSONAL],
            ).reason
        )

        # Subject-keyword promo fallback (no category label at all).
        reasons.append(
            classify_category_heuristic(
                subject="50% off — limited time",
                sender="deals@shop.example",
                label_ids=[LABEL_INBOX],
            ).reason
        )

        # Automated-sender fallback: plain, and urgent-subject escalation
        # (#1266).
        reasons.append(
            classify_category_heuristic(
                subject="Build #4219 passed — all tests green",
                sender="noreply@ci.example.com",
                label_ids=[LABEL_INBOX],
            ).reason
        )
        reasons.append(
            classify_category_heuristic(
                subject="[SEV1] API latency above SLA",
                sender="alerts@acme-corp.example.com",
                label_ids=[LABEL_INBOX],
            ).reason
        )

        # IMPORTANT / STARRED -- all three matched-label combinations (the
        # one reason that's built dynamically, not from a literal).
        reasons.append(
            classify_category_heuristic(
                subject="Re: budget review",
                sender="ceo@company.example",
                label_ids=[LABEL_INBOX, LABEL_IMPORTANT],
            ).reason
        )
        reasons.append(
            classify_category_heuristic(
                subject="Recipe",
                sender="x@example.com",
                label_ids=[LABEL_STARRED],
            ).reason
        )
        reasons.append(
            classify_category_heuristic(
                subject="Recipe",
                sender="x@example.com",
                label_ids=[LABEL_IMPORTANT, LABEL_STARRED],
            ).reason
        )

        # List-Unsubscribe (#2643) -- confident, and commitment-veto.
        reasons.append(
            classify_category_heuristic(
                subject="This week at Northbay Supply",
                sender="news@northbay-supply.example",
                label_ids=[LABEL_INBOX],
                body="Check out what's new this week.",
                has_list_unsubscribe=True,
            ).reason
        )
        reasons.append(
            classify_category_heuristic(
                subject="NOTUS Weekly",
                sender="news@notus.example",
                label_ids=[LABEL_INBOX],
                body=(
                    "Your community pass renewal is due. Confirm by "
                    "Friday or your membership will be suspended."
                ),
                has_list_unsubscribe=True,
            ).reason
        )

        # No heuristic match at all -- final fallback.
        reasons.append(
            classify_category_heuristic(
                subject="Re: meeting at 3pm",
                sender="alice@company.example",
                label_ids=[LABEL_INBOX],
            ).reason
        )

        return reasons

    def test_covers_every_known_branch(self):
        """Sanity check on the harness itself: 16 calls above, one per
        distinct reason value (14 call sites, 3 of which are the dynamic
        IMPORTANT/STARRED combinations sharing one call site) -> 16
        collected, all non-empty."""
        reasons = self._all_reasons()
        assert len(reasons) == 16
        assert all(reasons), f"a branch returned an empty reason: {reasons!r}"

    def test_no_banned_tokens_in_any_emitted_reason(self):
        reasons = self._all_reasons()
        offenders = {text: _reason_violations(text) for text in reasons}
        offenders = {text: hits for text, hits in offenders.items() if hits}
        assert (
            not offenders
        ), f"classifier internals leaked into reason text: {offenders}"

    def test_updates_commitment_veto_matches_2744_example(self):
        """#2744's own live example: Gmail CATEGORY_UPDATES + a deadline
        in the body must read as a fact about the message, not the
        classifier's internal trace."""
        result = classify_category_heuristic(
            subject="Your monthly spending summary",
            sender="alerts@bank.example",
            label_ids=[LABEL_CATEGORY_UPDATES],
            body="Heads up: you have exceeded your budget for this month.",
        )
        assert result.confident is False
        assert "mentions a deadline" in result.reason
        assert not _reason_violations(result.reason)

    def test_important_starred_reasons_are_distinct_and_readable(self):
        """The one dynamically-built reason still names an observable
        fact (what was flagged), never a step the classifier took."""
        important_only = classify_category_heuristic(
            subject="Re: budget review",
            sender="ceo@company.example",
            label_ids=[LABEL_INBOX, LABEL_IMPORTANT],
        ).reason
        starred_only = classify_category_heuristic(
            subject="Recipe",
            sender="x@example.com",
            label_ids=[LABEL_STARRED],
        ).reason
        both = classify_category_heuristic(
            subject="Recipe",
            sender="x@example.com",
            label_ids=[LABEL_IMPORTANT, LABEL_STARRED],
        ).reason
        assert len({important_only, starred_only, both}) == 3
        for text in (important_only, starred_only, both):
            assert not _reason_violations(text)
