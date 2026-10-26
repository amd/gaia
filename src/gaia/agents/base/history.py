# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Model-facing conversation history, budgeted without splitting tool groups."""

import json
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Optional

from gaia.agents.base.turn_metrics import count_tokens
from gaia.logger import get_logger

logger = get_logger(__name__)


class TurnMessages(list):
    """Keep the original evidence when overflow recovery shrinks model input."""

    def __init__(self):
        super().__init__()
        self.recorded: list[dict] = []

    def append(self, message):
        super().append(message)
        self.recorded.append(deepcopy(message))

    def append_transient(self, message):
        """Send *message* this turn without recording it in the saved history."""
        super().append(message)

    def extend(self, messages):
        for message in messages:
            self.append(message)

    def finish(self, answer: str) -> list[dict]:
        """Close unfinished native calls truthfully before the final answer."""
        result = []
        pending = {}

        def close_pending():
            for call_id, name in pending.items():
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": "No result was recorded: the run stopped before "
                        "this call completed. Do not assume it succeeded.",
                    }
                )
            pending.clear()

        for message in deepcopy(self.recorded):
            if message.get("role") != "tool":
                close_pending()
            if message.get("tool_calls"):
                pending.update(
                    (call["id"], call["function"]["name"])
                    for call in message["tool_calls"]
                )
            if message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if call_id not in pending:
                    close_pending()
                    # Legacy plan execution never advertised a native call.
                    message = {
                        "role": "user",
                        "content": "[Recorded tool result: "
                        + message.get("name", "unknown")
                        + "] "
                        + json.dumps(message.get("content"), ensure_ascii=False),
                    }
                else:
                    pending.pop(call_id)

            result.append(message)
        close_pending()
        if (
            result
            and result[-1].get("role") == "assistant"
            and not result[-1].get("tool_calls")
        ):
            result[-1] = {"role": "assistant", "content": answer}
        else:
            result.append({"role": "assistant", "content": answer})
        return result


@dataclass(frozen=True)
class HistoryCut:
    """Turns the latest completed turn pushed out of the model's view."""

    turns: int
    tokens: int

    @property
    def notice(self) -> str:
        noun = "turn" if self.turns == 1 else "turns"
        return (
            f"Older messages are now out of the model's view ({self.turns} {noun}). "
            "Ask me to recall them if you need them."
        )


class CutReporter:
    """Remembers how many turns one conversation's last view hid.

    A smaller budget (a model switch, a long prompt) hides turns the newest
    turn did not push out; comparing with the last view catches those too.
    Before it has seen a view it falls back to the newest-turn check, so a
    restart does not re-announce a stable cut.
    """

    def __init__(self) -> None:
        self.hidden: Optional[int] = None

    def update(
        self,
        hidden: int,
        hidden_before_newest: int,
        tokens: int,
        on_cut: Optional[Callable[[HistoryCut], None]],
    ) -> None:
        baseline = hidden_before_newest if self.hidden is None else self.hidden
        self.hidden = hidden
        if on_cut is not None and hidden > baseline:
            on_cut(HistoryCut(turns=hidden, tokens=tokens))


def select_history(
    turns: list[list[dict[str, Any]]],
    budget: int,
    on_cut: Optional[Callable[[HistoryCut], None]] = None,
    reporter: Optional[CutReporter] = None,
) -> list[dict]:
    """Replay whole turns, evicting to half the estimated budget on overflow.

    A turn larger than the whole budget is replayed shortened, never dropped
    while its question and answer can fit: long tool results become stubs,
    then its longest texts are cut with a marker the model sees. Only a turn
    that still does not fit is left out.

    Replaying the same prefix makes eviction deterministic across restarts.
    The persistent transcript is never shortened. *on_cut* fires only when
    more turns are hidden than before, so a stable cut is reported once, not
    per turn; a shortened turn is still in view and is only logged. Without a
    *reporter*, "before" means without the newest turn.
    """
    reporter = reporter if reporter is not None else CutReporter()
    if budget <= 0:
        logger.warning(
            "No context budget remains for history; replaying none of the %d "
            "stored turn(s). Full evidence remains in the session database.",
            len(turns),
        )
        reporter.update(
            len(turns),
            max(len(turns) - 1, 0),
            sum(_turn_cost(t) for t in turns),
            on_cut,
        )
        return []
    plan = _plan_history(turns, budget)
    if plan.evicted or plan.unreplayable or plan.shortened:
        logger.warning(
            "Context budget excluded %d older turn(s), shortened %d turn(s) "
            "larger than the whole history budget and dropped %d that still did "
            "not fit; full evidence remains in the session database (history "
            "budget: %d estimated tokens).",
            plan.evicted,
            plan.shortened,
            plan.unreplayable,
            budget,
        )
    reporter.update(
        plan.evicted + plan.unreplayable,
        plan.hidden_before_newest,
        plan.hidden_tokens,
        on_cut,
    )
    return [message for index, _ in plan.retained for message in plan.turn(index)]


