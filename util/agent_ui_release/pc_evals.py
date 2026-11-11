#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run the ai-pc-evals benchmark through the Agent UI window, and record what the grader says.

The package is optional and moves between versions, so it is never assumed: pass
--evals-dir or set AI_PC_EVALS_DIR to the unzipped package (the folder holding
kwengine/ and tools/run_eval.py).

    pc_evals.py check                                    # version, clean copy, grading set up?
    pc_evals.py prepare RUN_DIR --suite smoke            # build task folders; prints folder + prompt file
    #   ...drive the app: new chat, folder chip -> task folder, paste the prompt, wait for the answer...
    pc_evals.py record RUN_DIR kw-00-smoke --port P      # answer + timing from the backend -> kwengine record
    pc_evals.py drive RUN_DIR --cdp 9333 --port P        # or: every task over DevTools, approvals logged
    pc_evals.py grade RUN_DIR                            # close the app first; prints the grader's summary.md

Scores come only from the package's grader. If grading cannot run (no answers
passphrase), the task is "not graded": never estimate a score. Do not open
secrets/, scorecard.json or judge-input.txt; the package's CLAUDE.md lists what
an operator may read.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

LEG = "gaia-agent-ui"
HEADERS = {"X-Gaia-UI": "1", "Accept": "application/json"}
PYTHON = (
    sys.executable
)  # --python overrides: the interpreter with the package's requirements


def evals_dir(a) -> Path:
    raw = a.evals_dir or os.environ.get("AI_PC_EVALS_DIR")
    if not raw:
        sys.exit(
            "No ai-pc-evals package given. Pass --evals-dir or set AI_PC_EVALS_DIR to the unzipped "
            "package; without it, skip the benchmark and say so in the report."
        )
    d = Path(raw).expanduser().resolve()
    if not (d / "kwengine").is_dir() or not (d / "tools" / "run_eval.py").exists():
        sys.exit(
            f"{d} is not an ai-pc-evals package (no kwengine/ or tools/run_eval.py)."
        )
    return d


