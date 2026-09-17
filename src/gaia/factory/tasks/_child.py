# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""One task attempt, in its own process. Invoked by ``runner.py``, not by hand.

This exists as a separate process for three reasons, all of which the parent
cannot achieve in-process:

* **Containment.** ``HOME``, ``USERPROFILE`` and ``GAIA_HOME`` are read during
  import, so redirecting them into the sandbox has to happen before the
  interpreter starts. Without that, a file search escapes the workspace into the
  developer's home directory — measured, not hypothetical: the first smoke run
  answered about the real GAIA repo instead of the task.
* **A timeout that bites.** A wedged agent loop cannot be interrupted from
  inside itself. The parent kills this process.
* **A clean slate per attempt.** Agent memory, indexes and caches live under
  ``~/.gaia``. Shared across arms, arm two inherits arm one's warm state and the
  comparison stops being fair.

The job spec arrives as a JSON file and the result leaves as one. Nothing is
printed to stdout that the parent depends on — the agent logs freely there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])

    repo = Path(job["repo"])
    for rel in ("hub/agents/chat/python", "hub/agents/gaia/python"):
        sys.path.insert(0, str(repo / rel))
    sys.path.insert(0, str(repo / "src"))

    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    workspace = Path(job["workspace"])
    kwargs = {
        "silent_mode": True,
        "max_steps": job["max_steps"],
        "allowed_paths": [str(workspace)],
        # Puts each step's composed prompt into the conversation record, which
        # is the only way to measure how context grows across a task — the
        # figure that sizes a KV cache. ``show_prompts`` stays off: this writes
        # them to the record, it does not print them.
        "debug_prompts": True,
    }
    if job["transport"] == "anthropic":
        kwargs.update(use_claude=True, claude_model=job["model"])
    else:
        kwargs["model_id"] = job["model"]
        if job.get("base_url"):
            kwargs["base_url"] = job["base_url"]

    payload: dict
    try:
        agent = GaiaAgent(GaiaAgentConfig(**kwargs))
        # The documented lever for an unattended host. The environment variable
        # is deliberately snapshotted before any ``.env`` merge, so a process
        # that imports gaia and *then* sets it is ignored — as this one would be.
        agent.console.auto_approve_gated_tools = True

        turns = [job["prompt"], *(job.get("follow_ups") or [])]
        merged = {
            "ok": True,
            "steps_taken": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration": 0.0,
            "result": "",
            "error_history": [],
            "conversation": [],
            "turn_metrics": {"llm_calls": [], "totals": {}, "prompt": {}},
            "turns": [],
        }
        for text in turns:
            result = agent.process_query(text)
            # The base agent reads conversation_history at the start of a turn
            # but never writes it back — persistence is the host's job. Without
            # this the agent meets every follow-up with no memory of what it
            # just did, and "continue" becomes an unanswerable request.
            agent.conversation_history = list(agent.conversation_history or []) + [
                {"role": "user", "content": text},
                {"role": "assistant", "content": str(result.get("result") or "")},
            ]
            merged["steps_taken"] += int(result.get("steps_taken") or 0)
            merged["input_tokens"] += int(result.get("input_tokens") or 0)
            merged["output_tokens"] += int(result.get("output_tokens") or 0)
            merged["duration"] += float(result.get("duration") or 0.0)
            merged["result"] = str(result.get("result") or "")
            merged["error_history"].extend(result.get("error_history") or [])
            merged["conversation"].extend(result.get("conversation") or [])
            tm = result.get("turn_metrics") or {}
            merged["turn_metrics"]["llm_calls"].extend(tm.get("llm_calls") or [])
            # The fixed prefill is a property of the agent, not of the turn, so
            # the last turn's value is the right one to keep rather than a sum.
            merged["turn_metrics"]["prompt"] = (
                tm.get("prompt") or merged["turn_metrics"]["prompt"]
            )
            for k, v in (tm.get("totals") or {}).items():
                if isinstance(v, (int, float)):
                    merged["turn_metrics"]["totals"][k] = (
                        merged["turn_metrics"]["totals"].get(k, 0) + v
                    )
            merged["turns"].append(
                {
                    "prompt": text[:200],
                    "steps": int(result.get("steps_taken") or 0),
                    "answer_chars": len(str(result.get("result") or "")),
                }
            )
        payload = merged
    except Exception as exc:  # noqa: BLE001 - reported to the parent, not hidden
        import traceback

        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }

    out.write_text(json.dumps(payload, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
