# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Provider-intent guard (#2164): a request explicitly naming an unconnected
mailbox provider must fail with the connectors framework's NOT_CONNECTED
message BEFORE any tool runs — never silently answer from a different mailbox.

The default behavior (no provider named → scan every connected mailbox) must
stay intact, as must requests naming a provider that IS connected.
"""

from unittest.mock import MagicMock, patch

import pytest
from gaia_agent_email.agent import _detect_targeted_mailboxes


# ---------------------------------------------------------------------------
# Detector — pure-function intent extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        # The live repro from #2164 (matrix N7).
        ("check my Outlook inbox", {"microsoft"}),
        ("check my outlook", {"microsoft"}),
        ("what's new in Outlook?", {"microsoft"}),
        ("triage my hotmail inbox", {"microsoft"}),
        ("summarize my gmail", {"google"}),
        ("search gmail for the invoice", {"google"}),
        ("scan my Google mailbox", {"google"}),
        ("check my gmail and my outlook", {"google", "microsoft"}),
        # No provider named — default multi-mailbox scan must be untouched.
        ("what's in my inbox", set()),
        ("run a pre-scan", set()),
        ("summarize my unread emails", set()),
        # Provider words as an email-address domain are NOT mailbox targeting.
        ("forward this to bob@outlook.com", set()),
        ("reply to alice@gmail.com", set()),
        # Provider words as a SENDER are NOT mailbox targeting.
        ("summarize the email from Microsoft about security", set()),
        ("archive the newsletter from Google", set()),
        # Work Microsoft 365 vocabulary (#2629) — mirrors the alias remap in
        # mailbox_state.PROVIDER_ALIASES, which sends these words to the work
        # connector, never the personal one.
        ("check my Microsoft 365 inbox", {"microsoft_work"}),
        ("check my microsoft365 inbox", {"microsoft_work"}),
        ("search office365 for the invoice", {"microsoft_work"}),
        ("search office 365 for the invoice", {"microsoft_work"}),
        ("check my o365 mailbox", {"microsoft_work"}),
        ("check my m365 mailbox", {"microsoft_work"}),
        ("check my entra mailbox", {"microsoft_work"}),
        # "exchange" is in the same alias table but is ordinary English too
        # ("in exchange for..."), so it only counts paired with a mailbox noun.
        ("check my exchange inbox", {"microsoft_work"}),
        ("scan my exchange mail", {"microsoft_work"}),
        ("I will give you X in exchange for Y", set()),
        ("let's exchange notes after the meeting", set()),
        ("check my exchange rate today", set()),
        # A bare "microsoft" must still mean the personal connector, and the
        # two mailboxes remain simultaneously detectable.
        ("check my microsoft account", {"microsoft"}),
        ("check my microsoft 365 and my outlook", {"microsoft_work", "microsoft"}),
        # "Microsoft Teams"/"Microsoft Office" remain excluded non-mailbox
        # products for the work vocabulary too.
        ("my microsoft teams meeting", set()),
        ("my microsoft office", set()),
    ],
)
def test_detect_targeted_mailboxes(query, expected):
    assert _detect_targeted_mailboxes(query) == expected


# ---------------------------------------------------------------------------
# process_query guard — with only Google connected (injected fake backend)
# ---------------------------------------------------------------------------


class _RecordingMailBackend:
    """GmailBackend-protocol fake that records every method invocation."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append(name)
            return {}

        return _record


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough to construct."""


def _build_agent(tmp_path, monkeypatch, gmail_backend):
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=gmail_backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        start_scheduler=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


@pytest.fixture
def google_only_agent(tmp_path, monkeypatch):
    backend = _RecordingMailBackend()
    agent = _build_agent(tmp_path, monkeypatch, backend)
    try:
        yield agent, backend
    finally:
        agent.close_db()


def test_unconnected_target_is_not_rejected_and_reaches_the_loop(
    google_only_agent, monkeypatch
):
    """#2590 regression: 'check my Outlook inbox' with only Google connected
    used to be rejected here with a canned "go to Settings" message BEFORE
    the agent loop ran — which meant setup_mailbox_access (the guided
    walkthrough) could never be reached no matter how the request was
    phrased. A provider that is simply not connected yet must fall through
    to the loop instead, so the agent can offer to connect it."""
    from gaia.agents.base.agent import Agent

    agent, backend = google_only_agent
    sentinel = {"status": "success", "result": "loop ran"}
    monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: sentinel)

    assert agent.process_query("check my Outlook inbox") is sentinel
    # Still never silently substituted Gmail for the named-but-unconnected
    # provider — the loop decides what to do (e.g. offer setup), not the
    # guard picking a different mailbox.
    assert backend.calls == []


def test_provider_named_while_another_is_connected_still_reaches_the_loop(
    google_only_agent, monkeypatch
):
    """#2590: naming an unconnected provider while a DIFFERENT provider is
    already connected-and-usable must still reach the loop for the named
    provider — not be swallowed by the guard, and not silently answered
    from the already-usable mailbox instead."""
    from gaia.agents.base.agent import Agent

    agent, backend = google_only_agent  # only google connected/usable
    sentinel = {"status": "success", "result": "loop ran"}
    monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: sentinel)

    assert agent.process_query("please set up my outlook mailbox") is sentinel
    assert backend.calls == []


def test_no_provider_request_still_reaches_the_loop(google_only_agent, monkeypatch):
    """No provider named → the guard must NOT fire; the normal loop (which
    scans every connected mailbox) runs."""
    from gaia.agents.base.agent import Agent

    agent, _ = google_only_agent
    sentinel = {"status": "success", "result": "loop ran"}
    monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: sentinel)

    assert agent.process_query("what's in my inbox") is sentinel


def test_connected_provider_target_passes_through(google_only_agent, monkeypatch):
    """Naming a provider that IS connected must not trip the guard."""
    from gaia.agents.base.agent import Agent

    agent, _ = google_only_agent
    sentinel = {"status": "success", "result": "loop ran"}
    monkeypatch.setattr(Agent, "process_query", lambda self, *a, **k: sentinel)

    assert agent.process_query("check my gmail inbox") is sentinel


def _build_pinned_agent(tmp_path, monkeypatch):
    """Both providers connected, session pinned to google — the ONE case the
    guard still rejects pre-flight (#2164's actual intent-conflict purpose,
    unaffected by #2590)."""
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    gmail = _RecordingMailBackend()
    outlook = _RecordingMailBackend()
    cfg = EmailAgentConfig(
        gmail_backend=gmail,
        outlook_backend=outlook,
        mail_provider="google",
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        start_scheduler=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent, gmail, outlook


def test_guard_error_is_surfaced_on_the_console(tmp_path, monkeypatch):
    """The message must reach the console (the SSE stream renders console
    events, not the return value). Uses the pinned-mailbox conflict — the
    only case the guard still rejects pre-flight; an unconnected target no
    longer does (#2590)."""
    agent, gmail, outlook = _build_pinned_agent(tmp_path, monkeypatch)
    try:
        printed = []
        monkeypatch.setattr(
            agent.console, "print_error", lambda msg: printed.append(msg), raising=False
        )

        agent.process_query("check my Outlook inbox")

        assert len(printed) == 1
        assert "pinned" in printed[0]
        assert "microsoft" in printed[0]
        assert gmail.calls == []
        assert outlook.calls == []
    finally:
        agent.close_db()


def test_session_pinned_to_other_mailbox_errors(tmp_path, monkeypatch):
    """Both providers available but the session is pinned to google →
    targeting microsoft must error (not silently serve Gmail), with a
    clear-the-selection remediation instead of a bogus 'connect' one."""
    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    gmail = _RecordingMailBackend()
    outlook = _RecordingMailBackend()
    cfg = EmailAgentConfig(
        gmail_backend=gmail,
        outlook_backend=outlook,
        mail_provider="google",
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        start_scheduler=False,
    )
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    try:
        result = agent.process_query("check my Outlook inbox")
        assert result["status"] == "failed"
        assert "pinned" in result["result"]
        assert gmail.calls == []
        assert outlook.calls == []
    finally:
        agent.close_db()
