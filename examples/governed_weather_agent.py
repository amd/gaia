#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Governed Weather Agent Example.

Same as :mod:`examples.weather_agent` but wraps every tool call through
a :class:`GaiaGovernanceAdapter`. The adapter is composed from the
in-repo reference implementations (stub policy engine, in-memory
checkpoint bridge / receipt service / static policy binding) so the
example runs with zero external services.

This example registers two **local tools** (alongside the open-meteo
MCP tools) so governance decisions are guaranteed to trigger:

* ``clear_weather_cache`` — tagged ``blocked``. When the LLM calls
  this tool, governance short-circuits with a BLOCK decision, issues
  a signed receipt, and the tool body never runs.
* ``subscribe_weather_alerts`` — tagged ``review``. Governance opens
  a checkpoint and asks the configured reviewer (the CLI prompt in
  ``_cli_reviewer`` below). On approve the tool runs; on reject it is
  refused. Either way the resolution is logged to the receipt store.

Run::

    uv run examples/governed_weather_agent.py

Say "clear the weather cache please" to see a BLOCK decision, or
"subscribe me to severe weather alerts for Austin" to see a REVIEW
decision, or ask any normal weather question to see ALLOW decisions on
the MCP tools.

The base ``Agent`` class is **not modified**. Governance is composed
onto the agent via :class:`GovernedAgentMixin`.
"""

from gaia import Agent, tool
from gaia.governance import (
    GaiaGovernanceAdapter,
    GovernedAgentMixin,
)
from gaia.governance.checkpoint_bridge import InMemoryCheckpointBridge
from gaia.governance.policy_binding import StaticPolicyBindingService
from gaia.governance.receipt_service import JsonlReceiptService
from gaia.governance.stubs import RuleBasedPolicyEngine
from gaia.mcp import MCPClientMixin
from gaia.mcp.client.config import MCPConfig
from gaia.mcp.client.mcp_client_manager import MCPClientManager

# Append-only audit log. Tail with `tail -f receipts.jsonl` to watch
# decisions live while the agent runs.
RECEIPTS_PATH = "receipts.jsonl"
# --- Local tools that will actually be reachable by the LLM --------------


@tool
def clear_weather_cache() -> dict:
    """Destructively clear all cached weather data.

    Use this when the user explicitly asks to reset, clear, or purge
    the weather cache.
    """
    # Body only executes if governance ALLOWs. With the default adapter
    # this tool is risk-tagged "blocked" and never runs.
    return {"status": "ok", "message": "weather cache cleared"}


@tool
def subscribe_weather_alerts(location: str, severity: str = "severe") -> dict:
    """Subscribe the user to recurring weather alerts for a location.

    Use this when the user asks to be notified, subscribed, or alerted
    about weather conditions at a specific location.
    """
    return {
        "status": "ok",
        "message": f"subscribed to {severity} alerts for {location}",
    }


# --- Agent -----------------------------------------------------------------


class WeatherAgent(Agent, MCPClientMixin):
    """Base weather agent — mirrors examples/weather_agent.py."""

    WEATHER_SERVER = {
        "name": "weather",
        "config": {
            "command": "uvx",
            "args": ["--from", "open-meteo-mcp", "open_meteo_mcp"],
        },
    }

    def __init__(self, **kwargs):
        self._mcp_manager = MCPClientManager(config=MCPConfig(config_file=None))
        kwargs.setdefault("model_id", "Qwen3-4B-Instruct-2507-GGUF")
        kwargs.setdefault("max_steps", 10)
        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        return (
            "You are a helpful weather assistant. Use the available MCP "
            "weather tools to answer weather questions. You also have two "
            "local tools:\n"
            "- clear_weather_cache: call this if the user asks to reset "
            "or clear the cache.\n"
            "- subscribe_weather_alerts: call this if the user asks to "
            "be notified or subscribed to alerts for a location."
        )

    def _register_tools(self) -> None:
        print("Connecting to MCP weather server...")
        success = self.connect_mcp_server(
            self.WEATHER_SERVER["name"], self.WEATHER_SERVER["config"]
        )
        print("  Connected" if success else "  Failed to connect")


class GovernedWeatherAgent(GovernedAgentMixin, WeatherAgent):
    """Weather agent with governance wired in via the mixin."""


# --- Adapter + demo wiring ------------------------------------------------


def build_default_adapter() -> GaiaGovernanceAdapter:
    """Compose an adapter using the in-repo reference implementations."""
    return GaiaGovernanceAdapter(
        policy_engine=RuleBasedPolicyEngine(policy_version="v0"),
        checkpoint_runtime=InMemoryCheckpointBridge(),
        receipt_service=JsonlReceiptService(RECEIPTS_PATH),
        policy_binding=StaticPolicyBindingService(
            version="v0", constitution_hash="constitution-dev"
        ),
    )


def _cli_reviewer(tool_name, tool_args, decision) -> bool:
    """Interactive CLI reviewer for REVIEW decisions.

    Used when the GAIA console's confirmation surface isn't available.
    Returning False fails the tool closed.
    """
    print(
        f"\n[review] tool={tool_name!r} args={tool_args!r} "
        f"reason={decision.reason!r}"
    )
    answer = input("[review] approve? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


DEFAULT_RISK_TAGS = {
    "clear_weather_cache": ["blocked"],
    "subscribe_weather_alerts": ["review"],
}


def _log_decision(tool_name, _tool_args, _action, decision):
    print(
        f"[governance] tool={tool_name!r} decision={decision.decision} "
        f"reason={decision.reason!r} policy={decision.policy_version}"
    )


def main():
    print("=" * 60)
    print("Governed Weather Agent — ACGS-lite action governance demo")
    print("=" * 60)
    print(
        "\nTry:\n"
        "  - 'What is the weather in Austin?'        (ALLOW)\n"
        "  - 'Subscribe me to alerts for Seattle.'   (REVIEW)\n"
        "  - 'Clear the weather cache please.'       (BLOCK)\n"
    )

    adapter = build_default_adapter()

    try:
        agent = GovernedWeatherAgent(
            governance_adapter=adapter,
            governance_actor_id="demo-user",
            governance_workflow_id="wf_demo",
            governance_risk_tags=DEFAULT_RISK_TAGS,
            governance_callback=_log_decision,
            governance_reviewer=_cli_reviewer,
        )
        print(f"Governed Weather Agent ready. Audit log: {RECEIPTS_PATH}\n")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Demo harness: report any startup failure (Lemonade, uvx, MCP)
        # as a single friendly message instead of a traceback.
        print(f"Error initializing agent: {exc}")
        print(
            "Make sure Lemonade server is running and `uv` is installed "
            "so `uvx` can fetch the weather MCP server."
        )
        return

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            result = agent.process_query(user_input)
            if result.get("result"):
                print(f"\nAgent: {result['result']}\n")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break


if __name__ == "__main__":
    main()
