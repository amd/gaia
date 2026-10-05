# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run an eval_flagship.yml lane on this machine, set up the way CI sets it up.

    python util/run_eval_lane.py --lane gaia-signal
    python util/run_eval_lane.py --category gaia_memory --category gaia_honesty

A hand-rolled local run drifts from CI in ways that change scores or do harm:
the backend started from the checkout instead of the staged ``~/gaia-eval``
fixtures, the real ``gh`` on PATH while tool calls are auto-approved (writes
aimed at real GitHub), the judge's credentials inherited by the agent under
test. This script takes the lane environment from ``util/eval_lane_matrix.py``
and repeats the workflow's setup step for step.

Categories run one at a time against one backend (CLAUDE.md: one eval per
Lemonade). Logs and scorecard paths land in ``--out``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_lane_matrix import REPO_ROOT, lane_env  # noqa: E402

LANES_FILE = REPO_ROOT / "eval" / "ci_lanes.json"
JUDGE_CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
FIXTURE_PORT = 8765
MCP_STUB = "tests/fixtures/gaia/mcp_stub/stage_mcp_stub.py"


def lane_categories(lane: str) -> list[str]:
    """The categories ``ci_lanes.json`` assigns to *lane*."""
    doc = json.loads(LANES_FILE.read_text(encoding="utf-8"))
    for entry in doc["lanes"]:
        if entry["lane"] == lane:
            return list(entry["categories"])
    known = ", ".join(e["lane"] for e in doc["lanes"])
    raise SystemExit(f"No lane named {lane!r} in {LANES_FILE}. Lanes: {known}")


def plan(categories: list[str], home: Path, port: int) -> dict:
    """Environment, working directory and extra eval args CI would use."""
    flags = lane_env(categories)
    env = {
        "GAIA_MEMORY_DISABLED": flags["memory_disabled"],
        "GAIA_MEMORY_ADMIN": flags["memory_admin"],
        "GAIA_MEMORY_MCP_ALWAYS": flags["memory_mcp"],
        "GAIA_EVAL_SCRIPTED_USER": "1",
        "GAIA_DOCUMENT_ROOTS": str(REPO_ROOT / "eval" / "corpus"),
        "NO_PROXY": "localhost,127.0.0.1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
    }
    cwd = REPO_ROOT
    extra_args: list[str] = []
    fixtures = flags["gaia_fixtures"] == "1"
    if fixtures:
        cwd = home / "gaia-eval"
        env.update(
            {
                "GAIA_HUB_URL": f"http://127.0.0.1:{FIXTURE_PORT}/fixture_hub",
                "GAIA_WEB_ALLOWED_HOSTS": "127.0.0.1",
                "GAIA_AUTO_APPROVE_TOOLS": "1",
                "GAIA_EVAL_MAILBOX": str(
                    REPO_ROOT / "tests" / "fixtures" / "gaia" / "email" / "eval_inbox.mbox"
                ),
                # The staged fake gh first: tool calls are auto-approved here.
                "PATH": str(home / ".gaia-eval-bin") + os.pathsep + os.environ["PATH"],
            }
        )
        extra_args = ["--exclude-tag", "live", "--exclude-tag", "local_blocked_win_shim"]
    return {
        "env": env,
        "cwd": cwd,
        "extra_args": extra_args,
        "fixtures": fixtures,
        "memory": flags["memory_disabled"] == "0",
        "backend": f"http://127.0.0.1:{port}",
    }


def _get(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _enable_memory(backend: str) -> None:
    req = urllib.request.Request(
        f"{backend}/api/memory/settings",
        data=b'{"memory_enabled": true}',
        method="PUT",
        headers={"X-Gaia-UI": "1", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read())
    if body.get("memory_enabled") is not True:
        raise SystemExit(
            f"The backend did not switch memory on ({body}). This lane measures "
            "long-term memory and cannot run with it off."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--lane", help="A lane from eval/ci_lanes.json")
    which.add_argument("--category", action="append", help="Category (repeatable)")
    parser.add_argument("--port", type=int, default=4200, help="Backend port")
    parser.add_argument(
        "--judge-model", default="claude-sonnet-4-6", help="Judge (CI's default)"
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "eval-out")
    args = parser.parse_args()

    categories = lane_categories(args.lane) if args.lane else args.category
    home = Path.home()
    p = plan(categories, home, args.port)
    args.out.mkdir(parents=True, exist_ok=True)

    if _get(f"{p['backend']}/api/health"):
        raise SystemExit(
            f"Something already answers on {p['backend']}. Stop it or pass --port: "
            "the eval must drive a backend started with this lane's environment."
        )

    env = {**os.environ, **p["env"]}
    if p["fixtures"]:
        subprocess.run(
            [sys.executable, "tests/fixtures/gaia/stage_eval_env.py", "--home", str(home)],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
    # The agent under test runs shell commands; only the judge gets credentials.
    backend_env = {k: v for k, v in env.items() if k not in JUDGE_CREDENTIALS}
    children = []
    try:
        if p["fixtures"]:
            children.append(
                subprocess.Popen(
                    [sys.executable, "tests/fixtures/gaia/serve_fixtures.py",
                     "--port", str(FIXTURE_PORT)],
                    cwd=REPO_ROOT,
                    env=backend_env,
                    stdout=open(args.out / "fixtures.log", "w", encoding="utf-8"),
                    stderr=subprocess.STDOUT,
                )
            )
        backend = subprocess.Popen(
            [sys.executable, "-m", "gaia.ui.server", "--port", str(args.port),
             "--host", "127.0.0.1"],
            cwd=p["cwd"],
            env=backend_env,
            stdout=open(args.out / "backend.log", "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        )
        children.append(backend)
        deadline = time.monotonic() + 600
        while not _get(f"{p['backend']}/api/health"):
            if backend.poll() is not None:
                raise SystemExit(
                    f"gaia.ui.server exited during startup ({backend.returncode}); "
                    f"see {args.out / 'backend.log'}"
                )
            if time.monotonic() > deadline:
                raise SystemExit(f"Backend not healthy in 10 min; see {args.out / 'backend.log'}")
            time.sleep(3)
        if p["memory"]:
            _enable_memory(p["backend"])

        failed = []
        for category in categories:
            if category == "gaia_mcp":
                subprocess.run([sys.executable, MCP_STUB, "install"], cwd=REPO_ROOT, check=True)
            print(f"=== {category}", flush=True)
            with open(args.out / f"{category}.log", "w", encoding="utf-8") as log:
                code = subprocess.run(
                    ["gaia", "eval", "agent", "--category", category,
                     "--backend", p["backend"], "--model", args.judge_model,
                     *p["extra_args"]],
                    cwd=REPO_ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                ).returncode
            if category == "gaia_mcp":
                subprocess.run([sys.executable, MCP_STUB, "remove"], cwd=REPO_ROOT, check=True)
            summary = [
                line.strip()
                for line in (args.out / f"{category}.log").read_text(encoding="utf-8").splitlines()
                if line.startswith(("Results:", "Avg score:", "Output:"))
            ]
            print(f"    exit {code}  " + "  ".join(summary), flush=True)
            if code != 0:
                failed.append(category)
        if failed:
            print(f"Harness errors (not a score verdict) in: {', '.join(failed)}")
            return 1
        return 0
    finally:
        for child in children:
            _stop_tree(child)


def _stop_tree(proc: subprocess.Popen) -> None:
    """Stop *proc* and what it spawned; terminate() alone orphans them on Windows."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
    proc.wait(timeout=30)


if __name__ == "__main__":
    sys.exit(main())
