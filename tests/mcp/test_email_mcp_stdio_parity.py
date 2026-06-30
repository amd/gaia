# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""REST <-> MCP-stdio parity for the email triage agent (#1104).

Acceptance criterion (#1104): the agent is invocable over MCP **stdio** with
the **same capabilities as REST** (#1229). This test is the guard for that
claim — it drives the *same* triage operation over two surfaces:

1. the REST-side ``EmailTriageService`` (the FastAPI-free service the
   ``/v1/email/triage`` endpoint calls), and
2. the new MCP **stdio** server (``python -m gaia_agent_email.mcp_server
   --transport stdio``), spoken to with the MCP Python SDK's stdio client,

and asserts the structured output is **byte-identical** for a fixed fixture.
Both surfaces call the *same* service through the *same* frozen #1262
contract, so identical output is the property we lock in here.

No Lemonade, no network, no Google credentials: triage is deterministic
(heuristic categorizer + rule-based summary), and the MCP server constructs
its agent with ``skip_lemonade=True``. The only subprocess is the stdio MCP
server itself, spawned on stdin/stdout.

The send-confirmation gate (#1264) is exercised too: a ``send_email`` MCP
call without a valid, payload-bound confirmation token must be REJECTED (a
structured error, never a send), and the draft -> send handshake must work —
mirroring the REST 403 gate, with the MCP side holding its OWN token store.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# The MCP Python SDK (stdio client) is an optional extra. Skip cleanly when
# it is not installed rather than erroring at collection time.
mcp_sdk = pytest.importorskip("mcp", reason="mcp SDK not installed ([mcp] extra)")

import anyio  # noqa: E402  (after importorskip)

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email.api_routes import (  # noqa: E402  (read-only import)
    EmailTriageService,
)
from gaia_agent_email.contract import (  # noqa: E402
    SCHEMA_VERSION,
    parse_response,
)

REPO_PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Fixed fixtures (contract request envelopes) — identical bytes feed BOTH
# surfaces. Keeping them module-level makes the "same input" guarantee
# obvious.
# ---------------------------------------------------------------------------

SINGLE_REQUEST: Dict[str, Any] = {
    "payload": {
        "kind": "single",
        "principal": {"email": "me@example.com"},
        "message": {
            "message_id": "m-1",
            "from": {"name": "Bob", "email": "bob@example.com"},
            "to": [{"email": "me@example.com"}],
            "subject": "Can you review the Q3 budget?",
            "body": (
                "Hi, please review the attached Q3 budget and reply by Friday. "
                "Let me know if anything looks off."
            ),
        },
    }
}

THREAD_REQUEST: Dict[str, Any] = {
    "payload": {
        "kind": "thread",
        "principal": {"email": "me@example.com"},
        "thread_id": "t-1",
        "messages": [
            {
                "message_id": "m-1",
                "thread_id": "t-1",
                "from": {"email": "bob@example.com"},
                "to": [{"email": "me@example.com"}],
                "subject": "Project kickoff",
                "body": "Let's kick off the project next week. Can you confirm the date?",
            },
            {
                "message_id": "m-2",
                "thread_id": "t-1",
                "from": {"email": "carol@example.com"},
                "to": [{"email": "me@example.com"}],
                "subject": "Re: Project kickoff",
                "body": "Adding Carol. Please send the agenda by Monday.",
            },
        ],
    }
}


# Batch envelope (#1887): one single + one thread item, reusing the exact
# message payloads above so the per-item decisions match the single fixtures.
BATCH_REQUEST: Dict[str, Any] = {
    "items": [
        SINGLE_REQUEST["payload"],
        THREAD_REQUEST["payload"],
    ]
}


def _rest_triage(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run the REST-side service and return the JSON-mode response dict.

    ``mode='json'`` so enums serialize to their string values exactly as the
    JSON-RPC wire form does — that is the representation we compare.
    """
    from gaia_agent_email.contract import EmailTriageRequest

    resp = EmailTriageService().triage_request(
        EmailTriageRequest.model_validate(request)
    )
    return resp.model_dump(mode="json")


def _rest_triage_batch(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run the REST-side batch service and return the JSON-mode response dict.

    Same deterministic path as :func:`_rest_triage` but for the #1887 batch
    envelope (``{items: […]}`` in, ``{results: […]}`` out).
    """
    from gaia_agent_email.contract import BatchTriageRequest

    resp = EmailTriageService().triage_batch(BatchTriageRequest.model_validate(request))
    return resp.model_dump(mode="json")


def _strip_runtime_usage(response: Dict[str, Any]) -> Dict[str, Any]:
    """Drop every ``usage`` block before a byte-for-byte parity compare.

    ``usage`` (#1540) reuses the runtime AgentResponse.stats measurement read
    from Lemonade's stateful ``/stats`` endpoint. None of its fields is
    reproducible across two independent runs (REST in this process, MCP in a
    subprocess): ``tokens_per_second`` is wall-clock-derived, and the token
    counts come from whichever request ``/stats`` last recorded, so they too
    drift between runs. Parity of the STRUCTURED triage decision (category,
    summary, action items, draft, suggested_action) is the property under
    test — not the reproducibility of a usage measurement, which each test
    asserts is *present* on both surfaces separately.

    Walks BOTH response shapes: the single ``{request_kind, result}`` envelope
    and the batch ``{results: [{index, result|error}, …]}`` envelope (#1887).
    """
    import copy

    out = copy.deepcopy(response)
    # Single-email envelope: drop result.usage.
    result = out.get("result")
    if isinstance(result, dict):
        result.pop("usage", None)
    # Batch envelope: drop usage from every per-item result.
    results = out.get("results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("result"), dict):
                item["result"].pop("usage", None)
    return out


# ---------------------------------------------------------------------------
# MCP stdio client helpers
# ---------------------------------------------------------------------------


def _server_params():
    from mcp.client.stdio import StdioServerParameters

    # ``StdioServerParameters.env`` REPLACES the child environment, so merge
    # with the parent. ``GAIA_EMAIL_MCP_FAKE_SEND`` is the explicit test seam:
    # it swaps the live Gmail send backend for an in-memory fake so no real
    # mail is touched. It is opt-in — production never sees it and the send
    # path fails loudly there if Google is not connected (no silent fallback).
    child_env = dict(os.environ)
    child_env["GAIA_EMAIL_MCP_FAKE_SEND"] = "1"
    return StdioServerParameters(
        command=REPO_PYTHON,
        args=["-m", "gaia_agent_email.mcp_server", "--transport", "stdio"],
        # Inherit cwd so ``gaia`` is importable in the child.
        cwd=str(Path(__file__).resolve().parents[2]),
        env=child_env,
    )


def _structured_from_result(result) -> Dict[str, Any]:
    """Extract the tool's returned dict from a CallToolResult.

    FastMCP serializes a ``dict`` return both as a JSON text block in
    ``content`` and (wrapped under ``result``) in ``structuredContent``. The
    text block is the clean, unwrapped JSON — parse that.
    """
    assert not result.isError, f"tool call errored: {result.content}"
    assert result.content, "tool call returned no content"
    text = result.content[0].text
    return json.loads(text)


async def _call_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Spawn the stdio MCP server, call one tool, return its structured dict."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return _structured_from_result(result)


async def _list_tool_names() -> list[str]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return [t.name for t in tools.tools]


async def _call_tool_raw(tool_name: str, arguments: Dict[str, Any]):
    """Like ``_call_tool`` but return the full CallToolResult (for gate tests
    where we need to inspect ``isError``)."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


class TestEmailMcpStdioParity:
    """The structured triage output is identical over REST and MCP stdio."""

    def test_tools_list_exposes_rest_surface(self):
        """The MCP server exposes the same capability set as the REST surface:
        triage, batch triage, draft, send."""
        names = anyio.run(_list_tool_names)
        assert {
            "triage_email",
            "triage_email_batch",
            "draft_reply",
            "send_email",
        } <= set(names)

    def test_single_email_parity(self):
        """Single-email triage: MCP stdio output == REST service output."""
        rest = _rest_triage(SINGLE_REQUEST)
        mcp_out = anyio.run(_call_tool, "triage_email", SINGLE_REQUEST)
        assert _strip_runtime_usage(mcp_out) == _strip_runtime_usage(
            rest
        ), "MCP stdio triage diverged from REST for the single-email fixture"
        # And it is a contract-valid response (extra='forbid' guards drift).
        parsed = parse_response(mcp_out)
        assert parsed.schema_version == SCHEMA_VERSION
        assert parsed.request_kind == "single"
        # The inbound sender gets a proposed reply.
        assert parsed.result.draft is not None
        assert parsed.result.draft.to[0].email == "bob@example.com"
        # usage echoes on both surfaces (#1540): same token counts, present TPS.
        assert parsed.result.usage is not None
        assert parse_response(rest).result.usage is not None

    def test_thread_parity(self):
        """Thread triage: MCP stdio output == REST service output."""
        rest = _rest_triage(THREAD_REQUEST)
        mcp_out = anyio.run(_call_tool, "triage_email", THREAD_REQUEST)
        assert _strip_runtime_usage(mcp_out) == _strip_runtime_usage(
            rest
        ), "MCP stdio triage diverged from REST for the thread fixture"
        parsed = parse_response(mcp_out)
        assert parsed.request_kind == "thread"

    def test_batch_parity(self):
        """Batch triage (#1887): MCP stdio output == REST service output.

        Drives the SAME ``{items: [single, thread]}`` envelope over both
        surfaces — ``triage_email_batch`` over MCP stdio and ``triage_batch``
        on the REST service — and asserts the structured ``results[]`` arrays
        are identical after stripping the non-reproducible per-item ``usage``.
        """
        rest = _rest_triage_batch(BATCH_REQUEST)
        mcp_out = anyio.run(_call_tool, "triage_email_batch", BATCH_REQUEST)
        assert _strip_runtime_usage(mcp_out) == _strip_runtime_usage(
            rest
        ), "MCP stdio batch triage diverged from REST for the batch fixture"
        # Both surfaces produce a 2-item, order-preserved results array, each
        # carrying a populated result (the deterministic path never errors).
        assert len(mcp_out["results"]) == 2
        for index, item in enumerate(mcp_out["results"]):
            assert item["index"] == index
            assert item["result"] is not None
            assert item["error"] is None
        # The batch results must match the single-path decisions for the same
        # inputs — index 0 mirrors the single fixture, index 1 the thread.
        single_rest = _strip_runtime_usage(_rest_triage(SINGLE_REQUEST))["result"]
        thread_rest = _strip_runtime_usage(_rest_triage(THREAD_REQUEST))["result"]
        stripped = _strip_runtime_usage(mcp_out)["results"]
        assert stripped[0]["result"] == single_rest
        assert stripped[1]["result"] == thread_rest
        # usage echoes on both surfaces for every successful item (#1540).
        assert mcp_out["results"][0]["result"]["usage"] is not None
        assert rest["results"][0]["result"]["usage"] is not None

    def test_suggested_action_present_on_both_surfaces(self):
        """Schema 2.0: suggested_action must be present in both REST and MCP stdio (#1615)."""
        rest = _rest_triage(SINGLE_REQUEST)
        mcp_out = anyio.run(_call_tool, "triage_email", SINGLE_REQUEST)
        # Both surfaces must carry suggested_action.
        rest_parsed = parse_response(rest)
        mcp_parsed = parse_response(mcp_out)
        assert hasattr(
            rest_parsed.result, "suggested_action"
        ), "REST result missing suggested_action"
        assert rest_parsed.result.suggested_action is not None
        assert hasattr(
            mcp_parsed.result, "suggested_action"
        ), "MCP result missing suggested_action"
        assert mcp_parsed.result.suggested_action is not None
        # And they agree.
        assert rest_parsed.result.suggested_action == mcp_parsed.result.suggested_action


class TestEmailMcpSendGate:
    """The send-confirmation gate (#1264) is enforced over MCP stdio too."""

    _SEND_PAYLOAD = {
        "to": [{"email": "bob@example.com"}],
        "subject": "Re: Q3 budget",
        "body": "Looks good, approved.",
    }

    def test_send_without_token_is_rejected(self):
        """A send with no confirmation token must NOT send — structured error."""
        result = anyio.run(_call_tool_raw, "send_email", dict(self._SEND_PAYLOAD))
        out = json.loads(result.content[0].text)
        assert out.get("sent") is not True
        assert out.get("error"), "missing actionable error on rejected send"
        assert "confirm" in out["error"].lower()

    def test_send_with_invalid_token_is_rejected(self):
        """A token that was never issued cannot authorize a send."""
        payload = {**self._SEND_PAYLOAD, "confirmation_token": "not-a-real-token"}
        result = anyio.run(_call_tool_raw, "send_email", payload)
        out = json.loads(result.content[0].text)
        assert out.get("sent") is not True
        assert out.get("error")

    def test_token_does_not_authorize_a_different_payload(self):
        """The token is bound to its exact (to, subject, body): a token minted
        for one message cannot be replayed to send different content
        (bait-and-switch). Security-critical — mirrors the REST gate."""

        async def _flow() -> Dict[str, Any]:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client

            async with stdio_client(_server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    draft = await session.call_tool(
                        "draft_reply", dict(self._SEND_PAYLOAD)
                    )
                    token = json.loads(draft.content[0].text)["confirmation_token"]
                    # Same token, attacker-swapped recipient + body.
                    send = await session.call_tool(
                        "send_email",
                        {
                            "to": [{"email": "attacker@evil.example.com"}],
                            "subject": "Re: Q3 budget",
                            "body": "Wire $10,000 to account 12345.",
                            "confirmation_token": token,
                        },
                    )
                    return json.loads(send.content[0].text)

        out = anyio.run(_flow)
        assert out.get("sent") is not True, "token authorized a swapped payload"
        assert out.get("error")

    def test_draft_then_send_handshake_succeeds(self):
        """Golden path: draft mints a payload-bound token; echoing it back in
        the same server session authorizes exactly that send.

        The token store lives in the server process, so draft + send must run
        against the SAME spawned server — one session, two calls.
        """

        async def _flow() -> Dict[str, Any]:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client

            async with stdio_client(_server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    draft = await session.call_tool(
                        "draft_reply", dict(self._SEND_PAYLOAD)
                    )
                    draft_out = json.loads(draft.content[0].text)
                    token = draft_out["confirmation_token"]
                    send = await session.call_tool(
                        "send_email",
                        {**self._SEND_PAYLOAD, "confirmation_token": token},
                    )
                    return json.loads(send.content[0].text)

        out = anyio.run(_flow)
        assert out.get("sent") is True, f"authorized send failed: {out}"
        assert out.get("sent_id")

    def test_token_is_single_use_over_stdio(self):
        """Replaying a consumed token must be rejected (no second send)."""

        async def _flow():
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client

            async with stdio_client(_server_params()) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    draft = await session.call_tool(
                        "draft_reply", dict(self._SEND_PAYLOAD)
                    )
                    token = json.loads(draft.content[0].text)["confirmation_token"]
                    first = await session.call_tool(
                        "send_email",
                        {**self._SEND_PAYLOAD, "confirmation_token": token},
                    )
                    second = await session.call_tool(
                        "send_email",
                        {**self._SEND_PAYLOAD, "confirmation_token": token},
                    )
                    return (
                        json.loads(first.content[0].text),
                        json.loads(second.content[0].text),
                    )

        first, second = anyio.run(_flow)
        assert first.get("sent") is True
        assert second.get("sent") is not True
        assert second.get("error")
