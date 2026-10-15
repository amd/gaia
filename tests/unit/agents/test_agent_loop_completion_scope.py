# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A finished turn is judged on what happened, not on how the reply arrived.

Covers four ways a correct turn was turned into a failure, or a broken reply
into an answer:

* outputs a script wrote (no tool reports their paths) and a folder named in
  the answer's prose must count as written;
* the read-back a completion check asks for must not be refused as drift;
* a reply stream that closed before the model finished is never an answer;
* a tool call whose JSON is malformed never runs with empty arguments.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, _claims_file_write, _write_stand_in
from gaia.agents.base.completion import CompletionEvidence
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.base.turn_scope import TurnScopeGuard
from gaia.chat.sdk import AgentResponse
from gaia.llm.providers.lemonade import LemonadeProvider, LemonadeStreamCutOffError

# --------------------------------------------------------------------------
# Script-written outputs and folders named in prose
# --------------------------------------------------------------------------


def _gaps(ledger, answer="Done."):
    return ledger.gaps(answer, _claims_file_write)


def _run_script(ledger, effect, successful=True, tool_name="run_python"):
    """One executor call whose code builds its output paths at run time."""
    args = {"code": "import os; os.makedirs(os.path.join(base, 'x'))"}
    before = ledger.snapshot(tool_name, args)
    time.sleep(0.02)  # coarse filesystem clocks must land inside the run
    effect()
    time.sleep(0.02)
    ledger.record(
        tool_name,
        args,
        {"status": "success" if successful else "error"},
        successful,
        before=before,
    )


def _read(ledger, path):
    text = Path(path).read_text()
    result = {"file_path": str(path), "content": text}
    ledger.delivered("read_file", {"file_path": str(path)}, result, result)


def _missing(gaps):
    return [g for g in gaps if g.startswith("No successful write")]


@pytest.mark.parametrize("tool_name", ["run_python", "execute_python_file"])
def test_a_folder_a_script_filled_counts_as_written(tmp_path, tool_name):
    ledger = CompletionEvidence(
        "Reconcile the files and save the outputs into reconciliation/",
        str(tmp_path),
    )
    out = tmp_path / "reconciliation"

    def effect():
        out.mkdir()
        (out / "exceptions.csv").write_text("ref,amount\n1,2\n")
        (out / "summary.xlsx").write_bytes(b"PK\x03\x04")

    _run_script(ledger, effect, tool_name=tool_name)
    gaps = _gaps(ledger)
    assert not _missing(gaps), gaps
    # Text output still needs its read-back; a binary one does not.
    assert any("exceptions.csv" in g and "read back" in g for g in gaps)
    assert not any("summary.xlsx" in g for g in gaps)
    _read(ledger, out / "exceptions.csv")
    assert _gaps(ledger) == []


def test_a_script_write_matches_a_write_file_write(tmp_path):
    """The same deliverable is complete whichever tool produced it."""
    query = "Save the report to `out/report.md`"
    by_tool = CompletionEvidence(query, str(tmp_path / "a"))
    by_script = CompletionEvidence(query, str(tmp_path / "b"))
    for ledger in (by_tool, by_script):
        Path(ledger.root, "out").mkdir(parents=True)
    path_a = Path(by_tool.root, "out", "report.md")
    path_a.write_text("hello")
    by_tool.record("write_file", {"file_path": str(path_a)}, {}, True)
    path_b = Path(by_script.root, "out", "report.md")
    _run_script(by_script, lambda: path_b.write_text("hello"))
    for ledger, path in ((by_tool, path_a), (by_script, path_b)):
        _read(ledger, path)
        assert _gaps(ledger) == []


def test_nested_outputs_under_a_relative_folder_count(tmp_path):
    ledger = CompletionEvidence("Export the tables into reports/2026/", str(tmp_path))
    deep = tmp_path / "reports" / "2026" / "q3" / "detail"

    def effect():
        deep.mkdir(parents=True)
        (deep / "table.bin").write_bytes(b"\x00\x01")

    _run_script(ledger, effect)
    assert _gaps(ledger) == []


