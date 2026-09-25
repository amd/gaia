# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fakes for the benchmark harness: agents that act at the process boundary.

The harness launches each agent as a child process. These replace
``harness.launch`` with a function that writes what the real child would —
the GAIA outcome file and progress log, or Claude Code's stream-json — so a
test exercises everything around the agent without a model.
"""

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import config as bench_config
from gaia.eval.bench import harness


@pytest.fixture
def bench_env(monkeypatch, tmp_path):
    """No live Lemonade, no real GAIA home, a private work root."""
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv(bench_config.ENV_WORK_ROOT, str(tmp_path / "work"))
    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    return tmp_path


def gaia_tool(name: str, args: Dict[str, Any], result: Any) -> List[Dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": {"tool_calls": [{"name": name, "tool_args": args}]},
        },
        {"role": "tool", "name": name, "tool_args": args, "content": result},
    ]


def cc_tool(
    n: int, name: str, args: Dict[str, Any], output: str
) -> List[Dict[str, Any]]:
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": f"t{n}", "name": name, "input": args}
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": f"t{n}", "content": output}
                ]
            },
        },
    ]


class FakeLaunch:
    """Stands in for ``harness.launch`` and records what each harness was given."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        #: ``act(workdir)`` does the agent's work and returns its final answer.
        self.act: Callable[[Path], str] = lambda workdir: "done"
        self.conversation: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.timed_out = False

    def __call__(self, cmd, *, env, cwd, timeout_s, stdout_path, stderr_path):
        self.calls.append(
            {
                "cmd": list(cmd),
                "env": dict(env),
                "cwd": Path(cwd),
                "timeout_s": timeout_s,
                "gh_config_empty": list(Path(env["GH_CONFIG_DIR"]).iterdir()) == [],
            }
        )
        stderr_path.write_text("")
        answer = self.act(Path(cwd))
        if "gaia.eval.bench.gaia_child" in cmd:
            return self._gaia(cmd, answer, stdout_path)
        return self._claude(answer, stdout_path)

    def _gaia(self, cmd, answer, stdout_path):
        spec_path = cmd[cmd.index("gaia.eval.bench.gaia_child") + 1]
        spec = json.loads(Path(spec_path).read_text())
        tools = [e for e in self.conversation if e.get("role") == "tool"]
        Path(spec["progress"]).write_text(
            "".join(json.dumps({"event": "llm_call"}) + "\n" for _ in range(2))
            + "".join(json.dumps(e) + "\n" for e in tools)
        )
        stdout_path.write_text("")
        if self.timed_out:
            return None, True
        Path(spec["outcome"]).write_text(
            json.dumps(
                {
                    "outcome": {
                        "result": answer,
                        "conversation": self.conversation,
                        "steps_taken": 2,
                        "input_tokens": 1000,
                        "output_tokens": 100,
                    },
                    "error": "",
                    "error_kind": "",
                    "cached_tokens": 400,
                }
            )
        )
        return 0, False

    def _claude(self, answer, stdout_path):
        events = list(self.events)
        if not self.timed_out:
            events.append(
                {
                    "type": "result",
                    "is_error": False,
                    "result": answer,
                    "num_turns": 3,
                    "total_cost_usd": 0.42,
                    "usage": {
                        "input_tokens": 10,
                        "cache_read_input_tokens": 500,
                        "cache_creation_input_tokens": 90,
                        "output_tokens": 70,
                    },
                }
            )
        text = "".join(json.dumps(e) + "\n" for e in events)
        if self.timed_out:
            text += '{"type": "assistant", "message": {"content": [{"ty'  # cut off
        stdout_path.write_text(text)
        return (None, True) if self.timed_out else (0, False)


@pytest.fixture
def fake_launch(monkeypatch, bench_env):
    launch = FakeLaunch()
    monkeypatch.setattr(harness, "launch", launch)
    monkeypatch.setattr(harness, "_which", lambda name: f"/bin/{name}")
    return launch


def one_task_file(tmp_path: Path, *ids: str) -> Path:
    source = json.loads(ft.TASKS_FILE.read_text(encoding="utf-8"))
    source["suites"]["one"] = list(ids)
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(source))
    return path


@pytest.fixture
def fake_agent_run(fake_launch, tmp_path):
    """Run one task through a harness whose agent answers and prints what it's told."""

    def run(
        answer: str = "done",
        tool_output: str = "",
        harness_name: str = harness.GAIA,
        model: str = "m",
        task: str = "02-bugfix",
        config: Optional[bench_config.BenchConfig] = None,
    ):
        fake_launch.act = lambda workdir: answer
        fake_launch.conversation = gaia_tool(
            "run_shell_command", {"command": "env"}, {"stdout": tool_output}
        )
        fake_launch.events = cc_tool(1, "Bash", {"command": "env"}, tool_output)
        out = tmp_path / "out"
        card = ft.run_suite(
            "one",
            model,
            out,
            tasks_file=one_task_file(tmp_path, task),
            config=config or bench_config.resolve(harness=harness_name),
        )
        return card, out

    return run
