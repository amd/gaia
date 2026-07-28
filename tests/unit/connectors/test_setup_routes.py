# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Authored setup-walkthrough content (#2590) — Outlook only, this PR.

Two things are worth defending:

1. There is exactly one source of truth for the Microsoft console walkthrough
   text. Five copies of this walkthrough have drifted apart in production
   once already (#2116: a missing enable-APIs step produced a 403 on first
   use) — the guard here asserts the two never diverge again.
2. Route lookup fails safely: an unknown provider returns ``None``, never a
   crash, so callers can render a defined "no guided walkthrough yet"
   response instead.
"""

from __future__ import annotations

from gaia.connectors import setup_routes as sr


def test_microsoft_route_is_registered():
    assert sr.get_route("microsoft") is sr.MS_PERSONAL
    assert sr.ROUTES["microsoft"] is sr.MS_PERSONAL


def test_unknown_provider_returns_none_not_a_crash():
    assert sr.get_route("google") is None
    assert sr.get_route("does-not-exist") is None


def test_route_has_exactly_one_credential_collecting_step():
    """The Microsoft client ID — never a secret (this is the whole point)."""
    credential_steps = [s for s in sr.MS_PERSONAL.steps if s.collects_credential]
    assert len(credential_steps) == 1
    assert credential_steps[0].id == "client_id"


def test_no_step_or_route_faq_mentions_a_client_secret_as_required():
    """Microsoft's route is public PKCE — the walkthrough must never suggest
    the user needs a secret, even in FAQ answers."""
    for step in sr.MS_PERSONAL.steps:
        for qa in step.faq:
            assert "client secret" not in qa.answer.lower()
    for qa in sr.MS_PERSONAL.faq:
        assert "you need" not in qa.answer.lower() or "secret" not in qa.answer.lower()


def test_steps_are_immutable():
    import dataclasses

    assert dataclasses.is_dataclass(sr.Step)
    step = sr.MS_PERSONAL.steps[0]
    try:
        step.title = "changed"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("Step must be frozen")


def test_render_console_steps_numbers_every_step_in_order():
    rendered = sr.render_console_steps(sr.MS_PERSONAL)
    lines = rendered.split("\n  ")
    # First line keeps its leading spaces from the join; strip for the check.
    numbers = [ln.split(".", 1)[0].strip() for ln in rendered.strip().split("\n")]
    assert numbers == [str(i + 1) for i in range(len(sr.MS_PERSONAL.steps))]


def test_console_steps_text_matches_each_step_instruction_verbatim():
    rendered = sr.render_console_steps(sr.MS_PERSONAL)
    for step in sr.MS_PERSONAL.steps:
        assert step.instruction in rendered
