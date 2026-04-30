# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Local-test agent for the OAuth connections layer (issue #915).

INSTALL: copy this file to ``~/.gaia/agents/oauth-test/agent.py``.

Run flow (see ../README.md for the full local-test recipe):
  1. Set ``GAIA_GOOGLE_CLIENT_ID`` to your Cloud Console desktop client id.
  2. Start the AgentUI: ``gaia chat --ui``.
  3. Open Settings → Connections → click "Connect" next to Google.
  4. Complete OAuth in your browser; AgentUI updates within ~2s.
  5. Switch the active agent to "OAuth Test (Gmail)".
  6. The first message triggers the consent dialog (REQUIRED_CONNECTORS
     surfaces the gmail.readonly scope claim).
  7. Click "Grant" — the agent now has gmail.readonly for your account.
  8. Ask the agent: "list 5 recent subjects". The reply lists subjects
     fetched live via the Gmail API.

This agent is intentionally tiny: one tool, one HTTP call, one bearer
token from get_access_token_sync. It exercises every layer of the
connections module end-to-end without needing any other GAIA feature.
"""

from __future__ import annotations

from typing import ClassVar, List

import requests

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.connectors import (
    AuthRequiredError,
    ConnectorRequirement,
    get_access_token_sync,
)


GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


class OAuthTestAgent(Agent):
    AGENT_ID = "oauth-test"
    AGENT_NAME = "OAuth Test (Gmail)"
    AGENT_DESCRIPTION = (
        "Demo agent for the connections layer — fetches the 5 newest Gmail "
        "subjects to exercise the OAuth flow end-to-end."
    )
    CONVERSATION_STARTERS = [
        "List 5 recent emails",
        "Show me my newest message subjects",
    ]

    # Declare the scope claim — the AgentUI consent dialog renders the
    # `reason` field in plain language.
    REQUIRED_CONNECTORS: ClassVar[List[ConnectorRequirement]] = [
        ConnectorRequirement(
            provider="google",
            scopes=[GMAIL_READONLY],
            reason="Read your Gmail inbox to summarize the 5 newest message subjects.",
        ),
    ]

    response_mode: str = "conversational"

    def _register_tools(self):
        # The base Agent class auto-registers methods decorated with @tool;
        # this hook is the canonical place to bind any extra runtime state.
        pass

    @tool(
        name="list_recent_subjects",
        description="List the 5 newest Gmail subjects for the connected account.",
    )
    def list_recent_subjects(self) -> dict:
        """
        Fetch the 5 newest Gmail subjects for the connected account.

        Returns a dict so the conversational mode can render it as JSON or
        the agent can summarize it. The bearer token comes from the
        connections layer; if the user hasn't granted this agent yet, the
        call raises AuthRequiredError(AGENT_NOT_GRANTED) and the AgentUI
        surfaces the consent dialog.
        """
        try:
            token = get_access_token_sync(
                provider="google",
                scopes=[GMAIL_READONLY],
            )
        except AuthRequiredError as e:
            return {
                "ok": False,
                "reason": e.reason.value,
                "message": str(e),
            }

        headers = {"Authorization": f"Bearer {token}"}
        list_resp = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"maxResults": 5},
            headers=headers,
            timeout=10,
        )
        list_resp.raise_for_status()
        ids = [m["id"] for m in list_resp.json().get("messages", [])]

        subjects: list[str] = []
        for mid in ids:
            m = requests.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                params={"format": "metadata", "metadataHeaders": "Subject"},
                headers=headers,
                timeout=10,
            )
            m.raise_for_status()
            for h in m.json().get("payload", {}).get("headers", []):
                if h.get("name") == "Subject":
                    subjects.append(h.get("value") or "(no subject)")
                    break
            else:
                subjects.append("(no subject)")

        return {"ok": True, "subjects": subjects}

    def get_system_prompt(self) -> str:
        return (
            "You are a tiny demo agent that helps test the GAIA OAuth "
            "connections layer. When the user asks for recent emails, "
            "call list_recent_subjects() once and reply with the list. "
            "If the call returns ok=False, explain the reason in plain "
            "English and suggest the user grant access in Settings → "
            "Connections."
        )
