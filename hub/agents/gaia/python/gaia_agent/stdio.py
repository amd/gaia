"""Run the flagship agent over stdin/stdout as newline-delimited JSON.

The collapsed transport: the TUI spawns this process once and keeps it, writing
one query per line to stdin and reading canonical events back as JSON lines. No
daemon, no HTTP port, no bearer token, no discovery file, no contract
negotiation, no model-slot lease — the failure modes those layers introduce
cannot occur because the layers are not there.

Two properties matter and both come from the process being long-lived:

* **The agent is built once.** Construction costs ~42s (embedding validation,
  two FAISS index rebuilds, scratchpad DB, filesystem index, web client); the
  turn itself costs ~2.5s. Building per request made every turn pay the 42s.
* **Anything the agent learns persists.** ``Agent.loaded_skills`` in particular:
  a skill activated in one turn is still active in the next, because it is the
  same object.

Subsystems are still constructed eagerly by ``GaiaAgent.__init__`` — making them
lazy is the remaining win, and it is what would let this process start instantly
and survive Lemonade not being up yet.

The chat model is Lemonade by default; ``--use-claude`` swaps it for the
Anthropic API (``--claude-model`` picks the model). Embeddings (RAG, memory,
code index) stay on Lemonade either way — Anthropic has no embeddings API.

The event vocabulary is the canonical one (``status`` / ``tool_call`` /
``tool_result`` / ``token`` / ``final`` / ``error``), identical to what the HTTP
surface emits, so the renderer does not care which transport it is reading.
Exactly one terminal event (``final`` or ``error``) ends every turn.

stdin carries two kinds of line. A plain line is a query. A JSON object with a
``gaia_control`` key is a **control message** — the back-channel a permission
prompt needs: without one the agent can ask "may I run this?" and the answer has
nowhere to travel, so every gated tool eventually auto-denies. Control messages
are read by a dedicated thread so they still land *while* a turn is in flight,
which is the only moment a confirmation decision is worth anything.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
from typing import Any, Dict, Optional

from gaia.logger import get_logger
from gaia.ui.sse_translation import TERMINAL_TYPES, CanonicalTranslator

logger = get_logger(__name__)

AGENT_ID = "gaia"

#: Key that marks a stdin line as a control message rather than a query.
#:
#: stdin carries free-text questions, so the discriminator has to be one a
#: question cannot accidentally be. A line only counts as control if it parses
#: as a JSON object AND carries this key — someone asking the agent to explain a
#: JSON snippet still gets an answer, not a silently swallowed line.
CONTROL_KEY = "gaia_control"

#: Control verbs. ``tool_decision`` answers the confirmation currently on
#: screen; ``bypass`` turns unattended approval on or off for the session.
CONTROL_TOOL_DECISION = "tool_decision"
CONTROL_BYPASS = "bypass"

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ALWAYS = "always"


class PermissionState:
    """Permission state that outlives any single turn.

    Two things have to survive a turn boundary, because a fresh
    ``SSEOutputHandler`` is built for each one: whether bypass is on, and which
    calls the user has granted "always". Losing either would re-prompt for a
    call the user already approved, which is the same defect as never having
    offered "always" at all.

    The lock matters: the stdin pump answers confirmations from its own thread
    while the turn thread is swapping ``handler`` around it.
    """

    def __init__(self, bypass: bool = False) -> None:
        self._lock = threading.Lock()
        self._bypass = bypass
        self._grants: set = set()
        self._handler: Any = None

    @property
    def bypass(self) -> bool:
        with self._lock:
            return self._bypass

    def set_bypass(self, enabled: bool) -> None:
        """Turn bypass on or off, taking effect on the very next gated tool.

        Applied to the live handler too, so a toggle mid-turn is not queued
        behind the turn it was meant to change.
        """
        with self._lock:
            self._bypass = enabled
            if self._handler is not None:
                self._handler.auto_approve_gated_tools = enabled
        logger.warning("Bypass permissions %s", "ENABLED" if enabled else "disabled")

    def attach(self, handler: Any) -> None:
        """Hand a turn's handler the session's accumulated permission state."""
        with self._lock:
            handler.auto_approve_gated_tools = self._bypass
            handler.session_grants().update(self._grants)
            # A human is on the other end of this pipe with a modal on screen,
            # so the wait is theirs to end — see confirm_tool_execution.
            handler.confirm_timeout_seconds = None
            self._handler = handler

    def detach(self, handler: Any) -> None:
        """Take the turn's grants back into the session and drop the handler."""
        with self._lock:
            self._grants.update(handler.session_grants())
            if self._handler is handler:
                self._handler = None

    def resolve(self, decision: str, confirm_id: Optional[str]) -> None:
        """Answer the confirmation the agent thread is parked on."""
        with self._lock:
            handler = self._handler
        if handler is None:
            logger.warning("Dropped a '%s' tool decision: no turn is running", decision)
            return
        handler.resolve_tool_confirmation(
            approved=decision in (DECISION_ALLOW, DECISION_ALWAYS),
            always=decision == DECISION_ALWAYS,
            confirm_id=confirm_id,
        )