def test_a_failed_script_does_not_count(tmp_path):
    ledger = CompletionEvidence("Save the outputs into out/", str(tmp_path))

    def effect():
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "half.csv").write_text("a\n")

    _run_script(ledger, effect, successful=False)
    assert _missing(_gaps(ledger))


def test_a_file_from_before_the_turn_does_not_count(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    old = out / "stale.csv"
    old.write_text("old\n")
    hour_ago = time.time() - 3600
    os.utime(old, (hour_ago, hour_ago))
    ledger = CompletionEvidence("Save the outputs into out/", str(tmp_path))
    _run_script(ledger, lambda: None)
    assert _missing(_gaps(ledger))


def test_a_folder_with_no_script_run_is_not_inspected(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x.csv").write_text("fresh\n")
    ledger = CompletionEvidence("Save the outputs into out/", str(tmp_path))
    assert _missing(_gaps(ledger))


def test_a_folder_outside_the_read_boundary_is_not_listed(tmp_path):
    denied = MagicMock()
    denied.validate_read.return_value = (False, "outside allowed paths")
    ledger = CompletionEvidence(
        "Save the outputs into out/", str(tmp_path), validator=denied
    )

    def effect():
        (tmp_path / "out").mkdir()
        (tmp_path / "out" / "x.csv").write_text("a\n")

    _run_script(ledger, effect)
    assert _missing(_gaps(ledger))


def test_scratch_files_inside_the_folder_do_not_count(tmp_path):
    scratch = tmp_path / "out" / ".scratch"
    ledger = CompletionEvidence(
        "Save the outputs into out/", str(tmp_path), scratch=str(scratch)
    )

    def effect():
        scratch.mkdir(parents=True)
        (scratch / "build.py").write_text("print(1)\n")

    _run_script(ledger, effect)
    assert _missing(_gaps(ledger))


def test_a_folder_named_in_prose_is_backed_by_writes_inside_it(tmp_path):
    """The answer names the task folder by its bare name; the cwd is elsewhere."""
    folder = tmp_path / "work" / "q3-handoff"
    folder.mkdir(parents=True)
    ledger = CompletionEvidence(
        "Build the tracker and draft the email.", str(tmp_path / "elsewhere")
    )
    email = folder / "followup_email.md"
    email.write_text("Hi all\n")
    ledger.record("write_file", {"file_path": str(email)}, {}, True)
    _read(ledger, email)
    answer = "Both files are saved and verified in `q3-handoff`."
    assert _gaps(ledger, answer) == []


def test_a_folder_named_in_prose_with_nothing_in_it_is_still_missing(tmp_path):
    ledger = CompletionEvidence("Draft the email.", str(tmp_path))
    other = tmp_path / "drafts" / "email.md"
    other.parent.mkdir()
    other.write_text("x")
    ledger.record("write_file", {"file_path": str(other)}, {}, True)
    _read(ledger, other)
    gaps = _gaps(ledger, "The email is saved in `outbox`.")
    assert any("outbox" in g for g in _missing(gaps))


def test_files_in_a_folder_named_in_prose_need_their_read_back(tmp_path):
    folder = tmp_path / "deliverables"
    folder.mkdir()
    ledger = CompletionEvidence("Write the memo.", str(tmp_path / "cwd"))
    memo = folder / "memo.md"
    memo.write_text("memo")
    ledger.record("write_file", {"file_path": str(memo)}, {}, True)
    gaps = _gaps(ledger, "The memo is saved in `deliverables`.")
    assert any("memo.md" in g and "read back" in g for g in gaps)


# --------------------------------------------------------------------------
# Turn scope: the read-back a check asks for
# --------------------------------------------------------------------------


def test_a_file_inside_a_requested_output_folder_is_in_scope(tmp_path):
    scope = TurnScopeGuard(failure_limit=4)
    scope.begin_turn("Save the outputs into reconciliation/", str(tmp_path))
    scope.mark_answered()
    inside = str(tmp_path / "reconciliation" / "exceptions.csv")
    assert scope.check("read_file", {"file_path": inside}) is None
    relative = os.path.join("reconciliation", "deep", "x.csv")
    assert scope.check("read_file", {"file_path": relative}) is None


def test_a_sibling_sharing_the_folders_prefix_is_not_in_scope(tmp_path):
    scope = TurnScopeGuard(failure_limit=4)
    scope.begin_turn("Save the outputs into reports/", str(tmp_path))
    scope.mark_answered()
    sibling = str(tmp_path / "reports2" / "x.csv")
    assert scope.check("read_file", {"file_path": sibling}) is not None


def test_a_check_naming_a_folder_opens_its_files(tmp_path):
    scope = TurnScopeGuard(failure_limit=4)
    scope.begin_turn("Reconcile the ledger.", str(tmp_path))
    scope.mark_answered()
    folder = tmp_path / "reconciliation"
    target = str(folder / "exceptions.csv")
    assert scope.check("read_file", {"file_path": target}) is not None
    scope.widen(f"[check:completion] No successful write to `{folder}`.")
    assert scope.check("read_file", {"file_path": target}) is None
    assert scope.check("read_file", {"file_path": "notes/other.md"}) is not None


@pytest.mark.parametrize("named", [".", "{root}", "{parent}"])
def test_the_working_folder_never_opens_everything(tmp_path, named):
    scope = TurnScopeGuard(failure_limit=4)
    root = tmp_path / "proj"
    root.mkdir()
    scope.begin_turn("Fix the bug.", str(root))
    scope.mark_answered()
    text = named.format(root=root, parent=tmp_path)
    scope.widen(f"[check:completion] No successful write to `{text}/`.")
    assert scope.check("read_file", {"file_path": str(root / "a.py")}) is not None


class LoopAgent(Agent):
    def _get_system_prompt(self):
        return "Use the tools."

    def _register_tools(self):
        @tool
        def read_file(file_path: str) -> dict:
            """Read a file."""
            return {
                "status": "success",
                "file_path": file_path,
                "content": Path(file_path).read_text(),
            }

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            Path(file_path).write_text(content)
            self.writes.append((file_path, content))
            return {"status": "success", "file_path": file_path}

        @tool
        def run_python(code: str) -> dict:
            """Execute Python in a real child process."""
            done = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=False,
            )
            return {
                "status": "success" if done.returncode == 0 else "error",
                "stdout": done.stdout,
                "stderr": done.stderr,
                "return_code": done.returncode,
            }


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = LoopAgent(silent_mode=True, skip_lemonade=True)
    agent.writes = []
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def call(name, **args):
    return {"tool": name, "tool_args": args}


def script(agent, *turns):
    """Non-streaming replies; a str is sent verbatim, anything else as JSON."""
    sent = []

    def send(messages, *a, **kw):
        sent.append([dict(m) for m in messages])
        assert turns_left, "unexpected model call"
        turn = turns_left.pop(0)
        text = turn if isinstance(turn, str) else json.dumps(turn)
        return MagicMock(text=text, stats={}, finish_reason="stop")

    turns_left = list(turns)
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    return sent


def _tool_results(sent):
    return [str(m["content"]) for m in sent[-1] if m.get("role") == "tool"]


_BUILD = (
    "import os\n"
    "out = os.path.join(os.getcwd(), 're' + 'ports')\n"
    "os.makedirs(out, exist_ok=True)\n"
    "open(os.path.join(out, 'summary.csv'), 'w').write('total,3\\n')\n"
)


def test_a_script_built_deliverable_completes_after_its_read_back(agent, tmp_path):
    """The script names no path literally; the read-back after the check runs."""
    sent = script(
        agent,
        call("run_python", code=_BUILD),
        {"answer": "The summary is in reports/summary.csv."},
        call("read_file", file_path=str(tmp_path / "reports" / "summary.csv")),
        {"answer": "The summary is in reports/summary.csv: total 3."},
    )
    result = agent.process_query("Summarize the data and save it into reports/")
    assert "No successful write" not in str(sent)
    assert not any("already answered" in r for r in _tool_results(sent))
    assert "total,3" in " ".join(_tool_results(sent))
    assert result["status"] == "success", result["result"]
    assert result["result"].startswith("The summary is in reports/summary.csv: total")


def test_the_work_a_completion_check_asks_for_is_not_refused(agent, tmp_path):
    """Sent back for a missing save, the agent may read its input to write it."""
    (tmp_path / "data.csv").write_text("SENTINEL-INPUT\n")
    sent = script(
        agent,
        {"answer": "Here is the summary: three rows."},
        call("read_file", file_path=str(tmp_path / "data.csv")),
        call("write_file", file_path="out/summary.md", content="three rows\n"),
        call("read_file", file_path="out/summary.md"),
        {"answer": "Saved the summary to `out/summary.md`: three rows."},
    )
    result = agent.process_query("Summarize the data and save it to `out/summary.md`")
    assert "[check:completion]" in str(sent[1][-1]["content"])
    results = " ".join(
        str(m["content"]) for turn in sent for m in turn if m.get("role") == "tool"
    )
    assert "SENTINEL-INPUT" in results
    assert "already answered" not in results
    assert result["status"] == "success", result["result"]


def test_a_missing_save_is_still_reported_after_the_re_ask(agent):
    script(
        agent,
        {"answer": "Saved the summary to `out/summary.md`."},
        {"answer": "Saved the summary to `out/summary.md`."},
    )
    result = agent.process_query("Save the summary to `out/summary.md`")
    assert result["status"] == "incomplete"
    assert "No successful write" in result["result"]


# --------------------------------------------------------------------------
# A reply stream that closed before the model finished
# --------------------------------------------------------------------------


def _provider_stream(chunks):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = iter(chunks)
        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        tools = [{"type": "function", "function": {"name": "run_python"}}]
        return list(
            provider.chat(
                messages=[{"role": "user", "content": "go"}],
                stream=True,
                tools=tools,
            )
        )


def _text(content):
    return {"choices": [{"delta": {"content": content}}]}


def _call_fragment(arguments, name=None):
    function = {"arguments": arguments, **({"name": name} if name else {})}
    return {
        "choices": [
            {"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": function}]}}
        ]
    }


