# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Measure dynamic tool loading on a real model, on against off, on this machine.

Runs the flagship through a short session from a cold prompt cache, once with
``GAIA_DYNAMIC_TOOLS`` on and once off, then compares the two. Both legs run on
the same box in the same minute, so the gates are ratios and need no committed
seconds that go stale when the hardware changes.

Needs a running Lemonade with the model available, and the GPU to itself: run it
when nothing else is using the model.

Usage::

    python util/measure_first_turn.py --model Gemma-4-E4B-it-GGUF --out results.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

#: (name, prompt, text the answer must contain). One session, in order.
TASKS = [
    ("hello", "hi, what can you do? one sentence.", None),
    ("math", "What is 17 times 23?", "391"),
    ("csv", "Which region had the highest revenue in sales.csv?", "South"),
    ("edit", "In notes.txt change Tuesday to Wednesday.", None),
    ("followup", "Thanks. Which file did you change earlier?", "notes"),
]

#: The first turn must read at most this share of what the full list reads.
MAX_FIRST_TURN_READ_RATIO = 0.65
#: Extra model calls allowed across the session: one run's normal wobble.
MAX_EXTRA_STEPS = 2
#: Tokens re-read beyond new content, as a share of the prompt, that still
#: counts as a cache hit. A broken cache re-reads nearly all of it.
MAX_FOLLOWUP_REREAD_RATIO = 0.5
#: One leg's six turns, model load included; a hung server fails the run.
LEG_TIMEOUT_SECONDS = 1800


