# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Writing a doc or the requested report after a test run leaves the run standing.

A task that asks for the code fix and then a write-up of it ends with the
write-up. Every passing run then came "before the last change", so the
answer's honest pass count was sent back for a re-run that changed nothing.
Only a write a test could depend on makes an earlier run stale now: code, test
fixtures, build inputs, and files inside a Python package.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.checks import CheckResult, attach_check
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import (
    TEST_RUN_STALE,
    is_code_mutation,
    is_doc_path,
    strip_verification_scope,
    unsupported_test_claim,
    verification_record,
)

CLAIM = "Fixed the parser. `pytest tests/` 12 passed."
HONEST = "Fixed the parser; the tests have not run since the last edit."


def _check(passed=True):
    return CheckResult(
        label="pytest",
        target="pytest tests/",
        kind="test",
        passed=passed,
        summary="12 passed in 0.50s" if passed else "1 failed, 11 passed in 0.50s",
    )


def _run():
    result = attach_check({"status": "success", "return_code": 0}, _check())
    return verification_record(
        "run_shell_command", {"command": "pytest tests/"}, result, errored=False
    )


def _write(path, tool_name="write_file", is_deliverable=None):
    return verification_record(
        tool_name,
        {"file_path": path, "content": "x"},
        {"status": "success"},
        errored=False,
        is_deliverable=is_deliverable,
    )


# ---------------------------------------------------------------------------
# Which paths are docs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "OUTPUT.md",
        "_task/OUTPUT.md",
        "docs/guides/chat.mdx",
        "CHANGELOG.md",
        "CHANGELOG",
        "README",
        "notes.txt",
        "report.csv",
        "C:\\work\\proj\\docs\\notes.rst",
    ],
)
def test_docs_and_reports_are_docs(path):
    assert is_doc_path(path)


@pytest.mark.parametrize(
    "path",
    [
        # A ``.py`` named like a doc is still code.
        "README.py",
        "docs/changelog.py",
        "CHANGELOG.md.py",
        # An expected-output file a test compares against.
        "tests/fixtures/expected.md",
        "tests/golden_output.txt",
        "C:\\work\\proj\\tests\\snapshot.md",
        "pkg/__snapshots__/view.md",
        # Build inputs with a doc suffix.
        "requirements.txt",
        "requirements-dev.txt",
        "constraints.txt",
        "CMakeLists.txt",
        # Unknown suffix, and nothing at all.
        "config.json",
        "settings.yaml",
        "",
        "   ",
    ],
)
def test_code_fixtures_and_build_inputs_are_not_docs(path):
    assert not is_doc_path(path)


def test_a_md_inside_a_python_package_is_not_a_doc(tmp_path):
    """A prompt template the package loads at runtime is code in all but suffix."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    assert not is_doc_path(str(package / "system_prompt.md"))
    assert is_doc_path(str(tmp_path / "system_prompt.md"))


# ---------------------------------------------------------------------------
# The record and the claim check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc", ["OUTPUT.md", "CHANGELOG.md", "docs/fix.mdx"])
def test_a_doc_written_after_the_run_leaves_the_claim_supported(doc):
    assert unsupported_test_claim(CLAIM, [_run(), _write(doc)]) is None


@pytest.mark.parametrize(
    "path", ["src/parser.py", "tests/fixtures/expected.md", "README.py"]
)
def test_a_code_write_after_the_run_still_makes_it_stale(path):
    claim, why = unsupported_test_claim(CLAIM, [_run(), _write(path)])
    assert claim == "12 passed"
    assert why == TEST_RUN_STALE


def test_a_doc_write_does_not_hide_an_earlier_code_edit():
    records = [_run(), _write("src/parser.py"), _write("OUTPUT.md")]
    assert unsupported_test_claim(CLAIM, records)[1] == TEST_RUN_STALE


def test_a_requested_output_with_an_unknown_suffix_leaves_the_run_standing():
    deliverable = {"result.json"}.__contains__
    record = _write("result.json", is_deliverable=deliverable)
    assert record["mutated"] is False
    assert unsupported_test_claim(CLAIM, [_run(), record]) is None


def test_a_requested_output_that_is_code_still_counts():
    """Asked to write ``fix.py``: tests that ran before it don't cover it."""
    deliverable = {"fix.py"}.__contains__
    record = _write("fix.py", is_deliverable=deliverable)
    assert record["mutated"] is True
    assert unsupported_test_claim(CLAIM, [_run(), record])[1] == TEST_RUN_STALE


def test_an_unrequested_json_still_counts():
    record = _write("config.json", is_deliverable={"result.json"}.__contains__)
    assert record["mutated"] is True


