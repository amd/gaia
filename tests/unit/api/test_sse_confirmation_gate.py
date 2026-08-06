# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the API-surface tool-confirmation gate (SWSPLAT-37449).

``SSEOutputHandler`` used to inherit ``OutputHandler.confirm_tool_execution``,
which returns ``True`` unconditionally. Because ``agent_registry`` installs that
handler on every API-served agent, the confirmation gate in
``Agent._execute_tool`` was a no-op on the network-exposed ``gaia api`` surface:
prompt injection reaching the agent could drive ``write_file`` /
``run_shell_command`` / any MCP write tool with no user in the loop (CWE-862).

The handler now fails closed and emits a ``tool_confirm_denied`` event so the
caller can see why. An opt-in env escape hatch exists for trusted localhost
deployments; it is off by default and logs loudly when on.
"""

import logging

import pytest

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION, Agent
from gaia.agents.base.console import OutputHandler
from gaia.agents.base.tools import tool
from gaia.api.sse_handler import ALLOW_UNCONFIRMED_TOOLS_ENV, SSEOutputHandler


@pytest.fixture(autouse=True)
def _clear_escape_hatch(monkeypatch):
    """Never inherit the escape hatch from the ambient environment."""
    monkeypatch.delenv(ALLOW_UNCONFIRMED_TOOLS_ENV, raising=False)


class _ApiAgent(Agent):
    """Agent wired the way ``AgentRegistry.get_agent`` wires one, with a canary
    tool whose body records execution."""

    def __init__(self, canary=None, **kwargs):
        # Bound before super().__init__ — _register_tools closes over it.
        self._fired = [] if canary is None else canary
        # AGENT_MODELS passes api_mode; only RoutingAgent accepts it.
        kwargs.pop("api_mode", None)
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return "api"

    def _register_tools(self) -> None:
        fired = self._fired

        @tool
        def write_file(path: str, content: str) -> str:
            """Gated: mutates the filesystem."""
            fired.append(path)
            return "WROTE"

        @tool
        def read_status() -> str:
            """Read-only; never gated."""
            return "OK"


def _make_agent(handler: SSEOutputHandler) -> _ApiAgent:
    from unittest.mock import patch

    with patch("gaia.agents.base.agent.AgentSDK"):
        return _ApiAgent(
            output_handler=handler,
            silent_mode=True,
            skip_lemonade=True,
        )


def _denial_events(handler: SSEOutputHandler):
    return [e for e in handler.get_events() if e["type"] == "tool_confirm_denied"]


class TestFailsClosed:
    def test_gated_tool_denied_and_body_never_runs(self):
        """The canary proves the tool body did not execute, not just that the
        return value looked like a denial."""
        handler = SSEOutputHandler()
        agent = _make_agent(handler)

        result = agent._execute_tool("write_file", {"path": "/tmp/pwn", "content": "x"})

        assert result.get("status") == "denied"
        assert agent._fired == []

    def test_ungated_tool_still_runs(self):
        """The gate must not turn the API into a read-only brick."""
        handler = SSEOutputHandler()
        agent = _make_agent(handler)

        assert agent._execute_tool("read_status", {}) == "OK"
        assert not _denial_events(handler)

    @pytest.mark.parametrize("gated_tool", sorted(TOOLS_REQUIRING_CONFIRMATION))
    def test_no_base_gated_tool_reaches_its_body(self, gated_tool):
        """Every name in the base set must be stopped by the agent gate, not
        just by the handler saying "no" to an arbitrary string."""
        handler = SSEOutputHandler()
        agent = _make_agent(handler)
        agent._tools_registry[gated_tool] = agent._tools_registry["write_file"]

        result = agent._execute_tool(gated_tool, {"path": "/tmp/pwn", "content": "x"})

        assert result.get("status") == "denied"
        assert agent._fired == []

    def test_handler_does_not_inherit_auto_approve(self):
        """Regression pin: the base implementation auto-approves, so inheriting
        it is exactly the bug. The override must be the handler's own."""
        assert OutputHandler.confirm_tool_execution(object(), "x", {}) is True
        assert (
            SSEOutputHandler.confirm_tool_execution
            is not OutputHandler.confirm_tool_execution
        )

    def test_not_advertised_as_blocking(self):
        """``blocking_confirmation`` means 'waits for a user decision'. This
        handler denies outright, so it must stay False."""
        assert SSEOutputHandler.blocking_confirmation is False
        assert SSEOutputHandler().blocking_confirmation is False


