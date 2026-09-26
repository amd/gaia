# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Authored setup-walkthrough content (#2590, #2594, #4090) — personal
Outlook, work/school Microsoft 365, personal Gmail, and Google Workspace.

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

import pytest

from gaia.connectors import setup_routes as sr


def test_microsoft_route_is_registered():
    assert sr.get_route("microsoft") is sr.MS_PERSONAL
    assert sr.ROUTES["microsoft"] is sr.MS_PERSONAL


def test_unknown_provider_returns_none_not_a_crash():
    assert sr.get_route("does-not-exist") is None
    assert sr.get_route("") is None


def test_google_route_is_registered():
    assert sr.get_route("google") is sr.GOOGLE_PERSONAL
    assert sr.ROUTES["google"] is sr.GOOGLE_PERSONAL


def test_google_route_has_two_credential_collecting_steps_id_and_secret():
    """Unlike Microsoft, Google requires a client secret."""
    credential_steps = [s for s in sr.GOOGLE_PERSONAL.steps if s.collects_credential]
    assert [s.id for s in credential_steps] == ["client_id", "client_secret"]


def test_only_the_google_secret_step_is_marked_sensitive():
    for route in (sr.GOOGLE_PERSONAL, sr.GOOGLE_WORKSPACE):
        for step in route.steps:
            assert step.sensitive == (step.id == "client_secret"), step.id
    for route in (sr.MS_PERSONAL, sr.MS_WORK):
        for step in route.steps:
            assert step.sensitive is False, step.id


def test_google_route_has_no_loopback_only_steps():
    """Google has no device-code flow for a personal client — every step
    applies whichever sign-in mode is asked for."""
    assert not any(s.loopback_only for s in sr.GOOGLE_PERSONAL.steps)
    assert sr.steps_for(sr.GOOGLE_PERSONAL, sign_in=sr.SIGN_IN_DEVICE_CODE) == (
        sr.steps_for(sr.GOOGLE_PERSONAL, sign_in=sr.SIGN_IN_LOOPBACK)
    )


def test_google_route_faq_confirms_a_secret_is_required():
    """The inverse of Microsoft's rule — Google's route must be honest that
    it DOES need a client secret."""
    route_qa = next(
        qa for qa in sr.GOOGLE_PERSONAL.faq if "secret" in qa.question_hints
    )
    assert "requires a client secret" in route_qa.answer.lower()


def test_google_console_steps_render_matches_provider_error(monkeypatch):
    """The one-source-of-truth guard for Google, mirroring the Microsoft
    guard above — providers/google.py must derive its console_steps from
    this same route, never a second hand-maintained copy (#2116)."""
    monkeypatch.delenv("GAIA_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setattr(
        "gaia.connectors.store.peek_provider_credentials", lambda provider: None
    )
    from gaia.connectors.errors import OAuthClientNotConfiguredError
    from gaia.connectors.providers.google import GoogleOAuthProvider

    try:
        GoogleOAuthProvider(client_id="", client_secret="")
    except OAuthClientNotConfiguredError as exc:
        rendered = sr.render_console_steps(sr.GOOGLE_PERSONAL)
        assert exc.console_steps == rendered
    else:
        raise AssertionError("expected OAuthClientNotConfiguredError")


def test_route_has_exactly_one_credential_collecting_step():
    """The Microsoft client ID — never a secret (this is the whole point)."""
    credential_steps = [s for s in sr.MS_PERSONAL.steps if s.collects_credential]
    assert len(credential_steps) == 1
    assert credential_steps[0].id == "client_id"


_SECRET_REQUIRED_PHRASES = (
    "you need a secret",
    "you'll need a secret",
    "you need a client secret",
    "you'll need a client secret",
    "requires a secret",
    "requires a client secret",
    "must provide a secret",
    "must provide a client secret",
)


def test_no_step_or_route_faq_ever_says_a_secret_is_required():
    """Microsoft's route is public PKCE — the walkthrough must never suggest
    the user needs a secret, even in FAQ answers. Truthfully reassuring that
    NO secret is needed is fine and expected (plan's lifted constraint) —
    only a claim that one IS required is the bug."""
    all_faqs = [
        qa
        for route in (sr.MS_PERSONAL, sr.MS_WORK)
        for step in route.steps
        for qa in step.faq
    ]
    all_faqs += list(sr.MS_PERSONAL.faq) + list(sr.MS_WORK.faq)
    assert all_faqs, "expected at least one authored FAQ answer to check"
    for qa in all_faqs:
        answer = qa.answer.lower()
        for phrase in _SECRET_REQUIRED_PHRASES:
            assert phrase not in answer, (qa.answer, phrase)


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


