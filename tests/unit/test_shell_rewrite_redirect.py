# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A refused file rewrite has to name the tool that can do the job.

`sed`, `awk`, `tee` and friends are not on ALLOWED_COMMANDS, so an agent that
reaches for one to change a file is refused either way. The problem was the
message: "only read-only, informational commands are allowed", followed by
read-only examples. That is a dead end — the agent wanted to change a file and
nothing in the refusal points at anything that can.

Measured on 30 corpus moments whose correct next action was an edit: the agent
shelled out on 19 of them (#3600).
"""

import pytest

from gaia.agents.tools.shell_tools import FILE_REWRITE_BINARIES, ShellToolsMixin

validate = ShellToolsMixin._validate_command


class TestARefusedRewritePointsAtTheEditTools:
    @pytest.mark.parametrize("binary", sorted(FILE_REWRITE_BINARIES))
    def test_every_rewrite_binary_names_edit_file(self, binary):
        result = validate(binary, [binary, "x"], f"{binary} x")
        assert result is not None, f"{binary} should still be refused"
        blob = " ".join(str(v) for v in result.values())
        assert "edit_file" in blob, f"{binary}'s refusal must name edit_file"

    def test_it_mentions_the_python_variant_and_file_creation(self):
        result = validate("sed", ["sed", "-i", "s/a/b/", "f.py"], "sed -i s/a/b/ f.py")
        blob = " ".join(str(v) for v in result.values())
        assert "edit_python_file" in blob
        assert "write_file" in blob

    def test_it_is_still_a_refusal_not_a_pass(self):
        """The command must not become runnable — only better explained."""
        result = validate("sed", ["sed", "-i", "s/a/b/", "f"], "sed -i s/a/b/ f")
        assert result["status"] == "error"
        assert result["has_errors"] is True


class TestNothingElseChanged:
    def test_an_unrelated_blocked_command_keeps_the_generic_message(self):
        result = validate("nmap", ["nmap", "-p", "80", "h"], "nmap -p 80 h")
        assert result is not None
        assert "not in the allowed list" in result["error"]
        assert "edit_file" not in " ".join(str(v) for v in result.values())

    def test_an_allowed_command_still_passes(self):
        assert validate("ls", ["ls", "-la"], "ls -la") is None

    def test_no_rewrite_binary_was_accidentally_allowlisted(self):
        """This guard only changes the message; it must never grant the binary."""
        from gaia.agents.tools.shell_tools import ALLOWED_COMMANDS

        assert not (FILE_REWRITE_BINARIES & ALLOWED_COMMANDS)
