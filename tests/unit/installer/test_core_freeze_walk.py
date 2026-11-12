# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The freeze smoke test starts every command the CLI registers.

In v0.26.0-rc18 five commands could not start in the frozen ``gaia``, and each
was found by a person typing it. The smoke test now lists every command from
the frozen binary's own parser and starts each one, so a command added to the
CLI is covered without anyone adding a case. This file checks the parts of
that walk that do not need a frozen build.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path

import pytest

from gaia.cli import build_parser

REPO = Path(__file__).resolve().parents[3]


def _load_smoke():
    spec = importlib.util.spec_from_file_location(
        "core_walk_smoke_test", REPO / "installer" / "core" / "smoke_test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


smoke = _load_smoke()


def _listed() -> list[dict]:
    """What the listing script prints, run here instead of in a frozen gaia."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(smoke.SUBCOMMAND_LISTING, {})  # pylint: disable=exec-used
    return smoke.parse_subcommands(out.getvalue())


COMMANDS = _listed()
STARTED = [c for c in COMMANDS if c["bare"] and smoke.never_started(c["path"]) is None]


def test_the_listing_reaches_nested_and_hidden_commands():
    paths = {" ".join(c["path"]) for c in COMMANDS}
    assert {"talk", "mcp serve", "mcp tui", "api", "schedule run"} <= paths
    # Hidden from `gaia -h`, still a command a script can run.
    assert "hub install" in paths
    assert len(paths) == len(COMMANDS), "an alias was listed twice"


@pytest.mark.parametrize("command", STARTED, ids=lambda c: " ".join(c["path"]))
def test_the_walks_arguments_get_past_the_parser(command):
    """A command the walk cannot get past argparse is never really started."""
    args = smoke.start_args(command)
    try:
        build_parser().parse_args(args)
    except SystemExit as e:
        pytest.fail(f"`gaia {' '.join(args)}` stopped in the parser (exit {e.code})")


def test_every_command_never_started_is_a_real_command():
    """A stale entry would read as coverage that is not there."""
    paths = {" ".join(c["path"]) for c in COMMANDS}
    assert set(smoke.NEVER_STARTED) <= paths


def test_every_known_broken_command_is_one_the_walk_starts():
    started = {" ".join(c["path"]) for c in STARTED}
    assert set(smoke.KNOWN_BROKEN) <= started
    for issue in smoke.KNOWN_BROKEN.values():
        assert issue.startswith("amd/gaia#"), "name the issue that tracks it"


def test_a_command_nobody_listed_is_started():
    assert smoke.never_started(["a-command-added-tomorrow"]) is None
    assert smoke.never_started(["lemonade", "embedded", "start"])
    assert smoke.never_started(["lemonade"]) is None


def test_most_commands_are_started():
    assert len(STARTED) > 0.75 * len(COMMANDS)


@pytest.mark.parametrize(
    "output",
    [
        # rc18: gaia mcp serve, gaia mcp tui, gaia mcp code-index.
        "Error: No module named 'mcp'",
        "ModuleNotFoundError: No module named 'mcp.server'",
        # rc18: gaia talk, whose remedy cannot work in a frozen app.
        "sounddevice is not installed. Run: pip install sounddevice",
        "Traceback (most recent call last):\n  File ...",
        "[PYI-1234:ERROR] Failed to execute script 'gaia_core' due to unhandled",
    ],
)
def test_a_command_that_could_not_load_its_code_fails_the_walk(output):
    assert smoke.broken_start(output)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "Error: the model server at http://127.0.0.1:1 is not answering.",
        "No schedule named 'smoke-walk'.",
        "usage: gaia mcp [-h] {start,stop,status} ...",
    ],
)
def test_a_command_that_started_and_refused_passes_the_walk(output):
    assert smoke.broken_start(output) is None


def test_the_walk_runs_in_the_smoke_test():
    source = (REPO / "installer" / "core" / "smoke_test.py").read_text(encoding="utf-8")
    assert "smoke.subcommands()" in source.split("def main(")[1]


def test_a_child_that_already_exited_does_not_spare_the_rest(monkeypatch):
    """The walk waits on the parent after this; a survivor would hang it."""
    psutil = pytest.importorskip("psutil")
    killed = []

    class _Gone:
        def kill(self):
            raise psutil.NoSuchProcess(1)

    class _Alive:
        def __init__(self, name, children=()):
            self.name, self._children = name, list(children)

        def children(self, recursive=False):
            return self._children

        def kill(self):
            killed.append(self.name)

    parent = _Alive("parent", [_Gone(), _Alive("grandchild")])
    monkeypatch.setattr(psutil, "Process", lambda _pid: parent)
    smoke.Smoke._end_tree(1234)
    assert killed == ["grandchild", "parent"]
