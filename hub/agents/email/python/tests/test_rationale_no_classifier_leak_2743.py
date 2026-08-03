# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""User-facing rationale must never leak classifier internals — the
read_tools.py / attention_tools.py half of #2744.

#2744 fixed ``triage_heuristics.py``'s own ``HeuristicResult(reason=...)``
literals and added a guard scoped to that one module (AST scan over
``HeuristicResult(...)`` call sites). ``read_tools.py`` and
``attention_tools.py`` compose their OWN ``rationale``/``why`` strings on
top of a heuristic's already-clean reason — wrapping it in session-
preference language, an SLM-override annotation, or a needs_review
fallback — and that composition is exactly what #2744's constructor-scoped
scan cannot see, since neither file calls ``HeuristicResult(...)``.

Leaks fixed here (#2743 redirect, checkpoint review):

- ``read_tools._apply_session_preferences``'s priority/low-priority sender
  wrapping (was: ``"priority sender (session preference): X -- raises
  salience only, category unchanged (content classified it Y: Z)"``).
- ``read_tools.triage_inbox_impl``'s ``force_llm`` bypass annotation (was:
  ``"forced LLM bypass (was: X)"``).
- ``read_tools.triage_inbox_impl``'s SLM-override annotation (was:
  ``"SLM classified as X (heuristic said: Y)"``).
- ``attention_tools._needs_review_item``'s unconfident fallback (was:
  ``"the heuristic was not confident about this message's category"``).

