# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia project trust`` CLI and unattended -p guidance notice."""

from __future__ import annotations

import json

import pytest

from gaia import project_trust_cli
from gaia.agents.base.agent import Agent
from gaia.agents.base.project_map import (
    PROJECT_ROOT_ENV,
    ProjectMapMixin,
    clear_project_map_cache,
)
from gaia.project_trust import (
    DECLINED,
    TRUSTED,
    folder_key,
    folder_trust,
    remember_folder_trust,
    trust_store_path,
)

CLAUDE_MODEL = "anthropic/claude-opus-5"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    monkeypatch.delenv("GAIA_PROJECT_CONTEXT", raising=False)
    clear_project_map_cache()
    yield
    clear_project_map_cache()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "my_repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "CLAUDE.md").write_text("# Instructions\n", encoding="utf-8")
    return root


class _FakeConsole:
    def __init__(self, answer=None):
        self.answer = answer
        self.asked = []
        self.full_access = False
        self.background_mode = False
        self.nobody_answers = False
        self.blocking_confirmation = False

    def auto_approve_confirmations_enabled(self):
        return False

    def confirm_tool_execution(self, tool, args):
        self.asked.append((tool, args))
        return bool(self.answer)

    def confirmation_timed_out(self, tool):
        return False


class _Base:
    def _on_task_start(self, user_input: str) -> None:
        pass


class _FakeAgent(ProjectMapMixin, _Base):
    _path_access_grants_unasked = Agent._path_access_grants_unasked

    def __init__(self, root, console, project_context="auto", model=CLAUDE_MODEL):
        self.config = type(
            "C",
            (),
            {
                "project_root": str(root),
                "auto_index": False,
                "project_context": project_context,
            },
        )()
        self.model_id = model
        self.console = console
        self._tools_registry = {}


def test_cli_list_empty(capsys):
    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "list"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No saved project trust answers" in out


def test_cli_list_populated(repo, tmp_path, capsys):
    remember_folder_trust(repo, TRUSTED)
    other = tmp_path / "other"
    other.mkdir()
    remember_folder_trust(other, DECLINED)

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "list"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "DECISION" in out
    assert "SAVED AT" in out
    assert folder_key(repo) in out
    assert "trusted" in out
    assert "declined" in out


def test_cli_list_marks_missing_folder(repo, tmp_path, capsys):
    gone = tmp_path / "deleted_folder"
    gone.mkdir()
    remember_folder_trust(gone, TRUSTED)
    gone.rmdir()

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "list"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[missing]" in out


def test_cli_list_json(repo, capsys):
    remember_folder_trust(repo, TRUSTED)

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "list", "--json"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["folder"] == folder_key(repo)
    assert data[0]["decision"] == TRUSTED
    assert data[0]["exists"] is True
    assert data[0]["at"]


def test_cli_list_corrupt_store_names_file_on_stderr(capsys):
    store = trust_store_path()
    store.write_text("{broken json", encoding="utf-8")

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "list"])
    rc = project_trust_cli.handle(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "project_trust.json" in err


def test_cli_add_and_decline(repo, capsys):
    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "add", str(repo)])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Trusted folder" in out
    assert folder_trust(repo) == TRUSTED

    args = parser.parse_args(["project", "trust", "decline", str(repo)])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Declined folder" in out
    assert folder_trust(repo) == DECLINED


def test_cli_forget_single_folder(repo, capsys):
    remember_folder_trust(repo, TRUSTED)
    assert folder_trust(repo) == TRUSTED

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "forget", str(repo)])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Forgot trust answer" in out
    assert folder_trust(repo) is None


def test_cli_forget_missing(repo, tmp_path, capsys):
    gone = tmp_path / "to_delete"
    gone.mkdir()
    remember_folder_trust(repo, TRUSTED)
    remember_folder_trust(gone, DECLINED)
    gone.rmdir()

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "forget", "--missing"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Forgot 1 missing folder" in out
    assert folder_trust(gone) is None
    assert folder_trust(repo) == TRUSTED


def test_cli_forget_all_with_yes(repo, tmp_path, capsys):
    remember_folder_trust(repo, TRUSTED)
    other = tmp_path / "other"
    other.mkdir()
    remember_folder_trust(other, DECLINED)

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "forget", "--all", "-y"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Forgot 2 saved folder answer(s)" in out
    assert folder_trust(repo) is None
    assert folder_trust(other) is None