def _evict_prompt_cache(model: str) -> None:
    """Replace the slot's cached prompt, so the next prefix is read from cold."""
    from gaia.llm.lemonade_client import (
        CONVERSATION_SLOT,
        LemonadeClient,
        lemonade_auth_headers,
    )

    client = LemonadeClient(model=model)
    client._ensure_model_loaded(model)  # pylint: disable=protected-access
    body = {
        "model": model,
        "messages": [{"role": "user", "content": f"evict {time.time()} " * 40}],
        "max_tokens": 1,
        "cache_prompt": True,
        "id_slot": CONVERSATION_SLOT,
    }
    request = urllib.request.Request(
        f"{client.base_url}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **lemonade_auth_headers(client.api_key),
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        response.read()


def _run_session(model: str, out: Path) -> None:
    """One leg, in its own process: run :data:`TASKS` and write what happened."""
    from gaia_agent import stdio
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    from gaia.agents.base.history import SessionHistory

    log = Path(os.environ["GAIA_TURN_LOG"])
    work = log.parent / "work"
    work.mkdir()
    (work / "sales.csv").write_text(
        "region,revenue\nNorth,1200\nSouth,3400\nEast,2100\nWest,900\n",
        encoding="utf-8",
    )
    (work / "notes.txt").write_text(
        "Meeting moved to Tuesday.\nBring the budget.\n", encoding="utf-8"
    )
    os.chdir(work)

    agent = GaiaAgent(
        config=GaiaAgentConfig(
            silent_mode=True,
            streaming=True,
            model_id=model,
            allowed_paths=[str(work)],
        )
    )
    agent.conversation_history = SessionHistory(str(log.parent / "history.sqlite3"))
    _evict_prompt_cache(model)

    answers: List[Dict[str, Any]] = []
    for name, prompt, expected in TASKS:
        agent.conversation_history.prepare(agent, prompt)
        started = time.perf_counter()
        result = agent.process_query(prompt)
        answer = result.get("result") or ""
        stdio._record_turn(agent, prompt, answer, result)
        answers.append(
            {
                "task": name,
                "wall_s": round(time.perf_counter() - started, 1),
                "ok": expected is None or expected.lower() in answer.lower(),
            }
        )
    agent.close()

    records = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    turns = [r for r in records if r.get("schema") == "gaia.turn/1"]
    for entry, turn in zip(answers, turns):
        servers = [c.get("server") or {} for c in turn.get("llm_calls", [])]
        entry["steps"] = len(servers)
        entry["reads"] = [s.get("prompt_n", 0) for s in servers]
        entry["cached"] = [s.get("cache_n", 0) for s in servers]
    out.write_text(json.dumps(answers, indent=1), encoding="utf-8")


def _lemonade_env() -> Dict[str, str]:
    """The server this process uses, pinned so a leg under an empty home finds it.

    GAIA's own server is found through its home's ``state.json``, and the
    credentials file marks its URL as one to re-resolve from there. A leg's
    empty home has no state file, so the leg would fall back to the default
    port. Hand it the resolved URL and key as a plain choice instead.
    """
    from gaia.llm.lemonade_client import (
        LemonadeClient,
        LemonadeClientError,
        resolve_lemonade_api_key,
        resolve_lemonade_base_url,
    )

    base_url = resolve_lemonade_base_url()
    api_key = resolve_lemonade_api_key(base_url=base_url)
    try:
        LemonadeClient(base_url=base_url, api_key=api_key).health_check()
    except LemonadeClientError as e:
        raise SystemExit(
            f"Lemonade at {base_url} is not answering, so there is nothing to "
            "measure. Start it first (`gaia lemonade embedded start`, then load "
            "its env file), or set LEMONADE_BASE_URL to a running server. "
            f"Detail: {e}"
        ) from e
    env = {"LEMONADE_BASE_URL": base_url}
    if api_key:
        env["LEMONADE_API_KEY"] = api_key
    return env


def _run_leg(
    model: str, dynamic: bool, lemonade: Dict[str, str]
) -> List[Dict[str, Any]]:
    """Run one leg in a clean process with an empty GAIA home."""
    home = Path(tempfile.mkdtemp(prefix="gaia-first-turn-"))
    out = home / "result.json"
    inherited = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GAIA_LEMONADE_EMBEDDED", "LEMONADE_API_KEY")
    }
    env = dict(
        inherited,
        **lemonade,
        GAIA_HOME=str(home / "home"),
        GAIA_DAEMON_HOME=str(home / "daemon"),
        GAIA_PROJECT_MAP_AUTO_INDEX="0",
        GAIA_AUTO_APPROVE_TOOLS="1",
        GAIA_DYNAMIC_TOOLS="1" if dynamic else "0",
        GAIA_TURN_LOG=str(home / "turns.jsonl"),
        PYTHONUTF8="1",
    )
    try:
        subprocess.run(
            [sys.executable, __file__, "--leg", str(out), "--model", model],
            env=env,
            check=True,
            timeout=LEG_TIMEOUT_SECONDS,
        )
        return json.loads(out.read_text("utf-8"))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def judge(on: List[Dict[str, Any]], off: List[Dict[str, Any]]) -> List[str]:
    """Return what is wrong with the *on* leg against the *off* leg, if anything."""
    for leg, turns in (("on", on), ("off", off)):
        if not all(sum(turn["reads"]) for turn in turns):
            return [
                f"the {leg} leg has a turn with no server timings, so nothing here "
                "was measured. Check that the backend reports prompt_n and cache_n."
            ]
    problems = []
    first_on, first_off = on[0]["reads"][0], off[0]["reads"][0]
    if first_on > first_off * MAX_FIRST_TURN_READ_RATIO:
        problems.append(
            f"first turn read {first_on} tokens against {first_off} with the full "
            f"list; the limit is {MAX_FIRST_TURN_READ_RATIO:.0%}. Something has "
            "grown the fixed prefix."
        )
    extra = sum(t["steps"] for t in on) - sum(t["steps"] for t in off)
    if extra > MAX_EXTRA_STEPS:
        problems.append(
            f"the session took {extra} more model calls than with the full list "
            f"(limit {MAX_EXTRA_STEPS}): the short list is costing the model turns."
        )
    for leg_turn in on:
        if not leg_turn["ok"]:
            problems.append(f"task {leg_turn['task']} gave the wrong answer.")
    for turn in on[1:]:
        for read, cached in zip(turn["reads"], turn["cached"]):
            total = read + cached
            if total and read > total * MAX_FOLLOWUP_REREAD_RATIO:
                problems.append(
                    f"task {turn['task']} re-read {read} of {total} tokens: the "
                    "prompt cache broke, so something changed the tools or the "
                    "system prompt mid-session."
                )
    return problems


def _table(on: List[Dict[str, Any]], off: List[Dict[str, Any]]) -> str:
    lines = [f"{'task':10}{'off s':>8}{'on s':>8}{'off read':>10}{'on read':>10}"]
    for a, b in zip(off, on):
        lines.append(
            f"{a['task']:10}{a['wall_s']:>8}{b['wall_s']:>8}"
            f"{sum(a['reads']):>10}{sum(b['reads']):>10}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="model id to measure")
    parser.add_argument("--out", help="write both legs here as JSON")
    parser.add_argument("--leg", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.leg:
        _run_session(args.model, Path(args.leg))
        return 0

    lemonade = _lemonade_env()
    off = _run_leg(args.model, dynamic=False, lemonade=lemonade)
    on = _run_leg(args.model, dynamic=True, lemonade=lemonade)
    print(_table(on, off))
    if args.out:
        Path(args.out).write_text(
            json.dumps({"model": args.model, "on": on, "off": off}, indent=1),
            encoding="utf-8",
        )
    problems = judge(on, off)
    for problem in problems:
        print(f"FAIL: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
