# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
BuilderAgent — built-in hidden agent that scaffolds custom GAIA agents.

Users interact with it via the "+" button in the Agent UI.  It asks for a
name, then calls the ``create_agent`` tool to write a YAML manifest under
``~/.gaia/agents/<id>/agent.yaml``.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.logger import get_logger

logger = get_logger(__name__)

# Agent ID cannot match any of these — they are reserved for built-in agents.
_RESERVED_IDS = {"chat", "gaia", "builder"}

# Allowed characters for a generated agent ID.
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,50}[a-z0-9]$")


@dataclass
class BuilderAgentConfig:
    """Configuration for BuilderAgent."""

    base_url: Optional[str] = None
    model_id: Optional[str] = None
    max_steps: int = 10
    streaming: bool = False
    debug: bool = False
    show_stats: bool = False
    silent_mode: bool = False
    output_dir: Optional[str] = None


class BuilderAgent(Agent):
    """Hidden built-in agent that creates custom agent scaffolds.

    Has a single tool — ``create_agent`` — which writes a YAML manifest to
    ``~/.gaia/agents/<id>/agent.yaml`` and hot-reloads it into the running
    registry so the new agent is immediately available without a server restart.
    """

    AGENT_ID = "builder"
    AGENT_NAME = "Gaia Builder"
    AGENT_DESCRIPTION = "Create a new custom GAIA agent through conversation"
    CONVERSATION_STARTERS = [
        "Help me create a custom agent",
        "I want to build a new agent",
    ]

    def __init__(self, config: Optional[BuilderAgentConfig] = None):
        config = config or BuilderAgentConfig()
        self.config = config

        effective_model_id = config.model_id or "Qwen3.5-35B-A3B-GGUF"
        effective_base_url = (
            config.base_url
            if config.base_url is not None
            else os.getenv("LEMONADE_BASE_URL", "http://localhost:8000/api/v1")
        )

        super().__init__(
            base_url=effective_base_url,
            model_id=effective_model_id,
            max_steps=config.max_steps,
            streaming=config.streaming,
            show_stats=config.show_stats,
            silent_mode=config.silent_mode,
            debug=config.debug,
            output_dir=config.output_dir,
        )

    def _create_console(self) -> AgentConsole:
        return AgentConsole()

    def _get_system_prompt(self) -> str:
        from gaia.agents.builder.system_prompt import BUILDER_SYSTEM_PROMPT

        return BUILDER_SYSTEM_PROMPT

    def _register_tools(self) -> None:
        _TOOL_REGISTRY.clear()
        self.register_builder_tools()

    def register_builder_tools(self) -> None:
        """Register the create_agent tool."""

        @tool
        def create_agent(name: str, description: str = "") -> str:
            """Create a new custom agent in the user's GAIA agents directory.

            Args:
                name: Human-readable agent name, e.g. "Widget Agent".
                description: One-sentence description of what the agent does.

            Returns:
                Confirmation message with the path to the created agent.yaml.
            """
            return _create_agent_impl(name, description)

    def _compose_system_prompt(self) -> str:
        """Compose system prompt without the base class JSON response format.

        The builder uses a conversational flow with simple tool-calling
        instructions embedded directly in its system prompt.  The base class
        ``_response_format_template`` (JSON-only + planning) conflicts with
        conversational greetings, so it is deliberately excluded here.
        """
        parts = []
        custom = self._get_system_prompt()
        if custom:
            parts.append(custom)
        if hasattr(self, "_format_tools_for_prompt"):
            tools_desc = self._format_tools_for_prompt()
            if tools_desc:
                parts.append(f"==== AVAILABLE TOOLS ====\n{tools_desc}")
        return "\n\n".join(p for p in parts if p)

    def process_query(  # type: ignore[override]
        self,
        user_input: str,
        max_steps: int = None,
        trace: bool = False,
        filename: str = None,
    ) -> Dict[str, Any]:
        """Simplified chat loop for the builder agent.

        Unlike the base class loop, this implementation:
        - Does NOT inject "ALWAYS BEGIN WITH A PLAN" instructions
        - Does NOT apply RAG workflow guards or planning-text detectors
        - Uses a simple 2-path parse: tool call → execute and continue;
          plain text / "answer" → return immediately
        - Always calls ``console.print_final_answer()`` so the SSE handler
          in ``_chat_helpers.py`` captures the final answer event.
        """
        import json
        import time

        start_time = time.time()
        self._current_query = user_input

        logger.debug("BuilderAgent processing: %s", user_input[:120])

        messages: list = []
        if hasattr(self, "conversation_history") and self.conversation_history:
            messages.extend(self.conversation_history)

        messages.append({"role": "user", "content": user_input})

        steps_limit = max_steps if max_steps is not None else self.max_steps
        self.console.print_processing_start(user_input, steps_limit, self.model_id)

        final_answer: Optional[str] = None
        steps_taken = 0

        while steps_taken < steps_limit and final_answer is None:
            steps_taken += 1
            self.console.print_step_header(steps_taken, steps_limit)

            try:
                if self.streaming:
                    response_stream = self.chat.send_messages_stream(
                        messages=messages, system_prompt=self.system_prompt
                    )
                    raw = ""
                    for chunk in response_stream:
                        if not chunk.is_complete:
                            self.console.print_streaming_text(chunk.text)
                            raw += chunk.text
                    self.console.print_streaming_text("", end_of_stream=True)
                    response = raw
                else:
                    chat_resp = self.chat.send_messages(
                        messages=messages, system_prompt=self.system_prompt
                    )
                    response = chat_resp.text
            except ConnectionError as exc:
                logger.error("BuilderAgent LLM connection error: %s", exc)
                final_answer = (
                    "I'm having trouble reaching the language model. "
                    "Please make sure Lemonade Server is running and try again."
                )
                break
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("BuilderAgent unexpected LLM error: %s", exc)
                final_answer = (
                    "Sorry, I ran into an unexpected problem. "
                    "Please try again in a moment."
                )
                break

            logger.debug("BuilderAgent response: %s", response[:300])
            messages.append({"role": "assistant", "content": response})

            # Reuse base-class parser: handles both plain text and JSON
            parsed = self._parse_llm_response(response)

            if "tool" in parsed and parsed["tool"]:
                tool_name = parsed["tool"]
                tool_args = parsed.get("tool_args", {})
                self.console.print_tool_usage(tool_name)
                self.console.start_progress(f"Executing {tool_name}")
                tool_result = self._execute_tool(tool_name, tool_args)
                self.console.stop_progress()
                self.console.print_tool_complete()
                result_str = (
                    json.dumps(tool_result)
                    if isinstance(tool_result, dict)
                    else str(tool_result)
                )
                messages.append(
                    {
                        "role": "user",
                        "content": f"Tool '{tool_name}' returned:\n{result_str}",
                    }
                )
                # Continue loop so the LLM can summarize the result
            else:
                final_answer = (
                    parsed.get("answer")
                    or response.strip()
                    or ("I wasn't able to generate a response. Please try again.")
                )

        if final_answer is None:
            final_answer = (
                "I've used the maximum number of steps. "
                "Check ~/.gaia/agents/ for any agents that were created."
            )

        self.console.print_final_answer(final_answer, streaming=self.streaming)
        self.console.print_completion(steps_taken, steps_limit)

        return {
            "answer": final_answer,
            "steps_taken": steps_taken,
            "duration": time.time() - start_time,
        }