def test_cli_forget_all_prompt_cancel(repo, monkeypatch, capsys):
    remember_folder_trust(repo, TRUSTED)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["project", "trust", "forget", "--all"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Cancelled" in out
    assert folder_trust(repo) == TRUSTED


def test_cli_alias_gaia_trust(repo, capsys):
    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)

    args = parser.parse_args(["trust", "add", str(repo)])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    assert folder_trust(repo) == TRUSTED

    args = parser.parse_args(["trust", "list"])
    rc = project_trust_cli.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert folder_key(repo) in out


def test_unattended_skips_with_stderr_notice(repo, capsys):
    console = _FakeConsole()
    agent = _FakeAgent(repo, console)
    agent._on_task_start("check")

    err = capsys.readouterr().err
    assert "has guidance files" in err
    assert "Run 'gaia project trust add' to load them without --full-access" in err
    assert str(repo.resolve()) in err
    assert agent._full_project_context() is False


def test_guidance_loads_after_trust_add(repo, capsys):
    console = _FakeConsole()
    agent = _FakeAgent(repo, console)
    agent._on_task_start("first")
    assert agent._full_project_context() is False

    remember_folder_trust(repo, TRUSTED)
    agent2 = _FakeAgent(repo, console)
    agent2._on_task_start("second")
    assert agent2._full_project_context() is True


def _parse(argv):
    parser = project_trust_cli.argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action")
    project_trust_cli.add_subparser(subparsers)
    return parser.parse_args(argv)


@pytest.mark.parametrize("prefix", [["project", "trust"], ["trust"]])
def test_cli_forget_a_deleted_folder_by_its_path(tmp_path, capsys, prefix):
    gone = tmp_path / "gone_repo"
    gone.mkdir()
    remember_folder_trust(gone, TRUSTED)
    gone.rmdir()

    rc = project_trust_cli.handle(_parse([*prefix, "forget", str(gone)]))

    assert rc == 0
    assert "Forgot trust answer" in capsys.readouterr().out
    assert folder_trust(gone) is None


def test_cli_forget_an_unknown_deleted_path_changes_nothing(repo, tmp_path, capsys):
    remember_folder_trust(repo, TRUSTED)

    rc = project_trust_cli.handle(
        _parse(["trust", "forget", str(tmp_path / "never_existed")])
    )

    assert rc == 0
    assert "No saved answer" in capsys.readouterr().out
    assert folder_trust(repo) == TRUSTED


@pytest.mark.parametrize("prefix", [["project", "trust"], ["trust"]])
def test_an_agent_started_process_cannot_trust_a_folder(
    repo, monkeypatch, capsys, prefix
):
    from gaia.skills.human_only import AGENT_CHILD_ENV

    monkeypatch.setenv(AGENT_CHILD_ENV, "1")

    rc = project_trust_cli.handle(_parse([*prefix, "add", str(repo)]))

    assert rc != 0
    assert "only you can trust a folder" in capsys.readouterr().err
    assert folder_trust(repo) is None


def test_an_agent_tool_in_process_cannot_trust_a_folder(repo, monkeypatch, capsys):
    from gaia.skills.human_only import AGENT_CHILD_ENV, agent_tool_running

    monkeypatch.delenv(AGENT_CHILD_ENV, raising=False)
    with agent_tool_running():
        rc = project_trust_cli.handle(_parse(["trust", "add", str(repo)]))

    assert rc != 0
    assert folder_trust(repo) is None


def test_an_agent_cannot_flip_a_declined_folder_to_trusted(repo, monkeypatch):
    from gaia.skills.human_only import AGENT_CHILD_ENV

    remember_folder_trust(repo, DECLINED)
    monkeypatch.setenv(AGENT_CHILD_ENV, "1")

    project_trust_cli.handle(_parse(["trust", "add", str(repo)]))

    assert folder_trust(repo) == DECLINED


def test_add_from_a_subfolder_trusts_the_repository(repo, monkeypatch):
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    assert project_trust_cli.handle(_parse(["trust", "add"])) == 0

    assert folder_trust(repo) == TRUSTED
    assert folder_trust(sub) is None


def test_add_does_not_climb_to_a_parent_that_only_has_guidance(tmp_path, monkeypatch):
    parent = tmp_path / "work"
    child = parent / "notes"
    child.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("# hostile\n", encoding="utf-8")
    monkeypatch.chdir(child)

    assert project_trust_cli.handle(_parse(["trust", "add"])) == 0

    assert folder_trust(child) == TRUSTED
    assert folder_trust(parent) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["trust", "list"],
        ["project", "trust", "list", "--json"],
        ["trust", "add"],
        ["trust", "forget", "--missing"],
    ],
)
def test_gaia_parser_routes_trust_commands(argv):
    from gaia.cli import build_parser

    args = build_parser().parse_args(argv)

    assert args.action in ("project", "trust")
    assert args.trust_action == argv[argv.index("trust") + 1]
