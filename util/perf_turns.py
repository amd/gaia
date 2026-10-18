# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Measure how busy GAIA keeps the LLM backend during real agent turns.

Drives the flagship agent's ``POST /v1/gaia/query`` route (what the Agent UI
and the TUI both relay to) and watches the llama.cpp server behind Lemonade:

* ``/slots`` sampled every ``--sample-ms`` gives per-slot busy intervals, so a
  turn's wall time splits into "the server was computing" and "it sat idle",
  and every idle gap is listed with where it fell in the turn.
* ``/metrics`` read before and after each turn gives the server's own
  prefill/decode seconds and token counts, which do not depend on sampling.

The headline number is **non-model time**: turn wall time minus the seconds
the backend spent computing for it.

Lemonade is often shared with other sessions. A turn whose ``/metrics``
request count moved by more than the turn's own calls is flagged ``foreign``
so its numbers are not mistaken for this agent's.

Usage::

    python util/perf_turns.py --sidecar http://127.0.0.1:4311 \\
        --token TOKEN --query "hello" --runs 3 --out runs.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

_METRIC_RE = re.compile(r"^llamacpp:(\w+)\s+([0-9.eE+-]+)$")


def resolve_backend(lemonade: str, model: Optional[str]) -> Tuple[str, str, int]:
    """The llama.cpp ``backend_url``, model name and pid Lemonade runs *model* on."""
    health = requests.get(f"{lemonade}/api/v1/health", timeout=5).json()
    for entry in health.get("all_models_loaded", []):
        if entry.get("type") != "llm":
            continue
        if model and entry.get("model_name") != model:
            continue
        url = entry["backend_url"].rstrip("/")
        return (
            (url[:-3] if url.endswith("/v1") else url),
            entry["model_name"],
            int(entry.get("pid") or 0),
        )
    raise SystemExit(
        f"No loaded LLM{' named ' + model if model else ''} on {lemonade}. "
        "Load the model in Lemonade first; this tool never loads or evicts one."
    )


def read_metrics(backend: str) -> Dict[str, float]:
    text = requests.get(f"{backend}/metrics", timeout=5).text
    out: Dict[str, float] = {}
    for line in text.splitlines():
        m = _METRIC_RE.match(line.strip())
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def lemonade_requests(lemonade: str) -> int:
    """Requests Lemonade has served in total, across every client."""
    return int(
        requests.get(f"{lemonade}/api/v1/stats", timeout=5)
        .json()
        .get("request_count_total", 0)
    )


def metrics_delta(
    before: Dict[str, float], after: Dict[str, float]
) -> Dict[str, float]:
    keys = (
        "prompt_tokens_total",
        "prompt_tokens_cached_total",
        "prompt_seconds_total",
        "tokens_predicted_total",
        "tokens_predicted_seconds_total",
        "n_decode_total",
    )
    return {k: round(after.get(k, 0.0) - before.get(k, 0.0), 4) for k in keys}


@dataclass
class SlotSampler:
    """Polls ``/slots`` and turns the samples into busy intervals per slot."""

    backend: str
    period_s: float
    samples: List[Tuple[float, Dict[int, bool]]] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None
    errors: int = 0

    def start(self) -> None:
        self._session = requests.Session()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            t = time.perf_counter()
            try:
                slots = self._session.get(f"{self.backend}/slots", timeout=2).json()
                self.samples.append(
                    (t, {s["id"]: bool(s.get("is_processing")) for s in slots})
                )
            except (requests.RequestException, ValueError, KeyError):
                self.errors += 1
            spent = time.perf_counter() - t
            self._stop.wait(max(0.0, self.period_s - spent))

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def busy_between(self, t0: float, t1: float) -> Dict[str, Any]:
        """Busy seconds (any slot / per slot) and idle gaps inside [t0, t1]."""
        window = [(t, s) for t, s in self.samples if t0 <= t <= t1]
        if len(window) < 2:
            return {"samples": len(window)}
        any_busy = 0.0
        per_slot: Dict[int, float] = {}
        both = 0.0
        gaps: List[Dict[str, float]] = []
        gap_start: Optional[float] = None
        for (ta, sa), (tb, _) in zip(window, window[1:]):
            dt = tb - ta
            busy = [sid for sid, on in sa.items() if on]
            for sid in busy:
                per_slot[sid] = per_slot.get(sid, 0.0) + dt
            if busy:
                any_busy += dt
                if len(busy) > 1:
                    both += dt
                if gap_start is not None:
                    gaps.append(
                        {
                            "at_s": round(gap_start - t0, 3),
                            "s": round(ta - gap_start, 3),
                        }
                    )
                    gap_start = None
            elif gap_start is None:
                gap_start = ta
        if gap_start is not None:
            gaps.append(
                {
                    "at_s": round(gap_start - t0, 3),
                    "s": round(window[-1][0] - gap_start, 3),
                }
            )
        span = window[-1][0] - window[0][0]
        return {
            "samples": len(window),
            "busy_s": round(any_busy, 3),
            "busy_pct": round(100 * any_busy / span, 1) if span else 0.0,
            "both_slots_s": round(both, 3),
            "per_slot_s": {str(k): round(v, 3) for k, v in sorted(per_slot.items())},
            "idle_gaps": [g for g in gaps if g["s"] >= 0.05],
        }


