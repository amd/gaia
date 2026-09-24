# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Completion evidence is per artifact, after the latest write, and delivered."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _claims_file_write
from gaia.agents.base.completion import CompletionEvidence, save_obligations
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture
def ledger(tmp_path):
    return CompletionEvidence("Save the summary to `summary.md`", str(tmp_path))


def write(ledger, path="summary.md", successful=True):
    ledger.record("write_file", {"file_path": path}, {"file_path": path}, successful)


def read(ledger, path="summary.md", content="observed", **fields):
    result = {"file_path": path, "content": content, **fields}
    ledger.delivered("read_file", {"file_path": path}, result, result)


def gaps(ledger, answer="Done."):
    return ledger.gaps(answer, _claims_file_write)


def test_unrelated_transcript_does_not_back_summary_claim(ledger):
    ledger.record(
        "transcribe_media",
        {"file_path": "video.mp4"},
        {"transcript_path": "transcript.txt"},
        True,
    )
    read(ledger, "transcript.txt")
    assert "summary.md" in " ".join(gaps(ledger, "I saved the result to `summary.md`."))


def test_successful_write_requires_readback(ledger):
    write(ledger)
    assert "read back" in " ".join(gaps(ledger))
    read(ledger)
    assert gaps(ledger) == []


def test_read_before_write_and_rewrite_are_not_observation(ledger):
    read(ledger)
    write(ledger)
    assert gaps(ledger)
    read(ledger)
    assert not gaps(ledger)
    write(ledger)
    assert gaps(ledger)


def test_failed_write_or_existing_file_does_not_fulfill_save(ledger, tmp_path):
    (tmp_path / "summary.md").write_text("old contents")
    write(ledger, successful=False)
    read(ledger)
    assert "No successful write" in " ".join(gaps(ledger))


def test_missing_save_does_not_need_a_false_claim(ledger):
    assert gaps(ledger, "Here are the exercises.")


def test_paging_requires_contiguous_coverage_from_zero(ledger):
    write(ledger)
    read(ledger, content="tail", offset=8, next_offset=None)
    assert gaps(ledger)
    read(ledger, content="head", offset=0, next_offset=4)
    assert gaps(ledger)
    read(ledger, content="body", offset=4, next_offset=8)
    assert not gaps(ledger)


def test_truncated_read_requires_all_delivered_archive_pages(ledger):
    write(ledger)
    original = {"file_path": "summary.md", "content": "full contents"}
    delivered = {
        "artifact": "output_1",
        "continuation": "read_tool_output",
        "content": "full...",
    }
    ledger.delivered("read_file", {"file_path": "summary.md"}, original, delivered)
    assert gaps(ledger)
    page = {"content": "6789", "offset": 6, "total_chars": 10}
    ledger.delivered("read_tool_output", {"artifact": "output_1"}, page, page)
    assert gaps(ledger)
    page = {"content": "012345", "offset": 0, "total_chars": 10}
    ledger.delivered("read_tool_output", {"artifact": "output_1"}, page, page)
    assert not gaps(ledger)


def test_old_archive_cannot_verify_a_later_write(ledger):
    write(ledger)
    raw = {"file_path": "summary.md", "content": "old"}
    ledger.delivered(
        "read_file", {}, raw, {"artifact": "old", "continuation": "read_tool_output"}
    )
    write(ledger)
    page = {"content": "old", "offset": 0, "total_chars": 3}
    ledger.delivered("read_tool_output", {"artifact": "old"}, page, page)
    assert gaps(ledger)


def test_metadata_and_binary_summaries_are_not_readback(ledger):
    write(ledger)
    read(ledger, content="[Binary file, 3 bytes]", is_binary=True)
    assert gaps(ledger)
    ledger.delivered("stat_file", {}, {"file_path": "summary.md"}, {"size": 3})
    assert gaps(ledger)


def test_paths_are_rooted_and_windows_paths_are_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = CompletionEvidence("Save to `out/result.md`", "C:\\Work")
    write(ledger, "c:\\WORK\\out\\result.md")
    read(ledger, "C:\\Work\\out\\result.md")
    assert not gaps(ledger)


