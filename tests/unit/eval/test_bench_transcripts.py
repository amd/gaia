# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Both transcript shapes read into the same calls, test runs and web uses."""

import pytest

from gaia.eval.bench import transcripts

from .conftest import cc_tool, gaia_tool

PASSED = "....\n4 passed in 0.02s\n"
FAILED = "..F.\n1 failed, 3 passed in 0.05s\n"


def _gaia(*calls):
    return {"prompt": "p", "answer": "a", "conversation": [e for c in calls for e in c]}


def _cc(*calls):
    return {"prompt": "p", "answer": "a", "events": [e for c in calls for e in c]}


def test_gaia_calls_pair_each_call_with_its_result():
    transcript = _gaia(
        gaia_tool("read_file", {"file_path": "a"}, {"status": "success"}),
        gaia_tool("run_shell_command", {"command": "pytest"}, {"stdout": PASSED}),
    )
    names = [name for name, _, _ in transcripts.tool_calls(transcript)]
    assert names == ["read_file", "run_shell_command"]


def test_a_partial_gaia_record_of_bare_tool_entries_is_read():
    record = {
        "conversation": [{"role": "tool", "name": "x", "tool_args": {}, "content": 1}]
        * 2
    }
    assert len(transcripts.tool_calls(record)) == 2


def test_claude_code_calls_are_read_from_its_events():
    transcript = _cc(cc_tool(1, "Bash", {"command": "pytest"}, PASSED))
    ((name, args, result),) = transcripts.tool_calls(transcript)
    assert (name, args["command"]) == ("Bash", "pytest")
    assert result == {"is_error": False, "output": PASSED}


@pytest.mark.parametrize("shape", [_gaia, _cc])
def test_checks_are_read_from_results_in_either_shape(shape):
    tool = (
        gaia_tool if shape is _gaia else (lambda n, a, r: cc_tool(1, n, a, r["stdout"]))
    )
    transcript = shape(tool("run", {"command": "pytest"}, {"stdout": FAILED}))
    checks = transcripts.checks_actually_run(transcript)
    assert "1 failed, 3 passed in 0.05s (did not pass)" in checks


def test_the_tools_own_check_record_beats_its_trimmed_output():
    record = {
        "stdout": "...",
        "check_result": {"summary": "948 passed in 9s", "passed": True},
    }
    transcript = _gaia(gaia_tool("run_shell_command", {"command": "pytest"}, record))
    assert "948 passed in 9s (passed)" in transcripts.checks_actually_run(transcript)


def test_no_test_run_is_stated_as_such():
    assert transcripts.checks_actually_run(_gaia()).startswith("No test-runner summary")


def test_claude_code_is_verified_only_by_a_passing_run_after_its_last_edit():
    edit = cc_tool(1, "Edit", {"file_path": "x"}, "ok")
    run = cc_tool(2, "Bash", {"command": "pytest"}, PASSED)
    later_edit = cc_tool(3, "Write", {"file_path": "y"}, "ok")
    assert transcripts.cc_tests_verified(_cc(edit, run))
    assert not transcripts.cc_tests_verified(_cc(edit, run, later_edit))
    assert not transcripts.cc_tests_verified(
        _cc(edit, cc_tool(2, "Bash", {"command": "pytest"}, FAILED))
    )


@pytest.mark.parametrize(
    "name, args, found",
    [
        ("WebSearch", {"query": "TheRock gfx90a"}, True),
        ("search_web", {"query": "x"}, True),
        (
            "Bash",
            {"command": "curl -s https://api.github.com/repos/ROCm/TheRock"},
            True,
        ),
        ("run_shell_command", {"command": "git fetch origin"}, True),
        ("run_shell_command", {"command": "gh pr list"}, True),
        ("Bash", {"command": "curl http://127.0.0.1:8000/health"}, False),
        ("Bash", {"command": "curl -s $URL"}, True),
        ("Bash", {"command": "wget https://example.com/fix.patch"}, True),
        ("Bash", {"command": "python -m pytest tests"}, False),
        ("read_file", {"file_path": "README.md"}, False),
    ],
)
def test_web_use_is_read_from_the_tool_record(name, args, found):
    shape = (
        _cc(cc_tool(1, name, args, ""))
        if name[0].isupper()
        else _gaia(gaia_tool(name, args, {}))
    )
    assert bool(transcripts.web_uses(shape)) is found


def test_the_tool_record_is_cut_to_fit():
    big = {"stdout": "x" * 50000}
    record = transcripts.tool_record(
        _gaia(gaia_tool("run_python", {"code": "print()"}, big))
    )
    assert len(record) <= transcripts.RECORD_CAP + 100
    assert record.startswith("[1] run_python(")
