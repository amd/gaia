# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Thin-client driver: run an agent's ``/query`` loop through the daemon relay
and render the canonical SSE event stream to the console (V2-8, #2152).

This is the CLI's data plane. ``gaia email "<q>"`` (and, later, ``gaia api`` /
other front-doors) ensure the daemon + sidecar, then stream
``POST /v1/<agent>/query`` through the daemon relay (``ANY /v1/<agent>/*``,
#2150), presenting ONLY the daemon client token — the sidecar's port and bearer
never leave the daemon. The seven canonical event types (spec #2015/#2016) —

    status | token | tool_call | tool_result | needs_confirmation | final | error

— are parsed off the SSE wire and rendered here; the stream ends with exactly one
``final`` or ``error``.

No silent fallbacks (CLAUDE.md): a daemon that is unreachable, a relay that
refuses the query, or a stream that ends without a terminal event all surface as
a loud, actionable :class:`~gaia.daemon.errors.DaemonError` or a rendered
terminal ``error`` — never a quiet in-process fallback that masks a broken
daemon.
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from gaia.daemon import client, paths
from gaia.daemon.constants import AUTH_SCHEME
from gaia.daemon.errors import DaemonError
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Canonical event types that terminate a ``/query`` stream (spec §3 — mirrors
#: the relay's own ``TERMINAL_TYPES``).
TERMINAL_TYPES = frozenset({"final", "error"})

#: Connect fast (a dead daemon should fail quickly); read generously — a single
#: upstream chunk spans a whole agent-loop step (matches the relay's READ_TIMEOUT).
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 300.0
#: Cancel POST must never wait out the read timeout.
_CANCEL_TIMEOUT = 10.0


@dataclass
class QueryOutcome:
    """The result of one relayed ``/query`` run."""

    exit_code: int
    terminal_type: Optional[str]  # "final" | "error" | None (no terminal seen)
    final_answer: Optional[str] = None
    error_detail: Optional[str] = None


class ConsoleRenderer:
    """Render canonical SSE events to the console.

    The agent's answer (streamed ``token`` deltas and the terminal ``final``)
    goes to **stdout** so ``gaia email -q`` stays pipe-friendly — its stdout is
    the answer, matching the retired in-process path. Progress (``status`` /
    ``tool_call`` / ``tool_result`` / ``needs_confirmation``) and errors go to
    **stderr** so they never pollute a captured answer. ``--verbose`` widens the
    progress detail (tool arguments, result payloads).
    """

    def __init__(self, *, verbose: bool = False, out=None, err=None) -> None:
        self._verbose = verbose
        self._out = out if out is not None else sys.stdout
        self._err = err if err is not None else sys.stderr
        # Track whether any answer token has been streamed so the terminal
        # ``final`` does not reprint the full answer on top of the live tokens.
        self._answer_streamed = False
        self.final_answer: Optional[str] = None
        self.error_detail: Optional[str] = None

    # -- dispatch ----------------------------------------------------------

    def render(self, event: Dict[str, Any]) -> None:
        etype = event.get("type")
        handler = getattr(self, f"_on_{etype}", None) if etype else None
        if handler is None:
            self._on_unknown(event)
            return
        handler(event)

    # -- per-type renderers ------------------------------------------------

    def _on_status(self, event: Dict[str, Any]) -> None:
        message = str(event.get("message", "")).strip()
        if message:
            print(f"  … {message}", file=self._err, flush=True)

    def _on_token(self, event: Dict[str, Any]) -> None:
        delta = event.get("delta", "")
        if not delta:
            return
        self._answer_streamed = True
        print(delta, end="", file=self._out, flush=True)

    def _on_tool_call(self, event: Dict[str, Any]) -> None:
        tool = event.get("tool", "unknown")
        if self._verbose:
            args = event.get("args") or {}
            print(f"  🔧 {tool}({_compact(args)})", file=self._err, flush=True)
        else:
            print(f"  🔧 {tool}", file=self._err, flush=True)

    def _on_tool_result(self, event: Dict[str, Any]) -> None:
        tool = event.get("tool", "unknown")
        if self._verbose:
            data = event.get("data")
            print(f"  ✓ {tool} → {_compact(data)}", file=self._err, flush=True)
        else:
            print(f"  ✓ {tool}", file=self._err, flush=True)

    def _on_needs_confirmation(self, event: Dict[str, Any]) -> None:
        action = event.get("action", "action")
        summary = str(event.get("summary", "")).strip()
        line = f"  ⚠️  confirmation needed: {action}"
        if summary:
            line += f" — {summary}"
        print(line, file=self._err, flush=True)

    def _on_final(self, event: Dict[str, Any]) -> None:
        answer = str(event.get("answer", "") or "")
        self.final_answer = answer
        if self._answer_streamed:
            # Tokens already printed the answer live — just end the line cleanly.
            if answer:
                print("", file=self._out, flush=True)
        elif answer:
            print(answer, file=self._out, flush=True)
        if self._verbose:
            usage = event.get("usage")
            if usage:
                print(f"  ℹ️  {_compact(usage)}", file=self._err, flush=True)

    def _on_error(self, event: Dict[str, Any]) -> None:
        detail = str(event.get("detail", "") or "unknown agent error")
        self.error_detail = detail
        if self._answer_streamed:
            print("", file=self._out, flush=True)
        source = event.get("source")
        suffix = f" (source: {source})" if source else ""
        print(f"❌ {detail}{suffix}", file=self._err, flush=True)

    def _on_unknown(self, event: Dict[str, Any]) -> None:
        # An unknown canonical type must be visible, never silently dropped
        # (contract §7, receiving-end rule).
        print(
            f"  [unknown event: {_compact(event)}]",
            file=self._err,
            flush=True,
        )

    def on_interrupt(self) -> None:
        """Rendered when the user Ctrl-C's mid-run."""
        if self._answer_streamed:
            print("", file=self._out, flush=True)
        print("⏹  cancelled.", file=self._err, flush=True)


def _compact(value: Any, limit: int = 200) -> str:
    """Compact one-line repr of a value for verbose/progress lines."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _iter_sse_events(response):
    """Yield parsed JSON events from an SSE ``response`` (requests, stream=True).

    Minimal SSE framing: accumulate ``data:`` field lines until a blank line, then
    parse the joined payload as JSON. Comment lines (``:`` heartbeats) are
    ignored. A malformed ``data:`` payload is logged loudly but does not abort the
    stream — the terminal-event contract is enforced by the caller.
    """
    data_lines: List[str] = []

    def _flush():
        if not data_lines:
            return None
        payload = "\n".join(data_lines)
        data_lines.clear()
        try:
            return json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            logger.warning(
                "agent_query: unparseable SSE data payload (%.120r) — skipped",
                payload,
            )
            return None

    for raw in response.iter_lines(decode_unicode=True):
        # requests yields None for keep-alive newlines on some transports.
        if raw is None:
            continue
        if raw == "":
            event = _flush()
            if event is not None:
                yield event
            continue
        if raw.startswith(":"):  # SSE comment / heartbeat
            continue
        field, _, value = raw.partition(":")
        if field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    # Flush a trailing frame with no terminating blank line (EOF mid-frame).
    event = _flush()
    if event is not None:
        yield event


def _best_effort_cancel(inst, agent_id: str, run_id: str) -> None:
    """Ask the relay to cancel the run (best-effort — the sidecar may be dead)."""
    import requests

    url = f"{inst.base_url}/v1/{agent_id}/query/{run_id}/cancel"
    try:
        requests.post(
            url,
            headers={"Authorization": f"{AUTH_SCHEME} {inst.token}"},
            timeout=_CANCEL_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        logger.warning(
            "agent_query: best-effort cancel for '%s' run_id=%s failed: %s",
            agent_id,
            run_id,
            e,
        )


def _consume(response, renderer: ConsoleRenderer) -> Optional[str]:
    """Render each event; return the terminal type seen (``final``/``error``) or
    ``None`` if the stream ended without one."""
    terminal_type: Optional[str] = None
    for event in _iter_sse_events(response):
        renderer.render(event)
        if event.get("type") in TERMINAL_TYPES:
            terminal_type = event.get("type")
            break
    return terminal_type


def run_query(
    agent_id: str,
    query: str,
    *,
    context: Optional[List[Dict[str, str]]] = None,
    model: Optional[str] = None,
    max_steps: Optional[int] = None,
    renderer: Optional[ConsoleRenderer] = None,
    verbose: bool = False,
) -> QueryOutcome:
    """Ensure the daemon + *agent_id* sidecar, stream ``POST /v1/<agent>/query``
    through the relay, and render the canonical SSE events.

    The CLI presents ONLY the daemon client token; it never learns the sidecar's
    port or bearer. Returns a :class:`QueryOutcome` whose ``exit_code`` is 0 on a
    terminal ``final``, 1 on a terminal ``error`` (or a stream that ends without
    one), and 130 on Ctrl-C. Raises :class:`DaemonError` if the daemon/relay
    cannot be reached at all — a broken daemon is loud, not masked.
    """
    import requests

    renderer = renderer or ConsoleRenderer(verbose=verbose)

    # Ensure the daemon is up and the sidecar is running (loud on failure).
    inst = client.ensure_agent(agent_id)

    run_id = str(uuid.uuid4())
    payload: Dict[str, Any] = {
        "query": query,
        "run_id": run_id,
        "context": context or [],
    }
    if model:
        payload["model"] = model
    if max_steps is not None:
        payload["max_steps"] = max_steps

    url = f"{inst.base_url}/v1/{agent_id}/query"
    headers = {
        "Authorization": f"{AUTH_SCHEME} {inst.token}",
        "Accept": "text/event-stream",
    }

    try:
        with requests.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
        ) as response:
            if response.status_code != 200:
                raise DaemonError(
                    f"the daemon relay refused the '{agent_id}' query "
                    f"(HTTP {response.status_code}): {client._error_detail(response)} "
                    f"Check `gaia daemon status` and the daemon log at "
                    f"{paths.log_path()}."
                )
            terminal_type = _consume(response, renderer)
    except KeyboardInterrupt:
        # Closing the stream already triggers the relay's cancel propagation;
        # send an explicit cancel too so the sidecar stops between tool steps.
        _best_effort_cancel(inst, agent_id, run_id)
        renderer.on_interrupt()
        return QueryOutcome(exit_code=130, terminal_type=None)
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not stream the '{agent_id}' query from the daemon at "
            f"{inst.base_url}: {e}. Check `gaia daemon status` and the daemon "
            f"log at {paths.log_path()}."
        ) from e

    if terminal_type is None:
        # The contract mandates exactly one terminal event — a stream that ends
        # without one is a failure, surfaced loudly rather than as a clean exit.
        detail = (
            f"the '{agent_id}' query stream ended without a terminal "
            "final/error event — the sidecar may have crashed mid-run. "
            f"Check `gaia daemon status` and the daemon log at {paths.log_path()}."
        )
        renderer.render({"type": "error", "detail": detail, "source": "cli"})
        return QueryOutcome(exit_code=1, terminal_type=None, error_detail=detail)

    return QueryOutcome(
        exit_code=0 if terminal_type == "final" else 1,
        terminal_type=terminal_type,
        final_answer=renderer.final_answer,
        error_detail=renderer.error_detail,
    )


__all__ = ["run_query", "QueryOutcome", "ConsoleRenderer", "TERMINAL_TYPES"]
