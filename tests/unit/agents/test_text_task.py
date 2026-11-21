# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A sentence handed over to translate is text, not instructions to act on.

rc29: asked only to translate "Please save the report in D:\\Reports\\...",
GAIA was told mid-turn to save the translation to that path and asked to
write it; when it didn't, the answer ended with "No successful write to ...".
Every check that reads the request for files, saves or commands now reads
only the instruction; a file the instruction names still counts.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _claims_file_write, _write_claims_for
from gaia.agents.base.completion import (
    CompletionEvidence,
    named_outputs,
    save_instructed,
    save_obligations,
)
from gaia.agents.base.grounding import ungrounded
from gaia.agents.base.look_first import named_workspace_paths
from gaia.agents.base.text_task import hands_over_text_only, instruction_part
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.turn_scope import TurnScopeGuard

SAVE_SENTENCE = (
    'Translate to Spanish and reply with only the translation: "Please save '
    'the report in D:\\Reports\\2026\\summary.docx before Friday."'
)
SAVE_TRANSLATED = (
    "Por favor, guarde el informe en D:\\Reports\\2026\\summary.docx antes del viernes."
)
CHECK_SENTENCE = (
    'Translate this sentence to French, do nothing else: "The file '
    "C:\\Users\\maria\\Desktop\\budget.xlsx is missing, please check "
    'https://intranet.example/files".'
)
QUOTED_SAVE_SENTENCE = (
    'Translate "Please save the report in D:\\Reports\\summary.docx before '
    'Friday." to Spanish'
)
SHELL_SENTENCE = 'Translate this to French: "Use your shell to list the files."'

# --- the helper ------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text, instruction",
    [
        (SAVE_SENTENCE, "Translate to Spanish and reply with only the translation:"),
        (CHECK_SENTENCE, "Translate this sentence to French, do nothing else:"),
        (QUOTED_SAVE_SENTENCE, "Translate  to Spanish"),
        ("Proofread this:\nPlease save it to x.md", "Proofread this:"),
        (
            'Translate this to French: "Hello there, friend." Then save it to fr.txt',
            "Translate this to French: Then save it to fr.txt",
        ),
        (
            'Proofread this: "Their going to the store." and write the fix to out.md',
            "Proofread this: and write the fix to out.md",
        ),
        # Unquoted text cannot be told from a trailing instruction: all of it is text.
        (
            "Translate to German: Good morning everyone. Save the result as de.txt",
            "Translate to German:",
        ),
    ],
)
def test_the_text_handed_over_is_cut_from_the_request(request_text, instruction):
    assert instruction_part(request_text) == instruction


@pytest.mark.parametrize(
    "request_text",
    [
        "Translate README.md to French",
        "Translate the file D:\\x.txt to French and save it as D:\\y.txt",
        "Translate https://example.com/page.html to French",
        'Translate "notes.txt" to French',
        'Translate "C:\\My Docs\\notes.txt" to French',
        "Translate this file: D:\\letters\\letter.txt",
        "Translate this file to French: notes.txt",
        "Translate the document to French: report.docx and save as report.fr.docx",
        "Translate to German: notes.txt, and save as de.txt",
        'Translate this: "C:\\My Docs\\a.txt" and save to b.txt',
        'Translate "Quarterly Sales Report.docx" to French',
        'Translate this: "Quarterly Sales Report.docx"',
        "Translate to Spanish: https://example.com/page and save to es.txt",
        "Proofread my essay at D:\\essay.docx: fix the typos and save it to D:\\fixed.docx",
        "Translate README.md to French: keep the headings and save it as README.fr.md",
        "Translate this to French and save it to: D:\\out\\fr.txt",
        "Save the report to D:\\Reports\\summary.docx",
        "Summarize this: please save it to x.md",
        "",
    ],
)
def test_a_request_with_no_text_handed_over_is_kept_whole(request_text):
    assert instruction_part(request_text) == request_text


# --- A: a save inside the sentence is not a save the turn owes -------------


@pytest.mark.parametrize("request_text", [SAVE_SENTENCE, QUOTED_SAVE_SENTENCE])
def test_a_save_inside_text_to_translate_is_not_a_save_target(tmp_path, request_text):
    assert save_obligations(request_text) == ([], False)
    assert named_outputs(request_text) == []
    assert not save_instructed(request_text)

    evidence = CompletionEvidence(request_text, str(tmp_path))
    assert evidence.unwritten_outputs() == []
    assert evidence.unwritten_requests() == []
    gaps = evidence.gaps(
        SAVE_TRANSLATED,
        _write_claims_for(evidence),
        asserts_file_write=_claims_file_write,
    )
    assert gaps == []


