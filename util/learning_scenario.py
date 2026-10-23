#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A week with Jordan: does one night of on-device learning make the agent theirs?

Jordan is a release engineer on the "Halcyon" project. Over a week of chats
the agent picked up things no base model can know — team jargon, how Jordan
likes answers written, a fact Jordan had to correct, and a lookup the agent
kept doing the slow way. This script writes that week into a fresh, isolated
GAIA home through GAIA's own stores, runs one learning cycle exactly as the
nightly job would, and prints the morning report: every held-out probe asked
of the base model and of the new adapter, with the raw model and no memory or
system prompt in the way, so any gain is in the weights.

Needs the training toolchain (``gaia learn setup``) and a running Lemonade
Server with Gemma-4-E4B-it-GGUF. Uses the real GPU; takes ~10-20 minutes on a
Ryzen AI Max+ 395.

    python util/learning_scenario.py --home C:/gaia-scenario

It never touches your own ``~/.gaia``. Lemonade is shared, so the base model is
reloaded without the scenario's adapter at the end (``--leave-loaded`` keeps
it loaded to chat with).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

BASE_MODEL = "Gemma-4-E4B-it-GGUF"

PREFERENCES = [
    "Jordan wants incident summaries to start with a severity tag such as "
    "[SEV2], followed by at most three bullet points.",
    "Jordan wants every time written in UTC with a Z suffix, such as 14:00Z, "
    "never in local time.",
]

CONCEPTS = [
    "The Thursday rollup is Jordan's weekly build-health digest, posted to the "
    "#halcyon-ops channel every Thursday at 16:00Z.",
    "Bluefleet is the Halcyon staging cluster in us-east-2; Redfleet is the "
    "Halcyon production cluster in eu-west-1.",
    "HAL-7 is the Halcyon on-call rotation, kept in PagerDuty schedule P7Q2.",
]

SMALL_TALK = [
    ("Can you suggest a good name for a release-notes channel?",
     "A few options: #release-notes, #ship-log, or #whats-new. #ship-log is short "
     "and reads well in notifications."),
    ("What's a quick way to explain semantic versioning?",
     "MAJOR.MINOR.PATCH: bump MAJOR for breaking changes, MINOR for new features "
     "that stay compatible, PATCH for bug fixes."),
]

CORRECTION = [
    ("What port does the Halcyon artifact cache listen on?",
     "Artifact caches usually listen on port 8080, so that's the likely one."),
    ("No, that's wrong: the Halcyon artifact cache listens on port 7443 over TLS.",
     "Thanks for the correction — the Halcyon artifact cache listens on port 7443, "
     "over TLS."),
]

STATUS_FILE = "C:/halcyon/build_status.md"
WORKFLOW_ASKS = [
    ("Is the nightly build green?", "Nightly build #4521 is green: all 312 tests passed."),
    ("Did last night's Halcyon build pass?", "Yes — nightly #4522 passed; 312 of 312 tests green."),
    ("What's the status of the nightly build?", "Nightly #4523 is red: 3 integration tests failed in deploy-smoke."),
]


def seed(home: Path) -> None:
    from gaia.agents.base.memory_store import MemoryStore
    from gaia.ui.database import ChatDatabase

    (home / "chat").mkdir(parents=True, exist_ok=True)
    chat = ChatDatabase(str(home / "chat" / "gaia_chat.db"))
    mem = MemoryStore(db_path=home / "memory.db")
    for text in PREFERENCES:
        mem.store("preference", text, source="user", confidence=0.9)
    for text in CONCEPTS:
        mem.store("fact", text, source="user", confidence=0.9)

    s = chat.create_session(title="Small talk", model=BASE_MODEL)["id"]
    for q, a in SMALL_TALK:
        chat.add_message(s, "user", q)
        chat.add_message(s, "assistant", a)

    s = chat.create_session(title="Artifact cache", model=BASE_MODEL)["id"]
    for q, a in CORRECTION:
        chat.add_message(s, "user", q)
        chat.add_message(s, "assistant", a)

    # The slow way, three times: two wrong file reads before the right lookup.
    for n, (q, a) in enumerate(WORKFLOW_ASKS):
        s = chat.create_session(title=f"Build status {n + 1}", model=BASE_MODEL)["id"]
        chat.add_message(s, "user", q)
        mem_session = f"scenario-workflow-{n}"
        steps = []
        for path in ("C:/halcyon/status.txt", "C:/halcyon/builds/latest.log"):
            mem.log_tool_call(mem_session, "read_file", {"file_path": path}, "",
                              success=False, error=f"File not found: {path}")
            steps.append({"type": "tool", "tool": "read_file", "detail": f"file_path: {path}",
                          "result": "File not found", "success": False})
        mem.log_tool_call(mem_session, "query_specific_file",
                          {"file_path": STATUS_FILE, "query": "nightly build status"},
                          a, success=True)
        steps.append({"type": "tool", "tool": "query_specific_file",
                      "detail": f"file_path: {STATUS_FILE}, query: nightly build status",
                      "result": "Found 1 relevant chunk(s)", "success": True})
        chat.add_message(s, "assistant", a, agent_steps=steps)
    mem.close()
    chat.close()


