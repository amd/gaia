# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Consolidated cross-surface never-auto-send guard (#1264).

The invariant: **email is never sent without explicit confirmation, on any
surface.** It is enforced independently at three layers, each with its own
focused test:

- agent tool loop — ``tests/unit/agents/test_email_agent_confirmation.py``
- REST API (#1229) — ``tests/test_api.py::TestEmailSendConfirmationGate``
- MCP stdio (#1104) — ``tests/mcp/test_email_mcp_stdio_parity.py``

Those tests each prove one surface. This file is the **regression guard for
the invariant as a whole**: it exercises the actual gate on *all three*
surfaces in one place, so a future change that quietly removes the gate from
one surface (while the other two stay green) fails here. It reuses the real
gates — it does not re-implement them.

Everything runs in-process and deterministically: no Lemonade, no network, no
live mail, and (deliberately) no MCP stdio subprocess. The stdio path is
covered by the parity test; spawning it here would also trip the
``tests/unit/mcp`` package shadowing the installed ``mcp`` SDK once
``repo_root`` is on ``sys.path``. Exercising the MCP gate through the agent
class directly is both faster and immune to that.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

# EmailTriageAgent + its REST/MCP surfaces ship as the standalone
# gaia-agent-email wheel (#1102); skip when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

# ---------------------------------------------------------------------------
# Shared tokenless payload — the SAME message is offered to every surface
# without a confirmation token. Every surface must refuse it.
# ---------------------------------------------------------------------------

_TO = "attacker@evil.example.com"
_SUBJECT = "Re: Q3 budget"
_BODY = "Wire $10,000 to account 12345."


# ===========================================================================
# Layer 1 — agent tool loop
# ===========================================================================


class _DenyingConsole:
    """A console that refuses every confirmation, recording the prompts.

    Stands in for the real attended consoles (CLI ``AgentConsole`` prompts the
    user; the UI ``SSEOutputHandler`` blocks on a frontend modal). Here the
    user effectively says "no" — which is exactly the condition under which
    the agent must not send.
    """

    def __init__(self) -> None:
        self.prompts: List[str] = []

    def confirm_tool_execution(self, tool_name: str, tool_args: Dict[str, Any]) -> bool:
        self.prompts.append(tool_name)
        return False

    # The base Agent only calls ``confirm_tool_execution`` on the gate path;
    # everything else the console exposes is a no-op for this test. Delegate
    # the rest to the silent console so construction succeeds.
    def __getattr__(self, name: str):  # pragma: no cover - passthrough
        from gaia.agents.base.console import SilentConsole

        return getattr(SilentConsole(), name)


def _make_probe_agent(console, sent_counter: Dict[str, int]):
    """A minimal real ``Agent`` that registers a sentinel ``send_now`` tool.

    The gate under test lives in the *base* ``Agent._execute_tool`` and is
    keyed on the tool name being in ``confirmation_required_tools()`` (the
    agent's own ``CONFIRMATION_REQUIRED_TOOLS`` merged with the generic base
    set, #1440) — it is surface-agnostic. So a tiny agent with a ``send_now``
    tool declared in that set exercises
    the identical code path the email agent's ``send_now`` flows through,
    without needing Lemonade or live Gmail (``EmailTriageAgent`` hard-wires a
    Lemonade connection at construction). The sentinel increments a counter
    when its body runs, letting us prove the body NEVER runs on a denial.
    """
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.tools import tool

    class _ProbeAgent(Agent):
        AGENT_ID = "never-auto-send-probe"
        # Declare the gated tool on the agent (#1440): agent-specific gated
        # tools now live on the owning class, merged with the generic base set.
        CONFIRMATION_REQUIRED_TOOLS = frozenset({"send_now"})

        def __init__(self, **kwargs: Any):
            kwargs.setdefault("skip_lemonade", True)
            kwargs.setdefault("silent_mode", True)
            self._console_override = console
            super().__init__(**kwargs)

        def _create_console(self):
            return self._console_override

        def _get_system_prompt(self) -> str:
            return "never-auto-send probe"

        def _register_tools(self) -> None:
            @tool
            def send_now(to: str, subject: str, body: str) -> str:
                """Send an email immediately. Requires user confirmation."""
                sent_counter["n"] += 1
                return "SENT"

            @tool
            def list_inbox() -> str:
                """List the inbox (read-only; never gated)."""
                return "INBOX"

    return _ProbeAgent()


class TestAgentLayerGate:
    """The base agent loop refuses to run a gated send when confirmation is
    withheld, and the real email send tools are registered for gating."""

    @pytest.mark.parametrize(
        "tool_name",
        ["send_draft", "send_now", "forward_message"],
    )
    def test_real_send_tools_are_gated(self, tool_name):
        """The actual email send/forward tool names are in the gate set.

        This binds the abstract gate mechanism to the concrete email tools:
        if a future refactor drops ``send_now`` (etc.) from the set, this
        fails even though the mechanism still works for other tools.
        """
        from gaia_agent_email.agent import EmailTriageAgent

        assert tool_name in EmailTriageAgent.confirmation_required_tools()

    def test_denied_confirmation_blocks_the_send(self):
        """A withheld confirmation → ``denied`` status, and the send tool's
        body is never executed (no auto-send)."""
        sent = {"n": 0}
        console = _DenyingConsole()
        agent = _make_probe_agent(console, sent)

        result = agent._execute_tool(
            "send_now", {"to": _TO, "subject": _SUBJECT, "body": _BODY}
        )

        assert result.get("status") == "denied"
        assert sent["n"] == 0, "send tool body ran despite a denied confirmation"
        assert console.prompts == ["send_now"], "the gate did not prompt for send_now"

    def test_non_gated_tool_runs_without_confirmation(self):
        """Positive control: a read-only tool is NOT gated — it runs without
        any confirmation prompt. Proves the gate is selective, not a blanket
        block that would make the ``denied`` result above meaningless."""
        sent = {"n": 0}
        console = _DenyingConsole()
        agent = _make_probe_agent(console, sent)

        result = agent._execute_tool("list_inbox", {})

        assert result == "INBOX"
        assert console.prompts == [], "a read-only tool was sent through the gate"


# ===========================================================================
# Layer 2 — REST API (#1229)
# ===========================================================================


def _addr(email: str):
    from gaia_agent_email.contract import EmailAddress

    return EmailAddress(email=email)


class _FakeRestBackend:
    """In-process send backend so the authorized REST path never touches live
    mail. Returns a Gmail-API-shaped ``{"id": ...}`` like the live backend."""

    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    def send_message(
        self, *, to: str, subject: str, body: str, headers: Dict[str, str] = None
    ) -> Dict[str, Any]:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return {"id": f"rest_fake_{len(self.sent)}"}


class TestRestLayerGate:
    """``POST /v1/email/send`` is rejected (403) without a valid, payload-bound
    confirmation token. Exercised by calling the route handler directly."""

    @pytest.fixture(autouse=True)
    def _require_api(self):
        pytest.importorskip("fastapi", reason="API extra not installed ([api])")

    def _send(self, request):
        import anyio
        from gaia_agent_email import api_routes as email_routes

        return anyio.run(email_routes.send_email, request)

    def test_send_without_token_is_403(self):
        from fastapi import HTTPException
        from gaia_agent_email.api_routes import EmailSendRequest

        req = EmailSendRequest(to=[_addr(_TO)], subject=_SUBJECT, body=_BODY)
        with pytest.raises(HTTPException) as excinfo:
            self._send(req)
        assert excinfo.value.status_code == 403
        assert "confirm" in str(excinfo.value.detail).lower()

    def test_draft_then_send_with_token_succeeds(self, monkeypatch):
        import anyio
        from gaia_agent_email import api_routes as email_routes
        from gaia_agent_email.api_routes import EmailDraftRequest, EmailSendRequest

        backend = _FakeRestBackend()
        monkeypatch.setattr(email_routes, "resolve_send_backend", lambda: backend)

        draft = anyio.run(
            email_routes.draft_reply,
            EmailDraftRequest(to=[_addr(_TO)], subject=_SUBJECT, body=_BODY),
        )
        token = draft.confirmation_token
        assert token

        sent = self._send(
            EmailSendRequest(
                to=[_addr(_TO)],
                subject=_SUBJECT,
                body=_BODY,
                confirmation_token=token,
            )
        )
        assert sent.sent is True
        assert sent.sent_id
        assert len(backend.sent) == 1

    def test_token_does_not_authorize_a_different_payload(self, monkeypatch):
        """A token minted for one message cannot send different content."""
        import anyio
        from fastapi import HTTPException
        from gaia_agent_email import api_routes as email_routes
        from gaia_agent_email.api_routes import EmailDraftRequest, EmailSendRequest

        backend = _FakeRestBackend()
        monkeypatch.setattr(email_routes, "resolve_send_backend", lambda: backend)

        draft = anyio.run(
            email_routes.draft_reply,
            EmailDraftRequest(
                to=[_addr("alice@example.com")], subject="Re: budget", body="OK"
            ),
        )
        token = draft.confirmation_token

        # Same token, attacker-swapped recipient + body → rejected.
        with pytest.raises(HTTPException) as excinfo:
            self._send(
                EmailSendRequest(
                    to=[_addr(_TO)],
                    subject=_SUBJECT,
                    body=_BODY,
                    confirmation_token=token,
                )
            )
        assert excinfo.value.status_code == 403
        assert backend.sent == [], "a swapped payload reached the backend"


# ===========================================================================
# Layer 3 — MCP stdio server (#1104)
# ===========================================================================


class TestMcpLayerGate:
    """``EmailTriageMCPAgent.send_email`` refuses to send without a valid,
    payload-bound token. Exercised in-process (the stdio transport itself is
    covered by ``tests/mcp/test_email_mcp_stdio_parity.py``)."""

    def _agent(self):
        from gaia_agent_email.mcp_server import EmailTriageMCPAgent

        # skip_lemonade defaults True inside the agent; triage/draft/send are
        # deterministic and need no model.
        return EmailTriageMCPAgent()

    def test_send_without_token_is_rejected(self):
        agent = self._agent()
        out = agent.execute_mcp_tool(
            "send_email",
            {"to": [{"email": _TO}], "subject": _SUBJECT, "body": _BODY},
        )
        assert out.get("sent") is not True
        assert out.get("error"), "missing actionable error on rejected send"
        assert "confirm" in out["error"].lower()

    def test_draft_then_send_handshake_succeeds(self, monkeypatch):
        monkeypatch.setenv("GAIA_EMAIL_MCP_FAKE_SEND", "1")
        agent = self._agent()
        payload = {"to": [{"email": _TO}], "subject": _SUBJECT, "body": _BODY}

        draft = agent.execute_mcp_tool("draft_reply", dict(payload))
        token = draft["confirmation_token"]
        assert token

        sent = agent.execute_mcp_tool(
            "send_email", {**payload, "confirmation_token": token}
        )
        assert sent.get("sent") is True
        assert sent.get("sent_id")

    def test_token_does_not_authorize_a_different_payload(self, monkeypatch):
        monkeypatch.setenv("GAIA_EMAIL_MCP_FAKE_SEND", "1")
        agent = self._agent()

        draft = agent.execute_mcp_tool(
            "draft_reply",
            {
                "to": [{"email": "alice@example.com"}],
                "subject": "Re: budget",
                "body": "OK",
            },
        )
        token = draft["confirmation_token"]

        out = agent.execute_mcp_tool(
            "send_email",
            {
                "to": [{"email": _TO}],
                "subject": _SUBJECT,
                "body": _BODY,
                "confirmation_token": token,
            },
        )
        assert out.get("sent") is not True, "token authorized a swapped payload"
        assert out.get("error")


# ===========================================================================
# Cross-surface meta-assertion — the same tokenless send is refused EVERYWHERE
# ===========================================================================


class TestCrossSurfaceInvariant:
    """One tokenless message, offered to all three surfaces at once. Every
    surface must refuse it. This is the single assertion that fails if ANY one
    surface ever starts auto-sending while the others stay gated — the exact
    regression this guard exists to catch."""

    def test_tokenless_send_is_refused_on_every_surface(self, monkeypatch):
        pytest.importorskip("fastapi", reason="API extra not installed ([api])")
        import anyio
        from gaia_agent_email import api_routes as email_routes
        from gaia_agent_email.api_routes import EmailSendRequest
        from gaia_agent_email.mcp_server import EmailTriageMCPAgent

        refusals: Dict[str, bool] = {}

        # -- agent surface: denied + impl never runs --
        sent_counter = {"n": 0}
        agent = _make_probe_agent(_DenyingConsole(), sent_counter)
        agent_result = agent._execute_tool(
            "send_now", {"to": _TO, "subject": _SUBJECT, "body": _BODY}
        )
        refusals["agent"] = (
            agent_result.get("status") == "denied" and sent_counter["n"] == 0
        )

        # -- REST surface: 403, no backend touched (and the gate fires before
        #    backend resolution, so a poisoned resolver must NOT be reached) --
        def _fail_resolver():
            raise AssertionError("REST backend resolved before the gate")

        monkeypatch.setattr(email_routes, "resolve_send_backend", _fail_resolver)
        from fastapi import HTTPException

        try:
            anyio.run(
                email_routes.send_email,
                EmailSendRequest(to=[_addr(_TO)], subject=_SUBJECT, body=_BODY),
            )
            refusals["rest"] = False
        except HTTPException as exc:
            refusals["rest"] = exc.status_code == 403

        # -- MCP surface: structured error, not sent --
        mcp_out = EmailTriageMCPAgent().execute_mcp_tool(
            "send_email",
            {"to": [{"email": _TO}], "subject": _SUBJECT, "body": _BODY},
        )
        refusals["mcp"] = mcp_out.get("sent") is not True and bool(mcp_out.get("error"))

        assert all(refusals.values()), (
            "never-auto-send invariant broken on at least one surface: " f"{refusals}"
        )
