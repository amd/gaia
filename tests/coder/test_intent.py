# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for :mod:`gaia.coder.intent` (Phase 5, §15.4, §15.8 P9).

No test makes a real Anthropic API call — every classifier invocation
passes a canned ``llm`` callable that returns deterministic JSON.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from gaia.coder import intent as intent_mod
from gaia.coder import trust as trust_mod
from gaia.coder.stores import audit as audit_store
from gaia.coder.stores import em_inbox as em_inbox_store
from gaia.coder.stores import feedback as feedback_store


def _make_llm(mapping: dict[str, dict]) -> Callable[[str], str]:
    """Build a fake ``llm`` callable that returns canned JSON by keyword match.

    The mapping is ``{substring-in-message: response-dict}``. We iterate in
    insertion order and return the first match so tests can pin a specific
    response by including a unique keyword in the input.
    """

    def call(prompt: str) -> str:
        for keyword, payload in mapping.items():
            if keyword.lower() in prompt.lower():
                return json.dumps(payload)
        # If nothing matched the response is deliberately free_form — the
        # test is probably asserting that specific bail-out path.
        return json.dumps({"intent": "free_form", "args": {}, "confidence": 0})

    return call


# ---------------------------------------------------------------------------
# Canonical phrase mapping (§15.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase, expected_intent",
    [
        ("enable self-edit", "enable_self_edit_session"),
        ("enable self-edit permanently", "enable_self_edit_permanent"),
        ("disable self-edit", "disable_self_edit"),
        ("promote to tier 3", "promote_tier"),
        ("what's my tier", "what_tier"),
    ],
)
def test_intent_classifier_maps_canonical_phrases(
    phrase: str, expected_intent: str
) -> None:
    """Five §15.4 canonical phrases route to the correct intent.

    The LLM is a hand-rolled lookup so this test is really asserting two
    things at once: (a) our prompt-build + parse pipeline hands off
    cleanly, and (b) the returned intent name survives :func:`dispatch`
    without being coerced to free_form.
    """
    llm = _make_llm(
        {
            "enable self-edit permanently": {
                "intent": "enable_self_edit_permanent",
                "args": {},
                "confidence": 95,
            },
            "enable self-edit": {
                "intent": "enable_self_edit_session",
                "args": {},
                "confidence": 92,
            },
            "disable self-edit": {
                "intent": "disable_self_edit",
                "args": {},
                "confidence": 93,
            },
            "promote to tier": {
                "intent": "promote_tier",
                "args": {"to_tier": 3},
                "confidence": 97,
            },
            "what's my tier": {
                "intent": "what_tier",
                "args": {},
                "confidence": 90,
            },
        }
    )
    result = intent_mod.classify_intent(phrase, llm=llm)
    assert result["intent"] == expected_intent
    assert result["confidence"] >= intent_mod.MIN_CONFIDENCE


def test_intent_low_confidence_bails_to_freeform() -> None:
    """§15.4: confidence < 70 must coerce to free_form."""

    def llm(_prompt: str) -> str:
        return json.dumps(
            {
                "intent": "promote_tier",
                "args": {"to_tier": 3},
                "confidence": 45,
            }
        )

    result = intent_mod.classify_intent("uhh maybe go higher", llm=llm)
    assert result["intent"] == "free_form"
    assert result["confidence"] == 0
    # The original classification is preserved in args for audit-log value.
    assert result["args"]["original"]["intent"] == "promote_tier"


def test_intent_unknown_intent_name_coerced_to_freeform() -> None:
    """Model hallucinations (non-catalog intents) fall through to free_form."""

    def llm(_prompt: str) -> str:
        return json.dumps({"intent": "eat_pizza", "args": {}, "confidence": 99})

    result = intent_mod.classify_intent("order a pizza", llm=llm)
    assert result["intent"] == "free_form"


