# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Claude (Anthropic) provider — chat + native tool calling; no embeddings.

Tool-calling turns are re-encoded into the same sentinel envelope the Lemonade
provider emits (``NATIVE_TOOL_CALLS_PREFIX`` / ``{"__tool_calls__": [...]}``),
so the agent loop's response parser works unchanged against either backend.
"""

import copy
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

# The factory imports this module only when the claude provider is requested,
# so the guarded import never taxes the Lemonade-only path.
try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from ..base_client import LLMClient
from .lemonade import _NATIVE_TC_KEY

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"

#: Context window every current Claude model provides, in tokens. Used to size
#: tool-result truncation: without it the agent squeezes a 200K-context model
#: into the local NPU's 32K budget and drops results it had ample room for.
#: Deliberately the floor of the range rather than the largest on offer.
CLAUDE_CTX_SIZE = 200_000

#: kwargs the Anthropic Messages API accepts from GAIA's chat layer. Everything
#: else (llama.cpp sampling knobs, ChatML ``stop`` token lists, temperature —
#: rejected with a 400 on current Claude models) is dropped with a debug log.
_PASSTHROUGH_KWARGS = frozenset({"max_tokens", "stop_sequences", "metadata", "timeout"})

#: ``max_tokens`` caps thinking + answer text together on current Claude
#: models, so small caps sized for Lemonade truncate mid-answer.
_MIN_MAX_TOKENS = 8192

#: Anthropic prompt caching is opt-in: without a ``cache_control`` breakpoint
#: nothing is ever cached. Anthropic renders ``tools`` -> ``system`` ->
#: ``messages``, so a breakpoint at the end of the system block covers the tool
#: schemas *and* the system prompt — the whole fixed prefill an agent re-sends
#: on every call of every turn.
#:
#: Caching is a prefix match with no partial credit, so the prefill gets two
#: markers: with only the system marker, one byte of drift anywhere in the
#: system prompt (a memory confidence score, a skill body swapping in) would
#: throw away the tool schemas as well. The marker after the last tool keeps
#: that segment readable whenever the tools themselves are unchanged.
#:
#: A third, rolling marker sits on the newest message so the conversation is
#: cached too: each call writes its history, and the next call — the same
#: history plus new turns — reads it back instead of re-billing it in full.
_CACHE_CONTROL = {"type": "ephemeral"}

#: Anthropic rejects a request carrying more breakpoints than this.
_MAX_CACHE_BREAKPOINTS = 4

#: Opaque reasoning blocks: never answer text, never a ``cache_control``
#: target, and only valid when sent back byte-for-byte.
_THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})

#: Block types a replayed tool turn may contain. A turn holding anything else
#: (a server tool block, say) is not kept, since it can't be rebuilt faithfully.
_REPLAYABLE_BLOCK_TYPES = _THINKING_BLOCK_TYPES | {"text", "tool_use"}

#: Tool turns whose original blocks are kept for replay. Older turns go back
#: without their thinking, which the API accepts.
_MAX_REPLAY_TURNS = 256

#: OpenAI ``tool_choice`` strings with a direct Anthropic equivalent.
_TOOL_CHOICE_MAP = {"none": {"type": "none"}, "auto": {"type": "auto"}}

_FINISH_REASON_MAP = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
}


def _require_anthropic():
    if anthropic is None:
        raise ImportError(
            "The 'anthropic' package is required for --use-claude. "
            "Install it with: uv pip install anthropic "
            '(or the eval extras: uv pip install -e ".[eval]")'
        )
    return anthropic


#: Usage counters read off every response. ``input_tokens`` is the uncached
#: remainder — the two cache fields carry the rest of the prompt.
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _usage_field(usage: Any, name: str) -> int:
    """One usage counter as a non-negative int. Absent or non-numeric reads 0 —
    older API versions omit the cache fields entirely."""
    value = getattr(usage, name, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


class _UsageTotals:
    """Usage accumulated across a stream's events.

    ``message_start`` is the only event carrying the cache counters;
    ``message_delta`` later reports the final ``output_tokens``. A counter is
    only ever replaced by a positive value, so a later event that omits a field
    (or reports it as 0) cannot erase what an earlier one established.
    """

    def __init__(self) -> None:
        for field in _USAGE_FIELDS:
            setattr(self, field, 0)

    def absorb(self, usage: Any) -> None:
        if usage is None:
            return
        for field in _USAGE_FIELDS:
            value = _usage_field(usage, field)
            if value:
                setattr(self, field, value)


def _cached_system(system: str) -> List[dict]:
    """System prompt as one text block carrying the cache breakpoint.

    The history is cached by the rolling marker on the newest message
    (``_cache_last_message``), but that entry is only reusable while every
    earlier message is byte-identical. This marker pins a second boundary at
    the tools+system prefix, which survives anything that rewrites history —
    a new session, a trimmed or edited turn — so the fixed prefill still reads
    from cache when the conversation entry cannot.
    """
    return [{"type": "text", "text": system, "cache_control": dict(_CACHE_CONTROL)}]


def _can_carry_breakpoint(block: Any) -> bool:
    if not isinstance(block, dict) or block.get("type") in _THINKING_BLOCK_TYPES:
        return False
    # The API refuses cache_control on an empty text block.
    return not (block.get("type") == "text" and not block.get("text"))


def _cache_last_message(messages: List[dict]) -> List[dict]:
    """Rolling breakpoint on the last cacheable block of the newest message.

    Copies rather than mutating: the agent appends to and resends the same
    list, so a marker left in it would add a breakpoint per turn.
    """
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str) and content:
        marked_content = [
            {"type": "text", "text": content, "cache_control": dict(_CACHE_CONTROL)}
        ]
    elif isinstance(content, list):
        marked_content = list(content)
        for index in range(len(marked_content) - 1, -1, -1):
            if _can_carry_breakpoint(marked_content[index]):
                marked_content[index] = {
                    **marked_content[index],
                    "cache_control": dict(_CACHE_CONTROL),
                }
                break
        else:
            # Thinking-only or empty: the tools+system markers still apply.
            logger.debug("Newest Claude message has no block that can be cached")
            return messages
    else:
        return messages
    marked = list(messages)
    marked[-1] = {**last, "content": marked_content}
    return marked


def _count_breakpoints(params: Dict[str, Any]) -> int:
    """Block-level ``cache_control`` markers, the unit Anthropic's limit counts.

    Walks block lists only — never into ``tool_use.input`` — so a tool argument
    that happens to be named ``cache_control`` is not mistaken for a marker.
    """

    def in_blocks(blocks: Any) -> int:
        if not isinstance(blocks, list):
            return 0
        total = 0
        for block in blocks:
            if isinstance(block, dict):
                total += "cache_control" in block
                if block.get("type") == "tool_result":
                    total += in_blocks(block.get("content"))
        return total

    return (
        in_blocks(params.get("tools"))
        + in_blocks(params.get("system"))
        + sum(in_blocks(m.get("content")) for m in params["messages"])
    )


def _without_cache_control(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: _without_cache_control(v)
            for k, v in node.items()
            if k != "cache_control"
        }
    if isinstance(node, list):
        return [_without_cache_control(v) for v in node]
    return node


def _canonical(node: Any) -> bytes:
    """Stable bytes for prefix comparison. Markers are excluded: they move every
    call, and the API's own prefix check ignores them too."""
    return json.dumps(
        _without_cache_control(node), sort_keys=True, ensure_ascii=False, default=str
    ).encode("utf-8")


