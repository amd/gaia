# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
SSE Output Handler - Bridges agent console events to Server-Sent Events.

Maps OutputHandler method calls (thinking, tool calls, steps, etc.)
to JSON events that the streaming endpoint sends to the frontend.
"""

import json
import logging
import math
import os
import queue
import re
import socket
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Optional

from gaia.agents.base.call_risk import NEEDS_PERSON_ARG, call_risk
from gaia.agents.base.console import Denial, OutputHandler, timeout_denial_message
from gaia.agents.base.tool_grants import PATH_ACCESS_PROMPT_TOOL, grant_scope
from gaia.agents.base.tool_outcome import FAILED_STATUSES
from gaia.agents.base.tool_outcome import tool_result_failed as _tool_result_failed
from gaia.agents.base.tools import (
    get_tool_display_label,
    get_tool_metadata,
    tool_call_progress_message,
)
from gaia.agents.base.turn_metrics import turn_log_path
from gaia.agents.base.verification import split_verification_scope
from gaia.llm.providers.lemonade import (
    TOOL_CALL_PROGRESS_INTERVAL_S,
    TOOL_CALL_PROGRESS_MIN_CHARS,
)
from gaia.ui import scripted_user
from gaia.ui.event_narration import (
    DEBUG_CHANNEL,
    derive_narration,
    format_count,
    grant_phrase,
    tool_phrase,
)
from gaia.ui.turn_summary import TurnLedger
from gaia.utils.json_string import (
    JsonStringStreamDecoder,
    extract_json_string_field,
    json_string_field_start,
)

logger = logging.getLogger(__name__)

_SUMMARY_CHAR_CAP = 300

#: Result keys naming a file the tool wrote; kept on the summary view a
#: relayed result is reduced to.
RESULT_PATH_KEYS = ("file_path", "image_path")

#: A tool result with one of these statuses did not do its job.
FAILED_TOOL_STATUSES = FAILED_STATUSES


def tool_result_failed(data: Any) -> bool:
    """Whether a tool result reports a failure, refusals included."""
    return _tool_result_failed(data, FAILED_TOOL_STATUSES)


#: Seconds the agent thread waits for a tool-confirm response before denying.
#: Matches the TUI's ``DeliverableConfirmationTimeout`` so a prompt waits as
#: long on every surface.
TOOL_CONFIRM_TIMEOUT_SECONDS = 10 * 60

#: Sentinel for "the caller did not pass a timeout". Distinct from ``None``,
#: which is a caller explicitly asking to wait for the human indefinitely.
_USE_HANDLER_TIMEOUT = object()

# ``DEBUG_CHANNEL`` marks an event as harness bookkeeping rather than a
# description of the user's work (#2804). The emitter is the only layer that
# knows which it is, so it tags at the source; downstream
# (:mod:`gaia.ui.sse_translation`) decides whether to forward it.

#: ``start_progress`` labels the agent loop uses to say "I am alive" rather than
#: "here is what I am doing". Compared lower-cased and stripped of trailing
#: punctuation. Anything else — a goal, a real narration — passes through.
_HARNESS_PROGRESS_LABELS = frozenset(
    {"thinking", "working", "processing", "generating", "generating response"}
)

# ── Shared LLM output cleaning patterns ─────────────────────────────────
# These regexes are the canonical definitions for filtering LLM noise.
# Other consumers (MCP server, frontend safety nets) should import from here
# rather than duplicating the patterns.

# Regex to detect raw tool-call JSON that LLMs sometimes emit as text content.
# Matches patterns like:
#   {"tool": "search_file", "tool_args": {...}}
#   {"thought": "...", "goal": "...", "tool": "search_file", "tool_args": {...}}
# The leading .* allows optional fields (thought, goal, plan) before "tool".
_TOOL_CALL_JSON_RE = re.compile(
    r'^\s*\{.*["\s]*tool["\s]*:\s*"[^"]+"\s*,\s*["\s]*tool_args["\s]*:\s*\{.*\}\s*\}\s*$',
    re.DOTALL,
)

# Regex for use with re.sub() to strip tool-call JSON from mixed content.
# Unlike _TOOL_CALL_JSON_RE (which matches whole strings), this variant
# matches tool-call JSON embedded anywhere within larger text and uses
# [^}]* for inner args to avoid over-matching past the closing braces.
# Also handles unquoted tool names (malformed JSON from some LLM quantizations).
_TOOL_CALL_JSON_SUB_RE = re.compile(
    r'\s*\{\s*"?tool"?\s*:\s*"[^"]+"\s*,\s*"?tool_args"?\s*:\s*\{'
    r"[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*"
    r"\}\s*\}",
    re.DOTALL,
)

# Regex to remove {"thought": "..."} JSON blocks from LLM output.
_THOUGHT_JSON_SUB_RE = re.compile(r'\s*\{\s*"thought"\s*:\s*"[^"]*"[^}]*\}\s*')

# Regex to detect {"answer": "..."} JSON blocks from LLM output.
# These duplicate the already-streamed text content and should be stripped.
_ANSWER_JSON_RE = re.compile(r'\s*\{\s*"answer"\s*:\s*"', re.DOTALL)

# Regex for use with re.sub() to strip {"answer": "..."} JSON blobs embedded
# in content.  Used in print_final_answer to remove trailing JSON wrappers
# that some models append after their plain-text response.
# Handles escaped quotes (\") inside the answer string value.
_ANSWER_JSON_SUB_RE = re.compile(
    r'\s*\{\s*"answer"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}', re.DOTALL
)

# Regex to remove <think>...</think> tags that some models output.
_THINK_TAG_SUB_RE = re.compile(r"<think>[\s\S]*?</think>")

# Regex to strip RAG/tool result JSON blobs that Qwen3 sometimes leaks into
# its text output. Pattern: {"status": "success", ..., "chunks": [...], ...}
# or {"chunks": [...], "scores": [...]} — these are tool results, not LLM prose.
# We strip them to avoid corrupting the DB-stored assistant message with raw
# JSON that downstream turns will misread as factual content.
# Note: chunks array contains nested objects like [{"text":"...", "score":...}]
# so we use [\s\S]*? with a lookahead to stop at the outer closing brace.
_RAG_RESULT_JSON_SUB_RE = re.compile(
    r'[}\s`]*\{[^{}]*"chunks"\s*:\s*\[[\s\S]*?\][^{}]*\}[}\s`]*',
    re.DOTALL,
)

# Regex to remove trailing unclosed code fences (``` at end of response).
_TRAILING_CODE_FENCE_RE = re.compile(r"\n?```\s*$")


def _strip_balanced_json_blobs(text: str, needle: "re.Pattern") -> str:
    """Remove every top-level ``{...}`` JSON object whose body matches *needle*.

    Brace- and string-aware: it walks the text tracking quote/escape state so a
    ``{`` or ``}`` inside a JSON string never miscounts depth, and it only
    removes *complete* balanced objects. This is why it is safe to run against
    arbitrary LLM prose — an unbalanced or non-matching object is left intact.
    """
    if not text:
        return text
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            out.append(text[i])
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        blob = text[i:j]
        if depth == 0 and needle.search(blob):
            i = j  # drop a complete, matching object
            continue
        out.append(blob)
        i = j
    return "".join(out)


# The agent's response protocol: {"thought", "goal", "plan", "tool",
# "tool_args", "answer"}. A streamed reply in this shape is the model talking to
# the harness, not to the user, and must never reach the transcript raw.
_ENVELOPE_KEYS = frozenset({"thought", "goal", "plan", "tool", "tool_args", "answer"})
# Keys only the protocol uses: one of them makes an object an envelope even
# when the model adds a key of its own.
_ENVELOPE_MARKER_KEYS = frozenset({"thought", "tool_args", "answer"})
_ENVELOPE_LEAD_RE = re.compile(r'\{\s*"(\w+)"\s*:')
_ENVELOPE_LEAD_PARTIAL_RE = re.compile(r'\{\s*(?:"\w*(?:"\s*)?)?')
_FENCE_OPEN_RE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\r?\n")
_FENCE_OPEN_PARTIAL_RE = re.compile(
    r"`{1,3}[ \t]*(?:j(?:s(?:o(?:n)?)?)?|J(?:S(?:O(?:N)?)?)?)?[ \t]*\r?"
)
_FENCE_CLOSE_RE = re.compile(r"\s*```")
_FENCE_CLOSE_PARTIAL_RE = re.compile(r"\s*`{0,2}")

_ENV_NONE = "none"  # the buffer does not open with an envelope
_ENV_PENDING = "pending"  # too short to tell yet
_ENV_OPEN = "open"  # an envelope has started and not closed
_ENV_COMPLETE = "complete"


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _json_object_end(text: str, start: int, resume: Optional[tuple] = None) -> tuple:
    """Index just past the ``{...}`` opening at *start*, or None if unclosed.

    String-aware, so a brace or an escaped quote inside a value never ends it.
    Also returns the scan state, so a caller whose text only grew can pass it
    back as *resume* instead of rescanning a long envelope on every token. The
    state's last item is where a top-level ``"answer"`` string value begins
    (just past its opening quote), unless a ``tool`` key came before it.
    """
    if resume:
        pos, depth, in_str, esc, expect_key, key_start, last_key, tool_seen, ans = (
            resume
        )
    else:
        pos, depth, in_str, esc = start, 0, False, False
        expect_key, key_start, last_key, tool_seen, ans = False, 0, "", False, None
    for j in range(pos, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
                if depth == 1 and expect_key:
                    last_key = text[key_start:j]
                    expect_key = False
                    tool_seen = tool_seen or last_key in ("tool", "tool_args")
        elif c == '"':
            in_str = True
            if depth == 1:
                if expect_key:
                    key_start = j + 1
                elif last_key == "answer" and not tool_seen and ans is None:
                    ans = j + 1
        elif c == "{":
            depth += 1
            expect_key = depth == 1
        elif c == "," and depth == 1:
            expect_key = True
        elif c == "}":
            depth -= 1
            if depth == 0:
                state = (j + 1, depth, in_str, esc, expect_key, key_start)
                return j + 1, state + (last_key, tool_seen, ans)
    state = (len(text), depth, in_str, esc, expect_key, key_start)
    return None, state + (last_key, tool_seen, ans)


def _leading_envelope(
    text: str, end_of_stream: bool, resume: Optional[tuple] = None
) -> "tuple[str, int, int, int, Optional[tuple]]":
    """Classify the head of a stream buffer against the response envelope.

    Returns ``(state, obj_start, obj_end, consumed, scan)``; the offsets are set
    only for ``_ENV_COMPLETE`` and *scan* only for ``_ENV_OPEN`` (pass it back
    as *resume* once more text is appended). An optional ```` ```json ````
    fence around the object is part of what is consumed.
    """
    none = (_ENV_NONE, 0, 0, 0, None)
    pending = (_ENV_PENDING, 0, 0, 0, None)
    i = _skip_ws(text, 0)
    fenced = False
    if text.startswith("`", i):
        fence = _FENCE_OPEN_RE.match(text, i)
        if fence is None:
            if _FENCE_OPEN_PARTIAL_RE.fullmatch(text, i):
                return pending
            return none
        fenced = True
        i = _skip_ws(text, fence.end())
    if i >= len(text):
        return pending
    if text[i] != "{":
        return none
    lead = _ENVELOPE_LEAD_RE.match(text, i)
    if lead is None:
        if _ENVELOPE_LEAD_PARTIAL_RE.fullmatch(text, i):
            return pending
        return none
    if lead.group(1) not in _ENVELOPE_KEYS:
        return none
    if resume is not None and resume[0] != i:
        resume = None
    end, scan = _json_object_end(text, i, resume[1] if resume else None)
    if end is None:
        return (_ENV_OPEN, 0, 0, 0, (i, scan))
    consumed = end
    if fenced:
        close = _FENCE_CLOSE_RE.match(text, end)
        if close is not None:
            consumed = close.end()
        elif not end_of_stream and _FENCE_CLOSE_PARTIAL_RE.fullmatch(text, end):
            return pending
    return (_ENV_COMPLETE, i, end, consumed, None)