def test_json_string_read_contract(ledger):
    write(ledger)
    raw = json.dumps({"content": "observed", "offset": 0, "next_offset": None})
    ledger.delivered("read_file", {"file_path": "summary.md"}, raw, raw)
    assert not gaps(ledger)


@pytest.mark.parametrize(
    "query",
    [
        "Write a poem about clouds",
        "How do I save a file to `x.md`?",
        "Do not save to `x.md`",
        "Explain how to write `x.md`",
        "What does this do?\n```python\nwrite('x.md')\n```",
    ],
)
def test_advice_and_prose_are_not_save_obligations(query):
    assert save_obligations(query) == ([], False)


def test_save_instruction_chooses_destination_not_source():
    assert save_obligations("Read `input.txt` and save the summary to `out.md`.") == (
        ["out.md"],
        True,
    )


def test_generic_save_needs_direct_output_not_transcription(tmp_path):
    ledger = CompletionEvidence("Summarize the video and save it", str(tmp_path))
    ledger.record("transcribe_media", {}, {"transcript_path": "input.txt"}, True)
    read(ledger, "input.txt")
    claim = "I saved the summary to a file."
    assert gaps(ledger, claim)
    write(ledger)
    read(ledger)
    assert not gaps(ledger, claim)


@pytest.mark.parametrize(
    "query",
    [
        "Save me some time and just give me the summary.",
        "Store this in memory: my favorite color is blue.",
        "Export as JSON please.",
        "Can you write a Python script that saves data to a file?",
        "Write a guide to Node.js.",
        "Write a script to clean logs/app.log.",
        "Write the steps to edit /etc/hosts.",
    ],
)
def test_topics_and_idioms_are_not_save_targets(query):
    assert save_obligations(query) == ([], False)