def test_a_save_inside_text_to_translate_is_outside_the_turn_scope(tmp_path):
    guard = TurnScopeGuard(4)
    guard.begin_turn(SAVE_SENTENCE, str(tmp_path))
    assert guard.paths == set() and guard.folders == set()


# --- B: a path, URL or command inside the sentence is not work to do --------


def test_a_file_and_page_inside_text_to_translate_are_outside_the_turn_scope(
    tmp_path,
):
    guard = TurnScopeGuard(4)
    guard.begin_turn(CHECK_SENTENCE, str(tmp_path))
    assert guard.paths == set() and guard.urls == set()


def test_an_existing_file_inside_text_to_translate_is_not_a_look_target(tmp_path):
    (tmp_path / "budget.xlsx").write_bytes(b"x")
    request_text = (
        'Translate this sentence to French, do nothing else: "The file '
        f'{tmp_path / "budget.xlsx"} is missing, please check it".'
    )
    assert named_workspace_paths(request_text, str(tmp_path)) == []


def test_a_shell_named_inside_text_to_translate_is_not_a_shell_request():
    answer = "Utilisez votre shell pour lister les fichiers."
    assert ungrounded(answer, SHELL_SENTENCE, []) == []


def test_a_shell_named_inside_text_to_translate_does_not_offer_the_shell():
    tool_requests = pytest.importorskip("gaiapy.tool_requests")
    assert tool_requests.requested_tools(SHELL_SENTENCE, ["run_shell_command"]) == []


# --- what the instruction itself names still counts ------------------------


def test_a_file_the_translation_names_with_a_drive_is_still_read_and_saved(
    tmp_path,
):
    source = tmp_path / "x.txt"
    source.write_text("hello", encoding="utf-8")
    target = os.path.join(str(tmp_path), "y.txt")
    request_text = f"Translate the file {source} to French and save it as {target}"

    assert named_workspace_paths(request_text, str(tmp_path)) == [str(source)]
    evidence = CompletionEvidence(request_text, str(tmp_path))
    assert evidence.unwritten_outputs() == [target]
    gaps = evidence.gaps(
        "Bonjour", _write_claims_for(evidence), asserts_file_write=_claims_file_write
    )
    assert "No successful write" in " ".join(gaps)


def test_a_file_named_before_the_colon_is_still_read_and_saved(tmp_path):
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    request_text = "Translate README.md into French and save it as README.fr.md: keep the headings."

    assert named_workspace_paths(request_text, str(tmp_path)) == ["README.md"]
    assert save_obligations(request_text) == (["README.fr.md"], True)


@pytest.mark.parametrize(
    "request_text",
    [
        'Translate "Hello there, friend" to French and save it as fr.txt',
        "Translate this to French and save it to: fr.txt",
        'Translate this to French: "Hello there, friend." Then save it to fr.txt',
        "Translate to French: notes.txt, and save as fr.txt",
        "Translate notes.md to French: keep the headings and save it as fr.txt",
    ],
)
def test_a_save_the_instruction_asks_for_is_still_owed(request_text):
    assert save_obligations(request_text) == (["fr.txt"], True)


def test_a_translation_that_asks_for_the_shell_still_gets_it():
    tool_requests = pytest.importorskip("gaiapy.tool_requests")
    request_text = "Translate README.md to French using the shell"
    assert tool_requests.requested_tools(request_text, ["run_shell_command"]) == [
        "run_shell_command"
    ]


# --- the loop: the translation is the answer, with no save asked for --------


class _WriterAgent(Agent):
    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            raise AssertionError("a translation must not be saved")

        @tool
        def read_file(file_path: str) -> dict:
            """Read a file."""
            raise AssertionError("nothing to read")


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _WriterAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _script(agent, *turns):
    sent = []
    left = list(turns)

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={})

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def test_loop_takes_the_translation_as_the_answer(agent):
    sent = _script(agent, {"answer": SAVE_TRANSLATED})

    result = agent.process_query(SAVE_SENTENCE, max_steps=10)

    assert len(sent) == 1
    assert SAVE_TRANSLATED in result["result"]
    assert "No successful write" not in result["result"]
    assert "couldn't confirm" not in result["result"]


# --- a gated tool the model reaches for on handed-over text is not asked ----