def _parse_envelope(obj_text: str) -> Optional[Dict[str, Any]]:
    """The envelope as a dict, or None when *obj_text* is ordinary JSON/text."""
    try:
        # strict=False: models put literal newlines inside string values.
        parsed = json.loads(obj_text, strict=False)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    keys = set(parsed)
    if keys <= _ENVELOPE_KEYS or keys & _ENVELOPE_MARKER_KEYS:
        return parsed
    return None


# Native tool-call markup some models write as text instead of a call (DeepSeek
# DSML and its older special tokens, Claude/Qwen-style XML, harmony channels).
# Each opener maps to the closer that ends the call; everything between is the
# call and never reaches the transcript.
_BAR = r"[|｜]"
_SEP = r"[▁_]"
_MARKUP_FAMILIES: tuple = (
    (
        re.compile(
            rf"<\s*{_BAR}\s*DSML\s*{_BAR}\s*(calls|function_calls|tool_calls)\s*>"
        ),
        lambda m: re.compile(
            rf"<\s*/\s*{_BAR}\s*DSML\s*{_BAR}\s*{re.escape(m.group(1))}\s*>"
        ),
    ),
    (
        re.compile(rf"<\s*{_BAR}\s*DSML\s*{_BAR}\s*invoke\s+name\s*="),
        lambda m: re.compile(rf"<\s*/\s*{_BAR}\s*DSML\s*{_BAR}\s*invoke\s*>"),
    ),
    (
        re.compile(rf"<{_BAR}tool{_SEP}calls{_SEP}begin{_BAR}>"),
        lambda m: re.compile(rf"<{_BAR}tool{_SEP}calls{_SEP}end{_BAR}>"),
    ),
    (
        re.compile(rf"<{_BAR}tool{_SEP}call{_SEP}begin{_BAR}>"),
        lambda m: re.compile(rf"<{_BAR}tool{_SEP}call{_SEP}end{_BAR}>"),
    ),
    # Plain-XML openers are ordinary words in prose ("wrap it in <tool_call>"),
    # so they count only at the start of a line; the token forms above do not.
    (re.compile(r"<tool_call>"), lambda m: re.compile(r"</tool_call>")),
    (re.compile(r"<function_calls>"), lambda m: re.compile(r"</function_calls>")),
    (re.compile(r"<invoke\s+name\s*="), lambda m: re.compile(r"</invoke>")),
    (
        re.compile(r"(?:<\|start\|>assistant)?<\|channel\|>\s*commentary\s+to="),
        lambda m: re.compile(r"<\|call\|>"),
    ),
)
_MARKUP_OPEN_RE = re.compile("|".join(f"(?:{o.pattern})" for o, _ in _MARKUP_FAMILIES))
_LINE_START_OPENERS = frozenset(o.pattern for o, _ in _MARKUP_FAMILIES[4:7])
_FENCE_MARK_RE = re.compile(r" {0,3}(`{3,}|~{3,})")


def _fence_after(
    text: str, fence: Optional[str], line_start: bool
) -> "tuple[Optional[str], bool]":
    """Markdown fence state and line-start state after *text*.

    *fence* is the open fence's marker (e.g. "```"), or None outside one. Only a
    marker at the start of a line opens or closes a fence, as in Markdown.
    """
    for k, line in enumerate(text.split("\n")):
        if k:
            line_start = True
        if line_start:
            mark = _FENCE_MARK_RE.match(line)
            if mark is not None:
                run = mark.group(1)
                if fence is None:
                    fence = run
                elif run[0] == fence[0] and len(run) >= len(fence):
                    fence = None
            line_start = not line.strip(" \t")
    return fence, line_start


# Leftover tags of a call whose opener streamed before this filter saw it.
_MARKUP_STRAY_RE = re.compile(
    rf"<\s*/?\s*{_BAR}\s*DSML\s*{_BAR}[^>\n]{{0,80}}>"
    rf"|<{_BAR}tool{_SEP}(?:calls?{_SEP}(?:begin|end)|sep){_BAR}>"
)
_MARKUP_TOOL_NAME_RE = re.compile(r'name\s*=\s*"([\w.-]+)"')
# The tool an opener that ends in ``name=`` names.
_MARKUP_INVOKED_NAME_RE = re.compile(r'\s*"([\w.-]+)"')
_ENVELOPE_TOOL_NAME_RE = re.compile(r'"tool"\s*:\s*"([\w.-]+)"')
# Longest opener/closer, so a tag split across two tokens is still recognised.
_MARKUP_TAIL = 64


def _partial_markup_start(text: str) -> Optional[int]:
    """Where a tag that may still grow into a tool-call opener starts, if any.

    Generous on purpose: holding plain text with a "<" costs at most
    ``_MARKUP_TAIL`` characters of latency, and leaking an opener costs the call.
    """
    window = max(0, len(text) - _MARKUP_TAIL)
    for lt in range(window, len(text)):
        if text[lt] != "<":
            continue
        tail = text[lt:]
        if "\n" in tail:
            continue
        # Harmony and DeepSeek special tokens carry a '>' before the deciding part.
        if ">" not in tail or tail.startswith(("<|", "<｜")):
            return lt
    return None


# Keys that make a reply the agent's protocol: a reply led by one of them that
# will not parse is a malformed envelope, not prose. "answer" is left out so a
# broken answer still reaches the old answer-extraction path.
_PROTOCOL_LEAD_KEYS = frozenset({"thought", "goal", "plan", "tool", "tool_args"})


def _is_malformed_envelope(obj_text: str, lead: "Optional[re.Match]") -> bool:
    if lead is None or lead.group(1) not in _PROTOCOL_LEAD_KEYS:
        return False
    try:
        json.loads(obj_text, strict=False)
    except ValueError:
        return True
    return False  # valid JSON that is not an envelope is the user's content


def _peel_response_socket(resp: Any) -> Optional[socket.socket]:
    """Dig the live TCP socket out of a ``requests`` streamed response.

    Needed by the email-relay cancel path (#2109): only ``socket.shutdown()``
    can wake a reader thread parked in a blocking ``recv`` — ``resp.close()``
    waits on the buffered reader's lock, which that parked read holds.

    Tries the known attribute chains across urllib3 v1/v2; returns ``None``
    for anything that isn't a real socket-backed response (mocks, already
    -released connections), letting the caller fall back to ``close()``.
    """
    raw = getattr(resp, "raw", None)
    if raw is None:
        return None
    for conn_attr in ("connection", "_connection"):
        sock = getattr(getattr(raw, conn_attr, None), "sock", None)
        if isinstance(sock, socket.socket):
            return sock
    # http.client.HTTPResponse -> BufferedReader -> SocketIO -> socket
    rawio = getattr(getattr(getattr(raw, "_fp", None), "fp", None), "raw", None)
    sock = getattr(rawio, "_sock", None)
    return sock if isinstance(sock, socket.socket) else None


# An envelope whose first key is "answer".
_ANSWER_FIRST_RE = re.compile(r'\s*\{\s*"answer"\s*:')