_FINISH = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
_USAGE = {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


@pytest.mark.parametrize(
    "chunks",
    [
        pytest.param([], id="nothing-arrived"),
        pytest.param([_text("Let me build the three")], id="mid-prose"),
        pytest.param(
            [{"choices": [{"delta": {"reasoning_content": "First I"}}]}],
            id="mid-reasoning",
        ),
        pytest.param(
            [_text("Now: "), _text('{"tool": "run_python", "tool_args": {"code": "x')],
            id="mid-text-tool-call",
        ),
        pytest.param(
            [_call_fragment('{"code": "import pan', name="run_python")],
            id="mid-native-arguments",
        ),
        pytest.param(
            [_call_fragment('{"code": "print(1)"}', name="run_python")],
            id="call-complete-but-no-finish",
        ),
    ],
)
def test_a_stream_that_never_finished_raises_at_every_boundary(chunks):
    with pytest.raises(LemonadeStreamCutOffError):
        _provider_stream(chunks)


@pytest.mark.parametrize(
    "chunks",
    [
        pytest.param([_text("Done."), _FINISH], id="finish-no-usage"),
        pytest.param([_text("Done."), _FINISH, _USAGE], id="finish-and-usage"),
        pytest.param([_text("Done."), _USAGE], id="usage-no-finish"),
        pytest.param(
            [_text("Cut"), {"choices": [{"delta": {}, "finish_reason": "length"}]}],
            id="length-is-its-own-signal",
        ),
    ],
)
def test_a_stream_the_server_ended_is_returned(chunks):
    assert "".join(_provider_stream(chunks)).startswith(("Done.", "Cut"))


def test_a_cut_off_native_call_is_never_emitted_as_a_call():
    with pytest.raises(LemonadeStreamCutOffError):
        for chunk in _provider_stream_iter(
            [_call_fragment('{"code": "print(1)"}', name="run_python")]
        ):
            assert "__tool_calls__" not in chunk


def _provider_stream_iter(chunks):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = iter(chunks)
        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
        yield from provider.chat(
            messages=[{"role": "user", "content": "go"}],
            stream=True,
            tools=[{"type": "function", "function": {"name": "run_python"}}],
        )


def _streams(agent, *replies):
    """Streaming replies; a list of text pieces ending in an exception is a cut."""
    calls = []

    def stream(*a, **kw):
        assert replies_left, "unexpected model call"
        reply = replies_left.pop(0)
        calls.append(reply)
        pieces = reply if isinstance(reply, list) else [json.dumps(reply)]
        for piece in pieces:
            if isinstance(piece, BaseException):
                raise piece
            yield AgentResponse(text=piece, is_complete=False)
        yield AgentResponse(text="", is_complete=True, stats={}, finish_reason="stop")

    replies_left = list(replies)
    agent.streaming = True
    agent.chat = MagicMock()
    agent.chat.send_messages_stream.side_effect = stream
    return calls


_FRAGMENT = [
    "Now I understand the data. Let me build it.\n\n",
    '{"thought": "build", "tool": "write_file", "tool_args": {"file_path": "a.md"',
    LemonadeStreamCutOffError(),
]


def test_a_cut_off_reply_is_requested_again_not_answered(agent, tmp_path):
    calls = _streams(
        agent,
        _FRAGMENT,
        call("write_file", file_path="a.md", content="real\n"),
        {"answer": "Wrote a.md."},
    )
    result = agent.process_query("Write a.md with the word real.")
    assert len(calls) == 3
    assert result["result"].startswith("Wrote a.md.")
    assert "Let me build it" not in result["result"]
    assert agent.writes == [("a.md", "real\n")]
    assert result["status"] == "success"


def test_a_reply_cut_off_every_time_fails_loudly(agent):
    calls = _streams(agent, _FRAGMENT, _FRAGMENT, _FRAGMENT, _FRAGMENT)
    result = agent.process_query("Write a.md.")
    assert len(calls) == 3
    assert "stopped partway through" in result["result"]
    assert "Let me build it" not in result["result"]
    assert agent.writes == []
    assert result["status"] == "failed"


def test_a_cut_off_reply_never_marks_the_turn_answered(agent, tmp_path):
    """Answered too early, the scope guard used to refuse the real work."""
    (tmp_path / "in.csv").write_text("SENTINEL-IN\n")
    _streams(
        agent,
        ["Let me read the input.\n", LemonadeStreamCutOffError()],
        call("read_file", file_path="in.csv"),
        {"answer": "The input has one row."},
    )
    result = agent.process_query("How many rows are in in.csv?")
    assert result["result"].startswith("The input has one row.")
    assert "SENTINEL-IN" in str(result["conversation"])
    assert "already answered" not in str(result["conversation"])


def test_plain_text_ending_inside_a_tool_call_is_not_an_answer(agent):
    reply = 'Let me build the files.\n\n{"thought": "x", "tool": "run_python", "tool_args": {"code": "import os\\nprint('
    with pytest.raises(ValueError, match="unfinished 'run_python' tool call"):
        agent._parse_llm_response(reply)


@pytest.mark.parametrize(
    "reply",
    [
        "Use braces like {this} in templates.",
        'The config looks like {"name": "x"} and that is all.',
        'An example call: {"tool": "read_file", "tool_args": {"file_path": "a"}} works.',
    ],
)
def test_plain_text_with_finished_braces_is_still_parsed(agent, reply):
    parsed = agent._parse_llm_response(reply)
    assert parsed.get("answer") or parsed.get("tool") == "read_file"


# --------------------------------------------------------------------------
# Malformed tool-call JSON never runs the tool with empty arguments
# --------------------------------------------------------------------------

_UNESCAPED_QUOTE = (
    '{"thought": "write it", "goal": "Write the build script", "tool": "write_file", '
    '"tool_args": {"file_path": "build.py", "content": "print("hi")"}}'
)
_CUT_ARGS = (
    '{"thought": "write it", "goal": "Write the script", "tool": "write_file", '
    '"tool_args": {"file_path": "build.py", "content": "import os\\n'
)
_ARGS_NOT_OBJECT = '{"thought": "t", "tool": "write_file", "tool_args": "build.py"'


@pytest.mark.parametrize(
    "reply,reason",
    [
        (_UNESCAPED_QUOTE, "Malformed tool_call arguments"),
        (_CUT_ARGS, "cut off before the JSON object closed"),
        (_ARGS_NOT_OBJECT, "not a JSON object"),
        (_UNESCAPED_QUOTE.replace('"tool_args": ', '"tool_args" :  '), "Malformed"),
    ],
)
def test_malformed_arguments_raise_instead_of_becoming_empty(agent, reply, reason):
    with pytest.raises(ValueError, match=reason):
        agent._parse_llm_response(reply)


def test_a_call_without_arguments_still_parses_through_the_fallback(agent):
    parsed = agent._parse_llm_response(
        '{"thought": "list", "goal": "See the skills", "tool": "list_skills" "x"}'
    )
    assert parsed["tool"] == "list_skills" and parsed["tool_args"] == {}
    assert parsed["goal"] == "See the skills"


def test_the_fallback_never_shows_template_text_as_the_goal(agent):
    no_goal = agent._parse_llm_response('{"thought": "t", "tool": "list_skills" "x"}')
    assert no_goal["goal"] == ""
    answer = agent._parse_llm_response('{"thought": "t", "answer": "Hi" "x"}')
    # A quote not followed by JSON structure is one the model forgot to escape.
    assert answer["answer"] == 'Hi" "x' and answer["goal"] == ""


def test_a_malformed_call_is_retried_and_the_run_is_not_failed(agent):
    script(
        agent,
        _UNESCAPED_QUOTE,
        call("write_file", file_path="build.py", content='print("hi")\n'),
        {"answer": "Wrote build.py."},
    )
    result = agent.process_query("Write build.py that prints hi.")
    assert agent.writes == [("build.py", 'print("hi")\n')]
    assert result["status"] == "success"
    assert not any(
        "Missing required arguments" in str(e) for e in result["error_history"]
    )


def test_malformed_calls_that_never_recover_still_fail(agent):
    script(agent, _UNESCAPED_QUOTE, _UNESCAPED_QUOTE, _UNESCAPED_QUOTE)
    result = agent.process_query("Write build.py that prints hi.")
    assert agent.writes == []
    assert result["status"] == "failed"


# --------------------------------------------------------------------------
# A whole-file write whose content is a stand-in
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ["PLACEHOLDER", "placeholder", "PLACEHOLDER\n", "REAL_SCRIPT", "FULL_SCRIPT_HERE"],
)
def test_a_stand_in_write_never_runs(agent, content):
    result = agent._execute_tool(
        "write_file", {"file_path": "m.py", "content": content}
    )
    assert result["status"] == "error" and "NOT run" in result["error"]
    assert agent.writes == []


