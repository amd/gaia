# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Lint and type-check runners are names for something already permitted.

``python`` is on the allowlist, so ``python -m ruff`` runs today. Refusing a
bare ``ruff`` therefore blocks nothing — it only costs the agent a step and
teaches it that checking its own work is unavailable. These tests pin that
the widening stays exactly that narrow: read-and-report tooling in, anything
that mutates an environment or belongs to another ecosystem out.
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.shell_tools import (
    ALLOWED_COMMANDS,
    PYTHON_CONSOLE_SCRIPTS,
    ShellToolsMixin,
)


class _Host(ShellToolsMixin):
    pass


@pytest.fixture
def run():
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    _Host().register_shell_tools()
    fn = _TOOL_REGISTRY["run_shell_command"]["function"]
    yield fn
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _refused(result) -> bool:
    return result.get("status") == "error"


class TestVerificationToolingIsReachable:
    @pytest.mark.parametrize("binary", sorted(PYTHON_CONSOLE_SCRIPTS))
    def test_each_console_script_is_allowed(self, run, binary):
        # --version is the one flag every one of these accepts, so the check
        # is about the allowlist verdict, not about the tool doing work.
        assert not _refused(run(command=f"{binary} --version", timeout=30))

    def test_every_listed_script_is_actually_on_the_allowlist(self):
        assert PYTHON_CONSOLE_SCRIPTS <= ALLOWED_COMMANDS


class TestTheWideningStaysNarrow:
    @pytest.mark.parametrize(
        "command",
        [
            "pip install requests",  # mutates the environment, reaches the network
            "npm test",  # another ecosystem, not a name for python -m
            "go test ./...",
            "cargo build",
            "make install",
            "rm -rf build",
            "curl https://example.com",
        ],
    )
    def test_commands_outside_the_rationale_stay_refused(self, run, command):
        assert _refused(run(command=command, timeout=10))

    def test_pytest_is_left_to_its_grant_policy(self, run):
        """Naming pytest here would be a no-op — its policy outranks the list.

        Listing it anyway would read as "pytest is allowed" to the next
        person, which is worse than leaving it out.
        """
        assert "pytest" not in PYTHON_CONSOLE_SCRIPTS
        result = run(command="pytest --version", timeout=10)
        assert _refused(result)
        assert "shell:execute:pytest" in (result.get("error") or "")


class TestWhatWeTellTheModelIsTrue:
    """The docstring is the tool schema. A false claim in it is a bug.

    The hint used to read "Only read-only, informational commands are
    allowed" long after python and the git write subcommands were added.
    The agent believed it, stopped trying to verify its work, and nearly
    every benchmark episode ended "unverified". These tests execute every
    claim rather than trusting the prose to stay in sync.
    """

    ADVERTISED = [
        "ls -la",
        "cat a",
        "head a",
        "grep x a",
        "find .",
        "wc -l a",
        "sort a",
        "diff a b",
        "stat a",
        "pwd",
        "git status",
        "git diff",
        "git add a",
        "git stash",
        "python --version",
        "python3 --version",
        "ruff --version",
        "black --version",
        "mypy --version",
        "coverage --version",
        "hostname",
        "whoami",
    ]

    RULED_OUT = [
        "pip install x",
        "npm i",
        "node a.js",
        "go build",
        "cargo run",
        "make",
        "rm a",
        "mv a b",
        "cp a b",
        "mkdir d",
        "touch f",
    ]

    @pytest.mark.parametrize("command", ADVERTISED)
    def test_every_advertised_command_actually_runs(self, run, command):
        assert not _refused(run(command=command, timeout=30))

    @pytest.mark.parametrize("command", RULED_OUT)
    def test_every_command_ruled_out_is_actually_refused(self, run, command):
        assert _refused(run(command=command, timeout=10))

    def test_the_refusal_names_a_way_forward(self, run):
        """An error the agent cannot act on is what caused the give-up."""
        hint = run(command="sed -n 1p a", timeout=10).get("hint") or ""
        assert "python -m pytest" in hint
        assert "write_file" in hint
        # "read-only git" is fine and true; the blanket claim is not.
        assert "Only read-only" not in hint, "the stale, untrue claim is back"