class SSEOutputHandler(OutputHandler):
    """
    OutputHandler that queues agent events as JSON for SSE streaming.

    Each console method call becomes a typed event pushed to a queue.
    The streaming endpoint reads from this queue and yields SSE events.
    """

    blocking_confirmation = True
    asks_past_preapproval = True

    #: How long to wait for a decision. A host that can actually deliver one
    #: raises this — a person reading a prompt routinely takes longer than a
    #: minute — but must not set it to ``None``: an unanswerable question with
    #: no bound is a turn that never ends, which reads as a hang rather than as
    #: the refusal it effectively is. See ``gaia_agent.stdio.PermissionState``,
    #: whose backstop deliberately outlasts its client's own bound.
    #:
    #: Class-level so a handler built with ``__new__`` — which callers that only
    #: need the confirmation gate do, to skip the queue setup — still has a
    #: usable value.
    confirm_timeout_seconds: Optional[float] = TOOL_CONFIRM_TIMEOUT_SECONDS

    #: The tool the live prompt is for, so an "always" answer grants the right
    #: one without the responder having to name it.
    _confirm_tool: Optional[str] = None

    #: The arguments of the live prompt, so an "always" answer grants the scope
    #: the user was actually shown rather than the whole tool.
    _confirm_args: Optional[Dict[str, Any]] = None

    #: Set when the client denied the live prompt because nobody answered.
    _confirm_timed_out: bool = False

    #: The arguments the model sent for the call about to be confirmed, when
    #: the prompt shows more than that (see :meth:`note_call_args`).
    _noted_call: Optional["tuple[str, Dict[str, Any]]"] = None

    #: The sidecar prompt a relayed run is parked on (see __init__).
    _relay_confirm_id: Optional[str] = None

    #: The sidecar question a relayed run is parked on, until it is answered.
    _relay_question_id: Optional[str] = None

    #: Outcome of the last ``tool_result``, echoed by the ``tool_end`` after it.
    _last_tool_success: bool = True

    #: The sidecar session a relayed run keeps in step with the chat's
    #: permission mode; ``None`` when no relayed run is doing so.
    active_relay_mode_session: Optional[str] = None

    def __init__(self, background_mode: bool = False):
        self.event_queue: queue.Queue = queue.Queue()
        self.cancelled = threading.Event()
        self._start_time: Optional[float] = None
        self._step_count = 0
        self._tool_count = 0
        self._last_tool_name: Optional[str] = None
        self._last_tool_label = ""
        self._stream_buffer = ""  # Buffer to detect and filter tool-call JSON
        self._in_thinking = False  # True while inside a <think>...</think> block
        self._json_filtered = False  # True after a JSON block was suppressed; used to eat trailing } artifacts
        # Where the brace scan of a still-open envelope stopped, and the head of
        # the buffer it scanned: a long tool call is scanned once, not per token.
        self._envelope_scan: Optional[tuple] = None
        self._envelope_head = ""
        # Head of a reply held whole because its envelope will not parse.
        self._broken_envelope_head = ""
        # Native tool-call markup in flight: the closer that ends it, the tail
        # held back in case a tag is split across tokens, and what it calls.
        self._markup_close: Optional["re.Pattern"] = None
        self._markup_pending = ""
        self._markup_tool: Optional[str] = None
        self._markup_chars = 0
        self._call_progress_next = 0.0
        # Code-fence and line-start state of the text shown so far this reply.
        self._shown_fence: Optional[str] = None
        self._shown_line_start = True
        # Line-start state of the raw stream, thinking included.
        self._raw_line_start = True
        # Set while streaming the value of a {"answer": "..."} reply.
        self._answer_stream: Optional[JsonStringStreamDecoder] = None
        # Tool confirmation state (blocking until frontend responds)
        self._confirm_lock = threading.Lock()
        self._confirm_event: Optional[threading.Event] = None
        self._confirm_result: bool = False
        self._confirm_timed_out: bool = False
        self._confirm_id: Optional[str] = None
        self._confirm_tool = None
        self._confirm_args = None
        self._tool_start_time: Optional[float] = None
        # The turn's place on the model (``turn_queue.TurnSlot``); given up
        # while a prompt or question waits on the user. None outside a chat run.
        self.turn_slot = None
        # Re-checks the model once the turn has its place back: another chat
        # may have unloaded or reloaded it meanwhile. Raises when it can't.
        self.on_model_regained = None
        # Autonomous loop support
        # background_mode=True: skip blocking user confirmation; immediately deny.
        self.background_mode: bool = background_mode
        # False on a transport with no route back for an answer (the stdio
        # agent): a question there would hang the turn, so none is asked.
        self.answers_questions: bool = True
        # Directive written by set_loop_state tool; read by AgentLoop after the run.
        self.loop_state_directive: Optional[Dict[str, Any]] = None
        # User input request queue (ordered, multi-slot keyed by request_id).
        self._user_input_queue: deque = deque()
        self._user_input_events: Dict[str, threading.Event] = {}
        self._user_input_results: Dict[str, str] = {}
        # Guards the three maps above. resolve_user_input() runs on a request
        # thread while the agent thread is timing out on the same request_id;
        # unsynchronized, that window lets a caller be told "accepted" for an
        # answer the run has already stopped waiting for.
        self._user_input_lock = threading.Lock()
        # Live response for an in-flight email /query relay (#2109), so the
        # cancel path can force a blocked read to error out by closing it from
        # another thread. None outside an active email-relay turn.
        self.active_relay_response: Optional[Any] = None
        # Proxy + run_id for that same in-flight email /query relay (#2595), so
        # a needs_input answer posted to /api/chat/user-input can be delivered
        # to the run that's actually waiting on it. None outside an active
        # email-relay turn, in lockstep with active_relay_response.
        self.active_relay_proxy: Optional[Any] = None
        self.active_relay_run_id: Optional[str] = None
        # Only this id may be answered, so a late click cannot approve a newer one.
        self._relay_confirm_id = None
        # A relayed needs_input question the sidecar run is parked on.
        self._relay_question_id: Optional[str] = None
        # Sealed turn record for the turn in flight, stashed by
        # print_turn_metrics and consumed by the next print_final_answer.
        self._turn_metrics: Optional[Dict[str, Any]] = None
        # Set by mark_turn_failed; the next answer event carries ``failed``.
        self._turn_failed = False
        # The answer this turn already put on the wire. A transport that ends
        # the turn on it cancels the run, so the run reads it back from here.
        self.delivered_answer: str = ""
        # What this turn's tools used, wrote and contacted, for the side rail.
        self.turn_ledger = TurnLedger()

    def _emit(self, event: Dict[str, Any]):
        """Push an event to the queue for SSE delivery."""
        if event and event.get("type") == "chunk":
            self._shown_fence, self._shown_line_start = _fence_after(
                event.get("content") or "", self._shown_fence, self._shown_line_start
            )
        self.event_queue.put(event)

    def _fence_at(self, unshown: str) -> "tuple[Optional[str], bool]":
        """Fence and line-start state at the end of *unshown*, after what was shown."""
        return _fence_after(unshown, self._shown_fence, self._shown_line_start)

    def _reset_reply_state(self) -> None:
        """Forget per-reply stream state; the next model call starts afresh."""
        self._shown_fence = None
        self._shown_line_start = True
        self._raw_line_start = True
        self._call_progress_next = 0.0

    def _elapsed(self) -> float:
        if self._start_time is None:
            return 0.0
        return round(time.time() - self._start_time, 2)

    # === Core Progress/State Methods ===

    def print_processing_start(self, query: str, max_steps: int, model_id: str = None):
        self._start_time = time.time()
        self._step_count = 0
        self._tool_count = 0
        self.delivered_answer = ""
        model_label = model_id or "LLM"
        self._emit(
            {
                "type": "status",
                "status": "working",
                "message": f"Processing with {model_label}...",
                "channel": DEBUG_CHANNEL,
            }
        )

    def print_step_header(self, step_num: int, step_limit: int):
        self._step_count = step_num
        self._emit(
            {
                "type": "step",
                "step": step_num,
                "total": step_limit,
                "status": "started",
                "channel": DEBUG_CHANNEL,
            }
        )

    def print_state_info(self, state_message: str):
        # Suppress internal agent state labels (PLANNING, DIRECT EXECUTION, etc.)
        # — they duplicate the thinking step that immediately follows.
        pass

    def print_thought(self, thought: str):
        self._emit(
            {
                "type": "thinking",
                "content": thought,
            }
        )

    def print_goal(self, goal: str):
        # Goals are less important than thoughts - emit as status
        # so they don't create redundant "thinking" steps in the UI.
        if goal:
            self._emit(
                {
                    "type": "status",
                    "status": "working",
                    "message": goal,
                }
            )

    def print_plan(self, plan: List[Any], current_step: int = None):
        # Convert plan items to strings for JSON serialization
        plan_strs = []
        narrations = []
        for step in plan:
            if isinstance(step, dict):
                if "tool" in step:
                    args_str = ""
                    if step.get("tool_args"):
                        args_str = " — " + ", ".join(
                            f"{k}={v!r}" for k, v in step["tool_args"].items()
                        )
                    plan_strs.append(f"{step['tool']}{args_str}")
                    narrations.append(
                        derive_narration(step["tool"], step.get("tool_args"))
                    )
                else:
                    plan_strs.append(json.dumps(step))
                    text = (
                        step.get("description") or step.get("goal") or step.get("step")
                    )
                    narrations.append(str(text) if text else plan_strs[-1])
            else:
                plan_strs.append(str(step))
                narrations.append(plan_strs[-1])

        self._emit(
            {
                "type": "plan",
                "steps": plan_strs,
                "step_narrations": narrations,
                "current_step": current_step,
            }
        )

    # === Tool Execution Methods ===

    def record_tool_started(self, tool_name: str) -> None:
        """Note a tool starting in the turn's ledger and start its timer."""
        self._tool_count += 1
        self._last_tool_name = tool_name
        self._tool_start_time = time.monotonic()
        self.turn_ledger.tool_started(tool_name)

    def record_tool_args(self, args: Dict[str, Any]) -> None:
        """Note the running tool's arguments in the turn's ledger."""
        self.turn_ledger.tool_args(args)

    def record_tool_result(self, data: Any, success: bool) -> Optional[float]:
        """Note the running tool's result and stop its timer.

        Returns the tool's latency in milliseconds, or None when no start was
        recorded.
        """
        self._last_tool_success = success
        self.turn_ledger.tool_result(data, success)
        if self._tool_start_time is None:
            return None
        latency_ms = round((time.monotonic() - self._tool_start_time) * 1000, 1)
        self._tool_start_time = None
        return latency_ms

    def print_tool_usage(self, tool_name: str):
        self.record_tool_started(tool_name)
        # Prefer an explicit display label from the tool registry; fall back
        # to the legacy description map in this module when none is provided.
        detail_label = get_tool_display_label(tool_name) or _tool_description(tool_name)
        self._last_tool_label = detail_label
        event = {
            "type": "tool_start",
            "tool": tool_name,
            "detail": detail_label,
            "narration": derive_narration(tool_name),
        }
        # Attach MCP server name if this is an MCP tool.
        # _mcp_server is set by MCPTool.to_gaia_format() during registration
        # in MCPClientMixin._register_mcp_tools() (see mcp/client/mcp_client.py).
        meta = get_tool_metadata(tool_name)
        if meta:
            mcp_server = meta.get("_mcp_server")
            if mcp_server:
                event["mcp_server"] = mcp_server
        self._emit(event)

    def print_tool_complete(self):
        self._tool_start_time = None  # Reset in case tool_result was skipped
        success, self._last_tool_success = self._last_tool_success, True
        self._emit(
            {
                "type": "tool_end",
                "success": success,
            }
        )

    def pretty_print_json(self, data: Dict[str, Any], title: str = None):
        # When title is "Arguments", emit tool args as a detail update
        # so the frontend can show what the tool was called with. Sent for an
        # empty call too: the translator holds tool_start until this arrives.
        if title == "Arguments":
            if not isinstance(data, dict):
                # The wire carries an object; log what the model really sent.
                logger.warning(
                    "Tool %s was called with %s arguments, not an object: %.200r",
                    self._last_tool_name,
                    type(data).__name__,
                    data,
                )
                data = {}
            # A call with no arguments keeps the tool_start label, not a blank.
            detail = (
                _format_tool_args(self._last_tool_name, data)
                if data
                else self._last_tool_label
            )
            self.record_tool_args(data)
            self._emit(
                {
                    "type": "tool_args",
                    "tool": self._last_tool_name,
                    "args": data,
                    "detail": detail,
                    "narration": derive_narration(self._last_tool_name, data),
                }
            )
            return

        # Render-map fix (#2109): for tools registered in _RENDER_TOOL_TO_LANG,
        # carry the card payload as result_data — the /query canonical
        # translator forwards result_data verbatim as tool_result.data, so the
        # frontend renders the typed card straight from the event instead of
        # depending on the LLM echoing it into the final answer (the former
        # fence-injection hack, #1000, removed here).
        render_card = self._render_card_payload(data)

        # For tool results, provide a detailed summary
        summary = _summarize_tool_result(data)
        success = not tool_result_failed(data)
        latency_ms = self.record_tool_result(data, success)
        event = {
            "type": "tool_result",
            "title": title,
            "summary": summary,
            "success": success,
        }
        # A relayed result is reduced to this event, and a "skipped" save
        # must not reach the chat's Outputs as made.
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            event["status"] = data["status"]
        # String-returning tools (notably email envelopes) are summarized by a
        # hard character cap. Tell downstream classifiers that an unparsable
        # summary may be incomplete instead of making them infer truncation.
        if not isinstance(data, dict) and len(str(data)) > _SUMMARY_CHAR_CAP:
            event["summary_truncated"] = True
        # Where the tool wrote, resolved: a relayed turn's Outputs need it.
        if isinstance(data, dict):
            for key in RESULT_PATH_KEYS:
                if isinstance(data.get(key), str):
                    event[key] = data[key]

        if latency_ms is not None:
            event["latency_ms"] = latency_ms

        # For command execution results, include structured output data
        # so the frontend can render a proper terminal view
        if (
            isinstance(data, dict)
            and "command" in data
            and ("stdout" in data or "stderr" in data)
        ):
            event["command_output"] = {
                "command": data.get("command", ""),
                "stdout": data.get("stdout", ""),
                "stderr": data.get("stderr", ""),
                "return_code": data.get("return_code", 0),
                "expected_exit": data.get("expected_exit"),
                "cwd": data.get("cwd", ""),
                "duration_seconds": data.get("duration_seconds"),
                "truncated": data.get("output_truncated", False),
            }

        # For file search results, include structured file list
        if isinstance(data, dict) and ("files" in data or "file_list" in data):
            files = data.get("file_list", data.get("files", []))
            if isinstance(files, list):
                # Keep the UI contract honest: "total" should never claim more accessible
                # files than we actually include in the event payload.
                # list_files reports bare names; the UI renders entries.
                limited_files = [
                    {"name": f} if isinstance(f, str) else f for f in files[:20]
                ]
                event["result_data"] = {
                    "type": "file_list",
                    "files": limited_files,  # Limit to 20 files
                    "total": len(limited_files),
                }

        # For search results with chunks, include structured chunk data
        # so the frontend can render expandable chunk cards
        if isinstance(data, dict) and "chunks" in data:
            chunks = data.get("chunks", [])
            if isinstance(chunks, list):
                structured_chunks = []
                for c in chunks[:8]:  # Limit to 8 chunks max
                    if isinstance(c, dict):
                        structured_chunks.append(
                            {
                                "id": c.get("chunk_id", 0),
                                "source": (
                                    Path(c["source_file"]).name
                                    if c.get("source_file")
                                    else None
                                ),
                                "sourcePath": c.get("source_file", ""),
                                "page": c.get("page"),
                                "score": (
                                    round(c.get("relevance_score", 0), 2)
                                    if c.get("relevance_score")
                                    else None
                                ),
                                "preview": (c.get("content", "") or "")[:150],
                                "content": (c.get("content", "") or "")[:800],
                            }
                        )
                    else:
                        structured_chunks.append(
                            {
                                "id": len(structured_chunks) + 1,
                                "preview": str(c)[:150],
                                "content": str(c)[:800],
                            }
                        )
                event["result_data"] = {
                    "type": "search_results",
                    "count": len(chunks),
                    "source_files": data.get("source_files", []),
                    "chunks": structured_chunks,
                }

        # The render-map card (if any) is authoritative for a registered tool —
        # set last so it always wins over the generic result_data shapes above.
        if render_card is not None:
            event["result_data"] = render_card

        self._emit(event)

    # === Status Messages ===

    def print_error(self, error_message: str, recoverable: bool = False):
        event: Dict[str, Any] = {
            "type": "agent_error",
            "content": str(error_message) if error_message else "Unknown error",
        }
        # Only set when True: keeps the wire shape unchanged for every
        # existing (fatal) caller, and lets a downstream translator treat a
        # missing/False flag as the pre-#2515 terminal default.
        if recoverable:
            event["recoverable"] = True
        self._emit(event)

    def print_warning(self, warning_message: str):
        self._emit(
            {
                "type": "status",
                "status": "warning",
                "message": warning_message,
            }
        )

    def print_info(self, message: str):
        self._emit(
            {
                "type": "status",
                "status": "info",
                "message": message,
            }
        )

    # === Progress Indicators ===

    def start_progress(self, message: str):
        # Filter redundant "Executing <tool_name>" progress messages -
        # these just echo the tool name which the frontend already shows.
        if message and message.lower().startswith("executing "):
            return
        # Emit as status (not thinking — thinking is reserved for LLM reasoning)
        text = message or "Working"
        event: Dict[str, Any] = {
            "type": "status",
            "status": "working",
            "message": text,
        }
        if text.strip().lower().rstrip(".…") in _HARNESS_PROGRESS_LABELS:
            event["channel"] = DEBUG_CHANNEL
        self._emit(event)

    def stop_progress(self):
        pass  # No-op for SSE - frontend manages its own spinners

    def report_progress(self, message: str):
        self._emit({"type": "status", "status": "working", "message": message})

    def report_phase(self, phase: str, message: str, **counts: int):
        self._emit(
            {
                "type": "status",
                "status": "working",
                "message": message,
                "phase": phase,
                **counts,
            }
        )

    # === Structured-render map (#2109) ===

    # Mapping from tool name to the card "kind" the frontend's render-card
    # registry draws (spec §4.2). Shared with the sidecar's own canonical
    # translator (``gaia_agent_email/sse_translation.py`` duplicates this map
    # to stay dependency-light) — a test in that package
    # (``test_render_tool_to_lang_maps_stay_in_sync``) pins the two dicts
    # equal so the duplication can't silently drift.
    _RENDER_TOOL_TO_LANG: ClassVar[Dict[str, str]] = {
        "pre_scan_inbox": "email_pre_scan",
        # #2765: a generic ``table`` card (no new client code) so the thread
        # view renders straight from tool data instead of model prose.
        "get_thread": "table",
    }

    def _render_card_payload(self, data: Any) -> Optional[Dict[str, Any]]:
        """Return the render-map card payload for the last-called tool, if any.

        ``@tool``-decorated functions return JSON strings (the dispatch in
        ``Agent._execute_tool`` returns them verbatim), so both string and
        dict envelope shapes are accepted here. Returns ``None`` — never
        raises — on any non-matching or malformed payload: a foreign tool, a
        failed (``ok=false``) envelope, or a ``kind`` that doesn't match the
        registered tool.
        """
        tool = self._last_tool_name or ""
        lang = self._RENDER_TOOL_TO_LANG.get(tool)
        if not lang:
            return None
        envelope: Dict[str, Any]
        if isinstance(data, dict):
            envelope = data
        elif isinstance(data, str):
            try:
                parsed = json.loads(data)
            except (ValueError, TypeError):
                return None
            if not isinstance(parsed, dict):
                return None
            envelope = parsed
        else:
            return None
        if not envelope.get("ok"):
            return None
        inner = envelope.get("data")
        if not isinstance(inner, dict) or inner.get("kind") != lang:
            return None
        return inner

    # === Completion Methods ===

    def print_turn_metrics(self, record: Dict[str, Any]):
        """Stash the sealed turn record for the answer event that follows."""
        self._turn_metrics = record

    def mark_turn_failed(self) -> None:
        """The answer that follows ends a turn a model failure cut short."""
        self._turn_failed = True

    def print_final_answer(
        self,
        answer: str,
        streaming: bool = True,  # pylint: disable=unused-argument
        total_tokens: Optional[int] = None,
        ttft_seconds: Optional[float] = None,
        tok_per_s: Optional[float] = None,
        input_tokens: Optional[int] = None,
        cached_tokens: Optional[int] = None,
    ):
        if answer:
            # Set aside the verification-scope line before the cleaners run: an
            # answer they strip to nothing (a card-echo) must stay empty, not
            # arrive as a message consisting only of the scope line (#3376).
            answer, scope_line = split_verification_scope(answer)
            answer = _THINK_TAG_SUB_RE.sub("", answer)
            # Extract answer text from {"thought":..., "answer":...} JSON before
            # the regex cleaners run.  _THOUGHT_JSON_SUB_RE would otherwise strip
            # the entire blob (including the answer value) leaving an empty string.
            answer = _clean_answer_json(answer.strip())
            # Strip any trailing {"answer": "..."} JSON blob that some models
            # append to their plain-text response.
            answer = _ANSWER_JSON_SUB_RE.sub("", answer)
            answer = _RAG_RESULT_JSON_SUB_RE.sub("", answer)
            answer = _TOOL_CALL_JSON_SUB_RE.sub("", answer)
            answer = _THOUGHT_JSON_SUB_RE.sub("", answer)
            answer = answer.strip()
            if answer and scope_line:
                answer = f"{answer}\n\n{scope_line}"
        event: Dict[str, Any] = {
            "type": "answer",
            "content": _fix_double_escaped(answer) if answer else answer,
            "elapsed": self._elapsed(),
            "steps": self._step_count,
            "tools_used": self._tool_count,
        }
        # Omit entirely when no real count exists — never emit a fake zero.
        # `_sum_conversation_tokens` returns 0 both when a real turn generated
        # zero output tokens (never happens for a completed answer) and when
        # no per-step stats were collected at all (the common "no real count"
        # case) — it can't tell the two apart, so treat <= 0 as unavailable,
        # same as the ttft guard below.
        if total_tokens is not None and total_tokens > 0:
            event["tokens"] = total_tokens
        # Same omit-don't-fake rule for ttft: real value from Lemonade's own
        # generation timing, or nothing.
        if (
            ttft_seconds is not None
            and math.isfinite(ttft_seconds)
            and ttft_seconds > 0
        ):
            event["ttft"] = round(ttft_seconds, 3)
        # And for the generation rate: the backend's own measurement or
        # nothing. A rate derived from the turn's wall clock would count tool
        # time as generation time and read an order of magnitude low.
        if tok_per_s is not None and math.isfinite(tok_per_s) and tok_per_s > 0:
            event["tok_per_s"] = round(tok_per_s, 1)
        # Input and cached counts ride the same omit-don't-fake rule. Cached is
        # reported at 0 rather than omitted when the turn had input tokens: a
        # backend that counted the prompt and cached none of it is telling us
        # zero, which is a different statement from "nobody counted".
        if input_tokens is not None and input_tokens > 0:
            event["input_tokens"] = input_tokens
            if cached_tokens is not None and cached_tokens >= 0:
                event["cached_tokens"] = cached_tokens
        # Dev-mode only. Gated on the same env var that produced the record, so
        # an ordinary turn's payload stays byte-identical to before this existed.
        record, self._turn_metrics = self._turn_metrics, None
        if record is not None and turn_log_path() is not None:
            event["metrics"] = record
        if self._turn_failed:
            self._turn_failed = False
            event["failed"] = True
        self.delivered_answer = event["content"] or ""
        self._emit(event)

    def print_repeated_tool_warning(self):
        self._emit(
            {
                "type": "status",
                "status": "warning",
                "message": "Detected repetitive tool call pattern. Execution paused.",
            }
        )

    def print_completion(self, steps_taken: int, steps_limit: int):
        self._emit(
            {
                "type": "status",
                "status": "complete",
                "message": f"Completed in {steps_taken} steps",
                "steps": steps_taken,
                "elapsed": self._elapsed(),
            }
        )

    def print_step_paused(self, description: str):
        pass  # Not relevant for web UI

    def print_command_executing(self, command: str):
        self._emit(
            {
                "type": "tool_start",
                "tool": "run_shell_command",
                "detail": command,
                "narration": derive_narration(
                    "run_shell_command", {"command": command}
                ),
            }
        )

    def print_agent_created(self, agent_id: str) -> None:
        """Notify the frontend that a new agent is available in the registry."""
        self._emit({"type": "agent_created", "agent_id": agent_id})

    # === Optional Methods (with SSE-friendly implementations) ===

    def _take_leading_envelopes(self, end_of_stream: bool) -> bool:
        """Consume response envelopes at the head of the stream buffer.

        Holds an envelope until its closing brace however long it runs (a
        ``write_file`` call carries the whole file), then drops it; its
        ``answer``, if any, is emitted as the answer. The thought, goal and
        plan reach the user through ``print_thought``/``print_goal``/
        ``print_plan`` once the agent parses the full reply.

        Returns True when the caller must keep buffering.
        """
        if self._json_filtered:
            # The gap after a dropped envelope separates it from nothing.
            self._stream_buffer = self._stream_buffer.lstrip()
        if self._broken_envelope_head:
            if not self._stream_buffer.startswith(self._broken_envelope_head):
                self._broken_envelope_head = ""  # a cancelled stream left it behind
            elif end_of_stream:
                logger.debug(
                    "Dropped malformed envelope: %s", self._stream_buffer[:100]
                )
                self._broken_envelope_head = ""
                self._stream_buffer = ""
                self._json_filtered = True
                return False
            else:
                self._report_envelope_progress()
                return True
        if self._envelope_scan is None and self._shown_fence is not None:
            return False  # JSON inside a code block is the answer's own content
        while self._stream_buffer:
            # A reply that opens with its answer streams that answer as it
            # arrives (case 1b in print_streaming_text), not once it closes.
            if _ANSWER_FIRST_RE.match(self._stream_buffer):
                self._envelope_scan = None
                return False
            resume = self._envelope_scan
            if resume is not None and (
                len(self._stream_buffer) < resume[1][0]
                or not self._stream_buffer.startswith(self._envelope_head)
            ):
                resume = None
            state, start, end, consumed, scan = _leading_envelope(
                self._stream_buffer, end_of_stream, resume
            )
            self._envelope_scan = None
            if state == _ENV_OPEN and not end_of_stream:
                self._envelope_scan = scan
                self._envelope_head = self._stream_buffer[:256]
            if state == _ENV_NONE:
                if self._split_inline_envelope(end_of_stream):
                    continue
                return False
            if state == _ENV_PENDING:
                return not end_of_stream
            if state == _ENV_OPEN:
                answer_start = scan[1][-1] if scan else None
                if answer_start is not None:
                    # The answer streams as it arrives; the thought before it
                    # is dropped and the structure after it is the decoder's tail.
                    raw_value = self._stream_buffer[answer_start:]
                    self._stream_buffer = ""
                    self._envelope_scan = None
                    self._json_filtered = True
                    self._answer_stream = JsonStringStreamDecoder()
                    self._emit_answer_text(self._answer_stream.feed(raw_value))
                    if end_of_stream:
                        self._answer_stream = None
                    return True
                if not end_of_stream:
                    self._report_envelope_progress()
                    return True
                # Truncated mid-envelope (e.g. max tokens): still protocol.
                logger.debug(
                    "Dropped unterminated envelope: %s", self._stream_buffer[:100]
                )
                self._stream_buffer = ""
                self._json_filtered = True
                return False
            envelope = _parse_envelope(self._stream_buffer[start:end])
            if envelope is None:
                lead = _ENVELOPE_LEAD_RE.match(self._stream_buffer, start)
                if not _is_malformed_envelope(self._stream_buffer[start:end], lead):
                    return False
                # An unescaped quote in a long thought: the scan cannot be
                # trusted to have found its end, so the reply is held whole.
                self._broken_envelope_head = self._stream_buffer[:256]
                return self._take_leading_envelopes(end_of_stream)
            answer = envelope.get("answer")
            if isinstance(answer, str) and answer.strip():
                self._emit({"type": "answer", "content": answer})
            logger.debug("Filtered response envelope: %s", self._stream_buffer[:100])
            rest = self._stream_buffer[consumed:].lstrip()
            self._stream_buffer = ""
            self._json_filtered = True
            # Text that arrived in the envelope's last token skipped the markup filter.
            self._stream_buffer = self._filter_tool_markup(rest, end_of_stream)
        return False

    def _split_inline_envelope(self, end_of_stream: bool) -> bool:
        """Emit the prose before an envelope that opens on a line of its own.

        Models narrate a line, then write the call; the envelope is then not at
        the head of the buffer. True when the buffer now starts with it.
        """
        buf = self._stream_buffer
        pos = 0
        while True:
            nl = buf.find("\n", pos)
            if nl < 0:
                return False
            i = _skip_ws(buf, nl + 1)
            pos = nl + 1
            if i >= len(buf) or buf[i] != "{":
                continue
            if self._fence_at(buf[: nl + 1])[0] is not None:
                continue  # a JSON line in a code block is the answer's content
            if _leading_envelope(buf[i:], end_of_stream)[0] == _ENV_NONE:
                continue
            prose = buf[:i]
            if prose.strip():
                self._json_filtered = False
                self._emit({"type": "chunk", "content": prose})
            self._stream_buffer = buf[i:]
            return True

    def _report_envelope_progress(self) -> None:
        if self._call_progress_due(len(self._stream_buffer)):
            named = _ENVELOPE_TOOL_NAME_RE.search(self._stream_buffer)
            self._report_call_progress(
                len(self._stream_buffer),
                self._stream_buffer,
                named.group(1) if named else None,
            )

    def _call_progress_due(self, chars: int) -> bool:
        """Whether a held-back call is long enough, and it is time, to report it."""
        if chars < TOOL_CALL_PROGRESS_MIN_CHARS:
            return False
        now = time.monotonic()
        if now < self._call_progress_next:
            return False
        self._call_progress_next = now + TOOL_CALL_PROGRESS_INTERVAL_S
        return True

    def _report_call_progress(self, chars: int, held: str, tool: Optional[str]) -> None:
        """Say a held-back tool call is still being written, and how big it is.

        Only its size and the tool's kind are reported, never its content. An
        envelope that has not named a tool yet is still its "thought".
        """
        if tool is None and held:
            words = len(held.split())
            self.report_phase(
                "reasoning", f"Reasoning — {words:,} words so far", words=words
            )
            return
        self.report_phase(
            "tool_call", tool_call_progress_message(tool, chars), chars=chars
        )

    def _filter_tool_markup(self, text: str, end_of_stream: bool) -> str:
        """*text* with any native tool-call markup removed, as it streams.

        A call is held from its opener to its closer however long it runs, then
        dropped; the agent parses calls from the full reply. Markup inside a fenced
        code block is an example, not a call, and passes through.
        """
        text = self._markup_pending + text
        self._markup_pending = ""
        if self._markup_close is None and self._in_open_envelope():
            # Markup inside an envelope is string content; what follows the
            # envelope is filtered once the envelope is consumed.
            _, self._raw_line_start = _fence_after(text, None, self._raw_line_start)
            return text
        out: List[str] = []
        # Line-start state of the raw stream (thinking included) since the last call.
        line_base, line_from = self._raw_line_start, 0
        while text:
            if self._markup_close is not None:
                close = self._markup_close.search(text)
                if close is None:
                    keep = "" if end_of_stream else text[-_MARKUP_TAIL:]
                    self._markup_chars += len(text) - len(keep)
                    if self._markup_tool is None:
                        named = _MARKUP_TOOL_NAME_RE.search(text)
                        if named:
                            self._markup_tool = named.group(1)
                    self._markup_pending = keep
                    if not end_of_stream and self._call_progress_due(
                        self._markup_chars
                    ):
                        self._report_call_progress(
                            self._markup_chars, "", self._markup_tool
                        )
                    break
                if self._markup_tool is None:
                    named = _MARKUP_TOOL_NAME_RE.search(text, 0, close.start())
                    if named:
                        self._markup_tool = named.group(1)
                logger.debug(
                    "Filtered %s tool-call markup (%d chars)",
                    self._markup_tool,
                    self._markup_chars + close.end(),
                )
                text = text[close.end() :]
                self._markup_close = None
                self._json_filtered = True
                line_base, line_from = True, len(out)
                continue
            unshown = self._stream_buffer + "".join(out)
            line_prefix = "".join(out[line_from:])
            opener = self._next_markup_opener(unshown, text, line_base, line_prefix)
            if opener is None:
                cut = None if end_of_stream else _partial_markup_start(text)
                if cut is None and not end_of_stream and text.endswith(("`", "~")):
                    cut = len(text.rstrip("`~"))  # a fence may be half-written
                safe = text if cut is None else text[:cut]
                self._markup_pending = "" if cut is None else text[cut:]
                out.append(self._strip_stray_tags(unshown, safe))
                break
            start, end, closer = opener
            out.append(self._strip_stray_tags(unshown, text[:start]))
            self._markup_close = closer
            self._markup_tool = None
            self._markup_chars = end - start
            named = _MARKUP_INVOKED_NAME_RE.match(text, end)
            if named:
                self._markup_tool = named.group(1)
            text = text[end:]
        if end_of_stream and self._markup_close is not None:
            logger.debug("Dropped unterminated tool-call markup")
            self._markup_close = None
            self._json_filtered = True
        _, self._raw_line_start = _fence_after(
            "".join(out[line_from:]), None, line_base
        )
        return "".join(out)

    def _in_open_envelope(self) -> bool:
        """Whether the stream buffer holds the start of a response envelope."""
        if self._envelope_scan is not None:
            return True
        buf = self._stream_buffer
        if not buf.lstrip().startswith("{"):
            return False
        return _leading_envelope(buf, False)[0] in (_ENV_PENDING, _ENV_OPEN)

    def _strip_stray_tags(self, unshown: str, text: str) -> str:
        """*text* minus leftover markup tags, except inside a code block."""
        out: List[str] = []
        last = 0
        for m in _MARKUP_STRAY_RE.finditer(text):
            if self._fence_at(unshown + text[: m.start()])[0] is None:
                out.append(text[last : m.start()])
                last = m.end()
        out.append(text[last:])
        return "".join(out)

    def _next_markup_opener(
        self, unshown: str, text: str, line_base: bool, line_prefix: str
    ) -> "Optional[tuple]":
        """``(start, end, closer)`` of the first tool-call opener outside code."""
        pos = 0
        while True:
            m = _MARKUP_OPEN_RE.search(text, pos)
            if m is None:
                return None
            pos = m.end()
            if self._fence_at(unshown + text[: m.start()])[0] is not None:
                continue
            _, line_start = _fence_after(
                line_prefix + text[: m.start()], None, line_base
            )
            for open_re, make_close in _MARKUP_FAMILIES:
                fam = open_re.match(text, m.start())
                if fam is None:
                    continue
                if open_re.pattern in _LINE_START_OPENERS and not line_start:
                    break
                return m.start(), fam.end(), make_close(fam)

    def print_streaming_text(self, text_chunk: str, end_of_stream: bool = False):
        try:
            self._print_streaming_text(text_chunk, end_of_stream)
        finally:
            if end_of_stream:
                self._reset_reply_state()

    def _print_streaming_text(self, text_chunk: str, end_of_stream: bool) -> None:
        if text_chunk or (
            end_of_stream and (self._markup_pending or self._markup_close is not None)
        ):
            text_chunk = self._filter_tool_markup(text_chunk or "", end_of_stream)
        if text_chunk:
            # Buffer text to detect and suppress raw tool-call JSON that
            # LLMs sometimes emit as text content before the tool is invoked.
            self._stream_buffer += text_chunk

            # Inside a JSON answer string: decode escapes as they arrive and
            # drop the closing `"}` structure that follows the value.
            if self._answer_stream is not None:
                self._emit_answer_text(self._answer_stream.feed(self._stream_buffer))
                self._stream_buffer = ""
                if end_of_stream:
                    self._answer_stream = None
                return

            # ── Handle <think>...</think> blocks ──────────────────────
            # Route thinking content to thinking events, keep remainder
            # in buffer for normal tool-call filtering below.
            # Inside an open envelope a "<think>" is string content, and
            # searching a long held envelope on every token is quadratic.
            if self._envelope_scan is not None and not self._stream_buffer.startswith(
                self._envelope_head
            ):
                self._envelope_scan = None  # a cancelled stream left it behind
            while self._in_thinking or (
                self._envelope_scan is None and "<think>" in self._stream_buffer
            ):
                if self._in_thinking:
                    # We're inside a thinking block — look for closing tag
                    close_idx = self._stream_buffer.find("</think>")
                    if close_idx >= 0:
                        thinking_text = self._stream_buffer[:close_idx].strip()
                        if thinking_text:
                            self._emit({"type": "thinking", "content": thinking_text})
                        self._stream_buffer = self._stream_buffer[
                            close_idx + len("</think>") :
                        ]
                        self._in_thinking = False
                        continue  # Check for more <think> blocks
                    else:
                        # Still inside thinking — emit partial and wait
                        if self._stream_buffer.strip():
                            self._emit(
                                {"type": "thinking", "content": self._stream_buffer}
                            )
                        self._stream_buffer = ""
                        return
                else:
                    # Not in thinking — look for opening tag
                    open_idx = self._stream_buffer.find("<think>")
                    if open_idx >= 0:
                        # Emit any text before <think> as regular content,
                        # stripping thought/tool-call JSON artifacts that the
                        # model sometimes outputs before its think block.
                        before = self._stream_buffer[:open_idx]
                        before = _THOUGHT_JSON_SUB_RE.sub("", before)
                        before = _TOOL_CALL_JSON_SUB_RE.sub("", before)
                        if before.strip():
                            self._json_filtered = False
                            self._emit({"type": "chunk", "content": before})
                        else:
                            self._json_filtered = True
                        self._stream_buffer = self._stream_buffer[
                            open_idx + len("<think>") :
                        ]
                        self._in_thinking = True
                        # Inline <think> carries no word count, but it does
                        # prove the model has moved on from reading the prompt.
                        self.report_phase("reasoning", "Reasoning")
                        continue
                    else:
                        break  # No more <think> tags

            # If buffer is empty after thinking extraction, nothing left to do
            if self._take_leading_envelopes(end_of_stream):
                return
            if not self._stream_buffer:
                return

            stripped = self._stream_buffer.strip()

            # Case 0: Buffer starts with "{" — hold until we can identify the
            # JSON type (tool call vs final answer).  The LLM outputs either
            # {"tool": ..., "tool_args": {...}} or {"thought": ..., "answer": ...}.
            # We MUST see "tool" or "answer" before routing to Case 1/1b.
            # Releasing early (e.g., on "thought") causes partial JSON to leak
            # as text chunks and then get stripped by _THOUGHT_JSON_SUB_RE,
            # producing an empty response.
            # Hold limit: 8 KB for proper JSON objects ({"...}), 30 bytes for
            # curly braces in plain text (e.g. "Use {var} in your code").
            _looks_like_json_obj = bool(re.match(r'^\{\s*"', stripped))
            _hold_limit = 8192 if _looks_like_json_obj else 30
            if (
                stripped.startswith("{")
                and '"tool"' not in stripped
                and '"answer"' not in stripped
                and not end_of_stream
                and len(stripped) < _hold_limit
            ):
                return  # Wait for more tokens

            # Case 1: Buffer starts with "{" and has "tool" — pure JSON accumulation
            if stripped.startswith("{") and '"tool"' in stripped:
                if len(self._stream_buffer) > 2048:
                    self._emit({"type": "chunk", "content": self._stream_buffer})
                    self._stream_buffer = ""
                    self._json_filtered = False
                    return
                if stripped.endswith("}"):
                    if _TOOL_CALL_JSON_RE.match(stripped):
                        logger.debug("Filtered tool-call JSON: %s", stripped[:100])
                        self._stream_buffer = ""
                        self._json_filtered = True
                        return
                    # Also handle compound patterns where "tool"/"tool_args" are
                    # preceded by "thought"/"goal" keys, e.g.:
                    #   {"thought": "...", "goal": "...", "tool": "x", "tool_args": {...}}
                    cleaned = _TOOL_CALL_JSON_SUB_RE.sub("", stripped)
                    cleaned = _THOUGHT_JSON_SUB_RE.sub("", cleaned).strip()
                    if not cleaned:
                        logger.debug(
                            "Filtered compound tool-call JSON: %s", stripped[:100]
                        )
                        self._stream_buffer = ""
                        return
                    self._emit({"type": "chunk", "content": cleaned})
                    self._stream_buffer = ""
                    self._json_filtered = False
                # If end_of_stream, fall through to the flush block below
                # instead of returning (otherwise the buffer is never flushed).
                if not end_of_stream:
                    return

            # Case 1b: Buffer starts with "{" and has "answer" — raw JSON answer
            # The LLM sometimes emits {"answer": "..."} as the entire response.
            # Once the value's opening quote arrives, stream the decoded value.
            elif stripped.startswith("{") and '"answer"' in stripped:
                value_start = json_string_field_start(self._stream_buffer, "answer")
                if value_start is not None:
                    raw_value = self._stream_buffer[value_start:]
                    self._stream_buffer = ""
                    self._json_filtered = True
                    self._answer_stream = JsonStringStreamDecoder()
                    self._emit_answer_text(self._answer_stream.feed(raw_value))
                    if end_of_stream:
                        self._answer_stream = None
                    return
                if len(self._stream_buffer) > 4096:
                    # Safety: don't buffer forever
                    self._stream_buffer = ""
                    self._json_filtered = True
                    return
                if not end_of_stream:
                    return

            # Case 2: Buffer has "answer" embedded after normal text
            # e.g., "...some text. {"answer": "duplicated text..."}"
            # Strip the JSON portion, emit only the text before it.
            elif '"answer"' in stripped and '{"answer"' in self._stream_buffer:
                json_idx = self._stream_buffer.find('{"answer"')
                if json_idx >= 0:
                    text_before = self._stream_buffer[:json_idx].rstrip()
                    if text_before:
                        self._emit({"type": "chunk", "content": text_before})
                    # Buffer the JSON part — discard when complete
                    json_part = self._stream_buffer[json_idx:]
                    json_stripped = json_part.strip()
                    if json_stripped.endswith("}"):
                        logger.debug(
                            "Filtered embedded answer JSON: %s", json_stripped[:100]
                        )
                        self._stream_buffer = ""
                        self._json_filtered = True
                    else:
                        self._stream_buffer = json_part  # Keep buffering
                    return

            # Case 3: Buffer has "tool" embedded after normal text (e.g., "I'll help.\n{"tool":...")
            # Suppress the planning text before the JSON (system prompt forbids pre-tool
            # reasoning text) and discard the tool-call JSON itself.
            elif '"tool"' in stripped and '{"tool"' in self._stream_buffer:
                json_idx = self._stream_buffer.find('{"tool"')
                if json_idx > 0:
                    # Suppress text_before — it's pre-tool planning text that the system
                    # prompt explicitly forbids ("NEVER output planning text before a tool call").
                    # The tool will execute and its result will be shown instead.
                    json_part = self._stream_buffer[json_idx:]
                    self._stream_buffer = json_part
                    # Check if the JSON part is complete
                    json_stripped = json_part.strip()
                    if json_stripped.endswith("}"):
                        if _TOOL_CALL_JSON_RE.match(json_stripped):
                            logger.debug(
                                "Filtered embedded tool-call JSON (and preceding planning text): %s",
                                json_stripped[:100],
                            )
                            self._stream_buffer = ""
                            self._json_filtered = True
                            return
                        # JSON didn't match tool-call pattern — emit it as content
                        self._emit({"type": "chunk", "content": json_part})
                        self._stream_buffer = ""
                        self._json_filtered = False
                    return

            # Case 3.5: Buffer contains "chunks" — RAG tool-result JSON leaking
            # into the response stream.  Strip it out and emit the clean text.
            elif '"chunks"' in stripped:
                cleaned = _RAG_RESULT_JSON_SUB_RE.sub("", self._stream_buffer).strip()
                if cleaned:
                    self._emit({"type": "chunk", "content": cleaned})
                self._stream_buffer = ""
                return

            # Not tool-call JSON — emit the buffered content.
            # Suppress bare closing-brace artifacts (e.g. "}" or "}}") that appear
            # immediately after a JSON block was filtered — these are structural
            # remnants of JSON wrappers, not real text content.
            if self._json_filtered and re.match(r"^[\s}]+$", stripped):
                logger.debug("Suppressed JSON artifact: %r", stripped)
                self._stream_buffer = ""
                return
            self._json_filtered = False
            self._emit({"type": "chunk", "content": self._stream_buffer})
            self._stream_buffer = ""

        if end_of_stream and self._stream_buffer:
            self._take_leading_envelopes(end_of_stream=True)
        if end_of_stream and self._stream_buffer:
            # Flush any remaining buffer at end of stream
            stripped = self._stream_buffer.strip()
            is_json_fragment = bool(re.match(r"^[\s}]+$", stripped))
            if (
                not _TOOL_CALL_JSON_RE.match(stripped)
                and not _ANSWER_JSON_RE.search(stripped)
                and not is_json_fragment
            ):
                self._emit({"type": "chunk", "content": self._stream_buffer})
            self._stream_buffer = ""
        if end_of_stream:
            self._answer_stream = None

    def _emit_answer_text(self, text: str) -> None:
        if text:
            self.delivered_answer = (self.delivered_answer or "") + text
            self._emit({"type": "chunk", "content": text})

    # === Tool Confirmation (blocking) ===

    def insists_on_asking(self, tool_name: str, tool_args: Dict[str, Any]) -> bool:
        """True when this call must reach the prompt even if a grant covers it.

        Only the eval's scripted user insists: it has to see a call to decline
        it, and a skill grant would otherwise run ``pytest`` with nobody asked.
        """
        return scripted_user.declines(tool_name, tool_args)

    def note_call_args(self, tool_name: str, call_args: Dict[str, Any]) -> None:
        """Record what the model sent for the call the next prompt asks about.

        The prompt's arguments can carry what GAIA adds for display (the folder
        a shell call runs in, a risk note), so when they differ the
        ``permission_request`` also carries these as ``call_args``. Its only
        reader is ``CanonicalTranslator`` (the ``/query`` wire's ``args``); the
        Agent UI ignores it. Consumed by the next :meth:`confirm_tool_execution`,
        and only for the same tool.
        """
        self._noted_call = (tool_name, dict(call_args))

    def confirm_tool_execution(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        timeout: Optional[float] = _USE_HANDLER_TIMEOUT,
    ) -> bool:
        """Block the agent thread until the user approves or denies a tool call.

        Emits a ``permission_request`` SSE event so the frontend can show a modal
        and waits for ``resolve_tool_confirmation()``. Returns ``True`` if the
        user allows, ``False`` otherwise.

        *timeout* defaults to ``self.confirm_timeout_seconds``. ``None`` waits
        indefinitely and no host sets it: a prompt the user never saw then
        produces a turn nothing can end, which is worse than either answer. A
        host with a live decision channel gives itself a generous bound instead
        — long enough that reading is never a way to lose the call, finite so
        the turn always ends saying why. Expiry denies; it never approves.

        The wait is interruptible either way — ``cancelled`` breaks the loop, so
        Ctrl+C and a closed stdin both end the run before any bound is reached.
        """
        if timeout is _USE_HANDLER_TIMEOUT:
            timeout = self.confirm_timeout_seconds
        noted, self._noted_call = self._noted_call, None
        call_args = noted[1] if noted is not None and noted[0] == tool_name else None

        # A call only a person may approve: pre-approval never answers it.
        needs_person = (
            isinstance(tool_args, dict) and tool_args.get(NEEDS_PERSON_ARG) is True
        )
        if needs_person:
            tool_args = {k: v for k, v in tool_args.items() if k != NEEDS_PERSON_ARG}
            if not self.answers_confirmations:
                return self.deny_tool_execution(
                    tool_name,
                    f"'{tool_name}' needs your approval and nobody can answer on "
                    "this run. Start the request from the GAIA terminal UI or "
                    "Agent UI to approve it there.",
                )

        # Before auto-approve: the eval runs with it on, and this is its only no.
        if scripted_user.declines(tool_name, tool_args):
            self._emit(
                {
                    "type": "tool_confirm_denied",
                    "tool": tool_name,
                    "reason": "scripted_user",
                    "message": f"Tool '{tool_name}' was denied by the user.",
                }
            )
            logger.info("Scripted eval user declined '%s'", tool_name)
            return self.deny_tool_execution(
                tool_name, f"Tool '{tool_name}' was denied by the user."
            )

        # Full access and prior "always" grants are checked before anything is
        # emitted: neither has a question to ask, so putting a modal up would be
        # theatre. Checked per call, so toggling full access mid-run takes effect on
        # the very next gated tool.
        # Pre-approval answers tool prompts; only full access widens the file scope.
        widens_scope = tool_name == PATH_ACCESS_PROMPT_TOOL and not self.full_access
        if (
            self.auto_approve_confirmations_enabled()
            and not widens_scope
            and not needs_person
        ):
            self.log_auto_approval(tool_name)
            self._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": (
                        "Full access is on, so GAIA didn't ask first. "
                        f"{derive_narration(tool_name, tool_args)}"
                    ),
                }
            )
            return True

        if self.edit_is_auto_accepted(tool_name, tool_args):
            self._last_denial = None
            logger.info("Accept-edits mode ran '%s' inside the workspace", tool_name)
            self._emit(
                {
                    "type": "status",
                    "status": "info",
                    "message": (
                        "Accept edits is on, so GAIA didn't ask first. "
                        f"{derive_narration(tool_name, tool_args)}"
                    ),
                }
            )
            return True

        if self.call_is_granted(tool_name, tool_args):
            self._last_denial = None
            logger.info(
                "Confirmation-gated call to '%s' pre-approved for this session "
                "by the user",
                tool_name,
            )
            return True

        # Background mode: immediately deny — no semaphore hold, no waiting.
        if self.background_mode:
            unattended_message = (
                f"'{tool_name}' requires live user approval and cannot run "
                "unattended. Use request_user_input() to notify the user, "
                "then retry in a subsequent turn."
            )
            self._emit(
                {
                    "type": "tool_confirm_denied",
                    "tool": tool_name,
                    "reason": "unattended",
                    "message": unattended_message,
                }
            )
            logger.info(
                "Background mode: immediately denied confirmation for '%s'", tool_name
            )
            self._last_denial = Denial(tool_name, unattended_message)
            return False

        confirm_id = str(uuid.uuid4())
        with self._confirm_lock:
            self._confirm_event = threading.Event()
            self._confirm_result = False
            self._confirm_timed_out = False
            self._confirm_id = confirm_id
            self._confirm_tool = tool_name
            self._confirm_args = tool_args if isinstance(tool_args, dict) else {}
        self._last_denial = None

        request: Dict[str, Any] = {
            "type": "permission_request",
            "tool": tool_name,
            # The tool in plain words, for the question; ``tool`` stays the id.
            "tool_label": tool_phrase(tool_name),
            "args": tool_args,
            "confirm_id": confirm_id,
        }
        if call_args is not None and call_args != tool_args:
            request["call_args"] = call_args
        # Present only when an "always" answer would create a grant, and it
        # names exactly what that grant covers. A front-end must not offer the
        # choice without it: an unqualified "always" is read as far broader
        # than the invocation-scoped grant actually on offer.
        scope = grant_scope(tool_name, tool_args)
        if scope is not None:
            request["always_scope"] = scope.label
            # ``always_scope`` is matched as-is by the MCP control; this is its wording.
            always_label = grant_phrase(tool_name, scope.subject)
            if always_label:
                request["always_label"] = always_label
        # What this call does — `pytest` is not `rm -rf` — so the prompt's label
        # is earned by the command, not by the tool's worst case.
        risk = call_risk(tool_name, tool_args)
        if risk is not None:
            request["risk"] = risk
        # Advertised only when it is real. A front-end told "60 s" that then
        # never expires runs a countdown to nothing; one told nothing correctly
        # renders a prompt that waits.
        if timeout is not None:
            request["timeout_seconds"] = timeout
        self._emit(request)

        # Poll in short intervals so cancellation is detected promptly.
        deadline = None if timeout is None else time.monotonic() + timeout
        answered = False
        with self._away_from_model():
            while deadline is None or time.monotonic() < deadline:
                if self.cancelled.is_set():
                    break
                if self._confirm_event.wait(timeout=0.5):
                    answered = True
                    break
        if self.cancelled.is_set():
            self._clear_pending_confirmation()
            self._last_denial = Denial(
                tool_name,
                f"Confirmation for '{tool_name}' was abandoned: the run was "
                "cancelled before the user answered.",
            )
            return False

        if not answered:
            self._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": f"Confirmation for '{tool_name}' timed out ({timeout:g} s). Execution denied.",
                }
            )
            logger.warning("Tool confirmation timed out for '%s'", tool_name)
            self._clear_pending_confirmation()
            self._last_denial = Denial(
                tool_name,
                timeout_denial_message(tool_name, f"{timeout:g} s"),
                timed_out=True,
            )
            return False

        result = self._confirm_result
        timed_out = self._confirm_timed_out
        self._clear_pending_confirmation()
        if timed_out:
            # The client's own clock ran out and it said so: a timeout, never
            # "the user denied it".
            self._last_denial = Denial(
                tool_name,
                timeout_denial_message(tool_name, "the time allowed"),
                timed_out=True,
            )
        elif not result:
            self._last_denial = Denial(
                tool_name,
                f"Tool '{tool_name}' was denied by the user.",
            )
        return result

    def waiting_on(self) -> Optional[str]:
        """``"permission"`` or ``"question"`` while the turn is paused on the user.

        An answered prompt no longer counts, even while the turn waits in line
        to get back on the model.
        """
        with self._confirm_lock:
            unanswered = self._confirm_event is None or not self._confirm_event.is_set()
            if (
                self._confirm_id is not None and unanswered
            ) or self._relay_confirm_id is not None:
                return "permission"
        with self._user_input_lock:
            if self._relay_question_id is not None or any(
                not evt.is_set() for evt in self._user_input_events.values()
            ):
                return "question"
        return None

    @contextmanager
    def _away_from_model(self) -> Iterator[None]:
        """Let other chats use the model while this turn waits on the user.

        On the way back the turn retakes the model at the front of the line,
        and a Stop while it waits there ends the turn.
        """
        if not self.step_away_from_model():
            yield
            return
        try:
            yield
        finally:
            self.come_back_to_model()

    def step_away_from_model(self) -> bool:
        """Give the model to other chats while the turn waits on the user.

        Returns whether it was given up, i.e. whether
        :meth:`come_back_to_model` must follow once the wait ends.
        """
        slot = self.turn_slot
        try:
            return slot is not None and slot.step_away()
        except TimeoutError:
            # The hand-back may still run later; never use the model unheld.
            logger.error("Could not give the model up while waiting; stopping the turn")
            self.cancelled.set()
            return False

    def come_back_to_model(self) -> None:
        """Retake the model at the front of the line after a wait on the user.

        A Stop while the turn waits in line, or a model that can no longer be
        loaded, sets ``cancelled``; the caller checks it before going on.
        """
        if self.cancelled.is_set():
            return
        if not self.turn_slot.come_back(self.cancelled):
            # Withdrawn from the line: the turn must not use the model.
            self.cancelled.set()
        elif not self.cancelled.is_set() and self.on_model_regained is not None:
            try:
                self.on_model_regained()
            except Exception as exc:  # pylint: disable=broad-except
                # The model the turn needs is gone and can't be loaded:
                # end the turn before the approved tool runs, and say why.
                logger.error("Model check after the user answered failed: %s", exc)
                self._emit({"type": "agent_error", "content": str(exc)})
                self.cancelled.set()

    def _clear_pending_confirmation(self) -> None:
        """Drop the live prompt's slot so a later answer cannot resolve it."""
        with self._confirm_lock:
            self._confirm_id = None
            self._confirm_event = None
            self._confirm_tool = None
            self._confirm_args = None

    def print_policy_alert(
        self,
        tool_name: str,
        decision: str,
        reason: str,
        rule_ids: List[str],
        policy_version: str,
        receipt_id: Optional[str] = None,
    ) -> None:
        """Emit a policy alert event for a governance-blocked tool call."""
        event: Dict[str, Any] = {
            "type": "policy_alert",
            "tool": tool_name,
            "decision": decision,
            "reason": reason,
            "rule_ids": list(rule_ids),
            "policy_version": policy_version,
        }
        if receipt_id is not None:
            event["receipt_id"] = receipt_id
        self._emit(event)

    def resolve_tool_confirmation(
        self,
        approved: bool,
        always: bool = False,
        confirm_id: Optional[str] = None,
        timed_out: bool = False,
    ) -> bool:
        """Unblock the agent thread waiting in ``confirm_tool_execution()``.

        Called by the ``POST /api/chat/confirm-tool`` HTTP endpoint and by the
        TUI's stdio control channel. Returns ``False`` if there is no pending
        confirmation request.

        *always* grants the pending tool for the rest of the session, so this
        prompt and every later one for the same tool are approved — the grant
        the terminal prompt's ``[a]lways for this tool`` already makes.

        *timed_out* marks a denial the client made because nobody answered in
        time, so the agent reports a timeout rather than a refusal.

        *confirm_id*, when given, must match the live prompt. A decision typed
        against a prompt that has since expired or been replaced is dropped
        rather than applied to whatever is on screen now — the difference
        between approving what you read and approving what arrived while you
        were reading.
        """
        with self._confirm_lock:
            if confirm_id is not None and confirm_id != self._confirm_id:
                logger.warning(
                    "Dropped a tool decision for confirm_id %s: the live prompt "
                    "is %s",
                    confirm_id,
                    self._confirm_id,
                )
                return False
            if self._confirm_event is None:
                # Nothing is waiting: the next prompt starts a fresh slot, so an
                # answer recorded here would be reported delivered and then lost.
                return False
            if self._confirm_event.is_set():
                # Answered already: a second answer must not overwrite the first.
                return False
            if always and self._confirm_tool:
                self.grant_call_for_session(self._confirm_tool, self._confirm_args)
            self._confirm_result = (approved or always) and not timed_out
            self._confirm_timed_out = timed_out
            self._confirm_event.set()
        return True

    def close_active_relay_response(self) -> None:
        """Force-close a live email-relay HTTP response, if any (#2109).

        A between-events ``cancelled`` check alone cannot interrupt a relay
        worker thread parked in a blocking socket read — this is the other
        half of that seam: called from the cancel path (both the explicit
        ``/api/chat/cancel`` endpoint and the streaming generator's orphan
        cleanup) right after ``cancelled.set()``.

        The unblocking MUST go through ``socket.shutdown()``, not
        ``resp.close()``: CPython's buffered reader holds its internal lock
        for the whole blocking ``read()``, and ``close()`` waits on that same
        lock — so a cross-thread ``close()`` blocks until the read returns on
        its own (proven by the real-socket test in
        ``tests/unit/test_email_sidecar_proxy.py``). ``shutdown()`` bypasses
        the file-object layer entirely and wakes the parked ``recv``
        immediately; the relay's stream generator then closes the response
        itself on the way out. ``resp.close()`` remains only as the fallback
        when no underlying socket can be found (e.g. mocked responses).
        """
        resp = self.active_relay_response
        if resp is None:
            return
        sock = _peel_response_socket(resp)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                if os.name == "nt":
                    # Windows' select() ignores a local shutdown; only closing the
                    # handle wakes it. detach() leaves the object at fd -1.
                    socket.close(sock.detach())
                return  # reader wakes promptly; the stream owner closes resp
            except OSError:
                logger.debug(
                    "email relay: socket shutdown failed; falling back to close",
                    exc_info=True,
                )
        try:
            resp.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup, never fatal
            logger.debug("email relay: failed to close active response", exc_info=True)

    def tool_running(self) -> bool:
        """True from a tool's start until its result or end."""
        return self._tool_start_time is not None

    def awaiting_user_input(self) -> bool:
        """True while a question or a tool confirmation is waiting on the user."""
        if self._confirm_id is not None or self._relay_confirm_id is not None:
            return True
        with self._user_input_lock:
            return bool(self._user_input_queue) or self._relay_question_id is not None

    def request_user_input_blocking(
        self,
        message: str,
        choices: Optional[List[str]] = None,
        default_if_no_response: Optional[str] = None,
        timeout_seconds: int = 300,
        continue_if_no_response: bool = True,
        options: Optional[List[Dict[str, Any]]] = None,
        allow_free_text: bool = True,
        sensitive: bool = False,
    ) -> str:
        """Ask the user a question and block until a response arrives or timeout.

        Emits a ``user_input_request`` SSE event.  In background mode the request
        is still emitted (so it appears in the Goals dashboard), but returns
        ``"__NO_RESPONSE__"`` immediately instead of blocking.

        ``choices`` is the flat list of answer strings.  ``options`` is the
        richer form — ``[{"value", "label", "description"}, ...]`` — for surfaces
        that render a labelled picker; a bare yes/no cannot express "Gmail or
        Outlook?".  Both may be supplied; a surface that only understands
        ``choices`` keeps working unchanged.  ``sensitive`` tells the surface the
        answer is a credential, so it masks the typed characters — asking for an
        OAuth client secret with no way to say "this is a secret" is a defect,
        not a styling preference.

        Returns:
            User's response string, the chosen option, the default value on
            timeout, or ``"__NO_RESPONSE__"`` when no default is provided and
            the timeout expires.  Never returns empty string.
        """
        if not self.answers_questions:
            logger.info("Question not asked: this transport cannot return an answer")
            return (
                default_if_no_response
                if default_if_no_response is not None
                else "__NO_RESPONSE__"
            )

        request_id = str(uuid.uuid4())
        timeout_seconds = max(10, timeout_seconds)  # floor: 10 seconds

        # Register BEFORE emitting, mirroring confirm_tool_execution: a fast
        # client can answer before this thread reaches the registration, and
        # resolve_user_input would then reject a perfectly good answer.
        evt: Optional[threading.Event] = None
        if not self.background_mode:
            evt = threading.Event()
            with self._user_input_lock:
                self._user_input_events[request_id] = evt
                self._user_input_queue.append(request_id)

        self._emit(
            {
                "type": "user_input_request",
                "request_id": request_id,
                "message": message,
                "choices": choices or [],
                "options": options or [],
                "allow_free_text": bool(allow_free_text),
                "sensitive": bool(sensitive),
                "timeout_seconds": timeout_seconds,
                "continue_if_no_response": continue_if_no_response,
            }
        )

        if evt is not None and scripted_user.active():
            # The scripted eval user never answers: release the slot at once.
            with self._user_input_lock:
                self._user_input_queue = deque(
                    rid for rid in self._user_input_queue if rid != request_id
                )
                self._user_input_events.pop(request_id, None)
            evt = None

        if evt is None:
            # Background mode or the scripted user: nobody will answer.  Return
            # the sentinel so the caller can decide whether to proceed or retry.
            return (
                default_if_no_response
                if default_if_no_response is not None
                else "__NO_RESPONSE__"
            )

        # Block until resolved or timeout
        deadline = time.monotonic() + timeout_seconds
        with self._away_from_model():
            while time.monotonic() < deadline:
                if self.cancelled.is_set():
                    break
                if evt.wait(timeout=0.5):
                    break

        # Clean up. Under the lock and as one step, so a resolve landing at the
        # deadline either wins outright or is rejected — never accepted into a
        # slot this thread is about to drop.
        with self._user_input_lock:
            self._user_input_queue = deque(
                rid for rid in self._user_input_queue if rid != request_id
            )
            self._user_input_events.pop(request_id, None)
            response = self._user_input_results.pop(request_id, None)

        if response is not None:
            return response
        if default_if_no_response is not None:
            return default_if_no_response
        if not continue_if_no_response:
            # Signal loop to pause
            if self.loop_state_directive is None:
                self.loop_state_directive = {
                    "directive": "paused",
                    "reason": "User input timed out and continue_if_no_response=False",
                    "wake_in_seconds": 0,
                }
        return "__NO_RESPONSE__"

    def resolve_user_input(self, request_id: str, response: str) -> bool:
        """Unblock a pending ``request_user_input_blocking()`` call.

        Called by ``POST /api/chat/user-input`` and by the email sidecar's
        ``POST /v1/email/query/{run_id}/respond``.  Returns ``False`` if no
        request with that ID is pending, so the caller can answer 409 rather
        than report an accepted answer nothing will read.
        """
        with self._user_input_lock:
            evt = self._user_input_events.get(request_id)
            if evt is None:
                return False
            self._user_input_results[request_id] = response
            evt.set()
        return True

    def resolve_relay_input(self, request_id: str, value: str) -> bool:
        """Deliver an answer to a pending ``needs_input`` question on an
        in-flight email-relay run (#2595). Returns ``False`` when there is no
        active relay run to answer (already finished, or never one) so the
        caller can 404 rather than report an accepted answer nothing reads.
        Any sidecar rejection (e.g. the question is no longer pending)
        propagates as :class:`~gaia.ui.email_sidecar.errors.SidecarError` —
        never swallowed.
        """
        proxy = self.active_relay_proxy
        run_id = self.active_relay_run_id
        if proxy is None or run_id is None:
            return False
        proxy.respond_query(run_id, request_id, value)
        with self._user_input_lock:
            # A newer question may already be pending; only this one is answered.
            if self._relay_question_id == request_id:
                self._relay_question_id = None
        return True

    def set_relay_question(self, request_id: Optional[str]) -> None:
        """Record (or clear, with ``None``) the relayed question now pending."""
        with self._user_input_lock:
            self._relay_question_id = request_id

    def set_relay_confirmation(self, confirm_id: Optional[str]) -> None:
        """Record (or clear, with ``None``) the sidecar prompt now pending."""
        with self._confirm_lock:
            self._relay_confirm_id = confirm_id

    def relay_confirmation_pending(self) -> bool:
        with self._confirm_lock:
            return self._relay_confirm_id is not None

    def resolve_relay_confirmation(self, approved: bool, confirm_id: str) -> bool:
        """Hand the user's allow-once/deny to the sidecar run parked on it.

        Returns ``False`` when *confirm_id* is not the prompt pending on a live
        relayed run. Claimed under the lock before the POST, so a double click
        cannot send two answers; a sidecar rejection raises ``SidecarError``.
        """
        with self._confirm_lock:
            proxy = self.active_relay_proxy
            run_id = self.active_relay_run_id
            if (
                not confirm_id
                or confirm_id != self._relay_confirm_id
                or proxy is None
                or run_id is None
            ):
                return False
            self._relay_confirm_id = None
        proxy.tool_decision(run_id, "allow" if approved else "deny", confirm_id)
        return True

    def signal_done(self):
        """Signal that the agent has finished processing."""
        # Flush any pending thinking content
        if self._in_thinking and self._stream_buffer:
            self._emit({"type": "thinking", "content": self._stream_buffer})
            self._stream_buffer = ""
            self._in_thinking = False

        # A stopped stream can leave a call open or a tag tail held back.
        if self._markup_close is not None or self._markup_pending:
            self._stream_buffer += self._filter_tool_markup("", end_of_stream=True)

        # Flush any remaining stream buffer before signaling done
        if self._stream_buffer:
            self._take_leading_envelopes(end_of_stream=True)
        if self._stream_buffer:
            stripped = self._stream_buffer.strip()
            if not _TOOL_CALL_JSON_RE.match(stripped) and not _ANSWER_JSON_RE.search(
                stripped
            ):
                self._emit({"type": "chunk", "content": self._stream_buffer})
            self._stream_buffer = ""
        self._reset_reply_state()
        self._answer_stream = None
        self._emit(None)  # Sentinel value


