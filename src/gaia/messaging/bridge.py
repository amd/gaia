# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Transport-agnostic half of a messaging bridge to the flagship agent.

A messaging adapter (Slack today, Signal next) needs the same five things, none
of which are platform-specific: a long-lived ``gaia-agent`` child, one turn at a
time through it, an approval that can be answered *while* a turn is running, a
throttle so streamed tokens do not exceed a platform's edit rate, and a terminal
event for every turn even when the child dies. They live here so a second
adapter is a renderer and a socket, not a second bridge.

The wire is ``gaia_agent.stdio``'s: newline-delimited JSON, one query per stdin
line, canonical ``status`` / ``tool_call`` / ``tool_result`` /
``needs_confirmation`` / ``token`` / ``final`` / ``error`` events back, and a
``gaia_control`` line carrying an approval decision. The constants below are a
cross-process contract with that module, kept as literals rather than imported
because core must not depend on a hub wheel; ``tests/unit/messaging/
test_bridge_contract.py`` pins them against the real definitions.

Security posture: this module exposes no way to turn permission bypass on.
``gaia_agent.stdio`` accepts a ``bypass`` control verb, and a remote surface must
never be able to send it — an allowlisted user is trusted to ask for a tool, not
to silently disarm the gate for every later one. Bypass stays a local decision,
and ``tests/unit/messaging/test_bridge.py`` pins its absence.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from gaia.logger import get_logger

log = get_logger(__name__)

#: Discriminator marking a stdin line as control rather than a query.
#: Contract with ``gaia_agent.stdio.CONTROL_KEY``.
CONTROL_KEY = "gaia_control"

#: Wrapper for a query whose text contains newlines — stdin is read a line at a
#: time, so an unwrapped multi-line question becomes several unrelated turns.
#: Contract with ``gaia_agent.stdio.QUERY_KEY``.
QUERY_KEY = "gaia_query"

#: Control verb answering the confirmation currently pending.
CONTROL_TOOL_DECISION = "tool_decision"

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ALWAYS = "always"

#: Every decision the agent accepts. Anything else fails closed to a deny — the
#: agent does the same on its side, so a typo cannot become consent.
VALID_DECISIONS = frozenset({DECISION_ALLOW, DECISION_DENY, DECISION_ALWAYS})

#: The two events that END a turn. Exactly one arrives per turn.
TERMINAL_EVENTS = frozenset({"final", "error"})

#: The event that pauses a turn until a decision arrives.
NEEDS_CONFIRMATION = "needs_confirmation"

#: Console script installed by the ``gaia-agent-gaia`` wheel
#: (``[project.scripts] gaia-agent = "gaia_agent.stdio:main"``).
DEFAULT_AGENT_BINARY = "gaia-agent"


class AgentChannelError(RuntimeError):
    """The agent child could not be started, or died and cannot be used."""


@dataclass
class Turn:
    """One question and whatever the adapter needs to answer it.

    ``context`` is opaque here on purpose: Slack puts a channel id and a message
    timestamp in it, Signal would put a recipient. The bridge only carries it
    back to the adapter's callbacks.
    """

    text: str
    context: Any = None
    #: Who asked, for the audit line. Never used for authorization — the
    #: adapter has already decided that before a Turn exists.
    sender: str = ""


@dataclass
class _TurnState:
    """Bookkeeping for the turn currently in flight."""

    turn: Turn
    saw_terminal: bool = False
    pending_confirm_ids: List[str] = field(default_factory=list)