class TestDenialEvent:
    def test_event_emitted_with_actionable_message(self):
        handler = SSEOutputHandler()
        agent = _make_agent(handler)

        agent._execute_tool("write_file", {"path": "/tmp/pwn", "content": "x"})

        events = _denial_events(handler)
        assert len(events) == 1
        data = events[0]["data"]
        assert data["tool"] == "write_file"
        assert data["reason"] == "no_approval_channel"

        message = data["message"]
        # What was refused
        assert "write_file" in message
        # Why
        assert "no channel" in message
        # What to do instead
        assert "gaia chat --ui" in message
        # The documented opt-out, with its caveat
        assert ALLOW_UNCONFIRMED_TOOLS_ENV in message
        assert "shared or exposed host" in message

    def test_event_reaches_the_client_stream(self):
        """``should_stream_as_content`` filters the SSE stream in non-debug
        mode — a denial that gets filtered out is a silent denial."""
        handler = SSEOutputHandler(debug_mode=False)
        handler.confirm_tool_execution("run_shell_command", {"command": "rm -rf /"})

        event = _denial_events(handler)[0]
        assert handler.should_stream_as_content(event["type"]) is True

        content = handler.format_event_as_content(event)
        assert "Permission denied" in content
        assert "run_shell_command" in content

    def test_denial_is_logged(self, caplog):
        handler = SSEOutputHandler()
        with caplog.at_level(logging.WARNING, logger="gaia.api.sse_handler"):
            handler.confirm_tool_execution("write_file", {})
        assert "Denied confirmation-gated tool 'write_file'" in caplog.text


class TestEscapeHatch:
    def test_off_by_default(self):
        assert SSEOutputHandler().allow_unconfirmed_tools is False

    def test_env_var_opts_in(self, monkeypatch, caplog):
        monkeypatch.setenv(ALLOW_UNCONFIRMED_TOOLS_ENV, "1")
        with caplog.at_level(logging.WARNING, logger="gaia.api.sse_handler"):
            handler = SSEOutputHandler()
        assert handler.allow_unconfirmed_tools is True
        assert "prompt-injection backstop" in caplog.text

    def test_opted_in_tool_runs_and_warns(self, monkeypatch, caplog):
        monkeypatch.setenv(ALLOW_UNCONFIRMED_TOOLS_ENV, "1")
        handler = SSEOutputHandler()
        agent = _make_agent(handler)

        with caplog.at_level(logging.WARNING, logger="gaia.api.sse_handler"):
            result = agent._execute_tool(
                "write_file", {"path": "/tmp/ok", "content": "x"}
            )

        assert result == "WROTE"
        assert agent._fired == ["/tmp/ok"]
        assert "Auto-approved 'write_file'" in caplog.text
        warnings = [e for e in handler.get_events() if e["type"] == "warning"]
        assert any(
            "Auto-approved 'write_file'" in w["data"]["message"] for w in warnings
        )

    @pytest.mark.parametrize("value", ["0", "true", "yes", ""])
    def test_only_literal_one_opts_in(self, monkeypatch, value):
        """Anything other than "1" must not silently enable the bypass."""
        monkeypatch.setenv(ALLOW_UNCONFIRMED_TOOLS_ENV, value)
        assert SSEOutputHandler().allow_unconfirmed_tools is False

    def test_constructor_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv(ALLOW_UNCONFIRMED_TOOLS_ENV, "1")
        assert (
            SSEOutputHandler(allow_unconfirmed_tools=False).allow_unconfirmed_tools
            is False
        )
        monkeypatch.delenv(ALLOW_UNCONFIRMED_TOOLS_ENV)
        assert (
            SSEOutputHandler(allow_unconfirmed_tools=True).allow_unconfirmed_tools
            is True
        )


class TestRegistryWiring:
    def test_registry_installs_the_failing_closed_handler_as_the_console(self):
        """The bug was that ``AgentRegistry.get_agent`` hands every API-served
        agent this handler. Drive the real registry and assert the handler it
        installs is the one the agent actually consults — ``silent_mode=True``
        in ``AGENT_MODELS`` must not win over ``output_handler``."""
        from unittest.mock import patch

        from gaia.api.agent_registry import AgentRegistry

        registry = AgentRegistry()
        with patch.object(AgentRegistry, "_load_agent_class", return_value=_ApiAgent):
            with patch("gaia.agents.base.agent.AgentSDK"):
                agent = registry.get_agent("gaia-code")

        assert isinstance(agent.console, SSEOutputHandler)
        assert agent._execute_tool("write_file", {"path": "/x", "content": "y"}) == {
            "status": "denied",
            "error": "Tool 'write_file' was denied by the user.",
        }
        assert agent._fired == []


class TestNonStreamingSurface:
    """The OpenAI SDK defaults to non-streaming, which drops the event queue."""

    def test_denial_reason_is_prepended_to_the_response(self):
        from gaia.api.openai_server import _prepend_tool_denials

        handler = SSEOutputHandler()
        agent = _make_agent(handler)
        agent._execute_tool("write_file", {"path": "/tmp/pwn", "content": "x"})

        content = _prepend_tool_denials(agent, "I could not complete that.")

        assert "Refused to run 'write_file'" in content
        assert "gaia chat --ui" in content
        assert content.endswith("I could not complete that.")

    def test_clean_run_is_untouched(self):
        from gaia.api.openai_server import _prepend_tool_denials

        agent = _make_agent(SSEOutputHandler())
        assert _prepend_tool_denials(agent, "all good") == "all good"

    def test_repeated_attempts_are_not_repeated_in_the_response(self):
        """An agent re-tries a denied tool across steps; the caller should not
        get the same 500-char paragraph once per attempt."""
        from gaia.api.openai_server import _prepend_tool_denials

        handler = SSEOutputHandler()
        agent = _make_agent(handler)
        for _ in range(4):
            agent._execute_tool("write_file", {"path": "/tmp/pwn", "content": "x"})

        content = _prepend_tool_denials(agent, "done")

        assert content.count("Refused to run 'write_file'") == 1

    def test_other_events_are_not_consumed(self):
        """``_prepend_tool_denials`` must read the queue, not drain it."""
        from gaia.api.openai_server import _prepend_tool_denials

        handler = SSEOutputHandler()
        agent = _make_agent(handler)
        handler.print_info("keep me")
        agent._execute_tool("write_file", {"path": "/tmp/pwn", "content": "x"})

        _prepend_tool_denials(agent, "done")

        assert any(e["type"] == "info" for e in handler.get_events())


