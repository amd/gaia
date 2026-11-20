# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A permission prompt shows the command once, readably (#4446).

The prompt that prompted this printed the tool name twice and a full temp path
wrapped over three lines before the three words that mattered: `pytest -q
tests/`.
"""

import os

import pytest

from gaia.agents.base.call_risk import CODE_RISK_NOTE, SCRIPT_ARG
from gaia.ui.sse_translation import (
    SUMMARY_CODE_LINES,
    CanonicalTranslator,
    render_command_summary,
)

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
    assert event["summary"] == "pytest -q\nCan run any code, with your permissions."
    assert event["risk"] == "execute"
    assert "run_shell_command" not in event["summary"]


@pytest.mark.parametrize(
    "tool, args",
    [
        ("run_python", {"code": "print(1)"}),
        ("execute_python_file", {"file_path": f"{WS}/job.py"}),
    ],
)
def test_a_code_prompt_says_gaia_cannot_see_what_the_code_does(tool, args):
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia", debug=False)
    [event] = translator.translate(
        {"type": "permission_request", "tool": tool, "args": args, "risk": "execute"}
    )
    assert event["summary"].splitlines()[-1] == CODE_RISK_NOTE


def test_a_read_command_gets_no_risk_line():
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia", debug=False)
    [event] = translator.translate(
        {
            "type": "permission_request",
            "tool": "run_shell_command",
            "args": {"command": "ls"},
            "risk": "read",
        }
    )
    assert event["summary"] == "ls"


def test_other_tools_keep_the_labelled_summary():
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia", debug=False)
    [event] = translator.translate(
        {"type": "permission_request", "tool": "send_now", "args": {"to": "a@b.c"}}
    )
    assert event["summary"].startswith("Run 'send_now'")


def test_a_one_time_command_is_shown_verbatim_with_its_folder_and_note():
    """GAIA's checks did not vouch for it, so nothing in it is shortened."""
    command = f"cd {FAR} &&  echo " + "x" * 600 + " | sort"
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia", debug=False)
    [event] = translator.translate(
        {
            "type": "permission_request",
            "tool": "run_shell_command",
            "args": {
                "command": command,
                "working_directory": WS,
                "_risk_note": "Can run any code with your permissions.",
            },
            "risk": "execute",
        }
    )
    shown, folder, note = event["summary"].split("\n")
    assert shown == command
    assert folder == f"in {WS}"
    assert note == "Can run any code with your permissions."
    assert "always_scope" not in event


def test_a_command_run_in_the_home_folder_keeps_its_paths_home_rooted():
    """Stripped of the home folder, `ls ~/src` read as `ls src` — the project a
    TUI user is sitting in, not the home folder the command really lists."""
    home = os.path.expanduser("~")
    summary = render_command_summary(
        "run_shell_command",
        {"command": f"ls -d {home}/*/src/app; ls -d {home}/src/app"},
        workspace=home,
    )
    assert summary == "ls -d ~/*/src/app; ls -d ~/src/app"


SCRIPT = os.path.join(WS, "build_recon.py")


def _file_summary(script):
    return render_command_summary(
        "execute_python_file", {"file_path": SCRIPT, SCRIPT_ARG: script}, workspace=WS
    )


def test_a_python_file_prompt_shows_the_script_under_its_path():
    summary = _file_summary(
        {"text": "import os\nprint(os.getcwd())", "hidden_lines": 0}
    )

    assert summary.splitlines() == [
        "." + os.sep + "build_recon.py",
        "import os",
        "print(os.getcwd())",
    ]


def test_a_long_script_keeps_its_first_and_last_lines_like_code():
    code = "\n".join(f"print({i})" for i in range(30))
    lines = _file_summary({"text": code, "hidden_lines": 0}).splitlines()

    assert lines[1] == "print(0)" and lines[-1] == "print(29)"
    # A terminal prompt cannot show the rest, so the cut says where it is.
    assert (
        f"…[{30 - SUMMARY_CODE_LINES} more lines: open {SCRIPT} to read them]…" in lines
    )


def test_a_capped_script_counts_every_line_it_does_not_show():
    code = "\n".join(f"print({i})" for i in range(400))
    lines = _file_summary({"text": code, "hidden_lines": 250}).splitlines()

    assert lines[1] == "print(0)"
    assert lines[-1] == (
        f"…[{650 - SUMMARY_CODE_LINES} more lines not shown: open {SCRIPT} to read them]"
    )
    assert len(lines) == SUMMARY_CODE_LINES + 2


def test_an_unreadable_script_says_why_instead_of_showing_nothing():
    said = {
        reason: _file_summary({"unreadable": reason}).splitlines()
        for reason in ("missing", "not_text", "too_large", "unreadable")
    }

    assert (
        said["missing"][1] == "This file does not exist, so there is no code to show."
    )
    assert said["not_text"][1] == "This file is not text, so its code can't be shown."
    assert said["too_large"][1] == "This file is too large to show here."
    assert (
        said["unreadable"][1]
        == "GAIA could not read this file, so its code isn't shown."
    )
    assert all(len(lines) == 2 for lines in said.values())


def test_an_empty_script_says_it_is_empty():
    assert _file_summary({"text": "", "hidden_lines": 0}).splitlines()[1:] == [
        "This file is empty."
    ]


def test_a_python_file_prompt_with_no_script_still_names_the_file():
    path = os.path.join(WS, "build_recon.py")
    summary = render_command_summary(
        "execute_python_file", {"file_path": path}, workspace=WS
    )

    assert summary == "." + os.sep + "build_recon.py"