def _turn_cost(turn: list[dict[str, Any]]) -> int:
    return count_tokens(json.dumps(turn, ensure_ascii=False))


#: Tool results this short stay whole in a shortened turn; a stub saves little.
_STUB_MIN_CHARS = 500
_LEFT_OUT_PREFIX = "[Left out of the conversation history to fit the context window: "


def clip_marker(cut: int, total: int) -> str:
    return (
        f"\n\n[GAIA cut the last {cut:,} of this message's {total:,} characters "
        "to fit the model's context window; that part is not visible to you.]"
    )


def _clip_message(turn: list[dict], index: int, budget: int) -> int:
    """Keep the longest head of ``turn[index]`` that lets *turn* fit *budget*.

    The rest is replaced by a marker; returns how many characters were cut.
    """
    text = turn[index]["content"]

    def clipped(keep: int) -> str:
        return text[:keep] + clip_marker(len(text) - keep, len(text))

    low, high = 0, len(text) - 1
    while low < high:
        mid = (low + high + 1) // 2
        turn[index]["content"] = clipped(mid)
        if _turn_cost(turn) <= budget:
            low = mid
        else:
            high = mid - 1
    turn[index]["content"] = clipped(low)
    return len(text) - low


def clip_turn(turn: list[dict], budget: int) -> tuple[list[dict], list[tuple]]:
    """Cut the turn's longest text messages until it fits *budget*, marking each cut.

    Returns the turn and ``(index, cut, total)`` for every message cut.
    """

    def length(i: int) -> int:
        content = turn[i].get("content")
        return len(content) if isinstance(content, str) else 0

    turn = [dict(m) for m in turn]
    clips = []
    for index in sorted(range(len(turn)), key=lambda i: -length(i)):
        if _turn_cost(turn) <= budget:
            break
        total = length(index)
        if total <= len(clip_marker(total, total)):
            continue
        clips.append((index, _clip_message(turn, index, budget), total))
    return turn, clips


def _left_out_stub(name: str, arguments: str, text: str) -> str:
    from gaia.agents.base.context_eviction import _arg_summary, _outcome_line

    summary = _arg_summary(arguments)
    call = f"{name} {summary}" if summary else name
    return (
        f"{_LEFT_OUT_PREFIX}{call}; summary: {_outcome_line(text)}; "
        f"{len(text)} chars. Run the tool again if you need its text.]"
    )