class TestHttpSurface:
    """End-to-end through the real FastAPI app — the surface an attacker hits.

    Pins that the refusal survives the registry, the agent loop, the SSE
    formatter, and the streaming filter, none of which the unit tests above
    exercise together.
    """

    @staticmethod
    def _client(monkeypatch, canary):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from gaia.api import openai_server
        from gaia.api.agent_registry import AgentRegistry

        class _Probe(_ApiAgent):
            def __init__(self, **kwargs):
                super().__init__(canary=canary, **kwargs)

            def process_query(self, query, **kwargs):
                # Stand in for the LLM: emit exactly the tool call an injected
                # document would coerce out of the model.
                result = self._execute_tool(
                    "write_file", {"path": "/tmp/pwn", "content": query}
                )
                return {"status": "success", "result": f"tool result: {result}"}

        real_get_agent = AgentRegistry.get_agent

        def _fake_get_agent(self, model_id):
            """Only the agent class is stubbed — the real registry still builds
            it, so the SSE handler is installed exactly as in production."""
            with patch.object(AgentRegistry, "_load_agent_class", return_value=_Probe):
                with patch("gaia.agents.base.agent.AgentSDK"):
                    return real_get_agent(self, model_id)

        monkeypatch.setattr(AgentRegistry, "get_agent", _fake_get_agent)
        return TestClient(openai_server.app)

    def test_streaming_request_is_refused(self, monkeypatch):
        canary = []
        client = self._client(monkeypatch, canary)

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "stream": True,
                "messages": [{"role": "user", "content": "pwned by injected doc"}],
            },
        )

        assert response.status_code == 200
        assert "Permission denied" in response.text
        assert "gaia chat --ui" in response.text
        assert canary == []

    def test_non_streaming_request_is_refused(self, monkeypatch):
        canary = []
        client = self._client(monkeypatch, canary)

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "gaia-code",
                "stream": False,
                "messages": [{"role": "user", "content": "pwned by injected doc"}],
            },
        )

        content = response.json()["choices"][0]["message"]["content"]
        assert "Refused to run 'write_file'" in content
        assert canary == []


class TestBypassBannerReachesTheOperator:
    """The bypass is invisible once the server is up, so the terminal banner is
    the only place an operator can notice it. It previously lived inside
    ``app.start_server``, which ``gaia api start`` never calls — the warning was
    unreachable on the path operators actually use. These tests pin the banner
    to the real entry points, not to the helper.
    """

    def test_banner_prints_when_bypass_enabled(self, monkeypatch, capsys):
        from gaia.api.sse_handler import (
            ALLOW_UNCONFIRMED_TOOLS_ENV,
            warn_if_unconfirmed_tools_allowed,
        )

        monkeypatch.setenv(ALLOW_UNCONFIRMED_TOOLS_ENV, "1")
        assert warn_if_unconfirmed_tools_allowed() is True
        out = capsys.readouterr().out
        assert ALLOW_UNCONFIRMED_TOOLS_ENV in out
        assert "NO user approval" in out

    @pytest.mark.parametrize("value", [None, "0", "false", "true", "yes"])
    def test_banner_silent_unless_value_is_exactly_one(
        self, monkeypatch, capsys, value
    ):
        """A security control must not be switchable by a truthy accident."""
        from gaia.api.sse_handler import (
            ALLOW_UNCONFIRMED_TOOLS_ENV,
            warn_if_unconfirmed_tools_allowed,
        )

        if value is None:
            monkeypatch.delenv(ALLOW_UNCONFIRMED_TOOLS_ENV, raising=False)
        else:
            monkeypatch.setenv(ALLOW_UNCONFIRMED_TOOLS_ENV, value)
        assert warn_if_unconfirmed_tools_allowed() is False
        assert capsys.readouterr().out == ""

    def test_gaia_api_start_calls_the_banner(self):
        """``gaia api start`` inlines its own startup rather than calling
        ``app.start_server``. Assert the CLI path itself invokes the warning —
        a helper nothing calls is exactly the bug this guards."""
        import inspect

        from gaia import cli

        source = inspect.getsource(cli.handle_api_command)
        assert "warn_if_unconfirmed_tools_allowed()" in source

    def test_start_server_calls_the_banner(self):
        import inspect

        from gaia.api import app

        source = inspect.getsource(app.start_server)
        assert "warn_if_unconfirmed_tools_allowed()" in source