def _format_tool_args(  # pylint: disable=unused-argument
    tool_name: str, args: Dict[str, Any]
) -> str:
    """Format tool arguments into a human-readable string."""
    if not args:
        return ""

    parts = []
    for key, value in args.items():
        if value is None or value == "" or value is False:
            continue
        if value is True:
            parts.append(key)
        elif isinstance(value, (list, tuple)):
            parts.append(f"{key}: {', '.join(str(v) for v in value)}")
        elif isinstance(value, str) and len(value) > 150:
            parts.append(f"{key}: {value[:150]}...")
        else:
            parts.append(f"{key}: {value}")

    # A value that itself holds ", " would be split into fake rows by the UI.
    one_line = len(parts) <= 2 and not any(", " in p for p in parts)
    return ", ".join(parts) if one_line else "\n".join(parts)


def _count_summary(data: Dict[str, Any]) -> Optional[str]:
    """``{"status": "success", "skills": [...]}`` -> ``"18 skills"``.

    Returns ``None`` when nothing countable is present, so the caller keeps its
    own wording rather than inventing a number.
    """
    for key, value in data.items():
        if key == "status" or not isinstance(value, list) or not value:
            continue
        return format_count(len(value), key)
    return None


def _summarize_tool_result(data: Dict[str, Any]) -> str:
    """Create a detailed human-readable summary of a tool result."""
    if not isinstance(data, dict):
        return str(data)[:_SUMMARY_CHAR_CAP]

    # Command execution results
    if "command" in data and "stdout" in data:
        stdout = data.get("stdout", "")
        rc = data.get("return_code", 0)
        lines = stdout.strip().split("\n") if stdout.strip() else []
        if rc != 0 and data.get("expected_exit"):
            return f"{data['expected_exit']} (exit {rc})"
        if rc != 0:
            stderr = data.get("stderr", "")
            return f"Command failed (exit {rc})" + (
                f": {stderr[:150]}" if stderr else ""
            )
        if lines:
            # Show first few lines of output
            preview = "\n".join(lines[:5])
            if len(lines) > 5:
                preview += f"\n... ({len(lines)} lines total)"
            return preview
        return "Command completed (no output)"

    # File search results
    if "files" in data or "file_list" in data:
        files = data.get("file_list", data.get("files", []))
        count = data.get("count", len(files) if isinstance(files, list) else 0)
        display_msg = data.get("display_message", "")
        if isinstance(files, list) and files:
            file_names = []
            for f in files[:5]:
                if isinstance(f, dict):
                    name = f.get("name", f.get("filename", f.get("file_name", "")))
                    # Fallback: extract filename from file_path if name keys are missing
                    if not name and f.get("file_path"):
                        name = f["file_path"].replace("\\", "/").rsplit("/", 1)[-1]
                    directory = f.get("directory", "")
                    if directory and name:
                        file_names.append(f"{name} ({directory})")
                    elif name:
                        file_names.append(name)
                    elif directory:
                        file_names.append(directory)
                else:
                    file_names.append(str(f))
            result = "\n".join(f"  {name}" for name in file_names)
            if count > 5:
                result += f"\n  ... +{count - 5} more"
            return (
                (display_msg + "\n" + result)
                if display_msg
                else f"Found {count} file(s):\n{result}"
            )
        if display_msg:
            return display_msg
        return f"Found {count} file(s)"

    # Search/query results with chunks
    if "chunks" in data:
        chunks = data["chunks"]
        if isinstance(chunks, list):
            scores = data.get("scores", [])
            result = f"Found {len(chunks)} relevant chunk(s)"
            if scores:
                result += f" (best score: {max(scores):.2f})"
            # Show brief preview of top chunk
            if chunks and isinstance(chunks[0], str):
                preview = chunks[0][:120].replace("\n", " ")
                result += f'\n  Top match: "{preview}..."'
            return result

    # Search/query results generic
    if "results" in data:
        results = data["results"]
        if isinstance(results, list):
            return f"Found {len(results)} result(s)"
        return str(results)[:200]

    # Document indexing results
    if "num_chunks" in data or "chunk_count" in data:
        chunks = data.get("num_chunks", data.get("chunk_count", 0))
        filename = data.get("filename", data.get("file_path", ""))
        if data.get("already_indexed"):
            return f"Already indexed ({chunks} chunks)"
        if filename:
            return f"Indexed {filename} ({chunks} chunks)"
        return f"Indexed document ({chunks} chunks)"

    # File read results
    if "content" in data and "filepath" in data:
        content = data["content"]
        lines = content.split("\n") if isinstance(content, str) else []
        return f"Read {len(lines)} lines from {data.get('filename', data.get('filepath', 'file'))}"

    # list_indexed_documents results — has "documents" list + "count" + "total_chunks"
    if "documents" in data and "count" in data and "total_chunks" in data:
        count = data.get("count", 0)
        if count == 0:
            return "No documents indexed"
        docs = data.get("documents", [])
        names = [d.get("name", "?") for d in docs[:5] if isinstance(d, dict)]
        result = f"{count} document(s) indexed: {', '.join(names)}"
        if count > 5:
            result += f" (+{count - 5} more)"
        return result

    # Tabular analysis: what was counted, not how many columns the file has.
    row_count = data.get("row_count")
    if (
        isinstance(row_count, int)
        and row_count > 0
        and isinstance(data.get("columns"), list)
        and data.get("status") != "error"
    ):
        rows = format_count(row_count, "rows")
        groups = data.get("group_by_results")
        if isinstance(groups, list) and groups and data.get("group_by"):
            # The tool keeps only the 25 largest groups.
            top = "top " if len(groups) >= 25 else ""
            return f"{top}{format_count(len(groups), 'groups')} by {data['group_by']} · {rows}"
        return f"{rows} · {format_count(len(data['columns']), 'columns')}"

    # Status-based results
    if "status" in data:
        status = data["status"]
        msg = data.get("message", data.get("error", data.get("display_message", "")))
        if msg:
            return f"{status}: {str(msg)[:200]}"
        # A bare "success" tells the user nothing. Report what came back
        # instead, when the payload carries a countable collection (#2804).
        return _count_summary(data) or str(status)

    # Generic fallback - show more useful info
    keys = list(data.keys())[:6]
    return f"Result with keys: {', '.join(keys)}"