def _row(passed_base: bool, passed_new: bool) -> str:
    mark = {True: "pass", False: "FAIL"}
    return f"{mark[passed_base]:>5} -> {mark[passed_new]:<5}"


def report(run: dict) -> None:
    print("\n" + "=" * 78)
    print(f"MORNING REPORT  run {run['id']}  status: {run['status']}")
    print("=" * 78)
    print(run.get("summary", ""))
    if run["status"] == "failed":
        print(run.get("error"))
        return
    scores = run.get("scores") or {}
    base, cand = scores.get("base"), scores.get("candidate")
    if not (base and cand):
        return
    metrics = run.get("metrics") or {}
    print(f"\nTraining: {metrics.get('steps')} steps, {metrics.get('seconds')} s, "
          f"peak GPU {metrics.get('peak_gpu_gb')} GB; held-out loss "
          f"{metrics.get('base_eval_loss'):.3f} -> {metrics.get('eval_loss'):.3f}")
    print(f"Learned probes: base {base['learned_passed']}/{base['learned_total']}"
          f"  ->  adapter {cand['learned_passed']}/{cand['learned_total']}")
    print(f"General probes: base {base['regression_passed']}/{base['regression_total']}"
          f"  ->  adapter {cand['regression_passed']}/{cand['regression_total']}")
    print("\n  base -> adapter  question")
    by_q = {(r["kind"], r["question"]): r for r in base["results"]}
    for r in cand["results"]:
        b = by_q.get((r["kind"], r["question"]))
        if r["kind"] != "learned" or b is None:
            continue
        print(f"  {_row(b['passed'], r['passed'])}  {r['question'][:70]}")
        print(f"      base:    {b['reply'][:110]!r}")
        print(f"      adapter: {r['reply'][:110]!r}")
    print(f"\nDecision: {run.get('decision')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", required=True, help="Fresh folder (no spaces) for the scenario's GAIA home")
    ap.add_argument("--max-steps", type=int, default=600)
    ap.add_argument("--leave-loaded", action="store_true",
                    help="Keep Gemma loaded with the scenario adapter afterwards")
    args = ap.parse_args()

    home = Path(args.home).resolve()
    if home.exists() and any(home.iterdir()):
        sys.exit(f"{home} is not empty; pass a fresh folder so the scenario starts cold.")
    home.mkdir(parents=True, exist_ok=True)
    os.environ["GAIA_HOME"] = str(home)
    os.environ.pop("GAIA_MEMORY_DB", None)

    from gaia.learning.config import update_config
    from gaia.learning.cycle import run_cycle
    from gaia.learning.serving import reload_with_adapter
    from gaia.llm.lemonade_client import LemonadeClient

    print(f"Seeding a week of history into {home}")
    seed(home)
    update_config(base_model=BASE_MODEL, max_steps=args.max_steps)
    started = time.time()
    try:
        run = run_cycle(trigger="manual")
    finally:
        if not args.leave_loaded:
            reload_with_adapter(LemonadeClient(verbose=False), BASE_MODEL, base_only=True)
    report(run)
    print(f"\nWall time: {time.time() - started:.0f} s. Run record: "
          f"{home / 'learning' / 'runs' / run['id'] / 'run.json'}")
    (home / "scenario_result.json").write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")
    return 0 if run["status"] in ("promoted", "rejected") else 1


if __name__ == "__main__":
    sys.exit(main())