def test_intent_malformed_response_raises() -> None:
    """Non-JSON responses raise loudly — they are a prompt-class bug."""

    def llm(_prompt: str) -> str:
        return "not json at all"

    with pytest.raises(ValueError, match="no JSON object"):
        intent_mod.classify_intent("hi", llm=llm)


def test_build_prompt_includes_intent_table() -> None:
    """The composed prompt lists every intent name so the model can pick one."""
    prompt = intent_mod.build_prompt("hello")
    for name in intent_mod.INTENT_NAMES:
        if name == "free_form":
            continue
        assert name in prompt, f"prompt missing intent {name!r}"
    assert 'Message: """hello"""' in prompt


def test_build_prompt_allowed_subset_filters_intents() -> None:
    prompt = intent_mod.build_prompt("hi", allowed=["what_tier", "pause"])
    assert "what_tier" in prompt
    assert "pause" in prompt
    assert "promote_tier" not in prompt


def test_build_prompt_escapes_triple_quotes() -> None:
    """Embedded ``\"\"\"`` must not escape the XML-ish delimiter."""
    prompt = intent_mod.build_prompt('hi """ there')
    # Exactly ONE triple-quoted span should exist (the delimiter); the
    # user's version is rewritten to single-quotes.
    assert prompt.count('"""') == 2
    assert "'''" in prompt


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, current_tier: int = 3) -> intent_mod.HandlerContext:
    cfg_path = tmp_path / "em.toml"
    cfg = trust_mod.EMConfig(
        em_handle="kovtcharov-amd",
        em_channel="cli",
        current_tier=current_tier,
    )
    trust_mod.save_em_config(cfg_path, cfg)
    return intent_mod.HandlerContext(
        em_cfg_path=cfg_path,
        em_cfg=cfg,
        inbox_conn=em_inbox_store.open_store(tmp_path / "inbox.db"),
        feedback_conn=feedback_store.open_store(tmp_path / "fb.db"),
        audit_conn=audit_store.open_store(tmp_path / "audit.db"),
        session={},
    )


def test_dispatch_enable_self_edit_session_sets_session_flag(
    tmp_path: Path,
) -> None:
    ctx = _make_ctx(tmp_path)
    reply = intent_mod.dispatch(
        {
            "intent": "enable_self_edit_session",
            "args": {},
            "confidence": 90,
        },
        ctx,
    )
    assert "Dev mode is ON" in reply
    assert ctx.session["dev_mode_session"] is True
    # em.toml is NOT mutated — session-scoped grants don't persist.
    reloaded = trust_mod.load_em_config(ctx.em_cfg_path)
    assert reloaded.dev_mode_self_edit is False


def test_dispatch_enable_permanent_writes_em_toml(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    reply = intent_mod.dispatch(
        {
            "intent": "enable_self_edit_permanent",
            "args": {},
            "confidence": 98,
        },
        ctx,
    )
    assert "em.toml" in reply
    reloaded = trust_mod.load_em_config(ctx.em_cfg_path)
    assert reloaded.dev_mode_self_edit is True
    assert reloaded.dev_mode_enabled_at is not None
    assert reloaded.dev_mode_enabled_reason is not None


def test_dispatch_promote_happy_path(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    reply = intent_mod.dispatch(
        {
            "intent": "promote_tier",
            "args": {
                "to_tier": 4,
                "reason": "shipped 14 PRs clean",
                "em_signature": "kovtcharov-amd",
            },
            "confidence": 95,
        },
        ctx,
    )
    assert "tier 4" in reply
    assert ctx.em_cfg.current_tier == 4


def test_dispatch_promote_rejects_bad_signature(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(trust_mod.TrustError):
        intent_mod.dispatch(
            {
                "intent": "promote_tier",
                "args": {
                    "to_tier": 4,
                    "reason": "nope",
                    "em_signature": "wrong-user",
                },
                "confidence": 99,
            },
            ctx,
        )


def test_dispatch_free_form_returns_none(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    reply = intent_mod.dispatch(
        {"intent": "free_form", "args": {}, "confidence": 0}, ctx
    )
    assert reply is None
