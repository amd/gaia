# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""How far an "always allow" answer is allowed to reach.

The rule these pin: a grant may never exceed the call the prompt showed. One
keypress on a prompt about `gh auth token` must not hand over the shell for the
rest of the session — that is what bypass mode is for, and bypass mode is
explicit and indicated.
"""

import pytest

from gaia.agents.base.tool_grants import grant_scope


class TestShellGrantsAreScopedToTheCommand:
    @pytest.mark.parametrize(
        "command,expected",
        [
            ("gh auth token", "gh auth token"),
            ("gh issue list --limit 5", "gh issue list"),
            ("gh issue view 123", "gh issue view"),
            ("git commit -m 'wip'", "git commit"),
            ("ls", "ls"),
            ("echo hello", "echo hello"),
            # A path'd binary is still just the binary.
            ("/usr/bin/gh issue list", "gh issue list"),
            ("C:\\\\tools\\\\gh.exe issue list", "gh issue list"),
        ],
    )
    def test_scope_is_the_binary_and_its_subcommand(self, command, expected):
        scope = grant_scope("run_shell_command", {"command": command})
        assert scope is not None
        assert scope.label == expected
        assert scope.key == f"run_shell_command:{expected}"

    def test_a_grant_does_not_cover_a_different_subcommand(self):
        granted = grant_scope("run_shell_command", {"command": "gh issue list"})
        other = grant_scope("run_shell_command", {"command": "gh pr merge 12"})
        assert granted.key != other.key

    def test_a_grant_does_not_cover_a_different_binary(self):
        granted = grant_scope("run_shell_command", {"command": "gh issue list"})
        other = grant_scope("run_shell_command", {"command": "rm -rf build"})
        assert other is None or granted.key != other.key

    def test_arguments_do_not_split_the_grant(self):
        """`gh issue view 1` and `... 2` are the same permission decision."""
        one = grant_scope("run_shell_command", {"command": "gh issue view 1"})
        two = grant_scope("run_shell_command", {"command": "gh issue view 2"})
        assert one.key == two.key


class TestUngrantableCommands:
    """Where no honest narrow scope exists, "always" is not offered at all."""

    @pytest.mark.parametrize(
        "command,why",
        [
            ("bash -c 'rm -rf /'", "a shell runs whatever it is handed"),
            ("sh -c whoami", "same"),
            ("powershell -Command Get-Location", "same"),
            ("pwsh -c ls", "same"),
            ("python -c 'import os'", "an interpreter bounds nothing"),
            ("node -e 1", "same"),
            ("npx some-package", "fetches and runs arbitrary code"),
            ("sudo apt install x", "escalates"),
            ("env FOO=1 rm -rf /", "the binary is not the first word"),
            ("xargs rm", "runs what it is piped"),
            ("gh issue list | sh", "a pipe runs more than one thing"),
            ("echo hi && rm -rf x", "so does a conjunction"),
            ("cat x; rm y", "and a semicolon"),
            ("gh issue list > /etc/passwd", "a redirect changes the effect"),
            ("echo `rm -rf /`", "command substitution"),
            ("echo $(rm -rf /)", "the other spelling"),
            ("git -C /elsewhere commit", "a flag redirects the target"),
            ("gh 'unbalanced", "an unreadable command cannot be scoped"),
            ("", "nothing to scope"),
        ],
    )
    def test_no_grant_is_offered(self, command, why):
        assert grant_scope("run_shell_command", {"command": command}) is None, why

    def test_a_tool_with_no_scope_rule_gets_no_grant(self):
        """The default is no "always" — not a tool-wide one."""
        assert grant_scope("send_now", {"to": "a@b.com"}) is None
        assert grant_scope("some_future_tool", {"x": 1}) is None


class TestPathGrants:
    def test_scope_is_the_exact_file(self):
        scope = grant_scope("write_file", {"file_path": "/tmp/a/notes.md"})
        assert scope.key == "write_file:/tmp/a/notes.md"
        assert "notes.md" in scope.label

    def test_a_grant_does_not_cover_a_sibling_file(self):
        one = grant_scope("write_file", {"file_path": "/tmp/a/notes.md"})
        two = grant_scope("write_file", {"file_path": "/tmp/a/secrets.env"})
        assert one.key != two.key

    def test_the_same_file_two_ways_is_one_grant(self):
        one = grant_scope("write_file", {"file_path": "/tmp/a/./notes.md"})
        two = grant_scope("write_file", {"file_path": "/tmp/a/notes.md"})
        assert one.key == two.key

    def test_a_grant_does_not_cross_tools(self):
        write = grant_scope("write_file", {"file_path": "/tmp/x"})
        edit = grant_scope("edit_file", {"file_path": "/tmp/x"})
        assert write.key != edit.key


class TestGrantsAreAPureFunctionOfTheCall:
    def test_the_same_call_always_produces_the_same_key(self):
        args = {"command": "gh issue list"}
        assert (
            grant_scope("run_shell_command", args).key
            == grant_scope("run_shell_command", dict(args)).key
        )

    def test_malformed_args_do_not_raise(self):
        for bad in (None, [], "command", 7):
            assert grant_scope("run_shell_command", bad) is None