def parse_control(line: str) -> Optional[Dict[str, Any]]:
    """Return the control message on this stdin line, or None if it is a query."""
    if not line.startswith("{"):
        return None
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or CONTROL_KEY not in parsed:
        return None
    return parsed


def apply_control(message: Dict[str, Any], state: PermissionState) -> None:
    """Act on one control message.

    Nothing is written to stdout from here. The wire is turn-scoped — the reader
    only listens between a query and its terminal event — so an acknowledgement
    emitted outside a turn would be read as the first event of the NEXT turn and
    desynchronise the stream. The sender already knows what it sent.
    """
    verb = message.get(CONTROL_KEY)
    if verb == CONTROL_BYPASS:
        state.set_bypass(bool(message.get("enabled")))
    elif verb == CONTROL_TOOL_DECISION:
        decision = str(message.get("decision") or DECISION_DENY)
        if decision not in (DECISION_ALLOW, DECISION_DENY, DECISION_ALWAYS):
            # Fail closed: an unreadable decision is not consent.
            logger.warning("Unknown tool decision %r — denying", decision)
            decision = DECISION_DENY
        confirm_id = message.get("confirm_id")
        state.resolve(decision, str(confirm_id) if confirm_id else None)
    else:
        logger.warning("Ignored unknown control verb %r", verb)


def _pump_stdin(queries: "queue.Queue", state: PermissionState) -> None:
    """Read stdin forever, routing control lines away from the query queue.

    A dedicated thread is the whole point. The turn loop used to read stdin
    itself, so while a turn ran nothing was reading — which is exactly when a
    confirmation decision needs to arrive. Control messages are handled here,
    inline, while the agent thread is still parked on the prompt.
    """
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        control = parse_control(line)
        if control is None:
            queries.put(line)
            continue
        try:
            apply_control(control, state)
        except Exception:  # pylint: disable=broad-exception-caught
            # A malformed control line must never take the pump down: losing
            # this thread means every later confirmation hangs with nothing
            # able to answer it.
            logger.exception("control message failed: %s", line)
    queries.put(None)  # stdin closed


def _write(event: Dict[str, Any], out) -> None:
    """Emit one canonical event as a single JSON line, flushed immediately.

    Flushing per line is the whole contract: the reader is a line scanner on a
    pipe, so a buffered event is an event the user never sees until the turn
    ends — which is exactly the "frozen UI" this transport exists to avoid.
    """
    out.write(json.dumps(event, ensure_ascii=False) + "\n")
    out.flush()


#: Env override for the agent log file. Set it to give one session a private
#: log; leave it unset for the shared default.
LOG_PATH_ENV = "GAIA_AGENT_LOG"