def _has_thinking(message: dict) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") in _THINKING_BLOCK_TYPES for b in content
    )


def _without_thinking(message: dict) -> dict:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    return {
        **message,
        "content": [
            b
            for b in content
            if not (isinstance(b, dict) and b.get("type") in _THINKING_BLOCK_TYPES)
        ],
    }


#: What GAIA's history keeps of a tool turn: its joined text and its calls.
_TurnView = Tuple[str, Tuple[Tuple[Optional[str], Any, bytes], ...]]


def _turn_view(blocks: List[Any], restore=lambda name: name) -> _TurnView:
    text = "".join(
        b.get("text") or ""
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )
    calls = tuple(
        (b.get("id"), restore(b.get("name")), _canonical(b.get("input") or {}))
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "tool_use"
    )
    return text, calls


def _api_block(block: Any) -> dict:
    """A response block (or a stream's opening block) as the request-side dict
    that sends it back. Thinking fields are copied as-is, never rewritten."""
    btype = block.type
    if btype == "thinking":
        return {
            "type": "thinking",
            "thinking": getattr(block, "thinking", "") or "",
            "signature": getattr(block, "signature", "") or "",
        }
    if btype == "redacted_thinking":
        return {"type": "redacted_thinking", "data": getattr(block, "data", "") or ""}
    if btype == "text":
        return {"type": "text", "text": getattr(block, "text", "") or ""}
    if btype == "tool_use":
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": getattr(block, "input", None) or {},
        }
    return {"type": btype}


