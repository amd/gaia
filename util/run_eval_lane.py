# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run an eval_flagship.yml lane on this machine, set up the way CI sets it up.

    python util/run_eval_lane.py --lane gaia-signal
    python util/run_eval_lane.py --category gaia_memory --category gaia_honesty

A hand-rolled local run drifts from CI in ways that change scores or do harm:
the backend started from the checkout instead of the staged ``~/gaia-eval``
fixtures, the real ``gh`` on PATH while tool calls are auto-approved (writes
aimed at real GitHub), the judge's credentials inherited by the agent under
test, and agent state left in the real ``~/.gaia`` by an earlier run. This
script takes the lane environment from ``util/eval_lane_matrix.py`` and repeats
the workflow's setup step for step, including a fresh GAIA home per category
(``util/eval_gaia_home.py``) under ``--out``, deleted when the category ends
clean. A category with any result other than PASS keeps its home (chat
history, agent steps, memory) under ``--out/kept-homes/<category>``.

Categories run one at a time, each against its own backend (CLAUDE.md: one eval
per Lemonade). Logs and scorecard paths land in ``--out``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_gaia_home import env_for  # noqa: E402
from eval_lane_matrix import REPO_ROOT, lane_env  # noqa: E402

LANES_FILE = REPO_ROOT / "eval" / "ci_lanes.json"
BASELINE_META = (
    REPO_ROOT / "tests" / "fixtures" / "eval_baselines" / "gaia-flagship" / "meta.json"
)


def baseline_eval_model() -> str:
    """The Claude model CI drives and judges with: the one the baseline used."""
    model = json.loads(BASELINE_META.read_text(encoding="utf-8")).get("eval_model")
    if not model:
        raise SystemExit(
            f"{BASELINE_META} has no eval_model, so the CI judge is unknown. "
            "Restore it, or pass --judge-model."
        )
    return model


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
                    REPO_ROOT
                    / "tests"
                    / "fixtures"
                    / "gaia"
                    / "email"
                    / "eval_inbox.mbox"
                ),
                # The staged fake gh first: tool calls are auto-approved here.
                "PATH": str(home / ".gaia-eval-bin") + os.pathsep + os.environ["PATH"],
            }
        )
        extra_args = [
            "--exclude-tag",
            "live",
            "--exclude-tag",
            "local_blocked_win_shim",
        ]
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
        "--judge-model",
        default=None,
        help="Judge and driver (default: CI's, from the flagship baseline's meta.json)",
    )
    parser.add_argument(
        "--agent-model",
        help="Local model the agent runs (written as default_model in each "
        "category's GAIA home). Default: what a fresh home resolves, as in CI.",
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "eval-out")
    args = parser.parse_args()
    args.judge_model = args.judge_model or baseline_eval_model()

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
    # Absolute: eval_gaia_home.py check compares the exported paths verbatim.
    homes = (args.out / "gaia-homes").resolve()
    if homes.exists():
        shutil.rmtree(homes)
    staged_home = homes / "_staged"
    if p["fixtures"]:
        staged_home.mkdir(parents=True)
        subprocess.run(
            [
                sys.executable,
                "tests/fixtures/gaia/stage_eval_env.py",
                "--home",
                str(home),
                "--gaia-home",
                str(staged_home),
            ],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
    children = []
    try:
        if p["fixtures"]:
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "tests/fixtures/gaia/serve_fixtures.py",
                        "--port",
                        str(FIXTURE_PORT),
                    ],
                    cwd=REPO_ROOT,
                    env=_without_judge(env),
                    stdout=open(args.out / "fixtures.log", "w", encoding="utf-8"),
                    stderr=subprocess.STDOUT,
                )
            )
        failed = []
        for category in categories:
            cat_env = {**env, **category_home(homes / category, staged_home)}
            if args.agent_model:
                write_agent_model(Path(cat_env["GAIA_HOME"]), args.agent_model)
            code = _run_category(category, cat_env, p, args)
            if code != 0:
                failed.append(category)
        if failed:
            print(f"Harness errors (not a score verdict) in: {', '.join(failed)}")
            return 1
        return 0
    finally:
        for child in children:
            _stop_tree(child)
        shutil.rmtree(homes, ignore_errors=True)


def category_home(cat_home: Path, staged_home: Path) -> dict[str, str]:
    """Create *cat_home* empty (plus staged skills) and return its environment."""
    if cat_home.exists():
        raise SystemExit(
            f"{cat_home} already exists; every category must start on a GAIA "
            "home nothing has written to."
        )
    cat_home.mkdir(parents=True)
    if (staged_home / "skills").is_dir():
        shutil.copytree(staged_home / "skills", cat_home / "skills")
    return env_for(cat_home)


def write_agent_model(cat_home: Path, model: str) -> None:
    """Make *model* the agent's model in *cat_home*, as `gaia config set` would."""
    (cat_home / "config.json").write_text(
        json.dumps({"default_model": model}), encoding="utf-8"
    )


