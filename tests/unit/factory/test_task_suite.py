# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Prove each task's verifier discriminates before any model is scored.

A benchmark is only as trustworthy as the thing deciding pass from fail, and
that thing can be wrong in two directions:

* **Passes the untouched setup** — the task is already done before the agent
  starts, and every arm scores a free point.
* **Fails a correct solution** — no arm can ever pass, and the task silently
  measures nothing.

Each task is therefore run twice here: once against its bare setup (must fail)
and once against a known-good solution written by hand (must pass). Both
directions have to hold or the task is not fit to score anyone.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from oracles_extra import ORACLES_EXTRA, ORACLES_JUDGED

from gaia.factory.tasks.suite import BY_KEY, TASKS

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="task verifiers are shell commands"
)

#: A hand-written correct solution per task: the files a competent engineer
#: would leave behind. If a verifier rejects one of these, the verifier is
#: wrong, not the solution.
ORACLES = {
    "bug_fix_parser": {
        "config.py": '''\
def parse_config(text):
    """Parse KEY=VALUE lines into a dict. Blank lines and # comments are skipped."""
    out = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out
''',
    },
    "feature_dry_run": {
        "cleanup.py": '''\
import os
import sys


def cleanup(directory, dry_run=False):
    """Delete .tmp files in *directory*, or report them when *dry_run*."""
    removed = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".tmp"):
            if not dry_run:
                os.remove(os.path.join(directory, name))
            removed.append(name)
    return removed


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    for name in cleanup(args[0] if args else ".", dry_run=dry):
        print(f"would remove {name}" if dry else f"removed {name}")
''',
    },
    "test_authoring_slug": {
        "test_slugify.py": """\
from slugify import slugify


def test_lowercases():
    assert slugify("Hello") == "hello"


def test_strips_punctuation():
    assert slugify("Hello, World!") == "hello-world"


def test_collapses_whitespace():
    assert slugify("a   b") == "a-b"


def test_trims_edges():
    assert slugify("  spaced  ") == "spaced"
""",
    },
    "data_analysis_builds": {"answer.txt": "400.0\n"},
    "refactor_duplication": {
        "report.py": """\
def _stamp(d):
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def daily_header(d):
    return f"{_stamp(d)} daily"


def weekly_header(d):
    return f"{_stamp(d)} weekly"


def monthly_header(d):
    return f"{_stamp(d)} monthly"
""",
    },
    "doc_authoring_readme": {
        "README.md": """\
# tickle

Watch a directory and run a command whenever a file under it changes.

## Install

    pip install tickle

## Usage

    from tickle import watch

    watch("src", "pytest -q")

Every time a file under `src` changes, the command runs again. Use the
`interval` argument to change how often the directory is polled.
""",
    },
    "code_review_planted": {
        "review.md": """\
# Review of auth.py

- `hash_password` uses MD5, which is fast and unsuitable for passwords. Use a
  slow KDF such as bcrypt, scrypt or argon2.
- `verify` compares digests with `==`, which is not constant time and leaks
  information through timing. Use `hmac.compare_digest`.
- `get_user` builds SQL by string formatting, which allows SQL injection. Use a
  parameterised query.
""",
    },
    "qa_repo_purpose": {
        "answer.txt": "Python >=3.11, from requires-python in pyproject.toml.\n",
    },
    # The widened suite's solutions live beside this file; same contract.
    **ORACLES_EXTRA,
    **ORACLES_JUDGED,
}


