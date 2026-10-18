# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-turn performance recording for agent turns — dev mode, opt-in.

The agent already computes everything needed to explain its own latency and
then throws it away: per-step backend stats land in an in-memory ``conversation``
list, and the only numbers that ever reach a user are ttft and tok/s. That is
not enough to answer the question this exists for — *where did the 34 seconds
go?* — because the dominant cost is prefill, and prefill is invisible.

Enable with ``GAIA_TURN_LOG=<path>``. One JSON object per line, append-only, so
a session is greppable and two builds are diffable. Off by default; when off,
:class:`TurnRecorder` is never constructed and the agent pays nothing.

What it records that nothing else does
--------------------------------------
* **prefill tokens** — the system prompt + tool schemas re-sent on every call
* **cached vs new input tokens** — from the backend itself where it reports
  them (Anthropic's prefix cache does), otherwise the common prefix with the
  *previous* call's rendered prompt. That estimate is exactly the comparison
  llama.cpp makes, so it measures what we offer the cache; the two sources are
  recorded separately and never summed.
* **wall time split** — model time vs tool time vs time blocked on a human
  approving a tool vs agent overhead
* **total turn time** — user submit to final answer, the number a user feels
* **absolute timestamps** on the turn and every call, so a log lines up against
  Lemonade's own log and against a screen recording

Timing/counting failures are logged and swallowed rather than propagated: this
is an opt-in diagnostic, and a metrics bug must not be able to fail a user's
turn. That is deliberate and narrow — it applies to *recording*, never to the
agent's own work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, List, Optional

from gaia.utils.perf_trace import (  # noqa: F401  (re-exported)
    activate,
    current,
    note_server_timings,
    note_wait,
    span,
)

logger = logging.getLogger(__name__)

#: Env var holding the destination path. Unset (or empty) disables recording.
TURN_LOG_ENV = "GAIA_TURN_LOG"

#: Chars-per-token fallback when ``tiktoken`` is unavailable. Measured against
#: the flagship's own composed prompt (28,957 chars / 6,757 cl100k tokens).
_CHARS_PER_TOKEN = 4.28

SCHEMA = "gaia.turn/1"
POST_SCHEMA = "gaia.turn_post/1"


def turn_log_path() -> Optional[Path]:
    """Destination for turn records, or ``None`` when recording is off."""
    raw = os.environ.get(TURN_LOG_ENV, "").strip()
    return Path(raw).expanduser() if raw else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: Last sealed records, newest last, for the sidecar's debug endpoint.
RECENT_TURNS: Deque[Dict[str, Any]] = deque(maxlen=50)


@contextmanager
def pre_turn_span(owner: Any, name: str) -> Iterator[None]:
    """Time work done before the turn's recorder exists (e.g. memory recall).

    The span is parked on *owner* as ``_pre_turn_spans`` and folded into the
    next record, which then starts its clock at the earliest of them.
    """
    if turn_log_path() is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        pending = getattr(owner, "_pre_turn_spans", None) or []
        pending.append((name, started, time.perf_counter()))
        owner._pre_turn_spans = pending


class _Tokenizer:
    """cl100k when available, a measured char ratio otherwise.

    Absolute counts differ from Gemma's own tokenizer either way — what this is
    used for is ratios and deltas (how much a prompt shrank, what fraction of a
    prompt was cache-eligible), and both estimators preserve those.
    """

    def __init__(self) -> None:
        self._enc = None
        self.name = f"chars/{_CHARS_PER_TOKEN}"
        try:
            import tiktoken

            self._enc = tiktoken.get_encoding("cl100k_base")
            self.name = "tiktoken:cl100k_base"
        except Exception as e:  # noqa: BLE001 - optional dep, name records which
            logger.debug("tiktoken unavailable, using char-ratio estimate: %s", e)

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            return len(self._enc.encode(text, disallowed_special=()))
        return round(len(text) / _CHARS_PER_TOKEN)


_TOKENIZER: Optional[_Tokenizer] = None


def _tokenizer() -> _Tokenizer:
    global _TOKENIZER  # noqa: PLW0603 - one shared encoder, built on first use
    if _TOKENIZER is None:
        _TOKENIZER = _Tokenizer()
    return _TOKENIZER


def count_tokens(text: str) -> int:
    """Estimated token count for *text*, via this module's shared estimator."""
    return _tokenizer().count(text)


def _common_prefix_len(a: str, b: str) -> int:
    """Length of the longest shared leading substring of *a* and *b*.

    Binary search over slice equality rather than a per-character walk: these
    prompts run to 100K+ characters and mostly match, which is the worst case
    for a Python loop and the best case for C-level comparison. Measured at
    5.6ms -> 0.09ms on a 99K shared prefix, and it lands in the very
    ``overhead_s`` this recorder exists to explain.
    """
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


class TurnRecorder:
    """Accumulates one turn's performance record. Construct per turn.

    The agent calls :meth:`start_llm_call` / :meth:`end_llm_call` around each
    backend request and :meth:`record_tool` around each tool execution, then
    :meth:`finish` once, which writes the record and returns it.
    """

    def __init__(
        self,
        *,
        query: str,
        agent_name: str,
        model_id: Optional[str],
        system_prompt: str,
        tool_schemas: Optional[List[Dict[str, Any]]],
        tool_names: Optional[List[str]] = None,
        skills_active: Optional[List[str]] = None,
        history_messages: int = 0,
        path: Optional[Path] = None,
        started: Optional[float] = None,
        prompt_fragments: Optional[Dict[str, int]] = None,
    ) -> None:
        tok = _tokenizer()
        self.path = path if path is not None else turn_log_path()
        self.turn_id = uuid.uuid4().hex[:12]
        self.started_at = _now()
        # *started* (a perf_counter value) backdates the turn to when a mixin
        # began working on it, so pre-turn recall counts in total_s.
        self._t0 = started if started is not None else time.perf_counter()
        # Held from construction rather than re-passed to finish(): all three
        # are fixed when the turn opens, and asking twice let the model id be
        # resolved a second time and disagree with what the turn actually ran.
        self.query = query
        self.agent_name = agent_name
        self.model_id = model_id

        schema_json = json.dumps(tool_schemas) if tool_schemas else ""
        system_tokens = tok.count(system_prompt)
        schema_tokens = tok.count(schema_json)

        self.prompt = {
            "system_chars": len(system_prompt),
            "system_tokens": system_tokens,
            "tool_schema_chars": len(schema_json),
            "tool_schema_tokens": schema_tokens,
            "fixed_prefill_tokens": system_tokens + schema_tokens,
            "tools_sent": len(tool_schemas) if tool_schemas else 0,
            "tool_names": list(tool_names or []),
            "skills_active": list(skills_active or []),
            "history_messages": history_messages,
            "token_estimator": tok.name,
            # Which system prompt went out, and how big each mixin's part of it
            # was: a changed hash between two turns of one chat is a full
            # re-read on a model whose template puts the tools after it.
            "system_sha": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[
                :12
            ],
            "fragments": dict(prompt_fragments or {}),
        }

        self.llm_calls: List[Dict[str, Any]] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self.spans: List[Dict[str, Any]] = []
        self._open_call: Optional[Dict[str, Any]] = None
        self._prev_rendered = ""
        # Spans recorded after finish() — post-answer work the user may still
        # wait on (memory writes, the next turn's lock) — go to a second line.
        self._sealed = False
        self._post_spans: List[Dict[str, Any]] = []

    # ── spans and sub-call timings ─────────────────────────────────────────

    def _rel(self, t: float) -> float:
        return round(t - self._t0, 4)

    def add_span(
        self, name: str, started: float, ended: float, attrs: Dict[str, Any]
    ) -> None:
        entry = {
            "name": name,
            "start_s": self._rel(started),
            "s": round(ended - started, 4),
        }
        if attrs:
            entry.update(attrs)
        (self._post_spans if self._sealed else self.spans).append(entry)

    def note_client(self, key: str, seconds: float) -> None:
        """Accumulate a client-side wait (lock, lease, probe) on the open call."""
        call = self._open_call
        if call is None:
            return
        client = call.setdefault("client", {})
        client[key] = round(client.get(key, 0.0) + seconds, 4)

    def note_server(self, timings: Dict[str, Any]) -> None:
        """Keep the backend's prefill/decode split and its cache reuse."""
        call = self._open_call
        if call is None:
            return
        server = {
            k: timings[k]
            for k in ("prompt_n", "prompt_ms", "cache_n", "predicted_n", "predicted_ms")
            if isinstance(timings.get(k), (int, float))
        }
        if "prompt_ms" in server or "predicted_ms" in server:
            server["compute_s"] = round(
                (server.get("prompt_ms", 0.0) + server.get("predicted_ms", 0.0)) / 1000,
                4,
            )
        call["server"] = server

    # ── LLM calls ──────────────────────────────────────────────────────────

    def start_llm_call(self, step: int, rendered_prompt: str) -> None:
        """Open a call record. *rendered_prompt* is the full text going out
        (system + history + current), used for the cache-prefix comparison."""
        tok = _tokenizer()
        shared_chars = _common_prefix_len(self._prev_rendered, rendered_prompt)
        total_tokens = tok.count(rendered_prompt)
        cached_tokens = tok.count(rendered_prompt[:shared_chars])
        now = time.perf_counter()
        self._open_call = {
            "step": step,
            "at": _now(),
            "start_s": self._rel(now),
            "_t": now,
            "input_tokens_local": total_tokens,
            "input_tokens_cached": cached_tokens,
            "input_tokens_new": max(0, total_tokens - cached_tokens),
            "cache_hit_ratio": (
                round(cached_tokens / total_tokens, 4) if total_tokens else 0.0
            ),
        }
        self._prev_rendered = rendered_prompt

    def mark_llm_call_end(self) -> None:
        """Stamp the open call's wall time now, before the caller fetches stats.

        Lemonade's ``/stats`` is a network round-trip; folded into ``wall_s`` it
        would be reported as model time on every call.
        """
        call = self._open_call
        if call is not None:
            call.setdefault("wall_s", round(time.perf_counter() - call["_t"], 4))

    def end_llm_call(self, stats: Optional[Dict[str, Any]] = None) -> None:
        """Close the open call record, folding in whatever the backend reported.

        ``stats`` is stored verbatim under ``stats_raw`` — every field the
        backend returns, not a curated subset, because the next question about
        latency is rarely the one the curation anticipated.
        """
        call = self._open_call
        if call is None:
            logger.debug("end_llm_call with no open call; ignoring")
            return
        call.setdefault("wall_s", round(time.perf_counter() - call["_t"], 4))
        call.pop("_t", None)

        stats = stats or {}
        call["stats_raw"] = stats
        ttft = _num(stats.get("time_to_first_token"))
        call["ttft_s"] = ttft
        call["tok_per_s"] = _num(stats.get("tokens_per_second"))
        call["input_tokens"] = _num(stats.get("input_tokens")) or _num(
            stats.get("prompt_tokens")
        )
        call["output_tokens"] = _num(stats.get("output_tokens")) or _num(
            stats.get("completion_tokens")
        )
        # What the backend itself says it reused, when it says anything. A
        # remote prefix cache (Anthropic) reports this directly; a local
        # llama.cpp KV cache does not, and leaves these absent rather than 0 —
        # absent means "unmeasured", 0 means "measured, and it missed".
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            if key in stats:
                call[key] = _num(stats.get(key))
        # The backend's own per-request timings beat any stats endpoint: they
        # belong to this request, not to whichever one the server served last.
        server = call.get("server") or {}
        if "prompt_n" in server and "cache_n" in server:
            call["input_tokens"] = server["prompt_n"] + server["cache_n"]
            call["cache_read_input_tokens"] = server["cache_n"]
        if "predicted_n" in server:
            call["output_tokens"] = server["predicted_n"]

        # Prefill rate over the tokens the server actually had to process.
        # Far above the cold-start rate means the KV prefix was reused; at or
        # near it means the cache missed and the whole prompt was re-read.
        new_tokens = call.get("input_tokens_new") or 0
        if ttft and ttft > 0 and new_tokens:
            call["prefill_tok_per_s"] = round(new_tokens / ttft, 1)

        self.llm_calls.append(call)
        self._open_call = None

    # ── tool calls ─────────────────────────────────────────────────────────

    def record_tool(
        self,
        step: int,
        name: str,
        wall_s: float,
        ok: bool = True,
        waited_s: float = 0.0,
    ) -> None:
        """Record one tool execution.

        *wall_s* is the tool's own cost. *waited_s* is time blocked on a human
        approving it, kept separate because it belongs to neither the tool nor
        the model — folding it into either misreports where the turn went.
        """
        entry = {
            "step": step,
            "name": name,
            "at": _now(),
            "wall_s": round(wall_s, 4),
            "ok": bool(ok),
        }
        if waited_s:
            entry["waited_s"] = round(waited_s, 4)
        self.tool_calls.append(entry)

    # ── completion ─────────────────────────────────────────────────────────

    def finish(
        self,
        *,
        answer: str,
        steps: int,
    ) -> Dict[str, Any]:
        """Seal the record, append it to the log, and return it."""
        total_s = time.perf_counter() - self._t0
        llm_s = sum(c.get("wall_s", 0.0) for c in self.llm_calls)
        tool_s = sum(c.get("wall_s", 0.0) for c in self.tool_calls)
        waited_s = sum(c.get("waited_s", 0.0) for c in self.tool_calls)
        server_s = [
            c["server"]["compute_s"]
            for c in self.llm_calls
            if "compute_s" in c.get("server", {})
        ]
        client_waits: Dict[str, float] = {}
        for c in self.llm_calls:
            for key, value in (c.get("client") or {}).items():
                client_waits[key] = round(client_waits.get(key, 0.0) + value, 4)
        server_totals: Dict[str, Any] = {}
        if server_s and len(server_s) == len(self.llm_calls):
            compute = sum(server_s)
            server_totals = {
                # Seconds the backend computed for this turn; the rest of the
                # wall time is what GAIA (or a tool, or the user) cost.
                "server_compute_s": round(compute, 3),
                "non_model_s": round(total_s - compute, 3),
            }

        record = {
            "schema": SCHEMA,
            "turn_id": self.turn_id,
            "agent": self.agent_name,
            "model": self.model_id,
            "started_at": self.started_at,
            "ended_at": _now(),
            # The number a user actually feels: submit -> answer on screen.
            "total_s": round(total_s, 3),
            "query": self.query,
            "answer_chars": len(answer or ""),
            "steps": steps,
            "prompt": self.prompt,
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            # Two token sources, never mixed: ``*_server`` is what the backend
            # reported, ``*_local`` is what this recorder counted. They use
            # different tokenizers, so adding one to the other is meaningless —
            # the cached/new split is only ever valid against the local total.
            "totals": {
                "llm_s": round(llm_s, 3),
                "tool_s": round(tool_s, 3),
                # Blocked on a human approving a tool. Its own line so it
                # inflates neither tool_s nor overhead_s.
                "waiting_on_user_s": round(waited_s, 3),
                "overhead_s": round(max(0.0, total_s - llm_s - tool_s - waited_s), 3),
                "input_tokens_server": sum(
                    c.get("input_tokens") or 0 for c in self.llm_calls
                ),
                "output_tokens_server": sum(
                    c.get("output_tokens") or 0 for c in self.llm_calls
                ),
                "input_tokens_local": sum(
                    c.get("input_tokens_local") or 0 for c in self.llm_calls
                ),
                "input_tokens_cached_local": sum(
                    c.get("input_tokens_cached") or 0 for c in self.llm_calls
                ),
                "input_tokens_new_local": sum(
                    c.get("input_tokens_new") or 0 for c in self.llm_calls
                ),
                "input_tokens_cached_server": sum(
                    c.get("cache_read_input_tokens") or 0 for c in self.llm_calls
                ),
                "cache_write_tokens_server": sum(
                    c.get("cache_creation_input_tokens") or 0 for c in self.llm_calls
                ),
                **server_totals,
                "client_waits_s": client_waits,
            },
            "spans": self.spans,
            "gaps": self._gaps(),
        }
        self._sealed = True
        self._write(record)
        RECENT_TURNS.append(record)
        return record

    def _gaps(self) -> List[Dict[str, Any]]:
        """Time between consecutive model calls, and which spans filled it.

        The backend sits idle in these windows (unless something else uses
        it), so they are where pipelining can win back time.
        """
        gaps = []
        calls = [c for c in self.llm_calls if "start_s" in c and "wall_s" in c]
        for prev, nxt in zip(calls, calls[1:]):
            g0 = prev["start_s"] + prev["wall_s"]
            g1 = nxt["start_s"]
            inside = [
                {"name": sp["name"], "s": sp["s"]}
                for sp in self.spans
                if sp["start_s"] < g1 and sp["start_s"] + sp["s"] > g0
            ]
            tools = [
                t["name"] for t in self.tool_calls if t.get("step") == prev.get("step")
            ]
            gaps.append(
                {
                    "after_step": prev.get("step"),
                    "s": round(g1 - g0, 4),
                    "tools": tools,
                    "spans": inside,
                }
            )
        return gaps

    def write_post(self) -> Optional[Dict[str, Any]]:
        """Write spans recorded after the answer, as a ``gaia.turn_post/1`` line.

        That work runs after ``finish()`` — memory writes, extraction queueing —
        and still holds the agent before the next turn can start.
        """
        if not self._post_spans:
            return None
        record = {
            "schema": POST_SCHEMA,
            "turn_id": self.turn_id,
            "post_s": round(sum(sp["s"] for sp in self._post_spans), 4),
            "spans": self._post_spans,
        }
        self._write(record)
        return record

    def _write(self, record: Dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError as e:
            # Diagnostic-only: a log we cannot write must not fail the turn.
            logger.warning("could not append turn record to %s: %s", self.path, e)


def _num(value: Any) -> Optional[float]:
    """Numeric coercion that treats bools and junk as absent, not as zero."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_summary(record: Dict[str, Any]) -> str:
    """One-line human summary — what the TUI shows in dev mode."""
    t = record.get("totals", {})
    p = record.get("prompt", {})
    # A backend that reports its own cache accounting is ground truth; the
    # local prefix estimate is the stand-in for backends that report nothing.
    # A cold turn that only *wrote* the cache still counts as measured, so
    # turn 1 and turn 2 are drawn from the same source and compare directly.
    total = t.get("input_tokens_server", 0)
    cached = t.get("input_tokens_cached_server", 0)
    if not (cached or t.get("cache_write_tokens_server")) or not total:
        total = t.get("input_tokens_local", 0)
        cached = t.get("input_tokens_cached_local", 0)
    hit = f"{100 * cached / total:.0f}%" if total else "n/a"
    parts = [
        f"{record.get('total_s', 0):.1f}s total",
        f"{record.get('steps', 0)} steps",
        f"{p.get('fixed_prefill_tokens', 0) / 1000:.1f}k prefill",
        f"{p.get('tools_sent', 0)} tools",
        f"in {total:,} ({hit} cached)",
        f"out {t.get('output_tokens_server', 0):,}",
        f"model {t.get('llm_s', 0):.1f}s",
        f"tools {t.get('tool_s', 0):.1f}s",
    ]
    # Present only when every call reported server timings — a partial sum
    # would understate compute and overstate GAIA's share.
    if "server_compute_s" in t:
        parts.append(f"server {t['server_compute_s']:.1f}s")
        parts.append(f"non-model {t['non_model_s']:.1f}s")
    # Only when someone was actually asked to approve something — on every
    # other turn the figure is zero and the word is just noise.
    waiting = t.get("waiting_on_user_s", 0) or 0
    if waiting > 0:
        parts.append(f"waiting on you {waiting:.1f}s")
    return " · ".join(parts)


__all__ = [
    "POST_SCHEMA",
    "RECENT_TURNS",
    "SCHEMA",
    "TURN_LOG_ENV",
    "TurnRecorder",
    "activate",
    "current",
    "format_summary",
    "note_server_timings",
    "note_wait",
    "pre_turn_span",
    "span",
    "turn_log_path",
]
