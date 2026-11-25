# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent a host program talks to."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from gaia_sdk.engine import PROTOCOL, Engine, EngineError, find_engine
from gaia_sdk.tools import Tool, ToolRefusal
from gaia_sdk.tools import tool as as_tool

Message = Dict[str, str]


@dataclass(frozen=True)
class Budget:
    """What one turn may spend. ``None`` leaves a limit off.

    Checked when the model asks for a tool: once a limit is reached the tool
    is not run and nothing more is sent to the model.
    """

    model_calls: Optional[int] = None
    output_tokens: Optional[int] = None
    seconds: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("model_calls", "output_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(
                    f"Budget.{name} must be a positive integer, got {value!r}."
                )
        if self.seconds is not None and (
            isinstance(self.seconds, bool)
            or not isinstance(self.seconds, (int, float))
            or self.seconds <= 0
        ):
            raise ValueError(
                f"Budget.seconds must be a positive number, got {self.seconds!r}."
            )

    def as_wire(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in (
                ("model_calls", self.model_calls),
                ("output_tokens", self.output_tokens),
                ("seconds", self.seconds),
            )
            if value is not None
        }


@dataclass(frozen=True)
class Turn:
    """How one turn ended."""

    #: ``success``, ``incomplete``, ``failed`` or ``cancelled``.
    status: str
    #: Why it ended: ``answered``, ``max_steps``, ``model_calls``,
    #: ``output_tokens``, ``deadline``, ``model_call``, ...
    stop_reason: str
    #: The model's answer. Show it to an end user only when ``ok``.
    text: str
    #: Every tool call, in order: ``name``, ``arguments``, ``result``, ``failed``.
    tool_calls: List[Dict[str, Any]]
    #: ``model_calls``, ``input_tokens``, ``cached_tokens``, ``output_tokens``.
    usage: Dict[str, int]
    seconds: float
    #: For a failed model request: ``{"kind": "model_call", "message": ...}``.
    failure: Optional[Dict[str, Any]]
    #: One entry per model request: ``step``, ``seconds``, ``usage``.
    model_calls: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "success"


