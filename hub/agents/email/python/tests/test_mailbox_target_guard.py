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


def test_outlook_target_with_only_google_errors_before_any_tool(
    google_only_agent, monkeypatch
):
    """#2164: 'check my Outlook inbox' with only Google connected must return
    the connectors NOT_CONNECTED error — and never touch the Gmail backend or
    enter the agent loop."""
    from gaia.agents.base.agent import Agent

    agent, backend = google_only_agent

    def _loop_must_not_run(self, *args, **kwargs):
        raise AssertionError(
            "agent loop ran for an unconnected-provider request — the "
            "pre-flight guard did not fire"
        )

    monkeypatch.setattr(Agent, "process_query", _loop_must_not_run)

    result = agent.process_query("check my Outlook inbox")

    assert result["status"] == "failed"
    assert "NOT_CONNECTED" in result["result"]
    assert "microsoft" in result["result"]
    # The misleading substitution: no Gmail call may have happened.
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


def test_guard_error_is_surfaced_on_the_console(google_only_agent, monkeypatch):
    """The message must reach the console (the SSE stream renders console
    events, not the return value)."""
    agent, _ = google_only_agent
    printed = []
    monkeypatch.setattr(
        agent.console, "print_error", lambda msg: printed.append(msg), raising=False
    )

    agent.process_query("check my Outlook inbox")

    assert len(printed) == 1
    assert "NOT_CONNECTED" in printed[0]
    assert "microsoft" in printed[0]


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