def _stub_tool_results(turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn with each long tool result replaced by a one-line stub.

    Call ids, the question, every assistant message, refusals and worker
    answers stay as they were.
    """
    from gaia.agents.base.context_eviction import (
        _arguments_by_position,
        is_protected_result,
    )

    arguments = _arguments_by_position(turn)
    result = []
    for index, message in enumerate(turn):
        content = message.get("content")
        if isinstance(content, str) and len(content) > _STUB_MIN_CHARS:
            if message.get("role") == "tool" and not is_protected_result(message):
                stub = _left_out_stub(
                    message.get("name") or "tool", arguments.get(index, ""), content
                )
                message = {**message, "content": stub}
            elif message.get("role") == "user" and content.startswith(
                _RECORDED_RESULT_PREFIX
            ):
                # A tool result as text_tool_history renders it.
                name, _, text = content[len(_RECORDED_RESULT_PREFIX) :].partition("] ")
                if not is_protected_result({"name": name, "content": text}):
                    stub = _left_out_stub(name, "", text)
                    message = {
                        **message,
                        "content": f"{_RECORDED_RESULT_PREFIX}{name}] {stub}",
                    }
        result.append(message)
    return result


def _shorten_turn(turn: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    """Stub the turn's tool results, then cut its longest text, toward *target*."""
    stubbed = _stub_tool_results(turn)
    if _turn_cost(stubbed) <= target:
        return stubbed
    return clip_turn(stubbed, target)[0]


@dataclass
class _HistoryPlan:
    """Which turns ``select_history`` replays, as ``(index, cost)`` pairs.

    ``replacements`` maps a turn's index to the stubbed, cut copy replayed in
    its place because the whole turn was larger than the budget.
    """

    turns: list[list[dict[str, Any]]]
    retained: list[tuple[int, int]]
    evicted: int
    unreplayable: int
    hidden_tokens: int
    hidden_before_newest: int
    replacements: dict[int, list[dict[str, Any]]]

    @property
    def shortened(self) -> int:
        return len(self.replacements)

    def turn(self, index: int) -> list[dict[str, Any]]:
        return self.replacements.get(index, self.turns[index])


def _plan_history(turns: list[list[dict[str, Any]]], budget: int) -> _HistoryPlan:
    retained: deque = deque()
    used = 0
    evicted = 0
    unreplayable = 0
    hidden_tokens = 0
    hidden_before_newest = 0
    replacements: dict[int, list[dict[str, Any]]] = {}
    for index, turn in enumerate(turns):
        if index == len(turns) - 1:
            hidden_before_newest = evicted + unreplayable
        cost = _turn_cost(turn)
        if cost > budget:
            # To half, where overflow evicts down to, so the next turn keeps it.
            shortened = _shorten_turn(turn, budget // 2)
            shortened_cost = _turn_cost(shortened)
            if shortened_cost > budget:
                unreplayable += 1
                hidden_tokens += cost
                continue
            replacements[index] = shortened
            cost = shortened_cost
        retained.append((index, cost))
        used += cost
        if used > budget:
            target = budget // 2
            while len(retained) > 1 and used > target:
                _, removed = retained.popleft()
                used -= removed
                hidden_tokens += removed
                evicted += 1
    kept = {index for index, _ in retained}
    return _HistoryPlan(
        turns,
        list(retained),
        evicted,
        unreplayable,
        hidden_tokens,
        hidden_before_newest,
        {i: t for i, t in replacements.items() if i in kept},
    )


def transcript_turns(messages: list[dict]) -> list[list[dict]]:
    """Restore persisted model messages, or text for legacy completed turns."""
    turns = []
    pending = None
    for message in messages:
        role = message.get("role")
        if role == "user":
            # A message added mid-turn is saved as its own row; without a
            # recorded turn (stopped, failed) it must not replace the question.
            content = message["content"]
            if pending is not None:
                content = pending["content"] + "\n\n" + content
            pending = {"role": "user", "content": content}
        elif role in ("assistant", "autonomous"):
            recorded = message.get("model_messages")
            if recorded is not None:
                turns.append(recorded)
            elif role == "assistant" and pending is not None:
                turns.append(
                    [pending, {"role": "assistant", "content": message["content"]}]
                )
            pending = None
    return turns


def text_tool_history(turns: list[list[dict]]) -> list[list[dict]]:
    """Render recorded native tool evidence for a model without native tools."""
    result = deepcopy(turns)
    for turn in result:
        for index, message in enumerate(turn):
            if message.get("tool_calls"):
                calls = json.dumps(message["tool_calls"], ensure_ascii=False)
                content = message.get("content") or ""
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                turn[index] = {
                    "role": "assistant",
                    "content": content + "\n[Recorded tool calls] " + calls,
                }
            elif message.get("role") == "tool":
                content = message.get("content")
                turn[index] = {
                    "role": "user",
                    "content": "[Recorded tool result: "
                    + message.get("name", "unknown")
                    + "] "
                    + (
                        content
                        if isinstance(content, str)
                        else json.dumps(content, ensure_ascii=False)
                    ),
                }
    return result


def _is_cloud(agent: Any) -> bool:
    from gaia.llm.lemonade_client import LemonadeClient, cloud_model_provider

    model = getattr(agent, "model_id", None)
    provider = getattr(getattr(agent, "chat", None), "llm_client", None)
    backend = getattr(provider, "_backend", None)
    return bool(
        backend.cloud_model_provider(model)
        if isinstance(backend, LemonadeClient)
        else cloud_model_provider(model)
    )


#: Reply room reserved when the answering agent's own setting is not known.
DEFAULT_REPLY_TOKENS = 8192


def model_context_window(
    model: Optional[str],
    *,
    device: Optional[str] = None,
    claude: bool = False,
    cloud: bool = False,
) -> int:
    """The context window, in tokens, a turn on *model* is admitted with."""
    from gaia.llm.lemonade_client import GPU_CTX_SIZE, resolve_ctx_size

    if claude:
        from gaia.llm.providers.claude import CLAUDE_CTX_SIZE

        return CLAUDE_CTX_SIZE
    if cloud:
        # Remote admission policy, not a claim about the provider's context ceiling.
        return GPU_CTX_SIZE
    return resolve_ctx_size(model=model, device=device)


def context_window(agent: Any) -> int:
    """The context window, in tokens, the agent's model is admitted with.

    Re-read each call: the user or a client pin can change it, and the reply
    cap (``Agent._max_output_tokens``) is sized from this same value.
    """
    from gaia.llm.lemonade_client import LemonadeClient

    claude = bool(getattr(agent, "_use_claude", False))
    cloud = not claude and _is_cloud(agent)
    if not (claude or cloud):
        chat = getattr(agent, "chat", None)
        backend = getattr(getattr(chat, "llm_client", None), "_backend", None)
        # A client pinned to an exact window loads every model at that size.
        pin = getattr(backend, "ctx_size_override", None)
        if (
            isinstance(backend, LemonadeClient)
            and isinstance(pin, int)
            and not isinstance(pin, bool)
            and pin > 0
        ):
            return pin
        # The model actually sent, so a mid-session switch is sized correctly.
        model = getattr(chat, "effective_model", None)
        if isinstance(model, str) and model:
            return model_context_window(model, device=getattr(agent, "device", None))
    return model_context_window(
        getattr(agent, "model_id", None),
        device=getattr(agent, "device", None),
        claude=claude,
        cloud=cloud,
    )


def _reply_reserve(agent: Any) -> int:
    # A cloud model's 64K is an input budget: the reply is written into the
    # provider's own, far larger window, so it takes nothing from history here.
    if _is_cloud(agent) and not getattr(agent, "_use_claude", False):
        return 0
    # The cap actually sent per reply, which grows with a large local window.
    max_output = getattr(agent, "_max_output_tokens", None)
    if callable(max_output):
        value = max_output()
        # Only a real positive count; a stand-in agent falls back to the config.
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            # A client cap near the window size must not leave history no room.
            return min(value, context_window(agent) // 2)
    config = getattr(getattr(agent, "chat", None), "config", None)
    return getattr(config, "max_tokens", DEFAULT_REPLY_TOKENS)


def _budget_in_window(ctx: int, overhead: int, reply_reserve: int) -> int:
    return min(ctx // 2, ctx - overhead - reply_reserve - 2048)


def history_budget(agent: Any, query: str) -> int:
    """Reserve prompt, tools, output and safety margin for any transport."""
    ctx = context_window(agent)
    prompt = getattr(agent, "system_prompt", "")
    tools = getattr(agent, "_openai_tools", [])
    overhead = count_tokens(json.dumps([prompt, tools, query], ensure_ascii=False))
    return _budget_in_window(ctx, overhead, _reply_reserve(agent))


def relayed_history_budget(model: Optional[str], query: str, *, cloud: bool) -> int:
    """History budget for a turn another process answers with *model*.

    That agent's prompt and tools are not visible here, so only the query is
    reserved; the ``ctx // 2`` cap leaves the other half of the window for
    them, as in process. *cloud* is whether *model* is a Lemonade cloud model.
    """
    claude = bool(model) and model.startswith("claude-")
    cloud = cloud and not claude
    ctx = model_context_window(model, claude=claude, cloud=cloud)
    reserve = 0 if cloud else DEFAULT_REPLY_TOKENS
    return _budget_in_window(ctx, count_tokens(query), reserve)


def fit_pushed_history(
    agent: Any,
    messages: list[dict[str, Any]],
    query: str,
    on_cut: Optional[Callable[[HistoryCut], None]] = None,
) -> list[dict]:
    """Fit history another process pushed into *agent*'s own budget.

    The sender sized it without this agent's prompt, tools or reply cap, so it
    can overflow a small window. Each user message starts a turn; only whole
    older turns are dropped.
    """
    if not messages:
        return []
    turns: list[list[dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "user" or not turns:
            turns.append([])
        turns[-1].append(message)
    return select_history(turns, history_budget(agent, query), on_cut)


def turn_cost(turn: list[dict[str, Any]]) -> int:
    """Estimated tokens one turn takes in the history budget."""
    return _turn_cost(turn)


#: Tools whose results are document text, so the meter can show what files cost.
DOCUMENT_TOOLS = frozenset(
    {
        "query_documents",
        "query_specific_file",
        "search_indexed_chunks",
        "summarize_document",
        "dump_document",
        "read_file",
        "read_document",
    }
)

#: History this full warns that the next turns will push the oldest out.
HISTORY_WARN_FRACTION = 0.75

_RECORDED_RESULT_PREFIX = "[Recorded tool result: "


def _message_kind(message: dict[str, Any]) -> str:
    """``documents``, ``tools`` or ``history`` for one replayed message."""
    role = message.get("role")
    if role == "tool":
        return "documents" if message.get("name") in DOCUMENT_TOOLS else "tools"
    if message.get("tool_calls"):
        return "tools"
    content = message.get("content")
    if role == "user" and isinstance(content, str):
        if content.startswith(_RECORDED_RESULT_PREFIX):
            name = content[len(_RECORDED_RESULT_PREFIX) :].split("]", 1)[0]
            return "documents" if name in DOCUMENT_TOOLS else "tools"
    return "history"


def _turn_preview(turn: list[dict[str, Any]], limit: int = 60) -> str:
    for message in turn:
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str) and content:
            text = " ".join(content.split())
            return text if len(text) <= limit else text[: limit - 1] + "…"
    return "(a turn with no question text)"


#: System-prompt fragments the context breakdown names on their own.
_SKILL_FRAGMENTS = ("get_skill_catalog_system_prompt", "get_skills_system_prompt")
_MEMORY_FRAGMENTS = ("get_memory_system_prompt",)


def _fragments(agent: Any, prompt: str, methods: tuple) -> str:
    """The composed prompt's fragments that *methods* produced, joined."""
    origins = getattr(agent, "_mixin_prompt_origins", None) or {}
    return "\n\n".join(
        text for text, origin in origins.items() if origin in methods and text in prompt
    )


def _message_text(message: dict[str, Any]) -> str:
    """The text of one replayed message that reaches the model."""
    parts = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        parts.extend(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("text")
        )
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append(f"{function.get('name', '')} {function.get('arguments', '')}")
    return "\n".join(p for p in parts if p)


def context_usage(
    agent: Any,
    turns: list[list[dict[str, Any]]],
    query: str = "",
    count: Callable[[str], int] = count_tokens,
) -> dict[str, Any]:
    """What the next model call holds, by kind, and which turns go next.

    Mirrors ``select_history`` without reporting a cut, so it can be asked as
    often as the chat likes. Which turns are in view is decided with
    ``count_tokens``, as ``select_history`` decides it; the sizes reported are
    measured with *count*, which may be the loaded model's own tokenizer.

    ``skills`` and ``memory`` are the parts of ``system`` those fragments
    take; ``tool_definitions`` is the part of ``tools`` the schemas take, the
    rest being tool calls and results replayed from earlier turns.
    """
    window = context_window(agent)
    budget = history_budget(agent, query)
    prompt = getattr(agent, "system_prompt", "") or ""
    schemas = getattr(agent, "_openai_tools", None) or []
    tool_definitions = count(json.dumps(schemas, ensure_ascii=False)) if schemas else 0
    usage = {
        "window": window,
        "reply_reserve": _reply_reserve(agent),
        "system": count(prompt),
        "skills": count(_fragments(agent, prompt, _SKILL_FRAGMENTS)),
        "memory": count(_fragments(agent, prompt, _MEMORY_FRAGMENTS)),
        "tools": tool_definitions,
        "tool_definitions": tool_definitions,
        "documents": 0,
        "history": 0,
        "query": count(query) if query else 0,
        "history_budget": max(budget, 0),
        "history_used": 0,
        "turns_in_view": 0,
        "turns_hidden": len(turns),
        "at_risk": [],
        "at_risk_tokens": 0,
    }
    if budget > 0:
        plan = _plan_history(turns, budget)
        usage["turns_in_view"] = len(plan.retained)
        usage["turns_hidden"] = plan.evicted + plan.unreplayable
        texts: dict[str, list[str]] = {"documents": [], "tools": [], "history": []}
        for index, cost in plan.retained:
            usage["history_used"] += cost
            for message in plan.turn(index):
                texts[_message_kind(message)].append(_message_text(message))
        # One count per kind: *count* may be a request to the model server.
        for kind, parts in texts.items():
            if parts:
                usage[kind] += count("\n".join(parts))
        if usage["history_used"] >= budget * HISTORY_WARN_FRACTION:
            # Overflow evicts the oldest turns down to half the budget.
            remaining = usage["history_used"]
            for index, cost in plan.retained[:-1]:
                if remaining <= budget // 2:
                    break
                remaining -= cost
                usage["at_risk"].append(_turn_preview(turns[index]))
                usage["at_risk_tokens"] += cost
    usage["used"] = (
        usage["system"]
        + usage["tools"]
        + usage["documents"]
        + usage["history"]
        + usage["query"]
    )
    return usage


class SessionHistory(list):
    """A bounded model view backed by a complete, optionally durable transcript.

    ``clear`` also erases the archive so clearing a TUI session cannot resurrect
    earlier evidence. Each append is committed before the next turn starts.
    """

    def __init__(self, path=None):
        import sqlite3
        from pathlib import Path

        super().__init__()
        if path is not None:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Transcripts contain local file contents; keep new archives private.
            import os

            fd = (
                os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                if not path.exists()
                else None
            )
            if fd is not None:
                os.close(fd)
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS turns (id INTEGER PRIMARY KEY, messages TEXT NOT NULL)"
        )
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(turns)")}
        if "owner" not in columns:
            # The process running a stand-in turn (gaia.utils.process_owner);
            # NULL once the turn is recorded.
            self._db.execute("ALTER TABLE turns ADD COLUMN owner TEXT")
        self._db.commit()
        self._cuts = CutReporter()
        self._pending: Optional[int] = None

    def begin(self, messages: list[dict]) -> None:
        """Save a stand-in for the turn now starting, so the transcript has it
        even if the process is killed before the turn ends.

        The next :meth:`record` replaces it; :meth:`discard_pending` drops it.
        It is marked as this process's, so a process sharing the file leaves a
        turn that is still running out of its model view.
        """
        from gaia.utils.process_owner import current_owner

        with self._db:
            cursor = self._db.execute(
                "INSERT INTO turns(messages, owner) VALUES (?, ?)",
                (json.dumps(messages, ensure_ascii=False), current_owner()),
            )
        self._pending = cursor.lastrowid

    def keep_pending(self) -> None:
        """Leave the stand-in as the turn's entry: it never finished."""
        if self._pending is not None:
            with self._db:
                self._db.execute(
                    "UPDATE turns SET owner = NULL WHERE id = ?", (self._pending,)
                )
        self._pending = None

    def discard_pending(self) -> None:
        """Drop the stand-in of a turn that ended without being recorded."""
        if self._pending is None:
            return
        with self._db:
            self._db.execute("DELETE FROM turns WHERE id = ?", (self._pending,))
        self._pending = None

    def record(self, messages: list[dict]) -> None:
        """Persist a complete turn without modifying its native tool IDs."""
        payload = json.dumps(messages, ensure_ascii=False)
        with self._db:
            replaced = 0
            if self._pending is not None:
                replaced = self._db.execute(
                    "UPDATE turns SET messages = ?, owner = NULL WHERE id = ?",
                    (payload, self._pending),
                ).rowcount
            if not replaced:
                # No stand-in (or another process cleared the session since).
                self._db.execute("INSERT INTO turns(messages) VALUES (?)", (payload,))
        self._pending = None

    def prepare(
        self,
        agent: Any,
        query: str,
        on_cut: Optional[Callable[[HistoryCut], None]] = None,
    ) -> None:
        """Rebuild the bounded view for the currently selected model."""
        from gaia.utils.process_owner import owner_is_gone

        # A running turn's stand-in, ours or another live process's, is for
        # the transcript, not the model; a dead owner's never finished.
        turns = [
            json.loads(messages)
            for row_id, messages, owner in self._db.execute(
                "SELECT id, messages, owner FROM turns ORDER BY id"
            )
            if row_id != self._pending and (owner is None or owner_is_gone(owner))
        ]
        if (
            hasattr(agent, "_uses_native_tool_calls")
            and not agent._uses_native_tool_calls()
        ):
            turns = text_tool_history(turns)
        # Only a reporting rebuild moves the baseline; the others tell nobody.
        self[:] = select_history(
            turns,
            history_budget(agent, query),
            on_cut,
            self._cuts if on_cut is not None else None,
        )

    def clear(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM turns")
        super().clear()
        self._cuts = CutReporter()
        self._pending = None

    def close(self) -> None:
        self._db.close()
