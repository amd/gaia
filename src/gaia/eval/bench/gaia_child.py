# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The GAIA harness's child process: run the flagship on one task, write the outcome.

``python -m gaia.eval.bench.gaia_child SPEC.json``. The parent gives it an
environment with no credentials and the model gateway as its backend, and kills
it at the time cap. Every model call and tool result is appended to a progress
file as it happens, so a run cut off at the cap still leaves its record.
"""

from __future__ import annotations

import functools
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)


def _append(path: Path, entry: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")
        fh.flush()


def instrument(progress: Path) -> Callable[[Any], None]:
    """Wrap the agent's model calls and tool calls so each leaves a line in *progress*."""

    def _install(agent: Any) -> None:
        execute = agent._execute_tool  # pylint: disable=protected-access

        @functools.wraps(execute)
        def _execute(tool_name: str, tool_args: Dict[str, Any]) -> Any:
            result = execute(tool_name, tool_args)
            _append(
                progress,
                {
                    "role": "tool",
                    "name": tool_name,
                    "tool_args": tool_args,
                    "content": result,
                },
            )
            return result

        agent._execute_tool = _execute  # pylint: disable=protected-access
        chat = getattr(agent, "chat", None)
        for name in ("send_messages", "send_messages_stream"):
            send = getattr(chat, name, None)
            if send is None:
                continue

            def _counted(*args: Any, _send: Any = send, **kwargs: Any) -> Any:
                _append(progress, {"event": "llm_call"})
                return _send(*args, **kwargs)

            setattr(chat, name, _counted)

    return _install


def run(spec: Dict[str, Any]) -> Dict[str, Any]:
    from gaia.agents.base.agent import (  # pylint: disable=import-outside-toplevel
        _sum_cached_tokens,
    )
    from gaia.eval import flagship_tasks  # pylint: disable=import-outside-toplevel

    progress = Path(spec["progress"])
    progress.write_text("", encoding="utf-8")
    outcome, error, kind = (
        flagship_tasks._run_agent(  # pylint: disable=protected-access
            spec["prompt"],
            spec["model"],
            int(spec["max_steps"]),
            Path(spec["workdir"]),
            Path(spec["memory_db"]),
            full_access=bool(spec.get("full_access")),
            on_agent=instrument(progress),
        )
    )
    conversation: List[Dict[str, Any]] = outcome.get("conversation") or []
    return {
        "outcome": outcome,
        "error": error,
        "error_kind": kind,
        "cached_tokens": _sum_cached_tokens(conversation),
    }


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: python -m gaia.eval.bench.gaia_child SPEC.json\n")
        return 2
    spec = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    result = run(spec)
    Path(spec["outcome"]).write_text(json.dumps(result, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