@pytest.mark.parametrize("content", ["OK", "README", "x = 1\n", "# TODO\nprint(1)\n"])
def test_short_real_content_is_written(content):
    assert (
        _write_stand_in("write_file", {"file_path": "m.py", "content": content}) is None
    )


# --------------------------------------------------------------------------
# A large script output may be checked in part
# --------------------------------------------------------------------------


def _summary_run(ledger, code, stdout, successful=True, tool_name="run_python"):
    args = {"code": code} if tool_name == "run_python" else {"command": code}
    before = ledger.snapshot(tool_name, args)
    ledger.record(
        tool_name,
        args,
        {"status": "success" if successful else "error", "stdout": stdout},
        successful,
        before=before,
    )


def _big_csv(path, megabytes=50):
    row = "ref,amount,vendor,area\n" + "1234567890,25000.00,Acme Ltd,Estates\n" * 1
    chunk = row * 20000
    with open(path, "w") as f:
        while f.tell() < megabytes * 1024 * 1024:
            f.write(chunk)


@pytest.fixture
def big_output(tmp_path):
    ledger = CompletionEvidence("Save the cleaned data into out/", str(tmp_path))
    out = tmp_path / "out"
    target = out / "clean.csv"

    def effect():
        out.mkdir()
        _big_csv(target)

    _run_script(ledger, effect)
    return ledger, target