#: Turns (user+assistant pairs) carried into the next prompt. The base agent
#: already caps its own session history at 20 messages; 12 pairs stays under
#: that while covering far more back-reference than anyone types.
MAX_HISTORY_TURNS = 12


def _record_turn(agent: Any, query: str, answer: str) -> None:
    """Append this turn to the history the next prompt is built from.

    Without this the flagship is amnesiac over stdio. ``Agent`` composes each
    request as ``[system, *conversation_history, user]`` (see
    ``_build_messages``), and nothing in the base class ever appends to
    ``conversation_history`` — the HTTP surface fills it in per request
    (``gaia/ui/agent_loop.py``), and this transport did not. So every TUI turn
    reached the model as exactly two messages, system + the current question.

    The user-visible cost was not subtle: asked "print issue 2975" one turn
    after a triage of amd/gaia, the agent replied "I need to know which
    repository it belongs to". It was not being told.

    Only the question and the final answer are kept. Tool calls and their
    results belong to the turn that made them and the agent already threads
    those through its own loop; replaying them here would re-feed stale tool
    output into every later prompt.
    """
    if not query or not str(query).strip():
        return
    history = getattr(agent, "conversation_history", None)
    if history is None:
        logger.debug("[history] agent has no conversation_history attribute")
        return
    history.append({"role": "user", "content": str(query)})
    history.append({"role": "assistant", "content": str(answer or "")})
    # Trim in pairs so the window never opens on an assistant reply whose
    # question has been dropped — a dangling answer reads as the model
    # asserting something unprompted.
    excess = len(history) - MAX_HISTORY_TURNS * 2
    if excess > 0:
        del history[:excess]
    logger.debug("[history] recorded turn; %d message(s) carried", len(history))


def log_path() -> "Path":
    """Where the agent's log lands. One file, not a per-run directory.

    ``GAIA_AGENT_LOG`` overrides it. The shared default is right for a single
    agent, but several can run at once — a test harness driving one TUI while
    other agents run beside it, most obviously — and they all append to this one
    file. Interleaved records from two sessions are worse than no records: a
    failure from one agent reads as a failure of the one you are watching, which
    is how a timeout belonging to a neighbouring process becomes a bug report
    against yours. Every line also carries its pid (see ``_configure_logging``)
    so the shared default stays attributable when no override is set.
    """
    import os
    from pathlib import Path

    override = os.environ.get(LOG_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gaia" / "logs" / "gaia-agent.log"


def _configure_logging(real_stdout, *, dev: bool) -> "Path":
    """Send logs to a file and keep stdout carrying JSON events only.

    stdout is the wire: a single unstructured line desynchronises the reader's
    line scanner for the rest of the process's life, so this is a correctness
    requirement rather than tidiness. Handlers built at import time already hold
    the real stdout, so they are removed outright, and ``sys.stdout`` is rebound
    so a stray ``print`` in code we do not control cannot reach the wire either.

    User mode logs errors only — a healthy run should leave a boring file.
    ``--dev`` turns on DEBUG for the whole tree, because the questions a
    developer asks (which tool, how long, why that step) are answered by the
    records user mode drops.
    """
    import logging
    from pathlib import Path

    sys.stdout = sys.stderr

    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    loggers = [root] + [
        logging.getLogger(name) for name in list(logging.root.manager.loggerDict)
    ]
    for lg in loggers:
        for handler in list(getattr(lg, "handlers", []) or []):
            stream = getattr(handler, "stream", None)
            if isinstance(handler, logging.StreamHandler) and stream in (
                real_stdout,
                sys.stderr,
            ):
                lg.removeHandler(handler)
        lg.propagate = True

    level = logging.DEBUG if dev else logging.ERROR
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(level)
    # The pid is not decoration: agents share the default log file, and without
    # it two interleaved sessions are indistinguishable after the fact.
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | pid:%(process)d | %(levelname)s | %(name)s | %(message)s"
        )
    )
    root.handlers = [file_handler]
    root.setLevel(level)
    for lg in loggers:
        lg.setLevel(logging.NOTSET)
    return Path(path)


