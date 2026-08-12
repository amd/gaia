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

The event vocabulary is the canonical one (``status`` / ``tool_call`` /
``tool_result`` / ``token`` / ``final`` / ``error``), identical to what the HTTP
surface emits, so the renderer does not care which transport it is reading.
Exactly one terminal event (``final`` or ``error``) ends every turn.
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


def _write(event: Dict[str, Any], out) -> None:
    """Emit one canonical event as a single JSON line, flushed immediately.

    Flushing per line is the whole contract: the reader is a line scanner on a
    pipe, so a buffered event is an event the user never sees until the turn
    ends — which is exactly the "frozen UI" this transport exists to avoid.
    """
    out.write(json.dumps(event, ensure_ascii=False) + "\n")
    out.flush()


def log_path() -> "Path":
    """Where the agent's log lands. One file, not a per-run directory."""
    from pathlib import Path

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
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
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
    if any(
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


def run_turn(agent: Any, query: str, out) -> None:
    """Run one query to completion, streaming canonical events to *out*.

    Guarantees exactly one terminal event, whatever the agent does — a turn that
    ends without one leaves the reader waiting forever on a pipe that will never
    produce another byte.
    """
    from gaia.ui.sse_handler import SSEOutputHandler

    handler = SSEOutputHandler()
    agent.console = handler
    translator = CanonicalTranslator(run_id=None, agent_id=AGENT_ID)
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

    terminated = False
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
            if canonical.get("type") in TERMINAL_TYPES:
                terminated = True

    for canonical in translator.flush():
        _write(canonical, out)
        if canonical.get("type") in TERMINAL_TYPES:
            terminated = True

    worker.join(timeout=5.0)

    if terminated:
        return
    if "error" in result:
        _write(_terminal_error(result["error"]), out)
        return
    # The loop can finish without emitting an answer (the base agent handles some
    # failures internally and returns a message instead of raising). Surfacing
    # that message beats inventing a generic error.
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
    _write({"type": "final", "answer": answer}, out)


def main(argv: Optional[list] = None) -> int:
    """Read queries from stdin forever; one line in, one turn's events out."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="gaia-agent-gaia-stdio",
        description="Run the GAIA flagship agent over stdin/stdout JSONL.",
    )
    parser.add_argument("--model", default=None, help="model id override")
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
    args = parser.parse_args(argv)

    out = sys.stdout
    _configure_logging(out, dev=args.dev)

    # Built ONCE, before the first query, and kept for the life of the process.
    # A failure here is fatal and must say so on the turn the user actually
    # sent, not vanish into a dead pipe.
    try:
        from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

        config_kwargs: Dict[str, Any] = {"silent_mode": True}
        if args.model:
            config_kwargs["model_id"] = args.model
        agent = GaiaAgent(config=GaiaAgentConfig(**config_kwargs))
    except Exception as exc:
        print(traceback.format_exc(), file=sys.stderr)
        _write(_terminal_error(exc), out)
        return 1

    for line in sys.stdin:
        query = line.strip()
        if not query:
            continue
        try:
            run_turn(agent, query, out)
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