def fetch_record(sidecar: str, token: str) -> Optional[Dict[str, Any]]:
    """The agent's own record of its last turn (needs GAIA_TURN_LOG set)."""
    resp = requests.get(
        f"{sidecar}/v1/gaia/debug/turns",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    turns = resp.json().get("turns") or []
    return turns[-1] if turns else None


def summarize_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    t = rec.get("totals", {})
    spans: Dict[str, float] = {}
    for sp in rec.get("spans", []):
        spans[sp["name"]] = round(spans.get(sp["name"], 0.0) + sp["s"], 4)
    return {
        "turn_id": rec.get("turn_id"),
        "agent_total_s": rec.get("total_s"),
        "steps": rec.get("steps"),
        "llm_calls": len(rec.get("llm_calls", [])),
        "llm_s": t.get("llm_s"),
        "tool_s": t.get("tool_s"),
        "overhead_s": t.get("overhead_s"),
        "agent_server_compute_s": t.get("server_compute_s"),
        "agent_non_model_s": t.get("non_model_s"),
        "client_waits_s": t.get("client_waits_s"),
        "span_s": spans,
        "gaps": rec.get("gaps", []),
        "calls": [
            {
                "step": c.get("step"),
                "wall_s": c.get("wall_s"),
                "server": c.get("server"),
                "client": c.get("client"),
            }
            for c in rec.get("llm_calls", [])
        ],
        "tools": [(x["name"], x["wall_s"]) for x in rec.get("tool_calls", [])],
    }


class GpuSampler:
    """Per-process GPU compute utilization, from the Windows GPU Engine counters.

    One PowerShell ``Get-Counter`` loop at 1 s, so a turn records how busy
    the GPU was for *our* llama-server versus everyone else's: on a shared
    machine another session's model can halve this one's speed.
    """

    _SCRIPT = (
        r"$ErrorActionPreference='SilentlyContinue';"
        r"while($true){$t=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();"
        r"(Get-Counter '\GPU Engine(*engtype_compute*)\Utilization Percentage')"
        r".CounterSamples|?{$_.CookedValue -gt 0.5}|%{"
        r"if($_.InstanceName -match 'pid_(\d+)_'){"
        r"[Console]::Out.WriteLine([string]$t+' '+$matches[1]+' '+[math]::Round($_.CookedValue,1))}};"
        r"[Console]::Out.WriteLine([string]$t+' tick 0');[Console]::Out.Flush()}"
    )

    def __init__(self) -> None:
        import subprocess

        self.samples: List[Tuple[float, Dict[int, float]]] = []
        self._proc = None
        if sys.platform != "win32":
            return
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", self._SCRIPT],
            stdout=subprocess.PIPE,
            text=True,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        pending: Dict[int, float] = {}
        for line in self._proc.stdout:
            parts = line.split()
            if len(parts) != 3:
                continue
            if parts[1] == "tick":
                # Wall-clock ms -> this process's perf_counter timeline.
                t = time.perf_counter() - (time.time() * 1000 - float(parts[0])) / 1000
                self.samples.append((t, pending))
                pending = {}
            else:
                pid = int(parts[1])
                pending[pid] = pending.get(pid, 0.0) + float(parts[2])

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()

    def between(self, t0: float, t1: float, ours: int) -> Dict[str, Any]:
        window = [s for t, s in self.samples if t0 <= t <= t1]
        if not window:
            return {"samples": 0}
        mine = [min(100.0, s.get(ours, 0.0)) for s in window]
        other = [min(100.0, sum(v for p, v in s.items() if p != ours)) for s in window]
        return {
            "samples": len(window),
            "ours_busy_pct": round(statistics.mean(mine), 1),
            "others_busy_pct": round(statistics.mean(other), 1),
        }


def wait_quiet(
    sampler: "SlotSampler", gpu: "GpuSampler", ours: int, need_s: float, max_s: float
) -> float:
    """Block until the backend and the GPU have been idle for *need_s*.

    Returns the seconds waited; gives up after *max_s* (the run is then
    recorded with whatever contention its ``gpu`` field shows).
    """
    started = time.perf_counter()
    while time.perf_counter() - started < max_s:
        now = time.perf_counter()
        slots = [s for t, s in sampler.samples if t >= now - need_s]
        gpus = [s for t, s in gpu.samples if t >= now - need_s]
        busy_slot = any(any(v.values()) for v in slots)
        busy_gpu = any(sum(s.values()) > 10.0 for s in gpus)
        if slots and (gpus or gpu._proc is None) and not busy_slot and not busy_gpu:
            return round(time.perf_counter() - started, 1)
        time.sleep(0.5)
    return round(time.perf_counter() - started, 1)


def machine_load() -> Dict[str, Any]:
    """CPU load and free memory, so an outlier run can be explained."""
    try:
        import psutil
    except ImportError:
        return {"psutil": "missing"}
    vm = psutil.virtual_memory()
    return {
        "cpu_pct": psutil.cpu_percent(interval=0.5),
        "mem_avail_gb": round(vm.available / 2**30, 1),
    }


def run_turn(
    sidecar: str,
    token: str,
    query: str,
    session_id: Optional[str],
    timeout_s: float,
    documents: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """POST one query and timestamp the stream's milestones.

    Every tool confirmation is approved at once, one call at a time
    ("allow"), so a turn measures the agent and the backend, never how fast
    someone clicks Allow — and runs stay comparable, since no session-wide
    grant carries over. Only drive queries whose tools you would approve.
    """
    run_id = uuid.uuid4().hex
    body: Dict[str, Any] = {"query": query, "run_id": run_id, "context": []}
    if session_id:
        body["session_id"] = session_id
    if documents:
        body["documents"] = documents
    headers = {"Accept": "text/event-stream", "Authorization": f"Bearer {token}"}
    t0 = time.perf_counter()
    marks: Dict[str, float] = {}
    counts: Dict[str, int] = {"malformed": 0}
    tools: List[str] = []
    answer = ""
    with requests.post(
        f"{sidecar}/v1/gaia/query",
        json=body,
        headers=headers,
        stream=True,
        timeout=(5, timeout_s),
    ) as resp:
        if resp.status_code != 200:
            raise SystemExit(f"/query returned {resp.status_code}: {resp.text[:400]}")
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            try:
                event = json.loads(raw[5:].strip())
            except ValueError:
                counts["malformed"] += 1
                continue
            etype = event.get("type", "?")
            now = time.perf_counter() - t0
            counts[etype] = counts.get(etype, 0) + 1
            marks.setdefault(f"first_{etype}", round(now, 3))
            if etype == "needs_confirmation":
                requests.post(
                    f"{sidecar}/v1/gaia/query/{run_id}/tool_decision",
                    json={
                        "decision": "allow",
                        **(
                            {"confirm_id": event["confirm_id"]}
                            if event.get("confirm_id")
                            else {}
                        ),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                ).raise_for_status()
            if etype == "tool_call":
                tools.append(event.get("name") or event.get("tool") or "?")
            if etype == "final":
                answer = event.get("answer") or ""
                marks["final"] = round(now, 3)
            if etype == "error":
                answer = f"ERROR: {event.get('message') or event}"
    t1 = time.perf_counter()
    return {
        "t0": t0,
        "t1": t1,
        "wall_s": round(t1 - t0, 3),
        "marks": marks,
        "event_counts": counts,
        "tools": tools,
        "answer_chars": len(answer),
        "answer_head": answer[:160],
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--sidecar", default="http://127.0.0.1:4311")
    p.add_argument("--token", default=os.environ.get("GAIA_GAIA_SIDECAR_TOKEN", ""))
    p.add_argument(
        "--lemonade",
        default=os.environ.get(
            "LEMONADE_BASE_URL", "http://localhost:13305"
        ).removesuffix("/api/v1"),
    )
    p.add_argument("--model", default=None)
    p.add_argument(
        "--query",
        action="append",
        required=True,
        help="Repeat for a multi-turn chat in one session.",
    )
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--label", default="")
    p.add_argument(
        "--one-shot",
        action="store_true",
        help="Send no session_id, so the sidecar builds an agent per "
        "request. By default each run is one session, as the "
        "Agent UI and TUI send.",
    )
    p.add_argument(
        "--document",
        action="append",
        default=None,
        help="Absolute path attached to every turn (the documents field).",
    )
    p.add_argument("--sample-ms", type=float, default=25.0)
    p.add_argument(
        "--settle-s",
        type=float,
        default=2.0,
        help="Keep sampling this long after the stream closes, to "
        "catch post-turn backend work (titles, extraction).",
    )
    p.add_argument(
        "--between-s",
        type=float,
        default=0.0,
        help="Extra idle seconds between turns of a session (on top "
        "of --settle-s), e.g. to land a turn mid memory extraction.",
    )
    p.add_argument(
        "--quiet-s",
        type=float,
        default=5.0,
        help="Start a session's first turn only after the backend and "
        "GPU have been idle this long (0 disables).",
    )
    p.add_argument("--quiet-max-s", type=float, default=600.0)
    p.add_argument("--timeout-s", type=float, default=600.0)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    backend, model, backend_pid = resolve_backend(args.lemonade, args.model)
    sampler = SlotSampler(backend, args.sample_ms / 1000.0)
    sampler.start()
    gpu = GpuSampler()
    results = []
    try:
        for run in range(args.runs):
            session_id = None if args.one_shot else uuid.uuid4().hex
            for i, query in enumerate(args.query):
                if i and args.between_s:
                    time.sleep(args.between_s)
                waited = (
                    wait_quiet(
                        sampler, gpu, backend_pid, args.quiet_s, args.quiet_max_s
                    )
                    # Only before a session's first turn: later turns keep the
                    # real pacing, including landing on background work.
                    if args.quiet_s and i == 0
                    else 0.0
                )
                load = machine_load()
                before = read_metrics(backend)
                req_before = lemonade_requests(args.lemonade)
                turn = run_turn(
                    args.sidecar,
                    args.token,
                    query,
                    session_id,
                    args.timeout_s,
                    args.document,
                )
                time.sleep(args.settle_s)
                after = read_metrics(backend)
                pid_now = resolve_backend(args.lemonade, model)[2]
                req_delta = lemonade_requests(args.lemonade) - req_before
                d = metrics_delta(before, after)
                record = fetch_record(args.sidecar, args.token)
                agent = summarize_record(record) if record else None
                busy = sampler.busy_between(turn["t0"], turn["t1"])
                after_turn = sampler.busy_between(
                    turn["t1"], turn["t1"] + args.settle_s
                )
                server_s = (
                    d["prompt_seconds_total"] + d["tokens_predicted_seconds_total"]
                )
                rec = {
                    "label": args.label,
                    "run": run,
                    "turn": i,
                    "query": query,
                    "model": model,
                    **{k: v for k, v in turn.items() if k not in ("t0", "t1")},
                    "server_compute_s": round(server_s, 3),
                    "non_model_s": round(turn["wall_s"] - server_s, 3),
                    "metrics_delta": d,
                    "lemonade_requests": req_delta,
                    # A new pid means the model was reloaded: the counters
                    # reset and the run is not comparable.
                    "backend_reloaded": pid_now != backend_pid,
                    "gpu": gpu.between(turn["t0"], turn["t1"], backend_pid),
                    "cache_hit_pct": round(
                        100
                        * d["prompt_tokens_cached_total"]
                        / max(
                            1.0,
                            d["prompt_tokens_cached_total"] + d["prompt_tokens_total"],
                        ),
                        1,
                    ),
                    "slots": busy,
                    "after_turn_slots": after_turn,
                    "load": load,
                    "waited_quiet_s": waited,
                    "agent": agent,
                    "outside_agent_s": (
                        round(turn["wall_s"] - agent["agent_total_s"], 3)
                        if agent
                        else None
                    ),
                }
                results.append(rec)
                print(
                    f"[{args.label} r{run} t{i}] wall {rec['wall_s']:.1f}s "
                    f"server {server_s:.1f}s non-model {rec['non_model_s']:.1f}s "
                    f"busy {busy.get('busy_pct', 0)}% gpu {rec['gpu']} cache {rec['cache_hit_pct']}% "
                    f"prefill {d['prompt_tokens_total']:.0f} tok, out "
                    f"{d['tokens_predicted_total']:.0f} tok, first token "
                    f"{turn['marks'].get('first_token', '-')}s, reqs {req_delta}, "
                    f"outside-agent {rec['outside_agent_s']}s, steps "
                    f"{agent and agent['steps']}, tools {agent and agent['tools']}",
                    flush=True,
                )
                if args.out:
                    with open(args.out, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(rec) + "\n")
    finally:
        sampler.stop()
        gpu.stop()

    if len(results) > 1:
        for key in ("wall_s", "server_compute_s", "non_model_s"):
            vals = [r[key] for r in results if r["turn"] == 0]
            print(f"median {key} (turn 0): {statistics.median(vals):.2f}")
    if sampler.errors:
        print(f"WARNING: {sampler.errors} /slots samples failed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