class Agent:
    """The GAIA agent loop, with your prompt and your tools.

    Args:
        model: The model id sent to the provider.
        base_url: An OpenAI-compatible endpoint, e.g. ``https://host/v1``.
        api_key_env: Name of the environment variable holding the provider
            key. The key is read by the engine process and never sent to it.
        system_prompt: Sent to the model as written.
        tools: Functions decorated with ``@tool``, or plain typed functions.
        max_steps: Tool-using steps one turn may take.
        max_output_tokens: Cap on each model reply.
        context_size: The model's window in tokens.
        temperature: Sampling temperature.
        native_tools: Send tools as ``tools=`` (True) or describe them in the
            prompt (False), for a model without native tool calls.
        retries: Retries of a failed model request. 0: a failure ends the turn.
        budget: Limits applied to every turn; ``run(budget=...)`` overrides.
        on_model_call: Called once per model request with its ``step``,
            ``seconds`` and ``usage``.
        engine: Path to the ``gaiacpp`` binary, when it is not found otherwise.

    One agent runs one turn at a time. Close it, or use it as a context
    manager, to stop the engine process.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key_env: Optional[str] = None,
        system_prompt: str = "",
        tools: Sequence[Union[Tool, Callable[..., Any]]] = (),
        max_steps: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        context_size: Optional[int] = None,
        temperature: Optional[float] = None,
        native_tools: bool = True,
        retries: int = 0,
        budget: Optional[Budget] = None,
        on_model_call: Optional[Callable[[Dict[str, Any]], None]] = None,
        engine: Optional[str] = None,
    ):
        self._tools: Dict[str, Tool] = {}
        for entry in tools:
            item = entry if isinstance(entry, Tool) else as_tool(entry)
            if item.name in self._tools:
                raise ValueError(f"Two tools are named {item.name!r}.")
            self._tools[item.name] = item
        if budget is not None and not isinstance(budget, Budget):
            raise TypeError(f"budget must be a Budget, got {type(budget).__name__}.")
        self.budget = budget
        self.on_model_call = on_model_call
        self._turn_ids = itertools.count(1)

        init: Dict[str, Any] = {
            "type": "init",
            "protocol": PROTOCOL,
            "model": model,
            "base_url": base_url,
            "system_prompt": system_prompt,
            "tools": [item.declaration() for item in self._tools.values()],
            "native_tools": native_tools,
            "retries": retries,
        }
        for key, value in (
            ("api_key_env", api_key_env),
            ("max_steps", max_steps),
            ("max_output_tokens", max_output_tokens),
            ("context_size", context_size),
            ("temperature", temperature),
        ):
            if value is not None:
                init[key] = value

        self._engine = Engine(find_engine(engine))
        try:
            self._engine.send(init)
            ready = self._engine.receive()
        except EngineError:
            self._engine.close()
            raise
        if ready.get("type") != "ready" or ready.get("protocol") != PROTOCOL:
            self._engine.close()
            raise EngineError(
                f"The engine answered init with {ready!r}; this SDK speaks "
                f"protocol {PROTOCOL}. Use a gaiacpp build that matches it."
            )
        self.engine_version: str = str(ready.get("version", ""))

    def run(
        self,
        messages: Union[str, Sequence[Message]],
        budget: Optional[Budget] = None,
    ) -> Turn:
        """Run one turn and return how it ended.

        Args:
            messages: The user's message, or the conversation so far as
                ``{"role": "user" | "assistant", "content": ...}`` dicts ending
                with the user's new message. The engine keeps no history, so
                send what the model should see.
            budget: Limits for this turn, in place of the agent's own.

        A failed model request, a spent budget and a step limit are not
        exceptions: they are a ``Turn`` whose ``ok`` is False. ``EngineError``
        is raised only when the engine itself cannot run the turn.
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        if budget is not None and not isinstance(budget, Budget):
            raise TypeError(f"budget must be a Budget, got {type(budget).__name__}.")
        budget = budget if budget is not None else self.budget
        turn_id = f"t{next(self._turn_ids)}"
        request: Dict[str, Any] = {
            "type": "turn",
            "id": turn_id,
            "messages": [dict(message) for message in messages],
        }
        if budget is not None and budget.as_wire():
            request["budget"] = budget.as_wire()
        self._engine.send(request)

        model_calls: List[Dict[str, Any]] = []
        while True:
            event = self._engine.receive()
            kind = event["type"]
            if kind == "tool_request":
                self._engine.send(self._answer(event))
            elif kind == "model_call":
                call = {k: event.get(k) for k in ("step", "seconds", "usage")}
                model_calls.append(call)
                if self.on_model_call is not None:
                    self.on_model_call(call)
            elif kind == "final":
                return Turn(
                    status=event["status"],
                    stop_reason=event["stop_reason"],
                    text=event.get("text") or "",
                    tool_calls=list(event.get("tool_calls") or []),
                    usage=dict(event.get("usage") or {}),
                    seconds=float(event.get("seconds") or 0.0),
                    failure=event.get("failure"),
                    model_calls=model_calls,
                )
            else:
                raise EngineError(
                    f"The engine sent {kind!r} during a turn, which this SDK "
                    "does not know. The engine may be newer than the SDK."
                )

    def _answer(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Run the tool the engine asked for and build its ``tool_result``."""
        answer: Dict[str, Any] = {"type": "tool_result", "id": request["id"]}
        item = self._tools.get(request.get("name"))
        if item is None:
            answer["error"] = f"No tool named {request.get('name')!r} is registered."
            return answer
        try:
            answer["result"] = item.function(**(request.get("arguments") or {}))
        except ToolRefusal as refusal:
            answer["result"] = refusal.as_result()
        except Exception as e:  # pylint: disable=broad-except
            # The model is told the call failed and why; the turn goes on.
            answer["error"] = f"{type(e).__name__}: {e}"
        return answer

    def close(self) -> None:
        """Stop the engine process."""
        self._engine.close()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