def _tool_description(tool_name: str) -> str:
    """Return a human-readable description for known agent tools."""
    descriptions = {
        "query_documents": "Searching indexed documents for relevant content",
        "query_specific_file": "Searching a specific document for relevant content",
        "search_indexed_chunks": "Searching document chunks by keyword",
        "search_documents": "Searching indexed documents for relevant content",
        "search_file": "Searching for files matching a pattern",
        "read_file": "Reading file contents",
        "list_directory": "Listing directory contents",
        "run_shell_command": "Executing a shell command",
        "write_file": "Writing to a file",
        "create_file": "Creating a new file",
        "get_file_preview": "Previewing file contents",
        "index_document": "Indexing a document for retrieval",
        "evaluate_retrieval": "Evaluating document retrieval quality",
        # Registered per agent, so the global label lookup cannot see it.
        "set_reasoning_effort": "Setting reasoning effort",
    }
    return descriptions.get(tool_name, "")


def _clean_answer_json(text: str) -> str:
    """Strip {"answer": "..."} JSON wrapping from LLM output.

    LLMs sometimes wrap their entire response in a JSON envelope like
    ``{"answer": "the actual text..."}``.  This function detects that
    pattern and extracts only the answer content.  It handles valid JSON,
    JSON whose string contains literal newlines, and an envelope cut off
    before its closing quote (a stopped or truncated reply), in which case
    everything received is kept.
    """
    if not text:
        return text
    stripped = text.strip()
    if not (stripped.startswith("{") and '"answer"' in stripped):
        return text
    try:
        parsed = json.loads(stripped, strict=False)
        if isinstance(parsed, dict) and isinstance(parsed.get("answer"), str):
            return parsed["answer"]
        return text
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("Answer envelope is not valid JSON, decoding leniently: %s", exc)
    field = extract_json_string_field(stripped, "answer")
    if field is None:
        return text
    return field[0]


