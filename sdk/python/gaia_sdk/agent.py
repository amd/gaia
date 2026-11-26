# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent a host program talks to."""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from gaia_sdk.engine import PROTOCOL, AgentBusy, Engine, EngineError, find_engine
from gaia_sdk.tools import Tool, ToolRefusal
from gaia_sdk.tools import tool as as_tool

Message = Dict[str, str]

#: ``check(answer, tool_calls)`` returns what is wrong with the answer, or None.
AnswerCheck = Callable[[str, List[Dict[str, Any]]], Optional[str]]

#: A turn's stop reason when an answer check still objects after the one repair.
UNRESOLVED_CHECKS = "unresolved_checks"

_REPAIR = (
    "[check] {problems} Give your complete answer again: it replaces the one "
    "above, which the user will not see. Do not mention this check."
)


def _positive(name: str, value: Any, whole: bool) -> None:
    kinds = (int,) if whole else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds) or value <= 0:
        kind = "integer" if whole else "number"
        raise ValueError(f"{name} must be a positive {kind}, got {value!r}.")


@dataclass(frozen=True)
class Prices:
    """A model's list prices in dollars per million tokens.

    The engine carries no rate card, so a dollar budget and ``Turn.cost_usd``
    need the prices of the model you are calling.
    """

    input: float
    cached_input: float
    output: float

    def __post_init__(self) -> None:
        for name in ("input", "cached_input", "output"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError(
                    f"Prices.{name} must be a number, 0 or more, got {value!r}."
                )

    def as_wire(self) -> Dict[str, float]:
        return {
            "input": self.input,
            "cached_input": self.cached_input,
            "output": self.output,
        }


@dataclass(frozen=True)
class Budget:
    """What one turn may spend. ``None`` leaves a limit off.

    Checked when the model asks for a tool: once a limit is reached the tool
    is not run and nothing more is sent to the model. ``usd`` needs the agent
    to have ``prices``.
    """

    model_calls: Optional[int] = None
    output_tokens: Optional[int] = None
    seconds: Optional[float] = None
    usd: Optional[float] = None

    def __post_init__(self) -> None:
        for name, whole in (
            ("model_calls", True),
            ("output_tokens", True),
            ("seconds", False),
            ("usd", False),
        ):
            value = getattr(self, name)
            if value is not None:
                _positive(f"Budget.{name}", value, whole)

    def as_wire(self) -> Dict[str, Any]:
        return {
            key: value
            for key, value in (
                ("model_calls", self.model_calls),
                ("output_tokens", self.output_tokens),
                ("seconds", self.seconds),
                ("usd", self.usd),
            )
            if value is not None
        }


@dataclass(frozen=True)
class Turn:
    """How one turn ended."""

    #: ``success``, ``incomplete``, ``failed`` or ``cancelled``.
    status: str
    #: Why it ended: ``answered``, ``max_steps``, ``model_calls``,
    #: ``output_tokens``, ``deadline``, ``cost``, ``model_call``,
    #: ``unresolved_checks``, ...
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
    #: Dollars at the agent's ``prices``; None when it has none.
    cost_usd: Optional[float] = None
    #: What the answer checks still object to after the one repair.
    unresolved_checks: List[str] = field(default_factory=list)

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
        prices: The model's list prices, for ``Turn.cost_usd`` and ``Budget.usd``.
        answer_checks: Functions ``check(answer, tool_calls)`` run on each
            successful answer; one returns what is wrong, or None. The model
            is sent the problems once to repair. Any still found afterwards
            are in ``Turn.unresolved_checks`` and the turn is ``incomplete``.
        on_model_call: Called once per model request with its ``step``,
            ``seconds`` and ``usage``.
        engine: Path to the ``gaiacpp`` binary, when it is not found otherwise.

    One agent runs one turn at a time: a second ``run`` while one is
    running raises ``AgentBusy``. Use one agent per thread. Close it, or use
    it as a context manager, to stop the engine process.
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
        prices: Optional[Prices] = None,
        answer_checks: Sequence[AnswerCheck] = (),
        on_model_call: Optional[Callable[[Dict[str, Any]], None]] = None,
        engine: Optional[str] = None,
    ):
        self._tools: Dict[str, Tool] = {}
        for entry in tools:
            item = entry if isinstance(entry, Tool) else as_tool(entry)
            if item.name in self._tools:
                raise ValueError(f"Two tools are named {item.name!r}.")
            self._tools[item.name] = item
        if prices is not None and not isinstance(prices, Prices):
            raise TypeError(f"prices must be a Prices, got {type(prices).__name__}.")
        self._priced = prices is not None
        self.budget = self._checked_budget(budget)
        for check in answer_checks:
            if not callable(check):
                raise TypeError(
                    f"answer_checks must be callables, got {type(check).__name__}."
                )
        self._answer_checks = tuple(answer_checks)
        self.on_model_call = on_model_call
        self._turn_ids = itertools.count(1)
        self._running = threading.Lock()
        self._closed = False
        # A turn the engine is still running that this agent stopped following.
        self._out_of_step = False

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
            ("prices", prices.as_wire() if prices is not None else None),
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

    def _checked_budget(self, budget: Optional[Budget]) -> Optional[Budget]:
        if budget is None:
            return None
        if not isinstance(budget, Budget):
            raise TypeError(f"budget must be a Budget, got {type(budget).__name__}.")
        if budget.usd is not None and not self._priced:
            raise ValueError(
                "Budget.usd needs the agent's prices: pass "
                "prices=Prices(input=..., cached_input=..., output=...) in dollars "
                "per million tokens, or limit the turn with output_tokens or "
                "model_calls instead."
            )
        return budget

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
        for message in messages:
            if not isinstance(message, dict):
                raise TypeError(
                    "Each message must be a dict with 'role' and 'content', got "
                    f"{type(message).__name__}."
                )
        conversation = [dict(message) for message in messages]
        budget = self._checked_budget(budget) or self.budget
        if not self._running.acquire(blocking=False):
            raise AgentBusy(
                "This agent is already running a turn. One agent drives one "
                "engine process, so two turns at once would mix their events. "
                "Give each thread its own Agent, or wait for the first run() "
                "to return."
            )
        try:
            return self._run_checked(conversation, budget)
        finally:
            self._running.release()

    def _run_checked(
        self, conversation: List[Message], budget: Optional[Budget]
    ) -> Turn:
        turn = self._run_once(conversation, budget)
        if not turn.ok or not self._answer_checks:
            return turn
        problems = self._problems(turn.text, turn.tool_calls)
        if not problems:
            return turn
        # One repair, then the answer is returned with what is still wrong.
        repair = conversation + [
            {"role": "assistant", "content": turn.text},
            {"role": "user", "content": _REPAIR.format(problems=" ".join(problems))},
        ]
        second = self._run_once(repair, budget)
        merged = _merged(turn, second)
        if not second.ok:
            return replace(merged, unresolved_checks=problems)
        left = self._problems(merged.text, merged.tool_calls)
        if not left:
            return merged
        return replace(
            merged,
            status="incomplete",
            stop_reason=UNRESOLVED_CHECKS,
            unresolved_checks=left,
        )

    def _problems(self, text: str, tool_calls: List[Dict[str, Any]]) -> List[str]:
        found = []
        for check in self._answer_checks:
            problem = check(text, list(tool_calls))
            if problem:
                found.append(str(problem))
        return found

    def _run_once(self, messages: List[Message], budget: Optional[Budget]) -> Turn:
        turn_id = f"t{next(self._turn_ids)}"
        request: Dict[str, Any] = {
            "type": "turn",
            "id": turn_id,
            "messages": messages,
        }
        if budget is not None and budget.as_wire():
            request["budget"] = budget.as_wire()
        if self._closed:
            raise EngineError("This agent was closed. Create a new Agent.")
        if self._out_of_step:
            raise EngineError(
                "An earlier turn on this agent was interrupted before the engine "
                "finished it, so the two no longer agree on which turn is "
                "running. Close this agent and create a new one."
            )
        self._engine.send(request)
        self._out_of_step = True

        model_calls: List[Dict[str, Any]] = []
        callback_error: Optional[Exception] = None
        while True:
            try:
                event = self._engine.receive()
            except EngineError as refused:
                # The engine refused the turn before starting it, and is ready
                # for the next one.
                if refused.code == "bad_turn":
                    self._out_of_step = False
                raise
            kind = event["type"]
            if event.get("turn") not in (None, turn_id):
                raise EngineError(
                    f"The engine sent a {kind!r} event for turn "
                    f"{event.get('turn')!r} while {turn_id!r} was running."
                )
            if kind == "tool_request":
                self._send_tool_result(event)
            elif kind == "model_call":
                call = {k: event.get(k) for k in ("step", "seconds", "usage")}
                model_calls.append(call)
                if self.on_model_call is not None and callback_error is None:
                    try:
                        self.on_model_call(call)
                    except Exception as e:  # pylint: disable=broad-except
                        # Raised once the engine has finished the turn: leaving
                        # now would hand this turn's answer to the next run().
                        callback_error = e
            elif kind == "final":
                self._out_of_step = False
                if callback_error is not None:
                    raise callback_error
                return Turn(
                    status=event["status"],
                    stop_reason=event["stop_reason"],
                    text=event.get("text") or "",
                    tool_calls=list(event.get("tool_calls") or []),
                    usage=dict(event.get("usage") or {}),
                    seconds=float(event.get("seconds") or 0.0),
                    failure=event.get("failure"),
                    model_calls=model_calls,
                    cost_usd=event.get("cost_usd"),
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

    def _send_tool_result(self, request: Dict[str, Any]) -> None:
        """Answer one ``tool_request``; a result JSON cannot hold is a tool error."""
        try:
            self._engine.send(self._answer(request))
        except (TypeError, ValueError, RecursionError) as e:
            self._engine.send(
                {
                    "type": "tool_result",
                    "id": request["id"],
                    "error": (
                        f"{request.get('name')} returned a value that cannot be "
                        f"sent to the model as JSON ({type(e).__name__}: {e}). "
                        "Return dicts, lists, strings, numbers, booleans or None."
                    ),
                }
            )

    def close(self) -> None:
        """Stop the engine process."""
        self._closed = True
        self._engine.close()

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def _merged(first: Turn, second: Turn) -> Turn:
    """The repair turn's outcome, with both turns' work and spend."""
    usage = {
        key: int(first.usage.get(key, 0)) + int(second.usage.get(key, 0))
        for key in {*first.usage, *second.usage}
    }
    costs = (first.cost_usd, second.cost_usd)
    return replace(
        second,
        tool_calls=[*first.tool_calls, *second.tool_calls],
        usage=usage,
        seconds=first.seconds + second.seconds,
        model_calls=[*first.model_calls, *second.model_calls],
        cost_usd=None if None in costs else sum(costs),
    )
