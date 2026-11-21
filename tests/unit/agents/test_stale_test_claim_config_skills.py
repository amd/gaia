# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Config, data and skill edits after a test run make a "tests pass" claim stale.

Only docs and requested reports (prose, tables, documents, images) leave an
earlier run standing. A requested ``settings.yaml``, ``app.json``,
``schema.sql`` or ``Dockerfile`` is something the code or a test reads, and a
``SKILL.md`` or prompt file is loaded by the agent at runtime — editing one
after the run means the run no longer covers the code (#8213).
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
    feeds_tests,
    is_code_mutation,
    is_doc_path,
    strip_verification_scope,
    unsupported_test_claim,
    unverified_change,
    verification_record,
)

CLAIM = "Updated the config. `pytest tests/` 12 passed."
HONEST = "Updated the config; the tests have not run since the last edit."


def _run(target="pytest tests/"):
    check = CheckResult(
        label="pytest",
        target=target,
        kind="test",
        passed=True,
        summary="12 passed in 0.50s",
    )
    result = attach_check({"status": "success", "return_code": 0}, check)
    return verification_record(
        "run_shell_command", {"command": target}, result, errored=False
    )


def _write(path, tool_name="write_file", requested=(), key="file_path", root=None):
    wanted = set(requested)
    record = verification_record(
        tool_name,
        {key: path, "content": "x"},
        {"status": "success"},
        errored=False,
        is_deliverable=wanted.__contains__ if wanted else None,
        root=root,
    )
    record["args"] = {key: path}
    return record


CONFIG_AND_DATA = [
    "settings.yaml",
    "config/settings.yaml",
    "deploy/k8s/app.yml",
    "app.json",
    "conf/app.toml",
    "setup.ini",
    ".env",
    "config/.env.local",
    "schema.sql",
    "db/migrations/001_init.sql",
    "Dockerfile",
    "docker/Dockerfile.dev",
    "web/index.html",
    "styles/site.css",
    "notebooks/explore.ipynb",
    "a/b/c/d/e/deep.toml",
    "C:\\work\\proj\\config\\settings.yaml",
    "/srv/proj/config/app.json",
]


# ---------------------------------------------------------------------------
# A requested config or data file is not a report
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CONFIG_AND_DATA)
def test_a_requested_config_or_data_file_written_after_the_run_is_stale(path):
    record = _write(path, requested=[path])
    assert record["mutated"] is True
    assert unsupported_test_claim(CLAIM, [_run(), record]) == (
        "12 passed",
        TEST_RUN_STALE,
    )


@pytest.mark.parametrize("path", CONFIG_AND_DATA)
def test_an_unrequested_config_or_data_file_is_stale_too(path):
    assert _write(path)["mutated"] is True


@pytest.mark.parametrize("path", CONFIG_AND_DATA)
def test_the_verify_nudge_names_a_requested_config_file(path, tmp_path):
    if path.startswith(("C:\\", "/")):
        pytest.skip("an absolute path outside the project is not its change")
    record = _write(path, requested=[path])
    assert unverified_change([_run(), record], str(tmp_path)) == path


@pytest.mark.parametrize("tool_name", ["edit_file", "write_file", "replace_function"])
@pytest.mark.parametrize("key", ["file_path", "path"])
def test_every_write_tool_and_path_key_counts_a_config_edit(tool_name, key):
    record = _write("config/settings.yaml", tool_name, ["settings.yaml"], key)
    assert record["mutated"] is True


def test_a_bare_requested_name_does_not_exempt_a_config_file_elsewhere():
    """A request for ``settings.yaml`` matches any folder; it still counts."""
    record = _write("deploy/settings.yaml", requested=["settings.yaml"])
    assert record["mutated"] is True


def test_a_requested_report_in_a_data_folder_still_counts():
    """``report.csv`` is a report; ``data/report.csv`` is something a test reads."""
    assert _write("report.csv", requested=["report.csv"])["mutated"] is False
    assert _write("data/report.csv", requested=["report.csv"])["mutated"] is True