def test_a_50mb_script_output_checked_by_row_count_is_complete(big_output):
    ledger, target = big_output
    _summary_run(
        ledger,
        f"import pandas as pd; print(len(pd.read_csv(r'{target}')))",
        "1310720\n",
    )
    assert _gaps(ledger) == []


@pytest.mark.parametrize(
    "command", ["wc -l out/clean.csv", "tail -n 3 out\clean.csv", "head clean.csv"]
)
def test_a_shell_head_tail_or_count_checks_a_large_output(big_output, command):
    ledger, _ = big_output
    _summary_run(ledger, command, "ref,amount\n", tool_name="run_shell_command")
    assert _gaps(ledger) == []


def test_a_range_read_checks_a_large_output(big_output):
    ledger, target = big_output
    result = {
        "file_path": str(target),
        "content": "1 | ref,amount,vendor,area\n",
        "start_line": 1,
    }
    ledger.delivered("read_file", {"file_path": str(target)}, result, result)
    assert _gaps(ledger) == []


def test_a_large_output_never_checked_still_needs_a_check(big_output):
    ledger, _ = big_output
    assert any("clean.csv" in g and "read back" in g for g in _gaps(ledger))


def test_a_summary_script_that_crashed_is_not_a_check(big_output):
    ledger, target = big_output
    _summary_run(
        ledger,
        f"import pandas as pd; print(len(pd.read_csv(r'{target}')))",
        "Traceback (most recent call last):\nMemoryError\n",
        successful=False,
    )
    assert any("clean.csv" in g for g in _gaps(ledger))