def _terminal_error(exc: BaseException) -> Dict[str, Any]:
    """Actionable copy for a run-killing exception.

    Lemonade being unreachable is the common failure and its raw urllib3 repr
    tells a user nothing, so it gets named copy with the fix. Anything else is
    surfaced verbatim — a generic 'something went wrong' would hide the one
    detail that makes a bug reportable.
    """
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    # An Anthropic outage also says "connection refused"/"max retries" — the
    # Lemonade remediation would point at the wrong backend, so skip it.
    is_anthropic = "anthropic" in lowered or type(exc).__module__.startswith(
        "anthropic"
    )
    if not is_anthropic and any(
        s in lowered
        for s in (
            "connection refused",
            "max retries",
            "failed to establish",
            "newconnectionerror",
        )
    ):
        return {
            "type": "error",
            "detail": (
                "Local Lemonade Server is not reachable. Start it, then retry — "
                f"run `lemonade-server serve`. (underlying error: {text})"
            ),
        }
    return {"type": "error", "detail": text}


def run_turn(
    agent: Any,
    query: str,
    out,
    dev: bool = False,
    state: Optional[PermissionState] = None,
) -> None:
    """Run one query to completion, streaming canonical events to *out*.

    Guarantees exactly one terminal event, whatever the agent does — a turn that
    ends without one leaves the reader waiting forever on a pipe that will never
    produce another byte.

    *dev* opens the translator's debug channel, which is what carries the
    harness-internal lines: the step counter and the model banner. Without it
    they are dropped before they reach the wire, so a front-end that asks for
    developer output gets an empty developer view.

    *state* carries bypass and "always allow" across turns, and is what the
    stdin pump answers confirmations through. Omitted, the turn gets a fresh
    permission slate and no way to answer — the safe default, not a convenient
    one: no grant is ever inherited by accident.
    """
    from gaia.ui.sse_handler import SSEOutputHandler

    handler = SSEOutputHandler()
    agent.console = handler
    if state is not None:
        state.attach(handler)
    translator = CanonicalTranslator(run_id=None, agent_id=AGENT_ID, debug=dev)
    result: Dict[str, Any] = {}

    def _run() -> None:
        try:
            result["value"] = agent.process_query(query)
        except Exception as exc:  # surfaced as the turn's terminal error
            logger.exception("stdio turn failed")
            result["error"] = exc
        finally:
            handler.signal_done()

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    try:
        terminated = False
        # The answer as it went out on the wire. Captured here because this is
        # the path a normal turn takes: the translator emits the terminal event
        # and the function returns below, never reaching the fallback that
        # builds an answer from the return value.
        streamed_answer: Optional[str] = None
        while True:
            try:
                event = handler.event_queue.get(timeout=0.05)
            except queue.Empty:
                if not worker.is_alive() and handler.event_queue.empty():
                    break
                continue
            if event is None:  # signal_done sentinel
                break
            for canonical in translator.translate(event):
                _write(canonical, out)
                if canonical.get("type") == "final":
                    streamed_answer = str(canonical.get("answer") or "")
                if canonical.get("type") in TERMINAL_TYPES:
                    terminated = True

        for canonical in translator.flush():
            _write(canonical, out)
            if canonical.get("type") == "final":
                streamed_answer = str(canonical.get("answer") or "")
            if canonical.get("type") in TERMINAL_TYPES:
                terminated = True

        worker.join(timeout=5.0)

        if terminated:
            # The normal exit. A turn that ended in an error event is not
            # recorded: replaying a failure as if it were an answer teaches the
            # model that the failure is what it said.
            if streamed_answer is not None:
                _record_turn(agent, query, streamed_answer)
            return
        if "error" in result:
            _write(_terminal_error(result["error"]), out)
            return
        # The loop can finish without emitting an answer (the base agent handles
        # some failures internally and returns a message instead of raising).
        # Surfacing that message beats inventing a generic error.
        value = result.get("value")
        answer = ""
        if isinstance(value, dict):
            for key in ("answer", "response", "result", "output"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    answer = candidate
                    break
        elif isinstance(value, str):
            answer = value
        _record_turn(agent, query, answer)
        _write({"type": "final", "answer": answer}, out)
    finally:
        # Every exit path, including the early returns above: leaving a dead
        # turn's handler attached would send the next decision to a thread that
        # is no longer listening, and the prompt after it would hang.
        if state is not None:
            state.detach(handler)


def build_parser() -> "argparse.ArgumentParser":
    """The stdio transport's argv contract.

    The TUI appends ``--use-claude`` / ``--claude-model`` as literal strings to
    the child argv, so these exact spellings are load-bearing.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="gaia-agent-gaia-stdio",
        description="Run the GAIA flagship agent over stdin/stdout JSONL.",
    )
    parser.add_argument("--model", default=None, help="model id override")
    parser.add_argument(
        "--use-claude",
        action="store_true",
        help="Chat via the Anthropic API instead of local Lemonade (needs "
        "ANTHROPIC_API_KEY; embeddings stay on Lemonade).",
    )
    parser.add_argument(
        "--claude-model",
        default=None,
        help="Claude model id when --use-claude is set (default: claude-sonnet-5).",
    )
    parser.add_argument(
        "--json-events",
        action="store_true",
        help="Accepted for symmetry with the other subprocess agents; this "
        "transport only ever speaks JSON lines, so it changes nothing.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Developer mode: DEBUG-level logging to the log file instead of "
        "errors only.",
    )
    parser.add_argument(
        "--bypass-permissions",
        action="store_true",
        help="Start with confirmation prompts OFF: every gated tool runs "
        "without asking. Off unless passed, and the host can toggle it at any "
        "time over the control channel.",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """Read queries from stdin forever; one line in, one turn's events out."""
    args = build_parser().parse_args(argv)

    out = sys.stdout
    _configure_logging(out, dev=args.dev)

    state = PermissionState(bypass=args.bypass_permissions)

    # Built ONCE, before the first query, and kept for the life of the process.
    # A failure here is fatal and must say so on the turn the user actually
    # sent, not vanish into a dead pipe.
    try:
        from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

        # streaming=True is what turns the answer into ``token`` events. Without
        # it the turn is silent for its whole length and the finished text lands
        # in one frame — the transport could always carry tokens, the agent just
        # never produced any.
        config_kwargs: Dict[str, Any] = {"silent_mode": True, "streaming": True}
        if args.model:
            config_kwargs["model_id"] = args.model
        if args.use_claude:
            config_kwargs["use_claude"] = True
            if args.claude_model:
                config_kwargs["claude_model"] = args.claude_model
        agent = GaiaAgent(config=GaiaAgentConfig(**config_kwargs))
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        _write(_terminal_error(exc), out)
        return 1

    # stdin is read by its own thread so it keeps being read DURING a turn —
    # which is the only time a confirmation decision can arrive. The turn loop
    # takes queries off the queue the pump fills.
    queries: "queue.Queue" = queue.Queue()
    threading.Thread(
        target=_pump_stdin, args=(queries, state), daemon=True, name="stdin-pump"
    ).start()

    while True:
        query = queries.get()
        if query is None:  # stdin closed
            break
        try:
            run_turn(agent, query, out, dev=args.dev, state=state)
        except Exception as exc:  # never let one bad turn kill the process
            logger.exception("stdio turn crashed outside the run loop")
            _write(_terminal_error(exc), out)

    # stdin closed: the parent is done with us. The agent leaves non-daemon
    # threads behind (memory extraction, the filesystem watcher), so a plain
    # return would hang the interpreter at shutdown waiting on them — a one-shot
    # `run --query` sat for 400s until its caller killed it. Nothing here owns
    # unflushed state: events are flushed per line and the DBs commit per write.
    out.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
