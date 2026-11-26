# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model client that replies from a script, for tests that run the real loop."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterator, List, Optional, Union

from gaia.llm.base_client import LLMClient
from gaia.llm.tool_calls import tool_call, tool_calls_reply


class ScriptExhausted(BaseException):
    """The loop asked the scripted model for more replies than the test queued.

    A ``BaseException`` on purpose: the agent loop turns any ``Exception`` from
    a model call into a failed turn, which would hide the extra call.
    """


class ScriptedLLMClient(LLMClient):
    """Returns exactly the replies a test queued, in order, with no network.

    Unlike a mock of ``agent.chat``, this sits where a real model client sits,
    so ``process_query`` runs the whole loop: tool execution, the answer
    checks, token accounting.

    Example:
        model = ScriptedLLMClient()
        model.push(call="get_order", order_number="1001")
        model.push(say="It shipped on Monday.")
        agent = create_test_agent(MyAgent, llm_client=model)

        result = agent.process_query("Where is order 1001?")
        assert result["status"] == "success"
        assert result["result"] == "It shipped on Monday."

    A reply the test did not queue raises :class:`ScriptExhausted`, so a loop
    that makes an extra model call fails the test instead of passing on a
    canned default.
    """

    def __init__(self, replies: Optional[List[str]] = None):
        """*replies*: text replies to queue up front, each as ``push(say=...)``."""
        self._queue: List[Dict[str, Any]] = []
        self.call_history: List[Dict[str, Any]] = []
        self._last: Dict[str, Any] = {}
        for reply in replies or []:
            self.push(say=reply)

    @property
    def provider_name(self) -> str:
        return "scripted"

    def push(
        self,
        say: Optional[str] = None,
        *,
        call: Optional[str] = None,
        finish_reason: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
        raises: Optional[BaseException] = None,
        delay_s: float = 0.0,
        **tool_args: Any,
    ) -> "ScriptedLLMClient":
        """Queue the model's next reply.

        Args:
            say: Text the model writes. With *call*, text beside the tool call.
            call: Name of a tool to call; the remaining keyword arguments are
                its arguments.
            finish_reason: Reported for this reply. Defaults to ``"stop"``, or
                ``"tool_calls"`` for a tool call. ``"length"`` plays a reply
                cut off at the output-token cap.
            usage: OpenAI-shape token counts reported for this reply.
            raises: An exception the client raises instead of replying.
            delay_s: Seconds the client waits before it replies or raises,
                to play a slow model against a deadline. A tool argument of
                this name cannot be passed through ``push``.
        """
        if raises is None and say is None and call is None:
            raise ValueError("push() needs say=, call= or raises=.")
        if tool_args and call is None:
            raise ValueError(
                f"push() got tool arguments {sorted(tool_args)} without call=."
            )
        self._queue.append(
            {
                "say": say,
                "call": call,
                "args": dict(tool_args),
                "finish_reason": finish_reason,
                "usage": usage,
                "raises": raises,
                "delay_s": delay_s,
            }
        )
        return self

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[str, Iterator[str]]:
        prompt = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                prompt = message.get("content") or ""
                break
        self.call_history.append(
            {
                "method": "chat",
                "messages": [dict(m) for m in messages],
                "prompt": prompt,
                "model": model,
                "kwargs": dict(kwargs),
                "timestamp": time.time(),
            }
        )
        self._last = {}
        if not self._queue:
            raise ScriptExhausted(
                f"The scripted model was asked for reply {len(self.call_history)} "
                "but the script has no more. Queue another with push(), or "
                "check why the loop made an extra model call. Last message "
                f"sent: {str(prompt)[:200]!r}"
            )
        reply = self._queue.pop(0)
        if reply["delay_s"] > 0:
            time.sleep(reply["delay_s"])
        if reply["raises"] is not None:
            raise reply["raises"]
        text = self._render(reply, native=bool(kwargs.get("tools")))
        self._last = {
            "usage": reply["usage"],
            "finish_reason": reply["finish_reason"]
            or ("tool_calls" if reply["call"] else "stop"),
        }
        return iter([text]) if stream else text

    def _render(self, reply: Dict[str, Any], native: bool) -> str:
        if reply["call"] is None:
            return reply["say"]
        if native:
            call_id = f"call_{len(self.call_history)}"
            return tool_calls_reply(
                [tool_call(reply["call"], reply["args"], call_id)],
                content=reply["say"],
                finish_reason=reply["finish_reason"] or "tool_calls",
            )
        # The in-text format a model without native tool calls is asked for.
        return json.dumps({"tool": reply["call"], "tool_args": reply["args"]})

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[str, Iterator[str]]:
        return self.chat(
            [{"role": "user", "content": prompt}], model=model, stream=stream, **kwargs
        )

    def get_last_usage(self) -> Optional[dict]:
        return self._last.get("usage")

    def get_last_finish_reason(self) -> Optional[str]:
        return self._last.get("finish_reason")

    # The MockLLMProvider surface the assertion helpers read.

    @property
    def was_called(self) -> bool:
        return bool(self.call_history)

    @property
    def call_count(self) -> int:
        return len(self.call_history)

    @property
    def last_prompt(self) -> Optional[str]:
        return self.call_history[-1]["prompt"] if self.call_history else None

    @property
    def remaining(self) -> int:
        """Queued replies the loop has not asked for yet."""
        return len(self._queue)

    def set_responses(self, responses: List[str]) -> None:
        """Replace the queue with these text replies."""
        self._queue = []
        for reply in responses:
            self.push(say=reply)

    def reset(self) -> None:
        """Drop the call history; the queue is left as it is."""
        self.call_history = []
        self._last = {}