# ---------------------------------------------------------------------------
# Device-code (RFC 8628) has no redirect — the walkthrough must not send a
# device-code user to configure one, but the CLI-facing text (which covers
# whichever route the user takes) keeps it.
# ---------------------------------------------------------------------------


def test_route_has_exactly_one_loopback_only_step_the_redirect_uri():
    loopback_only = [s for s in sr.MS_PERSONAL.steps if s.loopback_only]
    assert len(loopback_only) == 1
    assert loopback_only[0].id == "redirect_uri"


def test_device_code_rendering_drops_the_redirect_uri_step():
    device_rendering = sr.render_console_steps(
        sr.MS_PERSONAL, sign_in=sr.SIGN_IN_DEVICE_CODE
    )
    redirect_step = next(s for s in sr.MS_PERSONAL.steps if s.loopback_only)
    assert redirect_step.instruction not in device_rendering


def test_loopback_rendering_is_the_default_and_keeps_the_redirect_uri():
    default_rendering = sr.render_console_steps(sr.MS_PERSONAL)
    loopback_rendering = sr.render_console_steps(
        sr.MS_PERSONAL, sign_in=sr.SIGN_IN_LOOPBACK
    )
    redirect_step = next(s for s in sr.MS_PERSONAL.steps if s.loopback_only)
    assert default_rendering == loopback_rendering
    assert redirect_step.instruction in default_rendering


def test_device_code_route_has_an_explicit_public_client_flows_step():
    """Relying on 'adding a Mobile & desktop platform sets this implicitly'
    is how a registration that skips it fails device code with the
    confusing AADSTS7000218 ('client_secret is required') — name the step."""
    steps = sr.steps_for(sr.MS_PERSONAL, sign_in=sr.SIGN_IN_DEVICE_CODE)
    ids = [s.id for s in steps]
    assert "public_client_flows" in ids
    assert "redirect_uri" not in ids


def test_steps_for_rejects_an_unknown_sign_in_mechanism():
    import pytest

    with pytest.raises(ValueError):
        sr.steps_for(sr.MS_PERSONAL, sign_in="carrier-pigeon")


# ---------------------------------------------------------------------------
# Work/school Microsoft 365 and Google Workspace (#4090). Both were deferred
# to an issue that was closed as not-planned; these assert the authored
# content exists, is reachable, and is not a copy of its personal sibling.
# ---------------------------------------------------------------------------


def test_every_route_key_is_registered():
    assert set(sr.ROUTES) == {
        "microsoft",
        "microsoft_work",
        "google",
        "google_workspace",
    }


def test_work_microsoft_route_is_registered():
    assert sr.get_route("microsoft_work") is sr.MS_WORK
    assert sr.ROUTES["microsoft_work"] is sr.MS_WORK
    assert sr.MS_WORK.provider == "microsoft_work"


def test_google_workspace_route_is_registered():
    assert sr.get_route("google_workspace") is sr.GOOGLE_WORKSPACE
    assert sr.ROUTES["google_workspace"] is sr.GOOGLE_WORKSPACE
    # The key is the account KIND; the provider field names the connector
    # that actually carries a Workspace mailbox. No google_workspace
    # ConnectorSpec exists, so this asymmetry is deliberate, not a typo.
    assert sr.GOOGLE_WORKSPACE.provider == "google"


def test_work_route_collects_a_client_id_and_never_a_secret():
    credential_steps = [s for s in sr.MS_WORK.steps if s.collects_credential]
    assert [s.id for s in credential_steps] == ["client_id"]
    assert credential_steps[0].verifiable is True


def test_work_route_has_exactly_one_loopback_only_step_the_redirect_uri():
    loopback_only = [s for s in sr.MS_WORK.steps if s.loopback_only]
    assert len(loopback_only) == 1
    assert loopback_only[0].id == "redirect_uri"