@pytest.mark.parametrize(
    "path", ["REPORT.CSV", "Summary.PDF", "out/chart.PNG", "notes.Markdown"]
)
def test_report_suffixes_match_in_any_case(path):
    assert _write(path, requested=[path])["mutated"] is False


@pytest.mark.parametrize(
    "path", ["report.csv.json", "summary.txt.sql", "chart.png.yaml"]
)
def test_only_the_last_suffix_decides(path):
    assert _write(path, requested=[path])["mutated"] is True


# ---------------------------------------------------------------------------
# Skill and prompt files feed the agent at runtime
# ---------------------------------------------------------------------------

SKILL_FILES = [
    "hub/skills/pdf-tools/SKILL.md",
    "hub/skills/pdf-tools/references/forms.md",
    "hub/skills/pdf-tools/scripts/fill.sh",
    "hub/skills/pdf-tools/scripts/fill.py",
    "hub/skills/pdf-tools/LICENSE.txt",
    "hub/skills/pdf-tools/README.md",
    ".claude/skills/review/SKILL.md",
    "skills/x/notes.txt",
    "SKILL.md",
    "my-skill/SKILL.md",
    "prompts/system.md",
    "app/prompts/planner.txt",
    "prompt/answer.md",
    "C:\\Users\\me\\.gaia\\skills\\triage\\SKILL.md",
    "/home/me/.gaia/skills/triage/SKILL.md",
]


@pytest.mark.parametrize("path", SKILL_FILES)
def test_a_skill_or_prompt_file_is_a_test_input_not_a_doc(path):
    assert feeds_tests(path)
    assert not is_doc_path(path)


@pytest.mark.parametrize("path", SKILL_FILES)
def test_editing_a_skill_file_after_the_run_is_stale(path):
    record = _write(path, "edit_file")
    assert record["mutated"] is True
    assert unsupported_test_claim(CLAIM, [_run(), record])[1] == TEST_RUN_STALE


@pytest.mark.parametrize("path", SKILL_FILES)
def test_a_requested_skill_file_still_counts(path):
    assert _write(path, requested=[path])["mutated"] is True


def test_skill_folders_above_the_project_root_are_not_read(tmp_path):
    """A project that lives under a ``skills`` folder is not all skill input."""
    project = tmp_path / "skills" / "proj"
    project.mkdir(parents=True)
    root = str(project)
    assert is_doc_path(str(project / "README.md"), root)
    assert not is_doc_path(str(project / "skills" / "x" / "SKILL.md"), root)


# ---------------------------------------------------------------------------
# No false alarms: docs that nothing loads stay docs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/README.md",
        "docs/guides/skills.md",
        "docs/prompting.md",
        "CHANGELOG.md",
        "notes/skill-ideas.md",
        "myskills/README.md",
        "prompting-guide/intro.md",
        "docs/img/skills.png",
    ],
)
def test_unrelated_docs_after_the_run_leave_the_claim_standing(path):
    record = _write(path)
    assert record["mutated"] is False
    assert unsupported_test_claim(CLAIM, [_run(), record]) is None


# ---------------------------------------------------------------------------
# Ordering: before vs after the run, re-runs, several runs
# ---------------------------------------------------------------------------


def test_an_edit_before_the_run_is_covered_by_it():
    records = [_write("config/settings.yaml"), _run()]
    assert unsupported_test_claim(CLAIM, records) is None


def test_a_skill_edit_then_the_same_test_again_is_fresh():
    records = [_run(), _write("hub/skills/x/SKILL.md", "edit_file"), _run()]
    assert unsupported_test_claim(CLAIM, records) is None