def _normalize_agent_id(name: str) -> str:
    """Convert a human name to a safe directory/YAML id.

    Rules:
    - Lowercase, spaces → hyphens
    - Strip characters that are not alphanumeric or hyphen
    - Strip leading/trailing hyphens
    - Strip exactly one trailing "-agent" suffix (deduplicate), then re-add it
    - Result must match ``_SAFE_ID_RE``

    Examples:
        "Widget Agent"      → "widget-agent"
        "My Agent Agent"    → "my-agent"
        "zoo"               → "zoo-agent"
    """
    slug = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-")).strip("-")
    # Remove trailing -agent suffix (may appear multiple times) then re-add once
    while slug.endswith("-agent"):
        slug = slug[: -len("-agent")].strip("-")
    return f"{slug}-agent" if slug else ""


def _create_agent_impl(name: str, description: str = "") -> str:
    """Core implementation of the create_agent tool, separated for testability."""
    from gaia.agents.builder.template import (
        TEMPLATE_COMMENTS,
        TEMPLATE_INSTRUCTIONS,
        TEMPLATE_STARTERS,
    )

    # ── 1. Normalize and validate the agent ID ──────────────────────────────
    agent_id = _normalize_agent_id(name.strip())

    if not agent_id or not _SAFE_ID_RE.match(agent_id):
        return (
            "Error: Invalid agent name. "
            "Please use letters, numbers, and spaces (e.g. 'Weather Agent')."
        )

    base_id = agent_id[
        : -len("-agent")
    ]  # strip the trailing -agent for reservation check
    if base_id in _RESERVED_IDS or agent_id in _RESERVED_IDS:
        return f"Error: '{name}' is reserved. Please choose a different name."

    # ── 2. Resolve and verify target path ───────────────────────────────────
    agents_dir = Path.home() / ".gaia" / "agents"
    target = (agents_dir / agent_id).resolve()

    # Guard against path traversal
    try:
        target.relative_to(agents_dir.resolve())
    except ValueError:
        return "Error: Invalid agent name (path traversal detected)."

    if target.exists():
        return (
            f"Error: An agent named '{agent_id}' already exists at {target}. "
            "Please choose a different name."
        )

    # ── 3. Build the YAML data dict ──────────────────────────────────────────
    data = {
        "manifest_version": 1,
        "id": agent_id,
        "name": name.strip(),
        "description": (
            description.strip()
            if description.strip()
            else f"Custom agent: {name.strip()}"
        ),
        "instructions": TEMPLATE_INSTRUCTIONS,
        "tools": [],
        "conversation_starters": TEMPLATE_STARTERS,
    }

    # ── 4. Write safely — never string interpolation ─────────────────────────
    target.mkdir(parents=True, exist_ok=True)
    yaml_path = target / "agent.yaml"

    # yaml.dump handles escaping; TEMPLATE_COMMENTS is appended as plain text
    yaml_content = yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=88,
    )
    yaml_content += TEMPLATE_COMMENTS
    yaml_path.write_text(yaml_content, encoding="utf-8")

    # ── 5. Round-trip validation ─────────────────────────────────────────────
    try:
        from gaia.agents.registry import AgentManifest

        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        AgentManifest(**raw)
    except Exception as exc:
        # Clean up and report — should never happen with our fixed template
        yaml_path.unlink(missing_ok=True)
        try:
            target.rmdir()
        except OSError:
            pass
        logger.error("builder: YAML validation failed for %s: %s", agent_id, exc)
        return f"Error: Generated YAML is invalid ({exc}). Please try again."

    # ── 6. Hot-reload into the running registry ───────────────────────────────
    try:
        from gaia.ui._chat_helpers import get_agent_registry

        registry = get_agent_registry()
        if registry is not None:
            registry.register_from_dir(target)
            logger.info("builder: Hot-reloaded agent '%s' into registry", agent_id)
    except Exception as exc:
        # Hot-reload failure is non-fatal — the agent exists on disk
        logger.warning("builder: Hot-reload skipped: %s", exc)

    return (
        f"Done! I've created your '{name.strip()}' agent at:\n\n"
        f"  {yaml_path}\n\n"
        "The agent is already loaded and ready to use — you'll see it in the "
        "agent selector in the GAIA UI.\n\n"
        "To customize it, open the YAML file and edit the `instructions` field "
        "with your own system prompt. You can also uncomment the `tools`, "
        "`models`, and `mcp_servers` sections to extend your agent's capabilities."
    )