def _without_judge(env: dict) -> dict:
    # The agent under test runs shell commands; only the judge gets credentials.
    return {k: v for k, v in env.items() if k not in JUDGE_CREDENTIALS}


def _run_category(category: str, env: dict, p: dict, args) -> int:
    """Start a backend on *category*'s GAIA home, run the eval, stop it."""
    cat_home = Path(env["GAIA_HOME"])
    subprocess.run(
        [sys.executable, "util/eval_gaia_home.py", "check", "--home", str(cat_home)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )
    if _get(f"{p['backend']}/api/health"):
        raise SystemExit(
            f"Something already answers on {p['backend']}. Stop it or pass --port: "
            "the eval must drive a backend started with this category's environment."
        )
    if category == "gaia_mcp":
        subprocess.run(
            [sys.executable, MCP_STUB, "install"], cwd=REPO_ROOT, env=env, check=True
        )
    log_path = args.out / f"backend-{category}.log"
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gaia.ui.server",
            "--port",
            str(args.port),
            "--host",
            "127.0.0.1",
        ],
        cwd=p["cwd"],
        env=_without_judge(env),
        stdout=open(log_path, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    code = None
    try:
        deadline = time.monotonic() + 600
        while not _get(f"{p['backend']}/api/health"):
            if backend.poll() is not None:
                raise SystemExit(
                    f"gaia.ui.server exited during startup ({backend.returncode}); "
                    f"see {log_path}"
                )
            if time.monotonic() > deadline:
                raise SystemExit(f"Backend not healthy in 10 min; see {log_path}")
            time.sleep(3)
        if p["memory"]:
            _enable_memory(p["backend"])
        _require_empty_memory(p["backend"], Path(env["GAIA_MEMORY_DB"]), category)

        print(f"=== {category}  (GAIA_HOME={cat_home})", flush=True)
        with open(args.out / f"{category}.log", "w", encoding="utf-8") as log:
            code = subprocess.run(
                [
                    "gaia",
                    "eval",
                    "agent",
                    "--category",
                    category,
                    "--backend",
                    p["backend"],
                    "--model",
                    args.judge_model,
                    *p["extra_args"],
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            ).returncode
        summary = [
            line.strip()
            for line in (args.out / f"{category}.log")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.startswith(("Results:", "Avg score:", "Output:"))
        ]
        print(f"    exit {code}  " + "  ".join(summary), flush=True)
        return code
    finally:
        if category == "gaia_mcp":
            subprocess.run(
                [sys.executable, MCP_STUB, "remove"], cwd=REPO_ROOT, env=env, check=True
            )
        # The daemon the backend spawned lives in this home; stop it there.
        subprocess.run(
            ["gaia", "daemon", "stop"],
            cwd=REPO_ROOT,
            env=env,
            check=False,
        )
        _stop_tree(backend)
        problem = _category_problem(code, args.out / f"{category}.log")
        if problem:
            kept = _keep_home(cat_home, args.out / "kept-homes" / category)
            print(f"    {problem}; its GAIA home is kept at {kept}", flush=True)
        else:
            shutil.rmtree(cat_home, ignore_errors=True)


def _category_problem(code, log_path: Path) -> str | None:
    """Why the category's GAIA home is worth keeping, or None when it passed."""
    if code is None:
        return "the eval did not run to completion"
    if code != 0:
        return f"the eval exited {code}"
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    outputs = [
        line.split("Output:", 1)[1].strip()
        for line in log.splitlines()
        if line.startswith("Output:")
    ]
    if not outputs:
        return f"{log_path} names no results directory"
    scorecard = Path(outputs[-1]) / "scorecard.json"
    if not scorecard.is_file():
        return f"{scorecard} was not written"
    statuses = [
        s.get("status")
        for s in json.loads(scorecard.read_text(encoding="utf-8")).get("scenarios", [])
    ]
    not_passed = [s for s in statuses if s != "PASS" and not str(s).startswith("SKIP")]
    if not_passed:
        return f"{len(not_passed)} scenario(s) did not pass"
    return None


def _keep_home(cat_home: Path, dest: Path) -> Path:
    """Move *cat_home* out of the per-run tree, which is wiped on exit."""
    if dest.exists():
        dest = dest.with_name(f"{dest.name}-{time.strftime('%Y%m%d-%H%M%S')}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(cat_home), str(dest))
    return dest


def _require_empty_memory(backend: str, memory_db: Path, category: str) -> None:
    """Refuse to run *category* unless the backend's own memory store is empty."""
    req = urllib.request.Request(
        f"{backend}/api/memory/stats", headers={"X-Gaia-UI": "1"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        stats = json.loads(resp.read())
    held = {
        "knowledge": stats["knowledge"]["total"],
        "procedures": stats["procedures"]["total"],
        "conversation_turns": stats["conversations"]["total_turns"],
    }
    if any(held.values()):
        raise SystemExit(
            f"The backend's memory is not empty at the start of {category}: {held}."
        )
    if not memory_db.exists():
        raise SystemExit(
            f"The backend did not create {memory_db}, so its memory store is not "
            f"{category}'s isolated GAIA home."
        )


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