class FileAgent(Agent):
    def _get_system_prompt(self):
        return "Use the file tools."

    def _register_tools(self):
        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write the requested file."""
            Path(file_path).write_text(content)
            return {"status": "success", "file_path": file_path}

        @tool
        def read_file(file_path: str, offset: int = 0, limit: int = 8000) -> dict:
            """Read the requested file."""
            text = Path(file_path).read_text()
            end = min(len(text), offset + limit)
            return {
                "status": "success",
                "file_path": file_path,
                "content": text[offset:end],
                "offset": offset,
                "next_offset": end if end < len(text) else None,
            }

        @tool
        def run_python(code: str) -> dict:
            """Execute Python in a real child process."""
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=False,
            )
            return {
                "status": "success" if result.returncode == 0 else "error",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
            }

        @tool
        def transcribe_media(file_path: str) -> dict:
            """Produce a transcript fixture."""
            path = str(Path(file_path).with_suffix(".txt"))
            Path(path).write_text("exercise alpha; exercise beta")
            return {"status": "success", "transcript_path": path}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = FileAgent(silent_mode=True, skip_lemonade=True)
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def call(name, **args):
    return {"tool": name, "tool_args": args}


def script(agent, *turns):
    sent = []

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert turns_left, "unexpected model call"
        return MagicMock(text=json.dumps(turns_left.pop(0)), stats={})

    turns_left = list(turns)
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def test_real_loop_requires_correct_file_write_and_readback(agent, tmp_path):
    sent = script(
        agent,
        call("transcribe_media", file_path="video.mp4"),
        {"answer": "I saved the result to `summary.md`."},
        call("write_file", file_path="summary.md", content="alpha\nbeta\n"),
        call("read_file", file_path="summary.md"),
        {"answer": "I saved the result to `summary.md`. It contains alpha and beta."},
    )
    result = agent.process_query(
        "Summarize the video and save to `summary.md`", max_steps=10
    )
    assert "[check:completion]" in sent[2][-1]["content"]
    assert (tmp_path / "summary.md").read_text() == "alpha\nbeta\n"
    assert result["status"] == "success"
    assert not result["completion_gaps"]
    assert agent.console.print_final_answer.call_args.args[0] == result["result"]


@pytest.mark.parametrize("max_steps", [1, 10])
def test_persistent_lie_is_suppressed_and_status_incomplete(agent, max_steps):
    claim = {"answer": "I saved the result to `summary.md`."}
    script(agent, claim, claim)
    result = agent.process_query("Save the result to `summary.md`", max_steps=max_steps)
    assert result["status"] == "incomplete"
    assert claim["answer"] not in result["result"]
    assert "No successful write" in result["result"]
    assert agent.console.print_final_answer.call_args.args[0] == result["result"]


def test_step_exhaustion_after_write_returns_incomplete(agent, tmp_path):
    script(agent, call("write_file", file_path="summary.md", content="real"))
    result = agent.process_query("Save to `summary.md`", max_steps=1)
    assert (tmp_path / "summary.md").read_text() == "real"
    assert result["status"] == "incomplete"
    assert "read back" in result["result"]


def test_turn_reset_does_not_reuse_prior_save(agent):
    script(
        agent,
        call("write_file", file_path="summary.md", content="real"),
        call("read_file", file_path="summary.md"),
        {"answer": "Saved to `summary.md`."},
    )
    assert (
        agent.process_query("Save to `summary.md`", max_steps=10)["status"] == "success"
    )
    script(agent, {"answer": "Saved to `summary.md`."})
    assert (
        agent.process_query("Save to `summary.md`", max_steps=1)["status"]
        == "incomplete"
    )


def test_executor_save_is_proven_by_file_change_and_readback(agent, tmp_path):
    script(
        agent,
        call(
            "run_python",
            code="from pathlib import Path; Path('summary.md').write_text('alpha')",
        ),
        call("read_file", file_path="summary.md"),
        {"answer": "Saved to `summary.md`."},
    )
    result = agent.process_query("Save to `summary.md`", max_steps=10)
    assert result["status"] == "success"
    assert (tmp_path / "summary.md").read_text() == "alpha"


def test_executor_mutation_invalidates_prior_readback(agent):
    script(
        agent,
        call("write_file", file_path="summary.md", content="alpha"),
        call("read_file", file_path="summary.md"),
        call(
            "run_python",
            code="from pathlib import Path; Path('summary.md').write_text('beta')",
        ),
        {"answer": "Saved to `summary.md`."},
    )
    result = agent.process_query("Save to `summary.md`", max_steps=4)
    assert result["status"] == "incomplete"
    assert "read back" in result["result"]


def test_executor_failure_after_write_cannot_reuse_old_evidence(agent):
    script(
        agent,
        call("write_file", file_path="summary.md", content="alpha"),
        call("read_file", file_path="summary.md"),
        call(
            "run_python",
            code="from pathlib import Path; Path('summary.md').write_text('broken'); raise RuntimeError('failed')",
        ),
        {"answer": "Saved to `summary.md`."},
    )
    result = agent.process_query("Save to `summary.md`", max_steps=4)
    assert result["status"] == "incomplete"
    assert "No successful write" in result["result"]


def test_cached_transcript_is_not_a_new_write(tmp_path):
    ledger = CompletionEvidence("", str(tmp_path))
    ledger.record(
        "transcribe_media", {}, {"transcript_path": "old.txt", "reused": True}, True
    )
    assert gaps(ledger, "I saved the transcript to `old.txt`.")


def test_permission_denied_paths_are_not_probed(ledger, monkeypatch):
    validator = MagicMock()
    validator.validate_read.return_value = (False, "outside scope")
    monkeypatch.setattr(
        ledger, "_stamp", lambda p: pytest.fail("denied path was probed")
    )
    assert ledger.snapshot("run_python", {}, validator) == {}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Save to out.md and compare input.txt", (["out.md"], True)),
        ("Save x.md and email it to me", (["x.md"], True)),
        ("Write a poem about `rain`", ([], False)),
        ("Save to `out one.md` and `out two.md`", (["out one.md", "out two.md"], True)),
    ],
)
def test_destination_scope(text, expected):
    assert save_obligations(text) == expected


def test_archived_single_page_does_not_prove_full_file_read(ledger):
    write(ledger)
    raw = {"file_path": "summary.md", "content": "head", "offset": 0, "next_offset": 4}
    ledger.delivered(
        "read_file", {}, raw, {"artifact": "page", "continuation": "read_tool_output"}
    )
    page = {"content": "serialized page", "offset": 0, "total_chars": 15}
    ledger.delivered("read_tool_output", {"artifact": "page"}, page, page)
    assert gaps(ledger)
    read(ledger, content="tail", offset=4, next_offset=None)
    assert not gaps(ledger)


def test_cleanup_claim_requires_observed_removal(agent, tmp_path):
    (tmp_path / "scratch.py").write_text("temporary")
    script(
        agent,
        {"answer": "I removed `scratch.py`."},
        {"answer": "I removed `scratch.py`."},
    )
    result = agent.process_query("Check the scratch file", max_steps=10)
    assert result["status"] == "incomplete"
    assert "No removal" in result["result"]
    assert (tmp_path / "scratch.py").exists()


def test_actual_executor_cleanup_is_supported(agent, tmp_path):
    (tmp_path / "scratch.py").write_text("temporary")
    script(
        agent,
        call(
            "run_python", code="from pathlib import Path; Path('scratch.py').unlink()"
        ),
        {"answer": "I removed `scratch.py`."},
    )
    result = agent.process_query("Remove the scratch file", max_steps=10)
    assert result["status"] == "success"
    assert not (tmp_path / "scratch.py").exists()


def test_code_edit_is_not_a_file_deletion_claim(ledger):
    assert ledger.cleanup_gaps("I removed the unused import from `app.py`.") == []


@pytest.mark.parametrize(
    "code",
    [
        "value = '" + "x" * 300 + "/text'",
        "value = 'hello\\x00/world'",
    ],
)
def test_uninspectable_literal_does_not_stop_executor(agent, code):
    script(agent, call("run_python", code=code), {"answer": "Computed the value."})
    assert agent.process_query("Compute a string", max_steps=5)["status"] == "success"


def test_non_directory_parent_does_not_crash_snapshot(ledger, tmp_path):
    (tmp_path / "notadir").write_text("file")
    ledger.requested = ["notadir/out.md"]
    # key(), not str(tmp_path / ...): Windows paths are case-normalized
    # (ntpath.normcase) for the ledger's own bookkeeping, so the raw
    # mixed-case tmp_path string never matches the snapshot's keys there.
    expected_key = ledger.key("notadir/out.md")
    assert ledger.snapshot("run_python", {}) == {expected_key: None}


def test_snapshot_without_validator_does_not_follow_external_symlink(ledger, tmp_path):
    outside = tmp_path.parent / "outside-3983.txt"
    outside.write_text("private")
    try:
        (tmp_path / "summary.md").symlink_to(outside)
    except OSError as error:
        # Windows requires SeCreateSymbolicLinkPrivilege (admin, or Developer
        # Mode) to create a symlink at all -- WinError 1314 means this
        # runner/user has neither, not that the guard under test is broken.
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("symlink creation requires elevated privilege on this host")
        raise
    try:
        assert ledger.snapshot("run_python", {}) == {}
    finally:
        outside.unlink()


def test_binary_save_requires_its_own_write_but_not_a_text_read(tmp_path):
    ledger = CompletionEvidence("Save the image to `out.png`", str(tmp_path))
    ledger.record("generate_image", {}, {"image_path": "other.png"}, True)
    assert gaps(ledger, "Saved to `out.png`.")
    ledger.record("generate_image", {}, {"image_path": "out.png"}, True)
    assert not gaps(ledger, "Saved to `out.png`.")


def test_refused_mutation_does_not_invalidate_a_completed_save(ledger):
    write(ledger)
    read(ledger)
    ledger.record(
        "write_file",
        {"file_path": "summary.md"},
        {"status": "denied"},
        False,
        executed=False,
    )
    assert not gaps(ledger)


def test_unknown_mutation_invalidates_ranges_and_old_archive(ledger, monkeypatch):
    from gaia.agents.base.completion import _UNOBSERVABLE

    write(ledger)
    raw = {"file_path": "summary.md", "content": "old content"}
    ledger.delivered(
        "read_file", {}, raw, {"artifact": "old", "continuation": "read_tool_output"}
    )
    read(ledger, content="old content")
    assert not gaps(ledger)
    monkeypatch.setattr(ledger, "_stamp", lambda p: _UNOBSERVABLE)
    ledger.record(
        "run_python", {}, {}, True, before={ledger.key("summary.md"): _UNOBSERVABLE}
    )
    read(ledger, content="tail", offset=7, next_offset=None)
    assert gaps(ledger)
    page = {"content": "old", "offset": 0, "total_chars": 3}
    ledger.delivered("read_tool_output", {"artifact": "old"}, page, page)
    assert gaps(ledger)
    read(ledger, content="new content")
    assert not gaps(ledger)


def test_native_tool_batch_uses_the_same_completion_evidence(agent):
    script(
        agent,
        {
            "__tool_calls__": [
                {
                    "id": "write",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"file_path": "summary.md", "content": "real"}
                        ),
                    },
                },
                {
                    "id": "read",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "summary.md"}),
                    },
                },
            ],
            "finish_reason": "tool_calls",
        },
        {"answer": "Saved to `summary.md`."},
    )
    result = agent.process_query("Save to `summary.md`", max_steps=10)
    assert result["status"] == "success"
    assert [entry["tool"] for entry in agent._turn_tool_executions] == [
        "write_file",
        "read_file",
    ]


def test_subclass_cannot_reintroduce_an_unsupported_test_claim(agent):
    agent.finalize_answer = lambda answer, conversation: "All tests pass."
    script(agent, {"answer": "I did not run tests."})
    result = agent.process_query("Check the code", max_steps=1)
    assert result["status"] == "incomplete"
    assert "All tests pass." not in result["result"]
    assert agent.console.print_final_answer.call_args.args[0] == result["result"]


@pytest.mark.parametrize(
    "query",
    [
        "What time does the store close?",
        "Can you explain the save button in Word?",
        "Is it safe to store passwords in a browser?",
        "Tell me about the Save the Children charity.",
        "Write a summary of this file: alpha, beta.",
    ],
)
def test_questions_that_mention_saving_answer_normally(agent, query):
    script(agent, {"answer": "Here is the answer."})
    result = agent.process_query(query, max_steps=5)
    assert result["status"] == "success"
    assert not result["completion_gaps"]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Can you save it to `a.md`?", (["a.md"], True)),
        ("Summarize this, then store the result.", ([], False)),
        ("Could you save this to notes.md?", (["notes.md"], True)),
        ("Write a haiku and store it in haiku.txt", (["haiku.txt"], True)),
        ("OK save it to todo.txt", (["todo.txt"], True)),
        ("Write a file called notes.md", (["notes.md"], True)),
        ("Please write it to a file.", ([], True)),
        ("List every U.S. state and save to `states.md`.", (["states.md"], True)),
    ],
)
def test_save_instructions_in_requests_are_still_obligations(query, expected):
    assert save_obligations(query) == expected


def test_symlinked_directory_is_the_same_output(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    ledger = CompletionEvidence(
        f"Save the summary to `{tmp_path / 'link' / 'out.md'}`", str(tmp_path)
    )
    write(ledger, str(real / "out.md"))
    read(ledger, str(real / "out.md"))
    assert not gaps(ledger)


def test_a_write_inside_a_named_folder_fulfills_it(tmp_path):
    ledger = CompletionEvidence("Save it into the `reports/` folder", str(tmp_path))
    write(ledger, "reports/summary.md")
    assert gaps(ledger)
    read(ledger, "reports/summary.md")
    assert not gaps(ledger)


def test_relative_save_resolves_like_the_file_tools(agent, tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.chdir(docs)
    # The real file tools report the resolved path they wrote.
    target = str(docs / "notes.txt")
    script(
        agent,
        call("write_file", file_path=target, content="hi"),
        call("read_file", file_path=target),
        {"answer": "Saved to `notes.txt`."},
    )
    result = agent.process_query("Save a greeting to `notes.txt`", max_steps=10)
    assert result["status"] == "success", result["completion_gaps"]
    assert (docs / "notes.txt").read_text() == "hi"


def test_connection_error_is_not_replaced_by_the_completion_report(agent):
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = ConnectionError("server down")
    result = agent.process_query("Save the report to `out.md`", max_steps=3)
    assert result["status"] == "failed"
    assert "No successful write" in result["result"]
    assert result["result"].index("No successful write") > 0


@pytest.mark.parametrize(
    "answer",
    [
        "Deleted files on Windows go to the Recycle Bin.",
        "Removed items stay in the archive for 30 days.",
    ],
)
def test_explaining_deletion_is_not_a_cleanup_claim(ledger, answer):
    assert ledger.cleanup_gaps(answer) == []


def test_terse_cleanup_report_naming_a_path_is_still_checked(ledger, tmp_path):
    (tmp_path / "scratch.py").write_text("still here")
    assert ledger.cleanup_gaps("Deleted `scratch.py`.")


@pytest.mark.parametrize(
    "answer",
    [
        "We removed Node.js 16 support in v2.0.",
        "I also removed README.md references from the summary.",
        "I deleted `gone.txt`.",
    ],
)
def test_removal_claims_that_match_the_disk_are_not_flagged(ledger, tmp_path, answer):
    (tmp_path / "README.md").write_text("present")
    assert ledger.cleanup_gaps(answer) == []


@pytest.mark.parametrize(
    "answer",
    [
        "I wrote a function below that saves to output.txt.",
        "Once saved to disk, the file is at report.md.",
    ],
)
def test_describing_code_or_conditions_is_not_a_save_claim(tmp_path, answer):
    ledger = CompletionEvidence("Write a function that saves results.", str(tmp_path))
    assert gaps(ledger, answer) == []


@pytest.mark.parametrize(
    "answer",
    [
        "I saved the summary that you asked for to `summary.md`.",
        "I've written the notes, which cover all five topics, to notes.md.",
        "The report that you wanted has been saved to `report.md`.",
    ],
)
def test_claims_with_a_relative_clause_are_still_checked(tmp_path, answer):
    ledger = CompletionEvidence(
        "Summarize what we discussed and save it", str(tmp_path)
    )
    assert gaps(ledger, answer)


@pytest.mark.parametrize(
    "query, answer",
    [
        ("Where are my Chrome downloads?", "Downloads are saved to ~/Downloads."),
        ("Who made Python?", "Guido van Rossum; programs are saved as .py files."),
    ],
)
def test_information_about_saving_is_not_a_claim(tmp_path, query, answer):
    assert gaps(CompletionEvidence(query, str(tmp_path)), answer) == []


def test_a_save_from_an_earlier_session_is_not_this_turns_claim(tmp_path):
    ledger = CompletionEvidence("Where did you save the report?", str(tmp_path))
    assert gaps(ledger, "I saved it to `report.md` in our previous session.") == []


def test_an_ordinary_edit_needs_no_readback(agent, tmp_path):
    (tmp_path / "notes.md").write_text("teh")
    script(
        agent,
        call("write_file", file_path="notes.md", content="the"),
        {"answer": "Fixed the typo."},
    )
    result = agent.process_query("Fix the typo in notes.md", max_steps=5)
    assert result["status"] == "success", result["completion_gaps"]


def test_removing_a_file_from_an_index_is_not_a_deletion(ledger, tmp_path):
    (tmp_path / "report.pdf").write_text("still here")
    assert ledger.cleanup_gaps("I removed report.pdf from the index.") == []
    assert ledger.cleanup_gaps("I removed `report.pdf`.")


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Write a Node.js server that returns hello", ([], False)),
        ("Write a summary of the meeting to summary.md", (["summary.md"], True)),
        ("Put them in actions.json", (["actions.json"], True)),
        ("Save the summary to summary.md, not to notes.md", (["summary.md"], True)),
        ("Save the list to the reports folder", ([], True)),
    ],
)
def test_write_put_and_negated_targets(query, expected):
    assert save_obligations(query) == expected


def test_malformed_paths_never_crash_the_turn(agent):
    script(
        agent,
        call("write_file", file_path="a\u0000b.md", content="x"),
        {"answer": "I saved it to `a\u0000b.md`."},
    )
    result = agent.process_query("Save it to a file", max_steps=5)
    assert result["status"] in {"incomplete", "failed"}


def test_a_first_person_save_claim_is_checked_without_a_save_verb(tmp_path):
    ledger = CompletionEvidence("Create notes.md with a summary of GAIA", str(tmp_path))
    assert gaps(ledger, "I saved the summary to `notes.md`.")


def test_a_trailing_word_keeps_the_destination():
    assert save_obligations("Save it to out/notes.txt thanks") == (
        ["out/notes.txt"],
        True,
    )


def test_a_shell_save_read_back_this_turn_is_complete(agent, tmp_path):
    script(
        agent,
        call(
            "run_python",
            code="from pathlib import Path; Path('listing.txt').write_text('a b')",
        ),
        call("read_file", file_path="listing.txt"),
        {"answer": "I saved the listing to `listing.txt`."},
    )
    result = agent.process_query("Save the directory listing to a file", max_steps=6)
    assert result["status"] == "success", result["completion_gaps"]


@pytest.mark.parametrize(
    "answer",
    [
        "I think downloads are saved to ~/Downloads by default.",
        "From what I can tell, your settings are stored in ~/.gaia/config.json.",
    ],
)
def test_a_hedged_answer_is_not_a_first_person_claim(tmp_path, answer):
    assert (
        gaps(CompletionEvidence("Where do downloads go?", str(tmp_path)), answer) == []
    )


@pytest.mark.parametrize(
    "answer",
    ["We saved the summary to notes.md.", "Created notes.md with the summary."],
)
def test_create_names_an_output_for_any_claim_form(tmp_path, answer):
    ledger = CompletionEvidence("Create notes.md with a summary", str(tmp_path))
    assert gaps(ledger, answer)


def test_create_a_project_is_not_a_file(tmp_path):
    assert save_obligations("Create a Node.js app that serves pages") == ([], False)
    for query in (
        "Create a summary of the errors in app.log",
        "Make sure the imports are sorted in main.py",
        "Create a Next.js landing page",
        "Make a Node.js based REST API",
    ):
        assert save_obligations(query) == ([], False), query
    assert save_obligations("Make a notes.md file summarizing it") == (
        ["notes.md"],
        True,
    )


@pytest.mark.parametrize(
    "code",
    [
        # The run that touched the file failed.
        "import sys; open('summary.md', 'w').write('x'); sys.exit(1)",
        # The run succeeded but wrote a different file than the claim names.
        "open('run.log', 'a').write('started')",
    ],
)
def test_shell_files_count_only_from_a_successful_run_of_the_named_path(
    agent, tmp_path, code
):
    script(
        agent,
        call("run_python", code=code),
        call("read_file", file_path="summary.md" if "summary" in code else "run.log"),
        {"answer": "I saved the summary to a file."},
    )
    result = agent.process_query("Save the summary to a file", max_steps=4)
    assert result["status"] == "incomplete"


@pytest.mark.parametrize(
    "answer",
    [
        "I've successfully saved the summary to notes.md.",
        "I have now saved the summary to notes.md.",
        "I successfully wrote the summary to notes.md.",
    ],
)
def test_adverbs_do_not_hide_a_first_person_claim(tmp_path, answer):
    ledger = CompletionEvidence("Summarize GAIA's key features", str(tmp_path))
    assert gaps(ledger, answer)


def test_an_impersonal_claim_gets_one_correction_then_stands(agent):
    info = {"answer": "Downloads are saved to ~/Downloads by default."}
    sent = script(agent, info, info)
    result = agent.process_query("Where do downloads go?", max_steps=5)
    assert result["status"] == "success"
    assert "[check:completion]" in sent[1][-1]["content"]
    assert "~/Downloads" in result["result"]


@pytest.mark.parametrize(
    "query, expected",
    [
        # "in X" is left to the claim check: X may be a source outside the cwd.
        ("Create a summary of GAIA in notes.md", ([], False)),
        ("Create a summary of the errors in app.log", ([], False)),
        ("Create report.md summarizing the project", (["report.md"], True)),
        ("Create a Next.js landing page", ([], False)),
    ],
)
def test_create_names_only_its_direct_object(tmp_path, query, expected):
    (tmp_path / "app.log").write_text("ERROR x")
    assert save_obligations(query) == expected