def run(
    d: Path, *args: str, check: bool = True, timeout: float = 600
) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run(
        [PYTHON, *args],
        cwd=d,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def bench_dir(run_dir: Path) -> Path:
    p = run_dir / "bench"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cmd_check(a) -> None:
    d = evals_dir(a)
    version = (
        (d / "VERSION").read_text(encoding="utf-8").strip()
        if (d / "VERSION").exists()
        else "?"
    )
    ev = run(d, "tools/evalversion.py", check=False)
    try:
        dirty = json.loads(ev.stdout).get("dirty")
    except ValueError:
        dirty = "unknown"
    keys = run(d, "tools/keys.py", "check", "--deep", check=False)
    graded = keys.returncode == 0 and "decrypted OK" in keys.stdout
    print(f"package  {d}\nversion  {version}\ndirty    {dirty}")
    print(
        "grading  ready"
        if graded
        else "grading  NOT set up: ask the person to run `python -m keyring set ai-pc-evals answers-passphrase` "
        "in a cmd/PowerShell window themselves. Tasks can still run; they stay ungraded until then."
    )
    if dirty not in (False, "false"):
        print(
            "warning: the copy is not clean (dirty != false); results are not comparable."
        )


def cmd_prepare(a) -> None:
    d = evals_dir(a)
    args = ["-m", "kwengine", "prepare", "--suite", a.suite, "--leg", LEG, "--json"]
    if a.tasks:
        args += ["--tasks", a.tasks]
    out = run(d, *args, check=False, timeout=3600)  # builds every task's inputs
    if out.returncode:
        sys.exit(f"kwengine prepare failed:\n{out.stderr or out.stdout}")
    prep = json.loads(out.stdout)
    b = bench_dir(a.run)
    (b / "prepared.json").write_text(
        json.dumps({"suite": a.suite, **prep}, indent=1), encoding="utf-8"
    )
    print(f"kw run dir: {prep['run_dir']}")
    for t in prep["tasks"]:
        pf = b / f"{t['id']}.prompt.txt"
        pf.write_text(t["prompt"], encoding="utf-8")
        print(
            f"\n{t['id']}  (cap {t['max_minutes']} min)\n  folder {t['folder']}\n  prompt {pf}"
        )
    print(
        "\nOne task at a time: a new chat per task, the folder chip set to the task folder, the prompt "
        "pasted whole. Approve tool prompts only inside the task folder."
    )


def get(port: int, path: str) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def ts(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def cmd_record(a) -> None:
    d = evals_dir(a)
    prep = json.loads((bench_dir(a.run) / "prepared.json").read_text(encoding="utf-8"))
    task = next((t for t in prep["tasks"] if t["id"] == a.task), None)
    if not task:
        sys.exit(f"{a.task} was not prepared in {a.run}.")
    if a.outcome == "finished":
        wip = get(a.port, "/api/system/work-in-progress")
        if wip["answers"]:
            sys.exit(
                "An answer is still being written; wait for it (app_state.py wait) before recording."
            )
    sid = a.session
    if not sid:
        sessions = get(a.port, "/api/sessions").get("sessions", [])
        sid = (
            max(sessions, key=lambda s: s.get("updated_at", ""))["id"]
            if sessions
            else None
        )
    if not sid:
        sys.exit("No chat found to record.")
    session = get(a.port, f"/api/sessions/{sid}")
    msgs = get(a.port, f"/api/sessions/{sid}/messages?limit=10000")["messages"]
    users = [m for m in msgs if m["role"] == "user"]
    replies = [m for m in msgs if m["role"] == "assistant"]
    if not users or task["prompt"][:80] not in users[0]["content"]:
        sys.exit(
            f"Chat {sid} does not start with {a.task}'s prompt; pass --session for the right chat."
        )
    if len(users) > 1:
        print(
            f"warning: the chat has {len(users)} user turns; a benchmark task gets one prompt."
        )
    answer = replies[-1]["content"] if replies else ""
    if a.outcome == "finished" and not answer:
        sys.exit(
            "The chat has no answer. Record it with --outcome stalled-asked-human or did-not-run."
        )
    wall = (
        (ts(replies[-1]["created_at"]) - ts(users[0]["created_at"])).total_seconds()
        if replies
        else 0
    )
    af = bench_dir(a.run) / f"{a.task}.answer.md"
    af.write_text(answer, encoding="utf-8")
    steps = sum(
        1
        for m in replies
        for st in (m.get("agent_steps") or [])
        if st.get("type") == "tool"
    )
    version = get(a.port, "/api/system/status").get("version", "")
    args = [
        "-m",
        "kwengine",
        "record",
        prep["run_dir"],
        a.task,
        "--answer-file",
        str(af),
        "--wall-seconds",
        f"{max(wall, 0):.0f}",
        "--harness",
        LEG,
        "--model",
        session.get("effective_model") or session.get("model") or "unknown",
        "--app-version",
        version,
        "--steps",
        str(steps),
        "--outcome",
        a.outcome,
        "--notes",
        f"Agent UI chat {sid}",
    ]
    if a.force:
        args.append("--force")
    out = run(d, *args, check=False)
    if out.returncode:
        sys.exit(f"kwengine record failed:\n{out.stderr or out.stdout}")
    print(
        f"recorded {a.task}: {wall:.0f}s, {steps} tool calls, model {args[args.index('--model') + 1]}"
    )


def approve(prompt: str, folder: str) -> bool:
    """Allow a permission prompt only for the task's own folder.

    It names the folder, or it is a command with no drive path and no `..`. Such a
    command runs in the chat's folder, which this chat already confined to the task
    folder. Denying those failed a task the agent had done right.
    """
    text = prompt.lower().replace("/", "\\")
    if folder.lower().replace("/", "\\") in text:
        return True
    asked = text.split("details")[0].split("?", 1)[-1]
    return ":\\" not in asked and "\\\\" not in asked and ".." not in text


def cmd_drive(a) -> None:
    """Run every prepared task through the window over DevTools, one chat each, then record it."""
    import time

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cdp

    prep = json.loads((bench_dir(a.run) / "prepared.json").read_text(encoding="utf-8"))
    log = []
    for task in prep["tasks"]:
        page = cdp.Page(a.cdp)
        if not page.eval(cdp.CLICK_JS % json.dumps("New chat")):
            sys.exit(
                "No 'New chat' button: is the window on the chat screen, in English?"
            )
        time.sleep(2)
        page.eval("document.querySelector('textarea').focus()")
        page.call("Input.insertText", {"text": task["prompt"]})
        for kind in ("keyDown", "keyUp"):
            page.call(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                },
            )
        deadline = time.time() + task["max_minutes"] * 60
        time.sleep(5)
        while time.time() < deadline:
            prompt = page.eval(
                "(() => { const d = document.querySelector('[class*=permission], [role=alertdialog]');"
                " return d ? d.innerText : '' })()"
            )
            if prompt:
                ok = approve(prompt, task["folder"])
                labels = ["Allow for this chat", "Allow once"] if ok else ["Deny"]
                choice = next(
                    (lb for lb in labels if page.eval(cdp.CLICK_JS % json.dumps(lb))),
                    None,
                )
                if choice is None:
                    sys.exit(
                        f"{task['id']}: a prompt has none of {labels}:\n{prompt[:300]}"
                    )
                log.append(
                    {"task": task["id"], "prompt": prompt[:400], "choice": choice}
                )
                print(f"{task['id']}: {choice}: {prompt.splitlines()[0][:80]}")
                time.sleep(3)
                continue
            if not get(a.port, "/api/system/work-in-progress")["answers"]:
                break
            time.sleep(4)
        page.close()
        timed_out = time.time() >= deadline
        print(f"{task['id']}: {'TIMED OUT' if timed_out else 'answered'}")
        cmd_record(
            argparse.Namespace(
                run=a.run,
                task=task["id"],
                port=a.port,
                session=None,
                force=a.force,
                outcome="stalled-asked-human" if timed_out else "finished",
                evals_dir=a.evals_dir,
            )
        )
    (bench_dir(a.run) / "approvals.json").write_text(
        json.dumps(log, indent=1), encoding="utf-8"
    )
    print(
        f"Every approval and denial: {bench_dir(a.run) / 'approvals.json'}. Close the app, then grade."
    )


def cmd_grade(a) -> None:
    d = evals_dir(a)
    prep = json.loads((bench_dir(a.run) / "prepared.json").read_text(encoding="utf-8"))
    started = dt.datetime.now().timestamp()
    # The judge grades every task in turn; a full suite takes hours, not a day.
    out = run(
        d,
        "tools/run_eval.py",
        "--grade-only",
        prep["run_dir"],
        check=False,
        timeout=8 * 3600,
    )
    print(out.stdout[-3000:])
    if out.returncode:
        msg = out.stderr or out.stdout
        if "passphrase" in msg.lower():
            sys.exit(
                "Not graded: no answers passphrase. Ask the person to set it; record the benchmark as "
                "not-graded in the report. Never estimate a score."
            )
        sys.exit(f"Grading failed:\n{msg[-2000:]}")
    # The grader names its own output folder; take it from there rather than assume one.
    where = re.search(r"^scorecard and summary:\s*(.+)$", out.stdout, re.M)
    summary = Path(where.group(1).strip()) / "summary.md" if where else None
    if not summary or not summary.exists() or summary.stat().st_mtime < started - 5:
        sys.exit(
            "Graded, but the output names no fresh summary.md; read the grader's output above "
            "and report the benchmark from it."
        )
    headline = re.search(
        r"^\*\*(\d+/\d+ passed)", summary.read_text(encoding="utf-8"), re.M
    )
    print(f"summary: {summary}")
    print(f"headline: {headline.group(1) if headline else '(none: read the summary)'}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--evals-dir", help="Unzipped ai-pc-evals package (or AI_PC_EVALS_DIR)"
    )
    ap.add_argument(
        "--python",
        default=sys.executable,
        help="Python with the package's requirements",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sp = sub.add_parser("prepare")
    sp.add_argument("run", type=Path)
    sp.add_argument("--suite", default="smoke")
    sp.add_argument("--tasks", help="Comma-separated ids or globs")
    sp.set_defaults(fn=cmd_prepare)
    sp = sub.add_parser("record")
    sp.add_argument("run", type=Path)
    sp.add_argument("task")
    env_port = os.environ.get("GAIA_UI_PORT")
    sp.add_argument(
        "--port",
        type=int,
        default=int(env_port) if env_port else None,
        required=not env_port,
        help="The test app's backend port (or GAIA_UI_PORT); never the person's own app",
    )
    sp.add_argument(
        "--session", help="Chat id (default: the most recently updated chat)"
    )
    sp.add_argument(
        "--outcome",
        default="finished",
        choices=("finished", "stalled-asked-human", "did-not-run"),
    )
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_record)
    sp = sub.add_parser(
        "drive", help="Run the prepared tasks through the window over DevTools"
    )
    sp.add_argument("run", type=Path)
    sp.add_argument(
        "--cdp",
        type=int,
        required=True,
        help="DevTools port (launch_isolated.py --cdp)",
    )
    sp.add_argument(
        "--port",
        type=int,
        default=int(env_port) if env_port else None,
        required=not env_port,
        help="The test app's backend port (or GAIA_UI_PORT)",
    )
    sp.add_argument(
        "--force", action="store_true", help="Replace results already recorded"
    )
    sp.set_defaults(fn=cmd_drive)
    sp = sub.add_parser("grade")
    sp.add_argument("run", type=Path)
    sp.set_defaults(fn=cmd_grade)
    a = ap.parse_args()
    global PYTHON
    PYTHON = a.python
    a.fn(a)


if __name__ == "__main__":
    main()
