# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A command that refuses to act must not report success.

`gaia kill` with no target printed a ❌, did nothing, and exited 0 — so
`gaia kill && next-step` ran next-step having killed nothing. Same shape as
a remedy that names a command which cannot work: the user (or script) is told
everything is fine when it is not.

These run the real CLI in a subprocess, because the exit code IS the thing
under test — calling the handler directly would not catch it.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_gaia(*argv):
    """Run `gaia <argv>` in-process-isolated and return the CompletedProcess."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(Path.home()),
        "PYTHONPATH": str(REPO_ROOT / "src"),
        # Keep the refusal paths from touching a real server.
        "LEMONADE_BASE_URL": "http://localhost:1/api/v1",
    }
    return subprocess.run(
        [sys.executable, "-m", "gaia.cli", *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    "argv, must_mention",
    [
        (["kill"], ["--lemonade", "--port"]),
        (["cache", "clear"], ["--context7", "--all"]),
    ],
)
def test_refusal_exits_non_zero_and_names_the_accepted_flags(argv, must_mention):
    result = _run_gaia(*argv)
    output = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"`gaia {' '.join(argv)}` refused to act but exited 0 — "
        f"`gaia {' '.join(argv)} && next-step` would run next-step.\n{output}"
    )
    for flag in must_mention:
        assert flag in output, f"refusal does not name {flag}:\n{output}"


def test_kill_refusal_sets_expectations_about_what_it_can_target():
    """ "Specify --lemonade or --port" sent users in circles when a stray agent
    process held no port — the refusal now says both flags target a port."""
    result = _run_gaia("kill")
    output = result.stdout + result.stderr

    assert "port" in output.lower()
    assert "PID" in output, f"refusal should point at the by-PID route:\n{output}"


def test_kill_lemonade_stays_chainable_when_nothing_is_running():
    """`gaia kill --lemonade && ...` is a documented idiom
    (docs/reference/troubleshooting.mdx). "Already stopped" is the desired end
    state, so it must NOT be turned into a failure — only the no-target
    refusal is an error."""
    result = _run_gaia("kill", "--port", "1")  # nothing listens on port 1

    assert result.returncode == 0, (
        "killing a port with no process must stay chainable; making it "
        "non-zero would break the documented `gaia kill --lemonade && ...`"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
