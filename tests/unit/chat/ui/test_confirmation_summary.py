# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A permission prompt shows the command once, readably (#4446).

The prompt that prompted this printed the tool name twice and a full temp path
wrapped over three lines before the three words that mattered: `pytest -q
tests/`.
"""

import os

from gaia.ui.sse_translation import CanonicalTranslator, render_command_summary

WS = os.path.join(os.path.expanduser("~"), "work", "proj")
FAR = os.path.join(
    os.path.expanduser("~"),
    "AppData",
    "Local",
    "Temp",
    "claude",
    "c0dab1c0-999a-4990-9b65-1b2e97960afd",
    "scratchpad",
    "newuser",
    "proj",
)


def test_a_leading_cd_becomes_a_short_location_line():
    summary = render_command_summary(
        "run_shell_command",
        {"command": f"cd {FAR} && python -m pytest -q tests/ 2>&1"},
        workspace=WS,
    )
    command, location = summary.split("\n")
    assert command == "python -m pytest -q tests/ 2>&1"
    assert location.startswith("in ~")
    assert location.endswith(os.path.join("newuser", "proj"))
    assert "…" in location  # the middle folders are elided, not wrapped
    assert "c0dab1c0" not in summary


def test_the_workspace_itself_is_never_spelled_out():
    summary = render_command_summary(
        "run_shell_command",
        {"command": f"python {os.path.join(WS, 'scratch', 'x.py')}"},
        workspace=WS,
    )
    assert summary == f"python {os.path.join('scratch', 'x.py')}"


def test_a_cd_to_root_never_garbles_the_command():
    root = os.path.abspath(os.sep)
    target = os.path.join(WS, "build")
    summary = render_command_summary(
        "run_shell_command", {"command": f"cd {root} && rm -rf {target}"}, workspace=WS
    )
    command, location = summary.split("\n")
    assert command == f"rm -rf {target}"
    assert location == f"in {root}"


def test_only_a_whole_path_prefix_is_shortened():
    other = os.path.join(os.sep, "mnt", "bk") + WS
    summary = render_command_summary(
        "run_shell_command",
        {"command": f"cat {os.path.join(other, 'secret')}"},
        workspace=WS,
    )
    assert summary == f"cat {os.path.join(other, 'secret')}"


def test_a_cd_is_shown_even_with_a_working_directory():
    ssh = os.path.join(os.path.expanduser("~"), ".ssh")
    summary = render_command_summary(
        "run_shell_command",
        {"command": f"cd {ssh} && cat id_rsa", "working_directory": WS},
        workspace=WS,
    )
    assert summary.splitlines()[-1] == "in " + os.path.join("~", ".ssh")


def test_a_long_command_keeps_its_head_and_its_tail():
    command = "echo start " + "x" * 600 + " the-end"
    summary = render_command_summary(
        "run_shell_command", {"command": command}, workspace=WS
    )
    assert summary.startswith("echo start")
    assert summary.endswith("the-end")
    assert "chars]…" in summary
    assert len(summary) < 300


def test_other_arguments_are_still_named():
    summary = render_command_summary(
        "run_shell_command", {"command": "pytest", "timeout": 60}, workspace=WS
    )
    assert summary == "pytest  (timeout=60)"


def test_long_code_keeps_its_first_and_last_lines():
    code = "\n".join(f"print({i})" for i in range(30))
    summary = render_command_summary("run_python", {"code": code}, workspace=WS)
    lines = summary.splitlines()
    assert lines[0] == "print(0)" and lines[-1] == "print(29)"
    assert any("more lines" in line for line in lines)


def test_the_translator_uses_it_and_carries_the_risk():
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia", debug=False)
    [event] = translator.translate(
        {
            "type": "permission_request",
            "tool": "run_shell_command",
            "args": {"command": "pytest -q"},
            "confirm_id": "c1",
            "always_scope": "pytest",
            "risk": "execute",
        }
    )
    assert event["summary"] == "pytest -q"
    assert event["risk"] == "execute"
    assert "run_shell_command" not in event["summary"]


def test_other_tools_keep_the_labelled_summary():
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia", debug=False)
    [event] = translator.translate(
        {"type": "permission_request", "tool": "send_now", "args": {"to": "a@b.c"}}
    )
    assert event["summary"].startswith("Run 'send_now'")