def _run(task, extra_files):
    """Materialise the task, overlay *extra_files*, return the verifier's code."""
    ws = Path(tempfile.mkdtemp(prefix="verify-check-"))
    try:
        for rel, body in task.setup.items():
            dest = ws / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Matches the runner: no platform newline translation, so the
            # workspace here is byte-identical to the one an arm gets.
            dest.write_text(body, encoding="utf-8", newline="")
        # The repo is created from the SETUP alone, so the starting state is the
        # one the prompt describes: artefacts tracked. The solution is applied
        # afterwards, exactly as an agent would apply it.
        if task.git_init:
            from gaia.factory.tasks.runner import _git_init

            _git_init(ws)
        for rel, body in extra_files.items():
            dest = ws / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8", newline="")
        if task.git_init and extra_files:
            subprocess.run(
                [
                    "git",
                    "rm",
                    "-r",
                    "--cached",
                    "-q",
                    "--ignore-unmatch",
                    ".env",
                    "build",
                ],
                cwd=str(ws),
                capture_output=True,
                text=True,
            )
        proc = subprocess.run(
            [shutil.which("bash"), "-c", task.verify],
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc.returncode, (proc.stdout + proc.stderr)[-1500:]
    finally:
        shutil.rmtree(ws, ignore_errors=True)


@pytest.mark.parametrize("key", sorted(BY_KEY))
def test_verifier_rejects_the_untouched_setup(key):
    """Nothing is done yet, so nothing may pass."""
    code, output = _run(BY_KEY[key], {})
    assert code != 0, (
        f"{key}: the verifier passes before the agent does any work, so every "
        f"arm scores a free point on it.\n{output}"
    )


@pytest.mark.parametrize("key", sorted(BY_KEY))
def test_verifier_accepts_a_correct_solution(key):
    """A competent solution must be recognised as one."""
    code, output = _run(BY_KEY[key], ORACLES[key])
    assert code == 0, (
        f"{key}: the verifier rejects a hand-written correct solution, so no "
        f"arm can ever pass it.\n{output}"
    )


def test_child_environment_contains_the_agent_in_its_sandbox():
    """The agent's idea of "home" must land inside the sandbox, not the developer's.

    ``allowed_paths`` gates reads and writes but not search: ``find_files``
    defaults to a scope that walks the current directory, then home, then
    previously indexed directories. Before this was contained, a task run
    reached out of its workspace and answered about the real GAIA checkout —
    so the benchmark was measuring the machine it ran on.
    """
    import subprocess
    import sys

    from gaia.factory.tasks.runner import child_env

    sandbox = Path(tempfile.mkdtemp(prefix="isolation-check-"))
    try:
        env = child_env(sandbox)
        for var in ("HOME", "USERPROFILE", "GAIA_HOME"):
            assert Path(env[var]).is_relative_to(sandbox), f"{var} escapes the sandbox"

        # Resolved by a real interpreter under that environment, because
        # Path.home() consults the variables in a platform-specific order that
        # asserting on the dict alone would not catch.
        proc = subprocess.run(
            [sys.executable, "-c", "import pathlib; print(pathlib.Path.home())"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        resolved = Path(proc.stdout.strip())
        assert resolved.is_relative_to(sandbox), (
            f"a child process resolves home to {resolved}, outside the sandbox — "
            "file search would escape into the developer's own files"
        )
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_instrumentation_never_counts_as_the_agents_work():
    """The harness's own log must not reach the judge as a submitted file.

    It did once. ``GAIA_TURN_LOG`` was a relative path, so the turn recorder
    wrote into the agent's working directory; the file then counted as created
    output and, at 12,000 characters, consumed the judge's entire budget. On
    ``refactor_duplication`` the judge scored two arms on that log and never saw
    the 263-character source file it was meant to review.
    """
    from gaia.factory.tasks.runner import INSTRUMENTATION, child_env

    sandbox = Path(tempfile.mkdtemp(prefix="instrumentation-check-"))
    try:
        turn_log = Path(child_env(sandbox)["GAIA_TURN_LOG"])
        assert turn_log.is_absolute(), "a relative log path lands in the workspace"
        assert not turn_log.is_relative_to(sandbox / "workspace"), (
            f"the turn log is written to {turn_log}, inside the agent's "
            "workspace, where it is indistinguishable from the agent's output"
        )
        assert (
            turn_log.name in INSTRUMENTATION
        ), "the log is excluded from artefacts by filename; keep the two in sync"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_expected_files_survive_a_crowded_submission():
    """The file under review is rendered even when a large incidental exists."""
    from gaia.factory.tasks.quality import render_submission

    episode = {
        "artefacts": {
            "noise.log": "x" * 50_000,
            "report.py": "def stamp(d): return d.isoformat()\n",
        }
    }
    rendered = render_submission(episode, expected=("report.py",))
    assert "--- report.py ---" in rendered
    assert "def stamp" in rendered, (
        "the reviewed file was crowded out by an incidental one — the exact "
        "failure that scored an arm on a log file"
    )


def test_every_task_has_an_oracle():
    missing = sorted({t.key for t in TASKS} - set(ORACLES))
    assert not missing, f"tasks with no proven-good solution: {missing}"


def test_task_keys_are_unique():
    keys = [t.key for t in TASKS]
    assert len(keys) == len(set(keys))