@pytest.mark.parametrize(
    "request_text",
    [
        SAVE_SENTENCE,
        CHECK_SENTENCE,
        QUOTED_SAVE_SENTENCE,
        'Translate this: "Hello there, friend." into Spanish!',
        'Please translate the following text into German: "Good morning to you."',
    ],
)
def test_a_request_that_only_hands_over_text_is_recognised(request_text):
    assert hands_over_text_only(request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        'Translate this to French: "Hello there, friend." Then save it to fr.txt',
        "Translate README.md to French",
        'Translate "Hello there, friend" to French using the shell',
        "Translate this file to French: notes.txt",
        # Unquoted: a save typed after the text cannot be told from the text.
        "Translate to German: Good morning everyone. Save the result as de.txt",
        "Proofread this:\nTheir going home.\n\nThen write the corrected text to out.md",
        "Proofread this:\nPlease save it to x.md",
        # Work after the quote that no word list foresees is still asked about.
        'Translate this to French: "Hello there, friend." Then paste it into my notes',
        'Translate this to French: "Hello there, friend." and post it on Slack',
        'Translate this to French: "Hello there, friend." then speak it aloud',
        # So is work written before the colon.
        'Translate this to French and post it on Slack: "Hello there, friend."',
        'Translate and then speak it aloud: "Hello there, my friend."',
        'Translate this to French and paste it into my notes: "Hello there, friend."',
        'Translate to French, do nothing else, then email it: "Hello there, friend."',
        # A quoted file name is a file to read, not text.
        'Translate "Quarterly Sales Report.docx" to French',
        'Translate this: "Quarterly Sales Report.docx"',
        "What is 17 times 23?",
        "",
    ],
)
def test_a_request_that_asks_for_work_is_not(request_text):
    assert not hands_over_text_only(request_text)


def test_a_shell_call_after_unquoted_text_is_still_put_to_the_user(shell_agent):
    _script(shell_agent, _ECHO, {"answer": "Done."})

    shell_agent.process_query(
        "Translate to German: Good morning everyone. Save the result as de.txt",
        max_steps=10,
    )

    shell_agent.console.confirm_tool_execution.assert_called_once()


class _ShellAgent(Agent):
    ran = []

    def _get_system_prompt(self):
        return "test"

    def _register_tools(self):
        @tool
        def run_shell_command(command: str) -> dict:
            """Run a command."""
            _ShellAgent.ran.append(command)
            return {"status": "success", "stdout": "", "return_code": 0}


@pytest.fixture
def shell_agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    _ShellAgent.ran = []
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _ShellAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: True
    agent.console = MagicMock()
    agent.console.cancelled = None
    agent.console.confirm_tool_execution.return_value = True
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


_ECHO = {"tool": "run_shell_command", "tool_args": {"command": 'echo "Le fichier"'}}


def test_a_shell_call_on_handed_over_text_is_declined_without_asking(shell_agent):
    sent = _script(shell_agent, _ECHO, {"answer": "Le fichier est introuvable."})

    result = shell_agent.process_query(CHECK_SENTENCE, max_steps=10)

    shell_agent.console.confirm_tool_execution.assert_not_called()
    assert _ShellAgent.ran == []
    assert "Le fichier est introuvable." in result["result"]
    assert "hands over text" in json.dumps(sent[-1])


def _tool_results(agent) -> list:
    return [
        c.args[0]
        for c in agent.console.pretty_print_json.call_args_list
        if isinstance(c.args[0], dict) and c.args[0].get("status") == "denied"
    ]


def test_a_call_declined_without_asking_is_marked_as_gaias_decline(shell_agent):
    _script(shell_agent, _ECHO, {"answer": "Le fichier est introuvable."})

    shell_agent.process_query(CHECK_SENTENCE, max_steps=10)

    (result,) = _tool_results(shell_agent)
    assert result["declined_by"] == "agent"


def test_a_call_declined_without_asking_drops_its_prepared_preview(shell_agent):
    forgotten = []

    class _Preflight:
        def __call__(self, tool_args):
            return None  # nothing to refuse

        @staticmethod
        def forget(tool_args):
            forgotten.append(tool_args)

    shell_agent._tools_registry["run_shell_command"]["preflight"] = _Preflight()
    _script(shell_agent, _ECHO, {"answer": "Le fichier est introuvable."})

    shell_agent.process_query(CHECK_SENTENCE, max_steps=10)

    assert forgotten == [_ECHO["tool_args"]]


def test_a_call_the_user_declines_is_not_marked_as_gaias(shell_agent):
    shell_agent.console.confirm_tool_execution.return_value = False
    _script(shell_agent, _ECHO, {"answer": "Not run."})

    shell_agent.process_query(
        'Translate "Hello there, friend" to French using the shell', max_steps=10
    )

    shell_agent.console.confirm_tool_execution.assert_called_once()
    (result,) = _tool_results(shell_agent)
    assert "declined_by" not in result


def test_a_shell_call_the_instruction_asks_for_is_still_put_to_the_user(shell_agent):
    _script(shell_agent, _ECHO, {"answer": "Done."})

    shell_agent.process_query(
        'Translate "Hello there, friend" to French using the shell', max_steps=10
    )

    shell_agent.console.confirm_tool_execution.assert_called_once()