Mirrors ``tests/unit/email/test_triage_heuristics.py``'s #2744 guard shape:
a static AST scan over every literal already in the source (so a future
branch reusing a banned token fails here immediately, before any test
happens to exercise it), plus a behavioral sweep driving the real
composition functions and checking what they actually emit.
"""

from __future__ import annotations

import ast
import base64
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# parents[0]=tests/, [1]=python/, [2]=email/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools import attention_tools as attention_tools_module  # noqa: E402
from gaia_agent_email.tools import read_tools as read_tools_module  # noqa: E402
from gaia_agent_email.tools.attention_tools import _needs_review_item  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    _apply_session_preferences,
    triage_inbox_impl,
)
from gaia_agent_email.tools.triage_heuristics import (  # noqa: E402
    LABEL_CATEGORY_PROMOTIONS,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# Shared banned-token vocabulary (#2744) -- a classifier-internal step or
# code-style separator, never a fact about the message.
# ---------------------------------------------------------------------------

_BANNED_SUBSTRINGS = ("category_", "escalating", "llm", "heuristic")
_BANNED_SEPARATOR = " -- "


def _reason_violations(text: str) -> List[str]:
    """Return which banned tokens (if any) appear in a rendered reason."""
    lowered = text.lower()
    hits = [token for token in _BANNED_SUBSTRINGS if token in lowered]
    if _BANNED_SEPARATOR in text:
        hits.append(repr(_BANNED_SEPARATOR))
    return hits


# ---------------------------------------------------------------------------
# Static guard: every rationale=/why= string literal already in the source.
# ---------------------------------------------------------------------------


def _string_literals_in(node: ast.AST) -> List[str]:
    """Every string-constant segment reachable inside node -- a plain
    literal, or the literal text segments of an f-string (the interpolated
    ``{...}`` parts are opaque at parse time and are not literals)."""
    out: List[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def _rationale_literals_from_module(module: Any) -> List[str]:
    """Statically collect every string literal appearing in a
    ``<subscript>["rationale"|"why"] = <value>`` assignment or a
    ``{"rationale"|"why": <value>, ...}`` dict-literal entry anywhere in
    module's source.

    Neither ``read_tools.py`` nor ``attention_tools.py`` calls
    ``HeuristicResult(reason=...)`` (that's ``triage_heuristics.py``'s own
    shape, #2744's scan target) — these two compose the field via a plain
    dict assignment or literal instead, so the scan looks for THOSE shapes.
    """
    source = inspect.getsource(module)
    tree = ast.parse(source)
    literals: List[str] = []
    keys = ("rationale", "why")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in keys
                ):
                    literals.extend(_string_literals_in(node.value))
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in keys:
                    literals.extend(_string_literals_in(value))
    return literals


class TestRationaleLiteralsNeverLeakClassifierInternals:
    """Static guard (#2743 redirect / #2744): every ``rationale=``/``why=``
    string literal already in read_tools.py or attention_tools.py must read
    as a fact about the message, never a step the classifier took. Fails
    the moment a new branch's literal reuses a banned token, even before
    any test happens to exercise that branch."""

    def test_scan_finds_the_known_literals(self):
        """Sanity check on the scan itself, so a change to how these two
        modules compose ``rationale``/``why`` (breaking the scan) fails
        loudly here instead of silently disabling the guard below."""
        literals = _rationale_literals_from_module(
            read_tools_module
        ) + _rationale_literals_from_module(attention_tools_module)
        assert len(literals) >= 8, (
            "expected the AST scan to find every rationale=/why= literal "
            f"in read_tools.py and attention_tools.py -- got {literals!r}; "
            "either a branch was removed/rewritten or the scan itself broke"
        )

    def test_no_banned_tokens_in_source_literals(self):
        literals = _rationale_literals_from_module(
            read_tools_module
        ) + _rationale_literals_from_module(attention_tools_module)
        offenders = {text: _reason_violations(text) for text in literals}
        offenders = {text: hits for text, hits in offenders.items() if hits}
        assert (
            not offenders
        ), f"classifier internals leaked into rationale/why text: {offenders}"


# ---------------------------------------------------------------------------
# Behavioral guard: drive the real composition functions, check what they
# actually emit at runtime -- not just what the source text says.
# ---------------------------------------------------------------------------


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _msg(
    msg_id: str,
    *,
    subject: str = "Neutral subject, no keyword signal",
    sender: str = "alice@example.com",
    label_ids: Optional[List[str]] = None,
    body: str = "Some neutral body content with no keyword signal at all.",
) -> Dict[str, Any]:
    return {
        "id": msg_id,
        "threadId": msg_id,
        "labelIds": label_ids or ["INBOX"],
        "snippet": body[:200],
        "internalDate": "1750000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"data": _b64url(body), "size": len(body.encode("utf-8"))},
        },
        "sizeEstimate": len(body),
    }


def _slm_by_id(mapping: Dict[str, str]):
    def _classifier(*, subject, sender, body, message_id=""):
        category = mapping.get(message_id)
        if category is None:
            return None
        return {"category": category, "confidence": 0.9, "source": "slm"}

    return _classifier


class TestEveryEmittedRationaleIsClean:
    """Drive the actual composition functions and check what they emit at
    runtime, including the one leak class #2744's own guard cannot reach:
    a heuristic's already-clean reason getting WRAPPED in classifier-
    internal language one layer up."""

    def test_priority_sender_rationale_is_clean(self):
        decision = {
            "from": "boss@example.com",
            "category": "FYI",
            "rationale": "Looks like an automated update",
        }
        prefs = {"priority_senders": {"boss@example.com"}, "low_priority_senders": set()}
        out = _apply_session_preferences(decision, prefs)
        assert not _reason_violations(out["rationale"]), out["rationale"]
        # #2632 requires the rule stated explicitly ("category unchanged"),
        # not merely the absence of an urgency claim.
        assert (
            out["rationale"]
            == "From a priority sender · category unchanged · Looks like an automated update"
        )

    def test_low_priority_sender_rationale_is_clean(self):
        decision = {
            "from": "newsletter@example.com",
            "category": "FYI",
            "rationale": "Looks like an automated update",
        }
        prefs = {
            "priority_senders": set(),
            "low_priority_senders": {"newsletter@example.com"},
        }
        out = _apply_session_preferences(decision, prefs)
        assert not _reason_violations(out["rationale"]), out["rationale"]
        assert (
            out["rationale"] == "From a low-priority sender · Looks like an automated update"
        )

    def test_force_llm_bypass_never_wraps_the_rationale(self):
        """force_llm used to wrap a confident heuristic's reason in
        "forced LLM bypass (was: X)" -- pure internal pipeline state the
        user has no reason to know. The fix shows the SAME reason
        force_llm=False would, unconditionally: a DIFFERENTIAL check
        against that baseline, not an assertion that the underlying
        heuristic text is itself clean -- that text's own cleanliness is
        triage_heuristics.py's responsibility, covered by its own #2744
        guard (a separate, not-yet-merged fix; irrelevant to what THIS
        module is responsible for: never adding a wrapper on top)."""
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(
            _msg(
                "promo-1",
                subject="50% off this weekend!",
                sender="deals@shop.example",
                label_ids=["INBOX", LABEL_CATEGORY_PROMOTIONS],
            )
        )
        baseline = triage_inbox_impl(gmail, max_messages=10, force_llm=False)
        bypassed = triage_inbox_impl(gmail, max_messages=10, force_llm=True)
        baseline_rationale = baseline["results"][0]["rationale"]
        bypassed_rationale = bypassed["results"][0]["rationale"]
        assert bypassed_rationale == baseline_rationale, (
            "force_llm must not change the rationale text at all -- got "
            f"{bypassed_rationale!r}, want the same {baseline_rationale!r} "
            "force_llm=False already produces"
        )
        assert "forced" not in bypassed_rationale.lower()
        assert "bypass" not in bypassed_rationale.lower()

    def test_slm_override_rationale_is_clean(self):
        """#2744's own leak class: an SLM override used to append
        "(heuristic said: X)" -- the heuristic's unconfident-escalation
        reason juxtaposed against a CONFIDENT verdict it now contradicts.
        The fix drops that parenthetical entirely."""
        gmail = FakeGmailBackend(user_email="me@example.com")
        gmail.add_message(_msg("nosig-1"))  # no label/keyword signal -> heuristic unconfident
        out = triage_inbox_impl(
            gmail,
            max_messages=10,
            slm_classifier=_slm_by_id({"nosig-1": "PROMOTIONAL"}),
        )
        results = out["results"]
        assert len(results) == 1
        assert results[0]["source"] == "slm"
        rationale = results[0]["rationale"]
        assert not _reason_violations(rationale), rationale
        assert rationale == "SLM classified as PROMOTIONAL"

    def test_needs_review_fallback_why_is_clean(self):
        item = _needs_review_item({"id": "m1", "subject": "Random note"}, provider=None)
        assert not _reason_violations(item["why"]), item["why"]
        assert item["why"] == "Not sure how to categorize this one"

    def test_needs_review_fallback_prefers_a_real_rationale_when_present(self):
        item = _needs_review_item(
            {"id": "m1", "subject": "Random note", "rationale": "Looks like an automated update"},
            provider=None,
        )
        assert item["why"] == "Looks like an automated update"