def test_a_summary_that_printed_nothing_is_not_a_check(big_output):
    ledger, target = big_output
    _summary_run(ledger, f"open(r'{target}').read()", "")
    assert any("clean.csv" in g for g in _gaps(ledger))


def test_a_summary_of_another_file_is_not_a_check(big_output):
    ledger, _ = big_output
    _summary_run(ledger, "print(len(open('raw.csv').readlines()))", "12\n")
    _summary_run(ledger, "print(len(open('unclean.csv').readlines()))", "12\n")
    assert any("clean.csv" in g for g in _gaps(ledger))


def test_a_run_that_rewrites_the_output_is_not_a_check_of_it(big_output):
    ledger, target = big_output
    code = f"open(r'{target}', 'a').write('x\n'); print('appended')"

    def run():
        args = {"code": code}
        before = ledger.snapshot("run_python", args)
        time.sleep(0.02)
        with open(target, "a") as f:
            f.write("x\n")
        ledger.record(
            "run_python", args, {"status": "success", "stdout": "ok\n"}, True, before
        )

    run()
    assert any("clean.csv" in g for g in _gaps(ledger))


@pytest.mark.parametrize("body", ["", "ref,amount\n1,2\n"], ids=["empty", "truncated"])
def test_a_partial_read_of_a_small_output_is_not_enough(tmp_path, body):
    """An empty or cut-short output is small, so it must be read whole."""
    ledger = CompletionEvidence("Save the cleaned data into out/", str(tmp_path))
    target = tmp_path / "out" / "clean.csv"

    def effect():
        target.parent.mkdir()
        target.write_text(body)

    _run_script(ledger, effect)
    result = {"file_path": str(target), "content": body[:4], "start_line": 1}
    ledger.delivered("read_file", {"file_path": str(target)}, result, result)
    _summary_run(ledger, "print(open('out/clean.csv').readline())", body[:10] or "\n")
    assert any("clean.csv" in g and "read back" in g for g in _gaps(ledger))


def test_an_empty_or_failed_read_of_a_large_output_is_not_a_check(big_output):
    ledger, target = big_output
    for result in (
        {"file_path": str(target), "content": "", "start_line": 1},
        {"file_path": str(target), "status": "error", "error": "denied"},
    ):
        ledger.delivered("read_file", {"file_path": str(target)}, result, result)
    assert any("clean.csv" in g for g in _gaps(ledger))


def test_a_large_file_written_by_write_file_still_needs_a_full_read(tmp_path):
    ledger = CompletionEvidence("Save the data to `out.csv`", str(tmp_path))
    target = tmp_path / "out.csv"
    _big_csv(target, megabytes=1)
    ledger.record("write_file", {"file_path": str(target)}, {}, True)
    result = {"file_path": str(target), "content": "ref,amount\n", "start_line": 1}
    ledger.delivered("read_file", {"file_path": str(target)}, result, result)
    assert any("out.csv" in g and "read back" in g for g in _gaps(ledger))