def test_an_edit_with_no_readable_target_still_counts():
    assert is_code_mutation("edit_file", {}) is True
    assert is_code_mutation("edit_file", "not a dict") is True


def test_a_tool_that_writes_nothing_is_never_a_mutation():
    assert is_code_mutation("read_file", {"file_path": "a.py"}) is False


@pytest.mark.parametrize(
    "tool_name", ["edit_file", "write_markdown_file", "write_python_file"]
)
def test_every_write_tool_follows_the_same_rule(tool_name):
    assert _write("OUTPUT.md", tool_name)["mutated"] is False
    assert _write("src/a.py", tool_name)["mutated"] is True


# ---------------------------------------------------------------------------
# Through the loop: the requested file comes from the request itself
# ---------------------------------------------------------------------------


class _Agent(Agent):
    run_result = attach_check({"status": "success", "return_code": 0}, _check())

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        agent = self

        @tool
        def sandbox_runner_for_doc_write_test(command: str) -> dict:
            """Run a command in a sandbox."""
            del command
            return agent.run_result

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"status": "success", "file_path": file_path}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        made = _Agent(silent_mode=True, skip_lemonade=True)
    made.streaming = False
    made.console.auto_approve_gated_tools = True
    return made


def _stub_chat(agent_, *responses):
    queue = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        if not queue:
            raise AssertionError("chat stub ran out of scripted responses")
        reply = MagicMock()
        reply.text = queue.pop(0)
        reply.stats = {}
        return reply

    chat = MagicMock()
    chat.send_messages = MagicMock(side_effect=_send)
    agent_.chat = chat
    return sent


def _call(tool_name, **args):
    return json.dumps({"thought": "", "tool": tool_name, "tool_args": args})


def _answer(text):
    return json.dumps({"thought": "done", "answer": text})


def _final(result):
    return strip_verification_scope(result["result"]).strip()


def test_the_requested_report_written_last_is_not_a_stale_change(agent):
    sent = _stub_chat(
        agent,
        _call("sandbox_runner_for_doc_write_test", command="pytest tests/"),
        _call("write_file", file_path="summary.html", content="<p>done</p>"),
        _answer(CLAIM),
    )

    result = agent.process_query(
        "Fix the parser, run the tests, then write summary.html with what you did.",
        max_steps=10,
    )

    assert len(sent) == 3, sent[-1][-1]["content"]
    assert _final(result) == CLAIM


def test_a_code_write_after_the_run_is_still_corrected(agent):
    sent = _stub_chat(
        agent,
        _call("sandbox_runner_for_doc_write_test", command="pytest tests/"),
        _call("write_file", file_path="parser.py", content="x = 1\n"),
        _answer(CLAIM),
        # Later checks on the honest rewrite may ask once more.
        *[_answer(HONEST)] * 3,
    )

    agent.process_query("Fix the parser and run the tests.", max_steps=10)

    assert "ran before the last change" in sent[3][-1]["content"]


# ---------------------------------------------------------------------------
# The verify-after-change nudge reads the same rule
# ---------------------------------------------------------------------------


def test_the_nudge_skips_a_requested_output_written_after_the_run(tmp_path):
    from gaia.agents.base.verification import unverified_change

    record = _write("result.json", is_deliverable={"result.json"}.__contains__)
    record["args"] = {"file_path": "result.json"}
    assert unverified_change([_run(), record], str(tmp_path)) is None


def test_the_nudge_still_names_a_code_change_after_the_run(tmp_path):
    from gaia.agents.base.verification import unverified_change

    record = _write("parser.py")
    record["args"] = {"file_path": "parser.py"}
    assert unverified_change([_run(), record], str(tmp_path)) == "parser.py"


# ---------------------------------------------------------------------------
# A requested output a test can read still counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/fixtures/expected.json",
        "tests/expected.md",
        "pytest.ini",
        "pyproject.toml",
        "package.json",
        "setup.cfg",
    ],
)
def test_a_requested_output_a_test_reads_still_counts(path):
    record = _write(path, is_deliverable={path}.__contains__)
    assert record["mutated"] is True
    assert unsupported_test_claim(CLAIM, [_run(), record])[1] == TEST_RUN_STALE


def test_package_data_below_the_package_folder_is_not_a_doc(tmp_path):
    package = tmp_path / "pkg"
    (package / "prompts").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    assert not is_doc_path(str(package / "prompts" / "system.md"))


def test_a_relative_path_is_judged_against_the_project_root(tmp_path, monkeypatch):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert is_doc_path("pkg/notes.md")
    assert not is_doc_path("pkg/notes.md", str(tmp_path))
    record = verification_record(
        "write_file",
        {"file_path": "pkg/notes.md"},
        {"status": "success"},
        errored=False,
        root=str(tmp_path),
    )
    assert record["mutated"] is True
