# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every command the daemon's sidecar errors tell a user to run must actually
work — parsed against the real argparse surface, and for the kill remedy,
EXECUTED against a real process.

A wrong remedy reads perfectly (CLAUDE.md, 4212d526): the failure mode is a
command that exists but means something else, so it survives review and only
fails for the user. Two shipped in this module's uninstall guard at once —
a comma-separated pid list that `kill` rejects outright, and `gaia kill`,
which cannot target an agent sidecar at all.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gaia.daemon.sidecars import install as install_svc

DAEMON_SIDECARS = Path(install_svc.__file__).parent

# Any `gaia ...` invocation embedded in a user-facing string in this package.
_GAIA_CMD = re.compile(r"`gaia ([^`]+)`")


# A message split across adjacent f-string literals leaves source noise
# (`… "\n            f"…`) inside the scraped span; stitch it back together.
_SOURCE_JOIN = re.compile(r'"\s*\n\s*f?"')

# `<id>`, `{agent_id}`, `{self.spec.agent_id}` — a stand-in for a real agent id.
_PLACEHOLDER = re.compile(r"[<{][^>}]*[>}]")


def _emitted_gaia_commands() -> "set[str]":
    found = set()
    for path in sorted(DAEMON_SIDECARS.glob("*.py")):
        for match in _GAIA_CMD.finditer(path.read_text(encoding="utf-8")):
            command = _SOURCE_JOIN.sub("", match.group(1))
            found.add(" ".join(command.split()))
    return found


def _parse(argv: "list[str]"):
    """Parse *argv* with the real CLI parser, or fail with what it said."""
    from gaia.cli import build_parser

    parser = build_parser()
    try:
        return parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on an unknown flag/subcommand
        pytest.fail(f"`gaia {' '.join(argv)}` is not a valid command (exit {exc.code})")


def test_every_gaia_command_named_in_an_error_actually_parses():
    """The class of bug this catches: a command that exists but means something
    else (`gaia install email` → the parser rejects the argument)."""
    commands = _emitted_gaia_commands()
    assert commands, "no remedies found — the scraper regex has drifted"

    checked = 0
    for command in sorted(commands):
        if "*" in command:
            continue  # `gaia hub *` names a command FAMILY, not an invocation
        # `<id>` / `{agent_id}` stand in for an agent; substitute a real one so
        # the required-argument check is exercised rather than skipped.
        argv = [
            "email" if _PLACEHOLDER.fullmatch(tok) else tok for tok in command.split()
        ]
        _parse(argv)
        checked += 1
    assert checked >= 6, f"only {checked} remedies checked — scraper drifted"


def test_gaia_kill_cannot_target_an_agent_sidecar():
    """Pins why `gaia kill` is not the remedy: its whole surface is
    --port/--lemonade, so there is no invocation that finds a stray sidecar by
    install path. (The message itself is asserted further down.)"""
    args = _parse(["kill"])
    assert getattr(args, "port", None) is None
    assert getattr(args, "lemonade", False) is False

    from gaia.cli import build_parser

    kill_flags = {
        action.dest
        for action in build_parser()
        ._subparsers._group_actions[0]
        .choices["kill"]
        ._actions
    }
    assert not kill_flags & {"agent", "agent_id", "pid", "path"}


def test_kill_command_is_space_separated_not_comma_separated():
    """`kill 1, 2` fails with `kill: illegal pid: 1,` — the list must be
    space-separated to be paste-able."""
    command = install_svc.kill_command([88154, 88156, 95249])
    assert "," not in command
    if os.name != "nt":
        assert command == "kill -9 88154 88156 95249"


@pytest.mark.skipif(os.name == "nt", reason="POSIX kill semantics")
def test_the_kill_remedy_actually_kills_a_real_process():
    """Run the exact string the error hands the user. Nothing else proves it:
    the shipped `kill <pids>` parsed fine in review and still failed twice —
    once on the commas, once because these processes ignore SIGTERM."""
    procs = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        for _ in range(2)
    ]
    pids = [p.pid for p in procs]
    try:
        command = install_svc.kill_command(pids)
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        assert result.returncode == 0, f"{command!r} failed: {result.stderr}"
        assert not result.stderr.strip(), f"{command!r} complained: {result.stderr}"

        deadline = time.monotonic() + 5
        for proc in procs:
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.05)
            assert proc.poll() is not None, f"pid {proc.pid} survived {command!r}"
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="POSIX kill semantics")
def test_the_old_comma_separated_remedy_really_does_fail():
    """Pins WHY this changed: the previous string is not merely ugly, the shell
    rejects the comma'd pids. Guards against someone 'tidying' it back.

    Note the exit code is NOT the tell — bash's builtin complains on stderr and
    still exits 0, while zsh reports `illegal pid` and exits 3. What is
    portable is that the comma'd pid is not killed, so a user who pastes it
    watches the uninstall fail again with the same message.
    """
    first, second = (
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        for _ in range(2)
    )
    try:
        result = subprocess.run(
            f"kill {first.pid}, {second.pid}",  # the shipped ", ".join() form
            shell=True,
            capture_output=True,
            text=True,
        )
        assert result.stderr.strip(), "the shell should complain about the comma"
        time.sleep(0.3)
        assert first.poll() is None, "the comma'd pid should NOT have been killed"
    finally:
        for proc in (first, second):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


def test_the_stray_process_error_names_the_working_command_and_sigkill():
    """The message itself, not just the helper."""
    from gaia.daemon.sidecars.errors import StopFailedError

    class _Proc:
        def __init__(self, pid, exe):
            self.info = {"pid": pid, "exe": exe, "cmdline": [exe]}

    install_dir = Path("/tmp/gaia-test-agents/email")
    fake = [
        _Proc(4242, str(install_dir / "email-agent")),
        _Proc(4243, str(install_dir / "email-agent")),
    ]
    import gaia.daemon.sidecars.install as mod

    real_psutil = __import__("psutil")
    original = real_psutil.process_iter
    real_psutil.process_iter = lambda attrs=None: fake
    try:
        with pytest.raises(StopFailedError) as exc_info:
            mod.assert_no_live_process_in(install_dir, "email")
    finally:
        real_psutil.process_iter = original

    detail = str(exc_info.value)
    assert "kill -9 4242 4243" in detail
    assert "gaia kill" not in detail  # the remedy that cannot do the job
    assert "SIGTERM" in detail