def _fix_double_escaped(text: str) -> str:
    """Fix double-escaped newlines/tabs from LLM output.

    Some models output literal '\\n' (two chars) instead of actual newlines,
    which breaks markdown rendering. Only unescape when there are significantly
    more literal \\n sequences than real newlines.
    """
    if not text:
        return text
    literal_count = text.count("\\n")
    real_count = text.count("\n")
    if literal_count > 2 and literal_count > real_count * 2:
        text = text.replace("\\n", "\n")
        text = text.replace("\\t", "\t")
        text = text.replace('\\"', '"')
    return text


def clean_reply_text(text: str) -> str:
    """Strip the LLM output artifacts a reply must not be stored with."""
    if not text:
        return text
    text = _TOOL_CALL_JSON_SUB_RE.sub("", text)
    # _clean_answer_json MUST run before _THOUGHT_JSON_SUB_RE, or a leaked
    # {"thought":..., "answer":...} blob is stripped whole, answer included.
    text = _clean_answer_json(text)
    text = _THOUGHT_JSON_SUB_RE.sub("", text)
    text = _RAG_RESULT_JSON_SUB_RE.sub("", text)
    # Mixed content: plain text followed by a duplicate {"answer": "..."} wrapper.
    text = _ANSWER_JSON_SUB_RE.sub("", text)
    text = _fix_double_escaped(text)
    # 3+ closing braces at the end are a leaked nested tool result.
    text = re.sub(r"\}{3,}\s*$", "", text).strip()
    # A code fence left after JSON extraction, e.g. "}\n```".
    text = re.sub(r"[\n\s]*`{3,}\s*$", "", text).strip()
    return text
