# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``CoderLLM`` — gaia-coder's single LLM seam.

Wraps :class:`gaia.eval.claude.ClaudeClient` (the same Anthropic SDK wrapper
the rest of the repo uses for evaluation) and exposes the two call shapes
the coder needs:

* :meth:`CoderLLM.complete` — one-shot text completion. Used by self-fix
  triage / critique / classify-failure / standup composer.
* :meth:`CoderLLM.chat_with_tools` — multi-turn tool-use loop primitive.
  Used by :class:`gaia.coder.agent.CoderAgent`'s interactive REPL.

Why a wrapper instead of using ``ClaudeClient`` directly?

1. **Tool-use API surface.** ``ClaudeClient.get_completion`` is a one-shot
   text→text call; the tool-use protocol needs the full ``Message`` object
   plus ``tools=`` and the ability to feed ``tool_result`` back. We expose
   that without leaking the Anthropic SDK shape into every caller.
2. **Per-call-type token caps.** §15.8 of ``docs/plans/coder-agent.mdx``
   specifies different ``max_tokens`` for triage / review / standup. The
   underlying ``ClaudeClient`` is constructed once with a single
   ``max_tokens`` value, so we override per call here.
3. **Cost telemetry.** Every call returns a structured ``Usage`` object
   carrying input / output token counts and a US-dollar cost computed
   from :data:`gaia.eval.config.MODEL_PRICING`. The REPL surfaces it.
4. **Fail-loudly defaults.** Missing ``ANTHROPIC_API_KEY`` raises with an
   actionable message; tool-use loop with no progress raises rather than
   silently hanging.

Default model is ``claude-sonnet-4-6`` (matches :data:`gaia.eval.config.
DEFAULT_CLAUDE_MODEL`). The coder plan §3.2 prescribes Opus 4.7 for
production review passes — those callers pass ``model=`` explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from gaia.eval.claude import ClaudeClient
from gaia.eval.config import DEFAULT_CLAUDE_MODEL, MODEL_PRICING
from gaia.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Usage:
    """Per-call token + cost telemetry."""

    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost_usd(self) -> float:
        return round(self.input_cost_usd + self.output_cost_usd, 6)


@dataclass(frozen=True)
class ToolUse:
    """One ``tool_use`` content block returned by the assistant.

    ``id`` is the Anthropic-supplied ``toolu_…`` correlation id. The caller
    runs the tool, then constructs a ``tool_result`` message echoing the
    same ``id`` so the assistant can correlate results to its requests.
    """

    id: str
    name: str
    input: Mapping[str, Any]


@dataclass(frozen=True)
class AssistantTurn:
    """One assistant message in a tool-use chat loop.

    Attributes:
        text: Concatenated text from every ``text`` content block. Empty
            string when the model only emitted ``tool_use`` blocks.
        tool_uses: Tool calls the assistant wants the caller to run. Empty
            when the model is "done" (``stop_reason == 'end_turn'``).
        stop_reason: One of ``end_turn`` / ``tool_use`` / ``max_tokens`` /
            ``stop_sequence``. ``tool_use`` means the loop must continue
            with ``tool_result`` messages.
        usage: Token + cost telemetry for this turn.
        raw_content: The full ``message.content`` block list. Preserved so
            it can be appended verbatim to the message history (Anthropic
            requires the *exact* assistant turn including ``tool_use``
            blocks to round-trip back as the next request's history).
    """

    text: str
    tool_uses: tuple[ToolUse, ...]
    stop_reason: str
    usage: Usage
    raw_content: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# CoderLLM
# ---------------------------------------------------------------------------