@dataclass
class _ReplayTurn:
    """A tool turn exactly as Claude produced it, thinking blocks included."""

    blocks: List[dict]
    view: _TurnView
    prefix_digest: str


def _cache_last_tool(tools: List[dict]) -> List[dict]:
    """Breakpoint on the final tool definition — the end of the tools segment.

    Copies rather than mutating: the agent hands the same list to every call.
    """
    marked = list(tools)
    marked[-1] = {**marked[-1], "cache_control": dict(_CACHE_CONTROL)}
    return marked


class ClaudeProvider(LLMClient):
    """Claude (Anthropic) provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_CLAUDE_MODEL,
        system_prompt: Optional[str] = None,
        max_retries: int = 3,
        timeout: float = 300.0,
        **_kwargs,
    ):
        sdk = _require_anthropic()

        # The repo keeps ANTHROPIC_API_KEY in .env — same load as gaia.eval.claude.
        from gaia.env import load_env  # pylint: disable=import-outside-toplevel

        load_env()

        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "ANTHROPIC_API_KEY not found in environment.\n\n"
                "The Claude backend (--use-claude) needs an Anthropic credential:\n"
                "  1. SUBSCRIPTION (recommended if you have Claude Code Max):\n"
                "     Run `claude setup-token`, follow the browser prompt, then\n"
                "     export the printed token as ANTHROPIC_API_KEY.\n"
                "  2. API KEY (billed to your Anthropic console):\n"
                "     export ANTHROPIC_API_KEY=sk-ant-...\n"
                "Either can also go in a `.env` file at the repo root."
            )

        client_kwargs: Dict[str, Any] = {"max_retries": max_retries, "timeout": timeout}
        if key.startswith("sk-ant-oat"):
            # OAuth tokens ride Authorization: Bearer, not x-api-key.
            client_kwargs["auth_token"] = key
            client_kwargs["default_headers"] = {"anthropic-beta": "oauth-2025-04-20"}
        else:
            client_kwargs["api_key"] = key

        self._anthropic = sdk
        self._client = sdk.Anthropic(**client_kwargs)
        self._model = (
            model if model and model.startswith("claude-") else DEFAULT_CLAUDE_MODEL
        )
        if model and not model.startswith("claude-"):
            logger.warning(
                "Ignoring non-Claude model id %r for the Claude provider; using %s",
                model,
                self._model,
            )
        self._system_prompt = system_prompt
        self._last_usage: Optional[dict] = None
        self._last_finish_reason: Optional[str] = None
        self._last_ttft_seconds: Optional[float] = None
        # Keyed by the turn's tool_use ids, which Anthropic makes unique.
        self._replay_turns: "OrderedDict[tuple, _ReplayTurn]" = OrderedDict()
        # Sanitized-name → GAIA-name; rebuilt per request by _to_anthropic_tools.

    @property
    def provider_name(self) -> str:
        return "Claude"

    # ── request shaping ─────────────────────────────────────────────────

    def _resolve_model(self, model: Optional[str]) -> str:
        """First claude-* candidate wins — callers routinely pass their local
        Lemonade model id through the shared ``model`` kwarg."""
        for candidate in (model, self._model, DEFAULT_CLAUDE_MODEL):
            if candidate and candidate.startswith("claude-"):
                return candidate
        return DEFAULT_CLAUDE_MODEL

    #: Anthropic's tool-name contract. GAIA names can be wider — skill tools are
    #: namespaced ``<skill>/<tool>`` and the ``/`` 400s the whole request — so
    #: names are sanitized outbound and mapped back on returned tool_use blocks.
    _TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")

    def _api_tool_name(self, name: str) -> str:
        if self._TOOL_NAME_RE.fullmatch(name):
            return name
        return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:128]

    @staticmethod
    def _restore_tool_name(name_map: Dict[str, str], api_name: str) -> str:
        if api_name not in name_map:
            # Every outbound name is registered, so a miss means request and
            # response were shaped against different tool sets. The message
            # reaches the user verbatim, so the diagnostic detail goes to the
            # log rather than into the exception.
            logger.error(
                "Tool %r is not in this request's outbound name map, so the "
                "response was parsed against a different tool set than the one "
                "sent. Registered: %s",
                api_name,
                sorted(name_map),
            )
            raise RuntimeError(
                f"Claude returned tool {api_name!r}, which was not in the tool "
                "set sent with this request."
            )
        return name_map[api_name]

    def _to_anthropic_tools(
        self, tools: Optional[List[dict]]
    ) -> tuple[Optional[List[dict]], Dict[str, str]]:
        """OpenAI ``{"type":"function","function":{...}}`` → Anthropic shape.

        Returns the converted tools *and* the restore map. The map belongs to
        the call, not the provider: one instance can serve two requests with
        different tool sets, and a map on ``self`` lets the second overwrite the
        first's names mid-flight.
        """
        name_map: Dict[str, str] = {}
        if not tools:
            return None, name_map
        converted = []
        for tool in tools:
            fn = tool.get("function") if tool.get("type") == "function" else None
            entry = (
                dict(tool)  # already Anthropic-shaped (name + input_schema)
                if fn is None
                else {
                    # .get so a nameless entry hits the guard below rather
                    # than dying on a bare KeyError.
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
            original = entry.get("name")
            if not original:
                raise ValueError(
                    "Tool definition has no name: "
                    f"{tool!r}. Anthropic requires a name on every tool entry "
                    "(custom, server, and client-side alike), and GAIA needs "
                    "one to route the model's call back to a registered tool."
                )
            api_name = self._api_tool_name(original)
            # Register identity names too: `write/file` sanitizes onto the
            # builtin `write_file`, and only a full map can see that clash.
            if api_name in name_map:
                clash = name_map[api_name]
                raise ValueError(
                    f"Tool names {clash!r} and {original!r} both map to "
                    f"{api_name!r} for the Anthropic API — the model's call "
                    "could not be routed back unambiguously. Rename one."
                )
            name_map[api_name] = original
            entry["name"] = api_name
            converted.append(entry)
        return converted, name_map

    def _split_system(self, messages: List[dict]) -> tuple:
        """Hoist system text and translate OpenAI tool history for Anthropic."""
        system_parts: List[str] = []
        cleaned: List[dict] = []
        index = 0
        while index < len(messages):
            msg = messages[index]
            role = msg.get("role", "user")
            content = msg.get("content")
            if role == "system":
                if content:
                    system_parts.append(str(content))
                index += 1
                continue
            if role == "assistant" and msg.get("tool_calls"):
                blocks = self._tool_use_content(msg)
                expected_ids = {
                    block["id"] for block in blocks if block["type"] == "tool_use"
                }
                result_messages = []
                result_index = index + 1
                while result_index < len(messages) and expected_ids:
                    result = messages[result_index]
                    if result.get("role") != "tool":
                        break
                    tool_call_id = result.get("tool_call_id")
                    if tool_call_id not in expected_ids:
                        break
                    result_messages.append(result)
                    expected_ids.remove(tool_call_id)
                    result_index += 1
                if not expected_ids:
                    cleaned.append({"role": "assistant", "content": blocks})
                    for result in result_messages:
                        self._append_tool_result(cleaned, result)
                    index = result_index
                    continue
                raise ValueError(
                    "Claude tool-call history requires a complete, immediately "
                    "adjacent result group; missing tool_call_id(s): "
                    + ", ".join(sorted(expected_ids))
                    + ". Append every role='tool' result directly after the "
                    "assistant turn that requested it — any user/system message "
                    "injected between them must come after the group."
                )
            if role == "tool":
                cleaned.append(
                    {
                        "role": "user",
                        "content": (
                            f"[Tool result: {msg.get('name', 'tool')}] "
                            f"{msg.get('content', '')}"
                        ),
                    }
                )
                index += 1
                continue
            if content is None or content == "":
                # Anthropic rejects empty message content outright.
                logger.debug("Dropping empty %s message for Claude request", role)
                index += 1
                continue
            cleaned.append({"role": role, "content": content})
            index += 1
        system = "\n\n".join(system_parts) if system_parts else self._system_prompt
        return system, cleaned

    @staticmethod
    def _tool_use_content(message: dict) -> List[dict]:
        content = message.get("content")
        blocks = []
        if content:
            if isinstance(content, list):
                blocks.extend(content)
            else:
                blocks.append({"type": "text", "text": str(content)})
        for tool_call in message["tool_calls"]:
            function = tool_call.get("function") or {}
            tool_call_id = tool_call.get("id")
            name = function.get("name")
            if not tool_call_id or not name:
                raise ValueError("Claude tool calls require both an id and a name.")
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Claude tool call {tool_call_id!r} has invalid JSON arguments."
                    ) from exc
            if not isinstance(arguments, dict):
                raise ValueError(
                    f"Claude tool call {tool_call_id!r} arguments must decode to an object."
                )
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call_id,
                    "name": name,
                    "input": arguments,
                }
            )
        return blocks

    @staticmethod
    def _append_tool_result(cleaned: List[dict], message: dict) -> None:
        tool_call_id = message.get("tool_call_id")
        if not tool_call_id:
            raise ValueError("Claude tool results require a tool_call_id.")
        block = {
            "type": "tool_result",
            "tool_use_id": tool_call_id,
            "content": message.get("content") or "[tool returned no output]",
        }
        if (
            cleaned
            and cleaned[-1]["role"] == "user"
            and isinstance(cleaned[-1]["content"], list)
            and all(
                isinstance(item, dict) and item.get("type") == "tool_result"
                for item in cleaned[-1]["content"]
            )
        ):
            cleaned[-1]["content"].append(block)
            return
        cleaned.append({"role": "user", "content": [block]})

    def _build_params(
        self,
        model: str,
        messages: List[dict],
        tools: Optional[List[dict]],
        kwargs: dict,
        tool_choice: Optional[str] = None,
    ) -> tuple[dict, Dict[str, str], str]:
        """Returns the request params, the tool-name restore map, and the digest
        of the request's prefix — the prefix any turn it produces is bound to."""
        system, cleaned = self._split_system(messages)
        if not cleaned:
            raise ValueError(
                "Claude request has no user/assistant messages after hoisting the "
                "system prompt — nothing to send."
            )

        dropped = sorted(k for k in kwargs if k not in _PASSTHROUGH_KWARGS)
        if dropped:
            logger.debug(
                "Dropping non-Anthropic kwargs for Claude request: %s", dropped
            )

        anthropic_tools, name_map = self._to_anthropic_tools(tools)
        replayed, prefix_digest = self._replay_thinking(
            system, anthropic_tools, cleaned
        )
        params: Dict[str, Any] = {
            "model": model,
            "messages": _cache_last_message(replayed),
        }
        for k in _PASSTHROUGH_KWARGS:
            if k in kwargs and kwargs[k] is not None:
                params[k] = kwargs[k]
        params["max_tokens"] = max(int(params.get("max_tokens") or 0), _MIN_MAX_TOKENS)
        if anthropic_tools:
            params["tools"] = _cache_last_tool(anthropic_tools)
        if tool_choice is not None:
            if not isinstance(tool_choice, str) or tool_choice not in _TOOL_CHOICE_MAP:
                raise ValueError(
                    f"The Claude provider does not support tool_choice="
                    f"{tool_choice!r}. Use one of: {', '.join(_TOOL_CHOICE_MAP)}."
                )
            if not anthropic_tools:
                raise ValueError(
                    f"tool_choice={tool_choice!r} was passed without tools; it "
                    "only applies to a request that offers tools. Pass tools= "
                    "as well, or drop tool_choice."
                )
            params["tool_choice"] = dict(_TOOL_CHOICE_MAP[tool_choice])
        if system:
            params["system"] = _cached_system(system)
        breakpoints = _count_breakpoints(params)
        if breakpoints > _MAX_CACHE_BREAKPOINTS:
            raise ValueError(
                f"Claude request carries {breakpoints} cache_control breakpoints; "
                f"Anthropic allows at most {_MAX_CACHE_BREAKPOINTS}. The Claude "
                "provider already places up to 3 (last tool, system prompt, "
                "newest message), so remove cache_control from the messages or "
                "tools you pass in."
            )
        return params, name_map, prefix_digest

    # ── thinking replay ─────────────────────────────────────────────────
    #
    # Thinking goes back unmodified with its tool turn, and only while
    # everything before it is unchanged (else a 400). The kept blocks must be an
    # unbroken run of the original sequence: dropping from the front is fine, a
    # gap in the middle is not.
    # https://platform.claude.com/docs/en/build-with-claude/preserved-thinking

    def _replay_thinking(
        self, system: Optional[str], tools: Optional[List[dict]], cleaned: List[dict]
    ) -> Tuple[List[dict], str]:
        """Restore the newest unbroken run of assistant turns' original blocks.

        Walks the messages in order with a running digest, so one edit voids
        every later turn, as the API does. Thinking blocks are left out of the
        digest: the API chains each block to the previous one rather than binding
        it as prefix, so dropping the oldest turns' thinking first stays valid.

        Any assistant turn whose thinking can't be restored (a final answer, a
        cut-off stream, an edited turn) ends the run: every older turn goes back
        without its thinking, so the kept blocks never have a gap.
        """
        digest = hashlib.sha256(_canonical({"system": system, "tools": tools}))
        out: List[dict] = []
        assistant_turns: List[Tuple[int, Optional[bool]]] = []
        for message in cleaned:
            if message["role"] == "assistant":
                restored = self._with_original_blocks(message, digest.hexdigest())
                # None: the caller sent its own thinking, which is left alone.
                kept: Optional[bool]
                if restored is not message:
                    kept = True
                elif _has_thinking(message):
                    kept = None
                else:
                    kept = False
                assistant_turns.append((len(out), kept))
                message = restored
            out.append(message)
            digest.update(b"\n" + _canonical(_without_thinking(message)))

        broken = False
        dropped = 0
        for index, kept in reversed(assistant_turns):
            if kept is False:
                broken = True
            elif kept and broken:
                # Strip only the thinking, so the rest stays byte-identical to
                # what earlier requests sent.
                out[index] = _without_thinking(out[index])
                dropped += 1
        if dropped:
            logger.debug(
                "Not replaying thinking for %d older tool turn(s): a later "
                "assistant turn has no stored thinking, and a gap would be rejected",
                dropped,
            )
        return out, digest.hexdigest()

    def _with_original_blocks(self, message: dict, prefix_digest: str) -> dict:
        """The turn with its stored original blocks, or ``message`` itself when
        they can't be restored."""
        content = message.get("content")
        if not isinstance(content, list):
            return message
        ids = tuple(
            b.get("id")
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        )
        turn = self._replay_turns.get(ids) if ids else None
        if turn is None:
            return message
        if _has_thinking(message):
            return message  # the caller already sent Anthropic-shaped thinking
        if _turn_view(content) != turn.view:
            logger.debug(
                "Not replaying thinking for tool turn %s: its text or tool calls "
                "were edited since Claude produced it",
                list(ids),
            )
            return message
        if prefix_digest != turn.prefix_digest:
            logger.debug(
                "Not replaying thinking for tool turn %s: the system prompt, tools "
                "or earlier messages changed since Claude produced it",
                list(ids),
            )
            return message
        return {**message, "content": copy.deepcopy(turn.blocks)}

    def _remember_turn(
        self, blocks: List[dict], name_map: Dict[str, str], prefix_digest: str
    ) -> None:
        """Keep a tool turn that carries thinking so the next request can send
        it back verbatim. ``blocks`` are in the API's order and shape."""
        if not any(b["type"] in _THINKING_BLOCK_TYPES for b in blocks):
            return
        ids = tuple(b["id"] for b in blocks if b["type"] == "tool_use")
        if not ids:
            return
        unreplayable = sorted(
            {b["type"] for b in blocks if b["type"] not in _REPLAYABLE_BLOCK_TYPES}
        )
        unsigned = any(
            (b["type"] == "thinking" and not b.get("signature"))
            or (b["type"] == "redacted_thinking" and not b.get("data"))
            for b in blocks
        )
        if unreplayable or unsigned:
            logger.debug(
                "Not keeping thinking for tool turn %s: %s",
                list(ids),
                (
                    f"it holds {unreplayable} blocks"
                    if unreplayable
                    else "a thinking block arrived without its signature"
                ),
            )
            return
        view = _turn_view(
            blocks, restore=lambda name: self._restore_tool_name(name_map, name)
        )
        self._replay_turns[ids] = _ReplayTurn(blocks, view, prefix_digest)
        self._replay_turns.move_to_end(ids)
        while len(self._replay_turns) > _MAX_REPLAY_TURNS:
            self._replay_turns.popitem(last=False)

    def _remember_streamed_turn(
        self,
        api_blocks: Dict[int, dict],
        tool_slots: Dict[int, dict],
        name_map: Dict[str, str],
        prefix_digest: str,
    ) -> None:
        blocks = [api_blocks[i] for i in sorted(api_blocks)]
        for index, slot in tool_slots.items():
            if index not in api_blocks:
                continue
            try:
                api_blocks[index]["input"] = json.loads(slot["function"]["arguments"])
            except json.JSONDecodeError:
                logger.debug(
                    "Not keeping thinking for tool turn %s: its streamed arguments "
                    "are not valid JSON",
                    slot["id"],
                )
                return
        self._remember_turn(blocks, name_map, prefix_digest)

    # ── error translation ───────────────────────────────────────────────

    def _raise_actionable(self, exc: Exception) -> None:
        anthropic = self._anthropic
        if isinstance(exc, anthropic.AuthenticationError):
            raise RuntimeError(
                "Anthropic rejected the credential (401). Check ANTHROPIC_API_KEY "
                "in your environment or repo .env — regenerate it at "
                "https://console.anthropic.com/ or via `claude setup-token`."
            ) from exc
        if isinstance(exc, anthropic.NotFoundError):
            raise RuntimeError(
                f"Anthropic model not found (404): {exc}. Pass a valid id via "
                "--claude-model (e.g. claude-sonnet-5)."
            ) from exc
        if isinstance(exc, anthropic.RateLimitError):
            raise RuntimeError(
                f"Anthropic rate limit hit (429): {exc}. Wait for the retry window "
                "or lower request volume; see console.anthropic.com for limits."
            ) from exc
        if isinstance(exc, anthropic.APIConnectionError):
            raise RuntimeError(
                f"Could not reach the Anthropic API: {exc}. Check network/proxy "
                "connectivity to api.anthropic.com, then retry."
            ) from exc
        if isinstance(exc, anthropic.APIStatusError):
            raise RuntimeError(
                f"Anthropic API error (HTTP {exc.status_code}): {exc.message}. "
                "See https://status.anthropic.com if this persists."
            ) from exc
        raise exc

    # ── chat ────────────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        return self.chat(
            [{"role": "user", "content": prompt}],
            model=model,
            stream=stream,
            **kwargs,
        )

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        stream: bool = False,
        tools: Optional[List[dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        self._last_usage = None
        self._last_finish_reason = None
        self._last_ttft_seconds = None
        params, name_map, prefix_digest = self._build_params(
            self._resolve_model(model), messages, tools, kwargs, tool_choice
        )

        if stream:
            return self._stream_chat(params, name_map, prefix_digest)

        start = time.monotonic()
        try:
            response = self._client.messages.create(**params)
        except Exception as exc:  # translated to actionable errors below
            self._raise_actionable(exc)
            raise  # unreachable — _raise_actionable always raises
        return self._parse_response(
            response, time.monotonic() - start, name_map, prefix_digest
        )

    def _parse_response(
        self,
        response,
        elapsed: float,
        name_map: Dict[str, str],
        prefix_digest: str,
    ) -> str:
        text_parts: List[str] = []
        tool_calls: List[dict] = []
        api_blocks: List[dict] = []
        for block in response.content:
            api_blocks.append(_api_block(block))
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": self._restore_tool_name(name_map, block.name),
                            "arguments": json.dumps(block.input or {}),
                        },
                    }
                )
            # thinking / redacted_thinking blocks are never answer text.

        self._capture_usage(response.usage, elapsed)

        stop_reason = response.stop_reason or ""
        if stop_reason == "refusal":
            raise RuntimeError(
                "Claude declined this request (stop_reason=refusal). Rephrase the "
                "query or check stop_details in the Anthropic console logs."
            )
        finish_reason = _FINISH_REASON_MAP.get(stop_reason, stop_reason)
        self._last_finish_reason = finish_reason or None
        if tool_calls:
            self._remember_turn(api_blocks, name_map, prefix_digest)
            return json.dumps(
                {
                    _NATIVE_TC_KEY: tool_calls,
                    "finish_reason": finish_reason,
                    "content": "".join(text_parts) or None,
                }
            )
        return "".join(text_parts)

    def _stream_chat(
        self, params: dict, name_map: Dict[str, str], prefix_digest: str
    ) -> Iterator[str]:
        start = time.monotonic()
        try:
            events = self._client.messages.create(**params, stream=True)
        except Exception as exc:
            self._raise_actionable(exc)
            raise  # unreachable — _raise_actionable always raises

        text_parts: List[str] = []
        tool_slots: Dict[int, dict] = {}
        api_blocks: Dict[int, dict] = {}
        stop_reason = ""
        usage_totals = _UsageTotals()
        try:
            for event in events:
                etype = event.type
                if etype == "message_start":
                    # The only event carrying the cache counters; message_delta
                    # then supersedes output_tokens with the final figure.
                    usage_totals.absorb(getattr(event.message, "usage", None))
                elif etype == "content_block_start":
                    block = event.content_block
                    api_blocks[event.index] = _api_block(block)
                    if block.type == "tool_use":
                        tool_slots[event.index] = {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": self._restore_tool_name(name_map, block.name),
                                "arguments": "",
                            },
                        }
                elif etype == "content_block_delta":
                    if self._last_ttft_seconds is None:
                        self._last_ttft_seconds = time.monotonic() - start
                    delta = event.delta
                    api_block = api_blocks.get(event.index, {})
                    if delta.type == "text_delta":
                        if api_block.get("type") == "text":
                            api_block["text"] += delta.text
                        text_parts.append(delta.text)
                        yield delta.text
                    elif delta.type == "input_json_delta":
                        slot = tool_slots.get(event.index)
                        if slot is not None:
                            slot["function"]["arguments"] += delta.partial_json
                    elif delta.type == "thinking_delta":
                        if api_block.get("type") == "thinking":
                            api_block["thinking"] += delta.thinking
                    elif delta.type == "signature_delta":
                        if api_block.get("type") == "thinking":
                            api_block["signature"] += delta.signature
                elif etype == "message_delta":
                    stop_reason = (
                        getattr(event.delta, "stop_reason", None) or stop_reason
                    )
                    usage_totals.absorb(getattr(event, "usage", None))
        except Exception as exc:
            self._raise_actionable(exc)
            raise  # unreachable — _raise_actionable always raises

        self._capture_usage(usage_totals, time.monotonic() - start)
        finish_reason = _FINISH_REASON_MAP.get(stop_reason, stop_reason)
        self._last_finish_reason = finish_reason or None

        if stop_reason == "refusal":
            raise RuntimeError(
                "Claude declined this request (stop_reason=refusal). Rephrase the "
                "query or check stop_details in the Anthropic console logs."
            )
        if tool_slots:
            for slot in tool_slots.values():
                if not slot["function"]["arguments"]:
                    slot["function"]["arguments"] = "{}"
            self._remember_streamed_turn(
                api_blocks, tool_slots, name_map, prefix_digest
            )
            yield json.dumps(
                {
                    _NATIVE_TC_KEY: [tool_slots[i] for i in sorted(tool_slots)],
                    "finish_reason": finish_reason,
                    "content": "".join(text_parts) or None,
                }
            )

    def _capture_usage(self, usage: Any, elapsed: float) -> None:
        """Record one call's usage, cache counters included.

        ``usage.input_tokens`` from Anthropic is the *uncached remainder*, not
        the prompt size: the prompt is that plus the cached reads and writes.
        Reporting the remainder as ``prompt_tokens`` would make a working cache
        look like the prompt had shrunk by 90%.
        """
        uncached = _usage_field(usage, "input_tokens")
        cache_read = _usage_field(usage, "cache_read_input_tokens")
        cache_write = _usage_field(usage, "cache_creation_input_tokens")
        output_tokens = _usage_field(usage, "output_tokens")
        prompt_tokens = uncached + cache_read + cache_write

        self._last_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": prompt_tokens + output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "uncached_input_tokens": uncached,
            "tokens_per_second": (
                round(output_tokens / elapsed, 2)
                if elapsed > 0 and output_tokens
                else 0.0
            ),
        }

    # ── stats ───────────────────────────────────────────────────────────

    def get_performance_stats(self) -> dict:
        return dict(self._last_usage) if self._last_usage else {}

    def get_last_usage(self) -> Optional[dict]:
        """Token-usage dict from the most recent ``chat()`` call, or ``None``."""
        return self._last_usage

    def get_last_finish_reason(self) -> Optional[str]:
        """Why the last reply ended. Set once the stream is fully consumed."""
        return self._last_finish_reason

    def get_last_ttft_seconds(self) -> Optional[float]:
        return self._last_ttft_seconds

    # embed() inherited from ABC - raises NotSupportedError (Anthropic has no
    # embeddings API; Lemonade keeps serving embeddings under --use-claude).

    def vision(self, images: list[bytes], prompt: str, **kwargs) -> str:
        import base64  # pylint: disable=import-outside-toplevel

        image_b64 = base64.b64encode(images[0]).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        result = self.chat(messages, **kwargs)
        return result if isinstance(result, str) else "".join(result)

    # load_model() / unload_model() inherited from ABC - raise NotSupportedError