def test_work_route_device_code_rendering_drops_the_redirect_uri_step():
    device_rendering = sr.render_console_steps(
        sr.MS_WORK, sign_in=sr.SIGN_IN_DEVICE_CODE
    )
    redirect_step = next(s for s in sr.MS_WORK.steps if s.loopback_only)
    assert redirect_step.instruction not in device_rendering
    assert redirect_step.instruction in sr.render_console_steps(sr.MS_WORK)


def test_work_route_is_not_a_copy_of_the_personal_one():
    """The account-type audience is the step a work user gets wrong — a
    copy-pasted 'any account' / 'common' audience is the failure mode."""
    account_type = next(s for s in sr.MS_WORK.steps if s.id == "account_type")
    assert "any account" not in account_type.instruction.lower()
    assert "common" not in account_type.instruction.lower()
    assert sr.render_console_steps(sr.MS_WORK) != sr.render_console_steps(
        sr.MS_PERSONAL
    )


def test_work_route_answers_the_admin_consent_question():
    """The one real divergence from a personal tenant: user consent can be
    turned off, and the user cannot tell that from the portal."""
    permissions = next(s for s in sr.MS_WORK.steps if s.id == "permissions")
    answers = " ".join(qa.answer.lower() for qa in permissions.faq)
    assert "admin consent" in answers


def test_workspace_route_has_two_credential_collecting_steps_id_and_secret():
    credential_steps = [s for s in sr.GOOGLE_WORKSPACE.steps if s.collects_credential]
    assert [s.id for s in credential_steps] == ["client_id", "client_secret"]


def test_workspace_route_has_no_loopback_only_steps():
    assert not any(s.loopback_only for s in sr.GOOGLE_WORKSPACE.steps)
    assert sr.steps_for(sr.GOOGLE_WORKSPACE, sign_in=sr.SIGN_IN_DEVICE_CODE) == (
        sr.steps_for(sr.GOOGLE_WORKSPACE, sign_in=sr.SIGN_IN_LOOPBACK)
    )


def test_workspace_consent_screen_is_internal_and_personal_stays_external():
    workspace = next(s for s in sr.GOOGLE_WORKSPACE.steps if s.id == "consent_screen")
    personal = next(s for s in sr.GOOGLE_PERSONAL.steps if s.id == "consent_screen")
    assert "Internal" in workspace.instruction
    assert "External" in personal.instruction


def test_personal_google_route_answers_the_workspace_question():
    """The live path: the mailbox chooser offers one 'Gmail' option for both
    account kinds, so a Workspace user walks GOOGLE_PERSONAL and its
    'choose External' instruction is wrong for them."""
    consent = next(s for s in sr.GOOGLE_PERSONAL.steps if s.id == "consent_screen")
    qa = next(q for q in consent.faq if "workspace" in q.question_hints)
    assert "Internal" in qa.answer


@pytest.mark.parametrize("route", list(sr.ROUTES.values()), ids=list(sr.ROUTES))
def test_every_route_is_structurally_complete(route):
    seen_ids = set()
    for step in route.steps:
        assert step.id and step.title and step.instruction, step
        assert step.id not in seen_ids, f"duplicate step id {step.id}"
        seen_ids.add(step.id)
    for qa in [q for s in route.steps for q in s.faq] + list(route.faq):
        assert qa.question_hints, qa
        assert qa.answer.strip(), qa
        for hint in qa.question_hints:
            assert hint == hint.lower(), hint


@pytest.mark.parametrize("route", list(sr.ROUTES.values()), ids=list(sr.ROUTES))
def test_every_route_renders_numbered_console_steps(route):
    rendered = sr.render_console_steps(route)
    numbers = [ln.split(".", 1)[0].strip() for ln in rendered.strip().split("\n")]
    assert numbers == [str(i + 1) for i in range(len(route.steps))]
    for step in route.steps:
        assert step.instruction in rendered


def test_no_comment_defers_scope_to_a_closed_issue():
    """#4090's last acceptance criterion — the module must not promise a
    route as future work, least of all against an issue closed not-planned."""
    import inspect

    source = inspect.getsource(sr).lower()
    for phrase in ("remaining scope", "not here yet", "still earn their own pr"):
        assert phrase not in source, phrase