class CoderLLM:
    """Single LLM seam for every gaia-coder call site.

    Constructed once at agent startup. Tests inject a ``client_factory``
    returning a stub ``ClaudeClient``-shaped object, or patch
    :meth:`complete` / :meth:`chat_with_tools` directly.

    Example::

        llm = CoderLLM()                        # default sonnet-4-6
        text = llm.complete("hello", max_tokens=64)
        turn = llm.chat_with_tools(
            messages=[{"role": "user", "content": "list .py files"}],
            tools=[{"name": "list_files", ...}],
            system="You are a coding assistant.",
        )
        if turn.tool_uses:
            ...  # run tools, append tool_result, loop
    """

    DEFAULT_MODEL: str = DEFAULT_CLAUDE_MODEL
    DEFAULT_MAX_TOKENS: int = 4096
    DEFAULT_TEMPERATURE: float = 0.0

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        max_retries: int = 3,
        client: Optional[ClaudeClient] = None,
    ) -> None:
        """Construct the LLM seam.

        Args:
            model: Anthropic model id. Defaults to :data:`DEFAULT_MODEL`.
            max_tokens: Default ``max_tokens`` for completions. Individual
                calls may override.
            max_retries: Forwarded to :class:`ClaudeClient` for transport
                retry. The Anthropic SDK applies exponential backoff.
            client: Pre-built :class:`ClaudeClient` for tests. When
                supplied, ``model`` / ``max_tokens`` / ``max_retries``
                are ignored and the supplied client's settings win.
        """
        self._model = model or self.DEFAULT_MODEL
        self._default_max_tokens = max_tokens
        if client is not None:
            self._client = client
        else:
            # Underlying ClaudeClient validates ANTHROPIC_API_KEY at
            # construction and raises with an actionable message if absent.
            self._client = ClaudeClient(
                model=self._model,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
        logger.debug(
            "CoderLLM ready (model=%s, default_max_tokens=%d)",
            self._model,
            self._default_max_tokens,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        """The default model name this seam routes to."""
        return self._model

    def complete(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        system: Optional[str] = None,
    ) -> str:
        """One-shot text completion.

        Used by the self-fix loop's triage / critique / classify-failure
        / standup callers — places that send a single rendered prompt and
        consume a single text reply.

        Args:
            prompt: Fully rendered user message.
            model: Override the default model for this call.
            max_tokens: Override the default ``max_tokens`` for this call.
            temperature: Override the default temperature (defaults to 0
                for determinism — review passes mandate it).
            system: Optional system prompt.

        Returns:
            The concatenated text content of the assistant response.

        Raises:
            ValueError: bubble-up from :class:`ClaudeClient` if
                ``ANTHROPIC_API_KEY`` is missing.
            anthropic.APIError: bubble-up on transport failure after
                retries are exhausted. Per fail-loudly we do not swallow.
        """
        kwargs: Dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": max_tokens or self._default_max_tokens,
            "temperature": (
                self.DEFAULT_TEMPERATURE if temperature is None else temperature
            ),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        message = self._client.client.messages.create(**kwargs)
        text_parts: List[str] = []
        for block in getattr(message, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                text_parts.append(text)
        result = "\n".join(text_parts)
        logger.debug(
            "CoderLLM.complete: model=%s in=%s out=%s",
            kwargs["model"],
            getattr(getattr(message, "usage", None), "input_tokens", "?"),
            getattr(getattr(message, "usage", None), "output_tokens", "?"),
        )
        return result

    def chat_with_tools(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tool_choice: Optional[Mapping[str, Any]] = None,
    ) -> AssistantTurn:
        """One assistant turn in a tool-use chat loop.

        Wraps the Anthropic tool-use API. The caller owns the message
        history and the tool dispatch — this method only sends one
        request and returns a parsed :class:`AssistantTurn`.

        Typical loop::

            history = [{"role": "user", "content": "do thing"}]
            while True:
                turn = llm.chat_with_tools(
                    messages=history, tools=tools, system=system,
                )
                # IMPORTANT: append the assistant's *full* content block
                # list so subsequent tool_result messages can correlate.
                history.append({"role": "assistant", "content": list(turn.raw_content)})
                if not turn.tool_uses:
                    break  # end_turn — done
                tool_results = []
                for use in turn.tool_uses:
                    output = dispatcher.run(use.name, use.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": use.id,
                        "content": str(output),
                    })
                history.append({"role": "user", "content": tool_results})

        Args:
            messages: Conversation history in Anthropic shape (each item
                ``{"role": "user"|"assistant", "content": str | list}``).
            tools: List of Anthropic tool definitions
                (``{"name": str, "description": str, "input_schema": {...}}``).
            system: Optional system prompt; cache markers are the
                caller's responsibility.
            model: Override the default model for this call.
            max_tokens: Override the default ``max_tokens`` for this call.
            temperature: Override the default temperature.
            tool_choice: Optional tool-choice constraint
                (``{"type": "auto"}`` / ``{"type": "any"}`` /
                ``{"type": "tool", "name": "x"}``).

        Returns:
            Parsed :class:`AssistantTurn`.
        """
        kwargs: Dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": max_tokens or self._default_max_tokens,
            "temperature": (
                self.DEFAULT_TEMPERATURE if temperature is None else temperature
            ),
            "messages": list(messages),
            "tools": list(tools),
        }
        if system:
            kwargs["system"] = system
        if tool_choice is not None:
            kwargs["tool_choice"] = dict(tool_choice)

        message = self._client.client.messages.create(**kwargs)
        return self._parse_turn(message, model_used=kwargs["model"])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse_turn(self, message: Any, *, model_used: str) -> AssistantTurn:
        """Convert an Anthropic ``Message`` object to an :class:`AssistantTurn`."""
        text_parts: List[str] = []
        tool_uses: List[ToolUse] = []
        raw_blocks: List[Mapping[str, Any]] = []
        for block in getattr(message, "content", []) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text = getattr(block, "text", "") or ""
                if text:
                    text_parts.append(text)
                raw_blocks.append({"type": "text", "text": text})
            elif block_type == "tool_use":
                tool_id = getattr(block, "id", "")
                name = getattr(block, "name", "")
                tinput = getattr(block, "input", {}) or {}
                if not tool_id or not name:
                    raise RuntimeError(
                        "tool_use block missing id or name "
                        f"(id={tool_id!r}, name={name!r}) — Anthropic API "
                        "shape change? raw block: " + repr(block)
                    )
                tool_uses.append(ToolUse(id=tool_id, name=name, input=dict(tinput)))
                raw_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": name,
                        "input": dict(tinput),
                    }
                )
            else:
                # Anthropic may add new block types; preserve verbatim so
                # the caller can round-trip them in history.
                raw_blocks.append({"type": block_type or "unknown"})

        usage = self._extract_usage(message, model_used=model_used)
        stop_reason = getattr(message, "stop_reason", "unknown") or "unknown"
        return AssistantTurn(
            text="\n".join(text_parts),
            tool_uses=tuple(tool_uses),
            stop_reason=stop_reason,
            usage=usage,
            raw_content=tuple(raw_blocks),
        )

    @staticmethod
    def _extract_usage(message: Any, *, model_used: str) -> Usage:
        """Pull token counts from the Anthropic message and cost-price them."""
        u = getattr(message, "usage", None)
        in_tok = int(getattr(u, "input_tokens", 0) or 0) if u is not None else 0
        out_tok = int(getattr(u, "output_tokens", 0) or 0) if u is not None else 0
        pricing = MODEL_PRICING.get(model_used, MODEL_PRICING["default"])
        in_cost = round((in_tok / 1_000_000.0) * pricing["input_per_mtok"], 6)
        out_cost = round((out_tok / 1_000_000.0) * pricing["output_per_mtok"], 6)
        return Usage(
            input_tokens=in_tok,
            output_tokens=out_tok,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
        )


__all__ = [
    "AssistantTurn",
    "CoderLLM",
    "ToolUse",
    "Usage",
]