class StreamThrottle:
    """Rate-limit edits of a streamed reply.

    Every messaging platform caps how often one message may be edited (Slack's
    ``chat.update`` is near one call per second per channel). Sending an edit per
    token gets the adapter rate-limited and the user a reply that arrives later
    than if it had not streamed at all.

    Accumulated text is flushed at most once per ``interval`` seconds, and
    :meth:`finish` always flushes whatever is left — the last token must never be
    the one the throttle swallowed.
    """

    def __init__(
        self,
        flush: Callable[[str], None],
        interval: float = 1.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._flush = flush
        self._interval = interval
        self._now = now
        self._text = ""
        self._flushed = ""
        self._last = 0.0

    @property
    def text(self) -> str:
        """Everything accumulated so far, flushed or not."""
        return self._text

    def add(self, chunk: str) -> None:
        """Accumulate a token, flushing if the interval has elapsed."""
        self._text += chunk
        now = self._now()
        if now - self._last >= self._interval:
            self._emit(now)

    def finish(self) -> None:
        """Flush any text the interval held back."""
        self._emit(self._now())

    def _emit(self, now: float) -> None:
        # An unchanged body is not worth an API call, and on Slack an edit that
        # changes nothing still counts against the channel's rate limit.
        if self._text == self._flushed:
            self._last = now
            return
        self._flush(self._text)
        self._flushed = self._text
        self._last = now


class AgentChannel:
    """One long-lived ``gaia-agent`` child, driven one turn at a time.

    The child is a single ``GaiaAgent`` that processes one query at a time, so
    turns are serialized here rather than raced: a message that arrives mid-turn
    is queued, and the adapter is told so it can say as much.

    Threads, and why there are three:

    * the **writer** pulls one turn off the queue, writes it, and waits for that
      turn's terminal event before pulling the next;
    * the **reader** owns stdout and dispatches every event;
    * the **caller's** thread posts decisions, which must land *while* a turn is
      in flight — that is the only moment an approval is worth anything.
    """

    def __init__(
        self,
        *,
        on_event: Callable[[Dict[str, Any], Turn], None],
        argv: Optional[Sequence[str]] = None,
        env: Optional[Dict[str, str]] = None,
        spawn: Optional[Callable[..., Any]] = None,
        on_busy: Optional[Callable[[Turn, int], None]] = None,
    ) -> None:
        self._argv = list(argv) if argv else [DEFAULT_AGENT_BINARY]
        self._env = env
        self._on_event = on_event
        self._on_busy = on_busy
        # Injectable so tests drive a fake child instead of spawning a real
        # agent, which costs ~42s to build before it answers anything.
        self._spawn = spawn or self._default_spawn
        self._proc: Any = None
        self._queue: "queue.Queue[Optional[Turn]]" = queue.Queue()
        self._active: Optional[_TurnState] = None
        self._active_lock = threading.RLock()
        self._turn_done = threading.Event()
        self._write_lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._writer: Optional[threading.Thread] = None
        self._closing = threading.Event()
        self._depth = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _default_spawn(self) -> Any:
        try:
            return subprocess.Popen(  # noqa: S603 - argv is ours, never user text
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._env,
                text=True,
                bufsize=1,
                encoding="utf-8",
            )
        except FileNotFoundError as e:
            raise AgentChannelError(
                f"The agent binary '{self._argv[0]}' is not on PATH, so no "
                f"message can be answered. Install it with "
                f"`gaia init --profile gaia`, or pass --agent-command with the "
                f"path to it. Docs: https://amd-gaia.ai/docs/guides/install"
            ) from e
        except OSError as e:
            raise AgentChannelError(
                f"Could not start the agent binary '{self._argv[0]}': {e}. "
                f"Check that it is executable, then retry."
            ) from e

    def start(self) -> None:
        """Spawn the child and begin serving turns."""
        if self._proc is not None:
            raise AgentChannelError(
                "This AgentChannel is already started. Build a second channel "
                "rather than starting one twice."
            )
        self._proc = self._spawn()
        self._reader = threading.Thread(
            target=self._read_loop, name="gaia-bridge-reader", daemon=True
        )
        self._writer = threading.Thread(
            target=self._write_loop, name="gaia-bridge-writer", daemon=True
        )
        self._reader.start()
        self._writer.start()
        log.info("Agent channel started: %s", " ".join(self._argv))

    def close(self) -> None:
        """Stop serving turns and terminate the child."""
        if self._closing.is_set():
            return
        self._closing.set()
        self._queue.put(None)
        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except (OSError, ValueError) as e:
                log.debug("Agent stdin already closed: %s", e)
            try:
                proc.terminate()
            except (OSError, AttributeError) as e:
                log.debug("Agent child already gone: %s", e)
        # Unblock a writer parked on a turn that will never terminate.
        self._turn_done.set()

    # ------------------------------------------------------------------
    # Submitting work
    # ------------------------------------------------------------------

    def submit(self, turn: Turn) -> int:
        """Queue a turn. Returns how many turns are ahead of it (0 = starting now).

        A non-zero return is the adapter's cue to tell the user their message is
        waiting rather than lost — silence while a previous turn runs reads as a
        broken bot.
        """
        if self._closing.is_set():
            raise AgentChannelError(
                "The agent channel is shutting down and cannot take new "
                "messages. Restart the adapter to serve again."
            )
        if self._proc is None:
            raise AgentChannelError(
                "The agent channel was never started — call start() before "
                "submitting a turn."
            )
        with self._active_lock:
            ahead = self._depth
            self._depth += 1
        self._queue.put(turn)
        if ahead and self._on_busy is not None:
            self._on_busy(turn, ahead)
        return ahead

    def decide(self, decision: str, confirm_id: Optional[str] = None) -> None:
        """Answer the confirmation currently pending.

        An unrecognized decision is sent as a deny rather than dropped: a
        decision that never arrives leaves the turn parked until it times out,
        which is a worse outcome than a clear refusal. The agent fails closed the
        same way on its side.
        """
        if decision not in VALID_DECISIONS:
            log.warning(
                "Unknown tool decision %r from the messaging surface — "
                "denying. Valid decisions: %s",
                decision,
                ", ".join(sorted(VALID_DECISIONS)),
            )
            decision = DECISION_DENY
        message: Dict[str, Any] = {
            CONTROL_KEY: CONTROL_TOOL_DECISION,
            "decision": decision,
        }
        if confirm_id:
            message["confirm_id"] = confirm_id
        self._write_line(json.dumps(message))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write_line(self, line: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AgentChannelError(
                "The agent child has no input stream — it was never started, "
                "or it has already exited. Restart the adapter."
            )
        with self._write_lock:
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise AgentChannelError(
                    f"Could not reach the agent child: {e}. It has most likely "
                    f"exited; restart the adapter and resend the message."
                ) from e

    def _write_loop(self) -> None:
        while not self._closing.is_set():
            turn = self._queue.get()
            if turn is None:
                return
            self._turn_done.clear()
            with self._active_lock:
                self._active = _TurnState(turn=turn)
            try:
                # Always wrapped: a bare line carrying a newline would be read as
                # several unrelated questions, and the agent would answer only
                # the first while insisting it had seen everything.
                self._write_line(json.dumps({QUERY_KEY: turn.text}))
            except AgentChannelError as e:
                self._dispatch_terminal_error(str(e))
                self._finish_turn()
                continue
            # The reader sets this on the turn's one terminal event.
            self._turn_done.wait()
            self._finish_turn()

    def _finish_turn(self) -> None:
        with self._active_lock:
            self._active = None
            if self._depth:
                self._depth -= 1

    def _read_loop(self) -> None:
        proc = self._proc
        stdout = getattr(proc, "stdout", None)
        if stdout is None:
            return
        try:
            # readline-until-empty rather than `for raw in stdout`: a pipe's
            # file iterator read-ahead can hold a line back until the buffer
            # fills, which on a streaming turn shows up as the reply arriving
            # all at once instead of token by token.
            for raw in iter(stdout.readline, ""):
                line = raw.strip()
                if not line:
                    continue
                self._handle_line(line)
        except (OSError, ValueError) as e:
            log.debug("Agent stdout closed: %s", e)
        # The stream ended. If a turn was still running, it will never get its
        # terminal event from the child, so synthesize one — a turn that just
        # goes quiet is the failure mode the daemon relay exists to prevent.
        if not self._closing.is_set():
            self._dispatch_terminal_error(
                "The agent stopped responding mid-answer and the reply is "
                "incomplete. It most likely crashed — check "
                "~/.gaia/logs, then resend the message."
            )
        self._turn_done.set()

    def _handle_line(self, line: str) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            # The agent writes JSON lines only; anything else is a stray print
            # from a dependency, and dropping it silently would hide a crash
            # banner. Log it and keep the stream in sync.
            log.warning("Ignored a non-JSON line from the agent: %.200s", line)
            return
        if not isinstance(event, dict):
            log.warning("Ignored a non-object event from the agent: %.200s", line)
            return

        with self._active_lock:
            state = self._active
        if state is None:
            # Events only flow between a query and its terminal event, so one
            # arriving outside a turn means the streams desynchronised.
            log.warning(
                "Dropped an agent event of type %r that arrived with no turn "
                "running",
                event.get("type"),
            )
            return

        etype = event.get("type")
        if etype == NEEDS_CONFIRMATION:
            confirm_id = event.get("confirm_id")
            if confirm_id:
                state.pending_confirm_ids.append(str(confirm_id))

        try:
            self._on_event(event, state.turn)
        except Exception:
            # A renderer that raises must not wedge the reader thread — the turn
            # would then never terminate and the channel would stop serving.
            log.exception("Messaging adapter raised while rendering an event")

        if etype in TERMINAL_EVENTS:
            state.saw_terminal = True
            self._turn_done.set()

    def _dispatch_terminal_error(self, detail: str) -> None:
        with self._active_lock:
            state = self._active
        if state is None or state.saw_terminal:
            return
        state.saw_terminal = True
        try:
            self._on_event({"type": "error", "detail": detail}, state.turn)
        except Exception:
            log.exception("Messaging adapter raised while rendering a crash")
        self._turn_done.set()