def test_stale_again_after_a_second_edit():
    records = [
        _run(),
        _write("hub/skills/x/SKILL.md", "edit_file"),
        _run(),
        _write("settings.yaml", requested=["settings.yaml"]),
    ]
    assert unsupported_test_claim(CLAIM, records)[1] == TEST_RUN_STALE


def test_several_runs_all_before_the_edit_are_all_stale():
    records = [
        _run("pytest tests/unit"),
        _run("pytest tests/integration"),
        _write("app.json"),
    ]
    assert unsupported_test_claim(CLAIM, records)[1] == TEST_RUN_STALE


def test_one_passing_run_after_the_edit_supports_the_claim():
    records = [
        _run("pytest tests/unit"),
        _write("app.json"),
        _run("pytest tests/integration"),
    ]
    assert unsupported_test_claim(CLAIM, records) is None


def test_a_failed_write_is_not_a_change():
    record = verification_record(
        "write_file",
        {"file_path": "settings.yaml", "content": "x"},
        {"status": "error", "error": "denied"},
        errored=True,
    )
    assert unsupported_test_claim(CLAIM, [_run(), record]) is None


def test_a_report_after_a_config_edit_does_not_hide_it():
    records = [
        _run(),
        _write("config/settings.yaml"),
        _write("report.md", requested=["report.md"]),
    ]
    assert unsupported_test_claim(CLAIM, records)[1] == TEST_RUN_STALE


# ---------------------------------------------------------------------------
# Edits through a shell or a script are not judged here, by design
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("run_shell_command", {"command": "sed -i s/a/b/ config/settings.yaml"}),
        ("run_python", {"code": "open('settings.yaml', 'w').write('x')"}),
    ],
)
def test_a_shell_or_python_edit_is_not_counted(tool_name, args):
    """Only named write tools count: a false correction on an honest answer
    costs more than a missed stale one (see ``_FILE_MUTATION_TOOLS``)."""
    assert is_code_mutation(tool_name, args) is False
    record = verification_record(
        tool_name, args, {"status": "success", "return_code": 0}, errored=False
    )
    assert unsupported_test_claim(CLAIM, [_run(), record]) is None


# ---------------------------------------------------------------------------
# Through the loop
# ---------------------------------------------------------------------------


class _Agent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        check = CheckResult(
            label="pytest",
            target="pytest tests/",
            kind="test",
            passed=True,
            summary="12 passed in 0.50s",
        )

        @tool
        def sandbox_runner_for_config_write_test(command: str) -> dict:
            """Run a command in a sandbox."""
            del command
            return attach_check({"status": "success", "return_code": 0}, check)

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            import os

            folder = os.path.dirname(file_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
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


@pytest.mark.parametrize(
    "path", ["config/settings.yaml", "hub/skills/demo/SKILL.md", "Dockerfile"]
)
def test_the_loop_corrects_a_pass_claim_after_a_config_or_skill_edit(agent, path):
    sent = _stub_chat(
        agent,
        _call("sandbox_runner_for_config_write_test", command="pytest tests/"),
        _call("write_file", file_path=path, content="x: 1\n"),
        _answer(CLAIM),
        *[_answer(HONEST)] * 3,
    )

    agent.process_query(
        f"Run the tests, then write {path} with the new settings.", max_steps=10
    )

    assert "ran before the last change" in sent[3][-1]["content"]


def test_the_loop_accepts_the_claim_after_the_same_test_reruns(agent):
    sent = _stub_chat(
        agent,
        _call("sandbox_runner_for_config_write_test", command="pytest tests/"),
        _call("write_file", file_path="hub/skills/demo/SKILL.md", content="x"),
        _call("sandbox_runner_for_config_write_test", command="pytest tests/"),
        _answer(CLAIM),
    )

    result = agent.process_query(
        "Update hub/skills/demo/SKILL.md and run the tests.", max_steps=10
    )

    assert len(sent) == 4, sent[-1][-1]["content"]
    assert strip_verification_scope(result["result"]).strip() == CLAIM
